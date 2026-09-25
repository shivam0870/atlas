"""Permission-bound source synchronization and evidence-backed knowledge workflows.

All document reads, including scheduled runs, use the application role and a current
user authority. Stored text is never interpreted as executable instructions.
"""

import asyncio
import difflib
import fnmatch
import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import InsufficientResources, UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from atlas import serving
from atlas.auth import ROLE_SCOPES, Identity, authenticate, require
from atlas.db import (
    access_context,
    application_transactions,
    bind_identity,
    identity_transaction,
    transaction,
)
from atlas.extraction import MAX_UPLOAD_BYTES, ExtractionError, extract_upload
from atlas.knowledge import accessible, invalidate, store_document

router = APIRouter(prefix="/api/workbench", tags=["workbench"])
MAX_SOURCE_FILES = 100
MAX_SYNC_BYTES = 25 * 1024 * 1024
MAX_GITHUB_RESPONSE_BYTES = 8 * 1024 * 1024
TEXT_SUFFIXES = {".md", ".txt", ".pdf", ".docx"}


def person(identity):
    if not identity.user_id:
        raise HTTPException(403, "Sign in as a workspace member to use this workflow")
    return identity.user_id


class SourceBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["github", "folder"]
    location: str = Field(min_length=1, max_length=400)
    space_id: UUID
    include_patterns: list[str] = Field(
        default_factory=lambda: ["**/*.md", "**/*.txt"], max_length=20
    )
    interval_hours: int = Field(default=24, ge=1, le=168)
    enabled: bool = True

    @field_validator("include_patterns")
    @classmethod
    def safe_patterns(cls, value):
        if not value or any(len(p) > 200 or ".." in p or p.startswith("/") for p in value):
            raise ValueError("Use 1–20 relative glob patterns without parent traversal")
        return value


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    include_patterns: list[str] | None = Field(default=None, max_length=20)
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    enabled: bool | None = None


class CompareBody(BaseModel):
    left_document_id: UUID
    left_version_id: UUID | None = None
    right_document_id: UUID
    right_version_id: UUID | None = None


class RelationshipBody(BaseModel):
    source_kind: Literal["document", "entity"]
    source_id: UUID
    target_kind: Literal["document", "entity"]
    target_id: UUID
    label: str = Field(default="related to", min_length=1, max_length=80)


class StepBody(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=4000)
    document_id: UUID | None = None
    version_id: UUID | None = None
    assignee_user_id: UUID | None = None
    requires_approval: bool = False


class PlaybookBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    space_id: UUID
    status: Literal["draft", "published"] = "draft"
    steps: list[StepBody] = Field(default_factory=list, max_length=50)

    @field_validator("steps")
    @classmethod
    def unique_steps(cls, value):
        if len({s.id for s in value}) != len(value):
            raise ValueError("Step identifiers must be unique")
        return value


class DraftBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    space_id: UUID
    document_ids: list[UUID] = Field(min_length=1, max_length=10)


class RunBody(BaseModel):
    name: str = Field(default="", max_length=120)


class StepProgress(BaseModel):
    completed: bool | None = None
    approved: bool | None = None
    note: str | None = Field(default=None, max_length=1000)


class BriefingBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    space_ids: list[UUID] = Field(default_factory=list, max_length=30)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    question: str = Field(default="", max_length=500)
    cadence: Literal["daily", "weekly"] = "weekly"
    enabled: bool = True


class BriefingPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    space_ids: list[UUID] | None = Field(default=None, max_length=30)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)
    question: str | None = Field(default=None, max_length=500)
    cadence: Literal["daily", "weekly"] | None = None
    enabled: bool | None = None


def source_location(kind, location):
    """Only GitHub's fixed HTTPS API or an operator-approved folder alias is accepted."""
    if kind == "github":
        location = location.removeprefix("https://github.com/").rstrip("/").removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", location):
            raise HTTPException(422, "Enter a GitHub repository as owner/repository")
    elif not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", location):
        raise HTTPException(422, "Use an operator-configured folder alias")
    return location


def configured_folders():
    """ATLAS_SOURCE_FOLDERS is a JSON object of tenant-qualified aliases to absolute paths."""
    try:
        value = json.loads(os.getenv("ATLAS_SOURCE_FOLDERS", "{}"))
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def folder_root(tenant, alias):
    configured = configured_folders().get(f"{tenant}:{alias}")
    if not isinstance(configured, str) or not Path(configured).is_absolute():
        raise HTTPException(422, "This folder alias has not been approved for this workspace")
    root = Path(configured).resolve(strict=True)
    if not root.is_dir():
        raise HTTPException(422, "The configured source folder is unavailable")
    return root


def read_confined_file(root, relative):
    """Open each path component without following symlinks, including concurrent swaps."""
    parts = PurePosixPath(relative).parts
    if (
        PurePosixPath(relative).is_absolute()
        or not parts
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise HTTPException(422, "Invalid source path")
    descriptors = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        for part in parts[:-1]:
            directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            descriptors.append(directory)
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise HTTPException(422, "Only regular source files are supported")
            return handle.read(MAX_UPLOAD_BYTES + 1)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def matches_path(path, patterns):
    return PurePosixPath(path).suffix.lower() in TEXT_SUFFIXES and any(
        fnmatch.fnmatchcase(path, p) or (p.startswith("**/") and fnmatch.fnmatchcase(path, p[3:]))
        for p in patterns
    )


@router.get("/sources")
async def sources(identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT *,last_run_at last_synced_at FROM atlas.source_connections ORDER BY name,id LIMIT 200"
            )
        ).fetchall()
    return {"items": rows}


@router.post("/sources", status_code=201)
async def create_source(body: SourceBody, identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    location = source_location(body.kind, body.location)
    if body.kind == "folder":
        folder_root(identity.tenant_id, location)
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "space", body.space_id, True)
        return await (
            await conn.execute(
                "INSERT INTO atlas.source_connections(tenant_id,id,name,kind,location,space_id,include_patterns,interval_hours,enabled,user_id,session_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    body.name,
                    body.kind,
                    location,
                    body.space_id,
                    body.include_patterns,
                    body.interval_hours,
                    body.enabled,
                    person(identity),
                    identity.session_id,
                ),
            )
        ).fetchone()


async def source_row(conn, source_id, lock=False):
    row = await (
        await conn.execute(
            "SELECT * FROM atlas.source_connections WHERE id=%s" + (" FOR UPDATE" if lock else ""),
            (source_id,),
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, "Source connection unavailable")
    return row


@router.patch("/sources/{source_id}")
async def edit_source(
    source_id: UUID, body: SourcePatch, identity: Identity = Depends(authenticate)
):
    require(identity, "admin")
    async with transaction(identity.tenant_id) as conn:
        row = await source_row(conn, source_id, True)
        update = body.model_dump(exclude_unset=True)
        if any(value is None for value in update.values()):
            raise HTTPException(422, "Source settings cannot be null")
        row.update(update)
        SourceBody(**row)
        return await (
            await conn.execute(
                "UPDATE atlas.source_connections SET name=%s,include_patterns=%s,interval_hours=%s,enabled=%s,user_id=%s,session_id=%s,last_error=NULL,next_run_at=now(),updated_at=now() WHERE id=%s RETURNING *",
                (
                    row["name"],
                    row["include_patterns"],
                    row["interval_hours"],
                    row["enabled"],
                    person(identity),
                    identity.session_id,
                    source_id,
                ),
            )
        ).fetchone()


@router.delete("/sources/{source_id}")
async def delete_source(source_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    async with transaction(identity.tenant_id) as conn:
        await source_row(conn, source_id, True)
        await conn.execute("DELETE FROM atlas.source_connections WHERE id=%s", (source_id,))
    return {"deleted": True}


@router.get("/sources/{source_id}/runs")
async def source_runs(source_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    async with transaction(identity.tenant_id) as conn:
        await source_row(conn, source_id)
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.source_runs WHERE source_id=%s ORDER BY created_at DESC LIMIT 100",
                (source_id,),
            )
        ).fetchall()
    return {"items": rows}


async def github_json(client, path):
    # Cap decoded response bytes while streaming; checking response.content would
    # allow a remote response to exhaust memory before the limit is enforced.
    async with client.stream("GET", "https://api.github.com/" + path) as response:
        if response.status_code != 200:
            raise HTTPException(422, "GitHub source is unavailable or rate limited")
        payload = bytearray()
        async for part in response.aiter_bytes(chunk_size=65536):
            if len(payload) + len(part) > MAX_GITHUB_RESPONSE_BYTES:
                raise HTTPException(422, "Repository response exceeds the source size limit")
            payload.extend(part)
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise HTTPException(422, "Unsupported GitHub source response")
        return result


async def workflow_request_limit(identity):
    quota = await serving.limits(identity.tenant_id)
    await serving.rate_limit(identity.tenant_id, quota["requests_per_minute"])


def briefing_terms(question):
    """Extract topic words, without interpreting conversational text as search syntax.

    Digest questions commonly contain instructions such as 'summarize changes to'.
    Requiring those words in the source silently drops relevant documents. Keep
    distinct subject words and match any of them; the selected spaces/documents
    remain the mandatory scope of every query.
    """
    framing = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "can",
        "changed",
        "changes",
        "change",
        "could",
        "daily",
        "describe",
        "digest",
        "do",
        "does",
        "for",
        "from",
        "give",
        "happened",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "latest",
        "me",
        "monday",
        "my",
        "new",
        "of",
        "on",
        "or",
        "our",
        "please",
        "recent",
        "review",
        "show",
        "since",
        "summarize",
        "summary",
        "tell",
        "than",
        "that",
        "the",
        "their",
        "them",
        "these",
        "this",
        "to",
        "updated",
        "updates",
        "was",
        "we",
        "weekly",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "would",
        "yesterday",
        "you",
        "your",
        "every",
        "last",
        "week",
        "month",
        "today",
        "about",
    }
    return list(
        dict.fromkeys(
            word
            for word in re.findall(r"[^\W_]+", question.casefold())
            if word not in framing and len(word) > 1
        )
    )[:32]


async def source_files(source):
    """Take a bounded complete snapshot before importing, so partial listings never delete."""
    if source["kind"] == "folder":

        def read_folder():
            root = folder_root(source["tenant_id"], source["location"])
            files: list[tuple[str, bytes]] = []
            total, inspected = 0, 0
            for candidate in root.rglob("*"):
                inspected += 1
                if inspected > 10000:
                    raise HTTPException(422, "Folder listing exceeds 10,000 entries")
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                relative = candidate.relative_to(root).as_posix()
                if not matches_path(relative, source["include_patterns"]):
                    continue
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    raise HTTPException(422, "Source path leaves its approved folder")
                data = read_confined_file(root, relative)
                total += len(data)
                if (
                    len(data) > MAX_UPLOAD_BYTES
                    or total > MAX_SYNC_BYTES
                    or len(files) >= MAX_SOURCE_FILES
                ):
                    raise HTTPException(422, "Source exceeds the bounded sync limit")
                files.append((relative, data))
            return files

        return await asyncio.to_thread(read_folder)
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=False,
        trust_env=False,
        headers={"Accept": "application/vnd.github+json"},
    ) as client:
        repo = quote(source["location"], safe="/")
        metadata = await github_json(client, f"repos/{repo}")
        branch = quote(metadata.get("default_branch", "main"), safe="")
        tree = await github_json(client, f"repos/{repo}/git/trees/{branch}?recursive=1")
        if tree.get("truncated"):
            raise HTTPException(422, "Repository listing is incomplete; narrow the source")
        selected = [
            entry
            for entry in tree.get("tree", [])
            if entry.get("type") == "blob"
            and entry.get("mode") != "120000"
            and matches_path(entry["path"], source["include_patterns"])
        ]
        if (
            len(selected) > MAX_SOURCE_FILES
            or sum(entry.get("size", 0) for entry in selected) > MAX_SYNC_BYTES
        ):
            raise HTTPException(422, "Source exceeds 100 files or 25 MiB per sync")
        import base64

        result = []
        total = 0
        for entry in selected:
            if entry.get("size", 0) > MAX_UPLOAD_BYTES or not re.fullmatch(
                r"[0-9a-f]{40,64}", entry["sha"]
            ):
                raise HTTPException(422, "Source file exceeds its size limit")
            blob = await github_json(client, f"repos/{repo}/git/blobs/{entry['sha']}")
            if blob.get("encoding") != "base64":
                raise HTTPException(422, "Unsupported GitHub blob encoding")
            data = base64.b64decode(blob["content"], validate=False)
            total += len(data)
            if len(data) > MAX_UPLOAD_BYTES or total > MAX_SYNC_BYTES:
                raise HTTPException(422, "Source exceeds the bounded sync limit")
            result.append((entry["path"], data))
        return result


async def sync_source(identity, source_id):
    require(identity, "admin")
    await workflow_request_limit(identity)
    run_id = uuid4()
    try:
        async with transaction(identity.tenant_id) as conn:
            source = await source_row(conn, source_id, True)
            await accessible(conn, "space", source["space_id"], True)
            await conn.execute(
                "UPDATE atlas.source_runs SET status='failed',error_code='interrupted',completed_at=now() WHERE source_id=%s AND status='running' AND created_at<now()-interval '10 minutes'",
                (source_id,),
            )
            await conn.execute(
                "INSERT INTO atlas.source_runs(tenant_id,id,source_id) VALUES(%s,%s,%s)",
                (identity.tenant_id, run_id, source_id),
            )
            await conn.execute(
                "UPDATE atlas.source_connections SET next_run_at=now()+make_interval(hours=>interval_hours) WHERE id=%s",
                (source_id,),
            )
    except UniqueViolation as exc:
        raise HTTPException(409, "A sync is already running") from exc
    except InsufficientResources as exc:
        raise HTTPException(429, "Workspace concurrent source sync limit reached") from exc
    counts = {"imported": 0, "unchanged": 0, "archived": 0}
    error = None
    try:
        async with asyncio.timeout(180):
            files = await source_files(source)
            seen = set()
            for path, data in files:
                seen.add(path)
                checksum = hashlib.sha256(data).hexdigest()
                async with transaction(identity.tenant_id) as conn:
                    await source_row(conn, source_id)
                    await accessible(conn, "space", source["space_id"], True)
                    previous = await (
                        await conn.execute(
                            "SELECT * FROM atlas.source_items WHERE source_id=%s AND path=%s",
                            (source_id, path),
                        )
                    ).fetchone()
                if previous and previous["content_hash"] == checksum:
                    async with transaction(identity.tenant_id) as conn:
                        await source_row(conn, source_id)
                        await accessible(conn, "document", previous["document_id"], True)
                        changed = await conn.execute(
                            "UPDATE atlas.documents SET lifecycle='active',updated_at=now() WHERE id=%s AND lifecycle='archived'",
                            (previous["document_id"],),
                        )
                        if changed.rowcount:
                            await invalidate(conn, identity.tenant_id)
                    counts["unchanged"] += 1
                    continue
                extracted = await extract_upload(PurePosixPath(path).name, data)
                stored = await store_document(
                    identity,
                    PurePosixPath(path).name,
                    PurePosixPath(path).name,
                    extracted,
                    data,
                    source["space_id"],
                    previous["document_id"] if previous else None,
                    source_key=f"connector:{source_id}:{hashlib.sha256(path.encode()).hexdigest()}",
                )
                async with transaction(identity.tenant_id) as conn:
                    await source_row(conn, source_id)
                    await conn.execute(
                        "INSERT INTO atlas.source_items(tenant_id,source_id,path,content_hash,document_id) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,source_id,path) DO UPDATE SET content_hash=excluded.content_hash,document_id=excluded.document_id,last_seen_at=now()",
                        (identity.tenant_id, source_id, path, checksum, stored["id"]),
                    )
                counts["imported"] += 1
            async with transaction(identity.tenant_id) as conn:
                await source_row(conn, source_id)
                old = await (
                    await conn.execute(
                        "SELECT path,document_id FROM atlas.source_items WHERE source_id=%s",
                        (source_id,),
                    )
                ).fetchall()
                for item in old:
                    if item["path"] not in seen:
                        await accessible(conn, "document", item["document_id"], True)
                        changed = await conn.execute(
                            "UPDATE atlas.documents SET lifecycle='archived',updated_at=now() WHERE id=%s AND lifecycle='active'",
                            (item["document_id"],),
                        )
                        counts["archived"] += changed.rowcount
                if counts["archived"]:
                    await invalidate(conn, identity.tenant_id)
    except (HTTPException, ExtractionError, httpx.HTTPError, OSError, ValueError, TimeoutError):
        # Persist fixed error codes, never provider payloads, document content, or filesystem paths.
        error = "sync_failed"
    async with transaction(identity.tenant_id) as conn:
        await source_row(conn, source_id)
        row = await (
            await conn.execute(
                "UPDATE atlas.source_runs SET status=%s,imported=%s,unchanged=%s,archived=%s,error_code=%s,completed_at=now() WHERE id=%s RETURNING *",
                (
                    "failed" if error else "completed",
                    counts["imported"],
                    counts["unchanged"],
                    counts["archived"],
                    error,
                    run_id,
                ),
            )
        ).fetchone()
        await conn.execute(
            "UPDATE atlas.source_connections SET last_run_at=now(),last_error=%s,updated_at=now() WHERE id=%s",
            (error, source_id),
        )
    return row


@router.post("/sources/{source_id}/sync")
async def run_source(source_id: UUID, identity: Identity = Depends(authenticate)):
    return await sync_source(identity, source_id)


async def evidence_version(conn, document_id, version_id=None):
    row = await (
        await conn.execute(
            "SELECT v.*,d.current_version_id,d.review_due_at,d.lifecycle FROM atlas.document_versions v JOIN atlas.documents d ON d.tenant_id=v.tenant_id AND d.id=v.document_id WHERE d.id=%s AND v.id=coalesce(%s,d.current_version_id)",
            (document_id, version_id),
        )
    ).fetchone()
    if not row or row["lifecycle"] == "trashed":
        raise HTTPException(404, "Source version not found or access unavailable")
    return row


def version_label(row):
    return {
        key: row[key]
        for key in ("document_id", "title", "number", "publication_status", "effective_at")
    } | {"version_id": row["id"]}


def compare_passages(left, right):
    """Bound CPU and result size while preserving exact, separately cited passages."""
    left_lines, right_lines = left.splitlines(), right.splitlines()
    if len(left_lines) > 20000 or len(right_lines) > 20000 or len(left) + len(right) > 2_000_000:
        raise HTTPException(422, "Choose smaller documents for interactive comparison")
    matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=True)
    changes: list[dict] = []
    added, removed, changed, size = 0, 0, 0, 0
    truncated = False
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        added += j2 - j1
        removed += i2 - i1
        changed += 1
        a, b = "\n".join(left_lines[i1:i2]), "\n".join(right_lines[j1:j2])
        if len(changes) >= 200 or size + len(a) + len(b) > 100000:
            truncated = True
            continue
        size += len(a) + len(b)
        changes.append(
            {
                "kind": tag,
                "left_start": i1 + 1,
                "left_end": i2,
                "right_start": j1 + 1,
                "right_end": j2,
                "left_text": a,
                "right_text": b,
            }
        )
    return {
        "changes": changes,
        "summary": {"added_lines": added, "removed_lines": removed, "changed_sections": changed},
        "truncated": truncated,
    }


def comparison_insights(changes):
    """Describe observable signals with exact evidence; implications remain review suggestions."""
    patterns = (
        (
            "requirement",
            r"\b(?:must|shall|required|mandatory|approval|optional|prohibited)\b",
            "Requirement or approval wording appears in these differing passages. Review whether the required action changed.",
        ),
        (
            "owner",
            r"\b(?:owner|owned|responsible|responsibility|accountable|team)\b",
            "Ownership wording appears in these differing passages. Review whether responsibility changed.",
        ),
        (
            "date",
            r"\b(?:\d{4}-\d{2}-\d{2}|deadline|effective|expires?|days?|weeks?|months?)\b",
            "Dates or timing appear in these differing passages. Review whether a deadline or effective period changed.",
        ),
        (
            "quantity",
            r"\b\d+(?:\.\d+)?(?:%|\b)",
            "Numeric values appear in these differing passages. Check the units and whether a limit or threshold changed.",
        ),
    )
    insights = []
    for change in changes:
        combined = change["left_text"] + "\n" + change["right_text"]
        for kind, pattern, explanation in patterns:
            if re.search(pattern, combined, flags=re.IGNORECASE):
                insights.append(
                    {
                        "kind": kind,
                        "text": explanation,
                        "left_passage": change["left_text"],
                        "right_passage": change["right_text"],
                        "left_start": change["left_start"],
                        "right_start": change["right_start"],
                        "requires_review": True,
                    }
                )
        if re.search(r"\b(?:required|must|mandatory)\b", change["left_text"], re.I) and re.search(
            r"\b(?:optional|not required|must not|prohibited)\b", change["right_text"], re.I
        ):
            insights.append(
                {
                    "kind": "possible_conflict",
                    "text": "The passages use potentially conflicting obligation terms. Check that they describe the same action and effective period before drawing a conclusion.",
                    "left_passage": change["left_text"],
                    "right_passage": change["right_text"],
                    "left_start": change["left_start"],
                    "right_start": change["right_start"],
                    "requires_review": True,
                }
            )
        if len(insights) >= 30:
            return insights[:30]
    return insights


@router.post("/compare")
async def compare_documents(body: CompareBody, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    await workflow_request_limit(identity)
    async with transaction(identity.tenant_id) as conn:
        left = await evidence_version(conn, body.left_document_id, body.left_version_id)
        right = await evidence_version(conn, body.right_document_id, body.right_version_id)
    comparison = await asyncio.to_thread(compare_passages, left["content"], right["content"])
    # Re-read both exact versions after comparison. Besides permissions, this
    # checks deletion/trash and refreshes publication/review metadata changed
    # during the CPU work, without switching the passages to a newer version.
    async with transaction(identity.tenant_id) as conn:
        left = await evidence_version(conn, body.left_document_id, left["id"])
        right = await evidence_version(conn, body.right_document_id, right["id"])
    warnings = []
    for side, row in (("left", left), ("right", right)):
        if row["id"] != row["current_version_id"] or row["publication_status"] != "published":
            warnings.append(f"The {side} source is not the current published version.")
        if row["review_due_at"] and row["review_due_at"] <= datetime.now(UTC):
            warnings.append(f"The {side} document is past its review date.")
    return {
        "left": version_label(left),
        "right": version_label(right),
        **comparison,
        "warnings": warnings,
        "insights": comparison_insights(comparison["changes"]),
    }


@router.get("/knowledge-map")
async def knowledge_map(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        documents = await (
            await conn.execute(
                "SELECT id,title,space_id,current_version_id FROM atlas.documents WHERE lifecycle='active' ORDER BY title,id LIMIT 500"
            )
        ).fetchall()
        entities = await (
            await conn.execute(
                "SELECT id,name,kind,space_id,document_ids,attributes FROM atlas.entities WHERE NOT archived ORDER BY name,id LIMIT 500"
            )
        ).fetchall()
        spaces = await (
            await conn.execute("SELECT id,name FROM atlas.spaces ORDER BY name,id LIMIT 500")
        ).fetchall()
        relationships = await (
            await conn.execute(
                "SELECT * FROM atlas.knowledge_relationships ORDER BY created_at DESC LIMIT 1000"
            )
        ).fetchall()
        teams = await (
            await conn.execute("SELECT id,name FROM atlas.teams ORDER BY name,id LIMIT 500")
        ).fetchall()
    nodes, edges, ids = [], [], set()
    for kind, rows, name_key in (
        ("document", documents, "title"),
        ("entity", entities, "name"),
        ("space", spaces, "name"),
        ("team", teams, "name"),
    ):
        for row in rows:
            node = f"{kind}:{row['id']}"
            ids.add(node)
            nodes.append(
                {
                    "id": node,
                    "kind": kind,
                    "resource_id": row["id"],
                    "label": row[name_key],
                    "space_id": row.get("space_id"),
                }
            )

    def edge(source, target, label, edge_id=None):
        if source in ids and target in ids:
            edges.append(
                {
                    "id": str(edge_id) if edge_id else f"{source}:{target}:{label}",
                    "source": source,
                    "target": target,
                    "label": label,
                    "editable": edge_id is not None,
                }
            )

    teams_by_name = {row["name"].casefold(): row["id"] for row in teams}
    entities_by_name = {row["name"].casefold(): row["id"] for row in entities}
    for kind, rows in (("document", documents), ("entity", entities)):
        for row in rows:
            edge(f"{kind}:{row['id']}", f"space:{row['space_id']}", "in space")
    for row in entities:
        source = f"entity:{row['id']}"
        for document in row["document_ids"]:
            edge(source, f"document:{document}", "documented by")
        team = row["attributes"].get("owner_team")
        if isinstance(team, str) and team.casefold() in teams_by_name:
            edge(source, f"team:{teams_by_name[team.casefold()]}", "owned by")
        dependencies = row["attributes"].get("dependencies", [])
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if isinstance(dependency, str) and dependency.casefold() in entities_by_name:
                    edge(source, f"entity:{entities_by_name[dependency.casefold()]}", "depends on")
    for row in relationships:
        edge(
            f"{row['source_kind']}:{row['source_id']}",
            f"{row['target_kind']}:{row['target_id']}",
            row["label"],
            row["id"],
        )
    # Teams without any visible relationship do not disclose directory information in this graph.
    visible_teams = {e["target"] for e in edges if e["target"].startswith("team:")}
    nodes = [n for n in nodes if n["kind"] != "team" or n["id"] in visible_teams]
    return {
        "nodes": nodes,
        "edges": edges,
        "truncated": any(len(rows) >= 500 for rows in (documents, entities, spaces, teams))
        or len(relationships) >= 1000,
    }


@router.post("/knowledge-map/relationships", status_code=201)
async def create_relationship(body: RelationshipBody, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    if body.source_id == body.target_id:
        raise HTTPException(422, "Choose two different resources")
    async with transaction(identity.tenant_id) as conn:
        for kind, node, write in (
            (body.source_kind, body.source_id, True),
            (body.target_kind, body.target_id, False),
        ):
            allowed = await (
                await conn.execute(
                    "SELECT atlas.can_workflow_node(%s,%s,%s) allowed", (kind, node, write)
                )
            ).fetchone()
            if not allowed or not allowed["allowed"]:
                raise HTTPException(404, "Relationship resource is unavailable")
        return await (
            await conn.execute(
                "INSERT INTO atlas.knowledge_relationships(tenant_id,id,source_kind,source_id,target_kind,target_id,label) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,source_kind,source_id,target_kind,target_id,label) DO UPDATE SET label=excluded.label RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    body.source_kind,
                    body.source_id,
                    body.target_kind,
                    body.target_id,
                    body.label,
                ),
            )
        ).fetchone()


@router.delete("/knowledge-map/relationships/{relationship_id}")
async def delete_relationship(relationship_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "DELETE FROM atlas.knowledge_relationships WHERE id=%s RETURNING id",
                (relationship_id,),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, "Relationship unavailable or cannot be edited")
    return {"deleted": True}


async def validate_steps(conn, steps, publish=False, validate_assignees=True):
    for step in steps:
        if bool(step.get("document_id")) != bool(step.get("version_id")):
            raise HTTPException(422, "Source references need both a document and an exact version")
        if step.get("document_id"):
            version = await evidence_version(
                conn, UUID(str(step["document_id"])), UUID(str(step["version_id"]))
            )
            if publish and (
                version["publication_status"] != "published"
                or version["id"] != version["current_version_id"]
            ):
                raise HTTPException(
                    422, "Published playbooks must reference current published versions"
                )
        if validate_assignees and step.get("assignee_user_id"):
            member = await (
                await conn.execute(
                    "SELECT 1 FROM atlas.memberships WHERE user_id=%s AND status='active'",
                    (step["assignee_user_id"],),
                )
            ).fetchone()
            if not member:
                raise HTTPException(422, "Step assignee must be an active workspace member")


async def safe_playbook(conn, row):
    # A former assignee does not invalidate the source permissions or prevent
    # editors from opening and repairing the playbook after someone leaves.
    await validate_steps(conn, row["steps"], validate_assignees=False)
    review = False
    for step in row["steps"]:
        if step.get("document_id"):
            version = await evidence_version(
                conn, UUID(step["document_id"]), UUID(step["version_id"])
            )
            step["needs_review"] = (
                version["id"] != version["current_version_id"]
                or version["publication_status"] != "published"
            )
            review |= step["needs_review"]
    return {**row, "needs_review": review}


@router.get("/playbooks")
async def playbooks(identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute("SELECT * FROM atlas.playbooks ORDER BY updated_at DESC LIMIT 100")
        ).fetchall()
        items = [await safe_playbook(conn, row) for row in rows]
    return {"items": items}


@router.post("/playbooks", status_code=201)
async def create_playbook(body: PlaybookBody, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    steps = [step.model_dump(mode="json") for step in body.steps]
    if body.status == "published" and not steps:
        raise HTTPException(422, "Add a step before publishing")
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "space", body.space_id, True)
        await validate_steps(conn, steps, body.status == "published")
        return await (
            await conn.execute(
                "INSERT INTO atlas.playbooks(tenant_id,id,space_id,name,description,status,user_id,steps) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    body.space_id,
                    body.name,
                    body.description,
                    body.status,
                    person(identity),
                    Jsonb(steps),
                ),
            )
        ).fetchone()


@router.post("/playbooks/draft")
async def draft_playbook(body: DraftBody, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    steps = []
    async with transaction(identity.tenant_id) as conn:
        await accessible(conn, "space", body.space_id, True)
        for document_id in body.document_ids:
            version = await evidence_version(conn, document_id)
            passages = [
                line.strip(" #-*\t") for line in version["content"].splitlines() if line.strip()
            ]
            for passage in passages[:5]:
                steps.append(
                    StepBody(
                        title=passage[:180],
                        instructions=passage[:4000],
                        document_id=document_id,
                        version_id=version["id"],
                    ).model_dump(mode="json")
                )
    return {
        "name": body.name,
        "space_id": body.space_id,
        "description": "Source excerpts prepared for editor review. Review each instruction before publishing.",
        "status": "draft",
        "steps": steps,
        "method": "extractive",
        "requires_review": True,
    }


async def playbook_row(conn, playbook_id, write=False):
    row = await (
        await conn.execute(
            "SELECT * FROM atlas.playbooks WHERE id=%s" + (" FOR UPDATE" if write else ""),
            (playbook_id,),
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, "Playbook not found or access unavailable")
    if write:
        await accessible(conn, "space", row["space_id"], True)
    return row


@router.get("/playbooks/{playbook_id}")
async def playbook_detail(playbook_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        return await safe_playbook(conn, await playbook_row(conn, playbook_id))


@router.patch("/playbooks/{playbook_id}")
async def edit_playbook(
    playbook_id: UUID, body: PlaybookBody, identity: Identity = Depends(authenticate)
):
    require(identity, "write")
    steps = [step.model_dump(mode="json") for step in body.steps]
    if body.status == "published" and not steps:
        raise HTTPException(422, "Add a step before publishing")
    async with transaction(identity.tenant_id) as conn:
        await playbook_row(conn, playbook_id, True)
        await accessible(conn, "space", body.space_id, True)
        await validate_steps(conn, steps, body.status == "published")
        return await (
            await conn.execute(
                "UPDATE atlas.playbooks SET name=%s,description=%s,space_id=%s,status=%s,steps=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *",
                (
                    body.name,
                    body.description,
                    body.space_id,
                    body.status,
                    Jsonb(steps),
                    playbook_id,
                ),
            )
        ).fetchone()


@router.delete("/playbooks/{playbook_id}")
async def delete_playbook(playbook_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "write")
    async with transaction(identity.tenant_id) as conn:
        await playbook_row(conn, playbook_id, True)
        await conn.execute("DELETE FROM atlas.playbooks WHERE id=%s", (playbook_id,))
    return {"deleted": True}


@router.post("/playbooks/{playbook_id}/runs", status_code=201)
async def start_playbook(
    playbook_id: UUID, body: RunBody, identity: Identity = Depends(authenticate)
):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        book = await safe_playbook(conn, await playbook_row(conn, playbook_id))
        if book["status"] != "published" or book["needs_review"]:
            raise HTTPException(
                409, "Publish a reviewed playbook using current sources before starting"
            )
        steps = [
            {**step, "completed": False, "approved": False, "note": ""} for step in book["steps"]
        ]
        return await (
            await conn.execute(
                "INSERT INTO atlas.playbook_runs(tenant_id,id,playbook_id,user_id,name,revision,steps) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    playbook_id,
                    person(identity),
                    body.name or book["name"],
                    book["revision"],
                    Jsonb(steps),
                ),
            )
        ).fetchone()


@router.get("/playbooks/{playbook_id}/runs")
async def playbook_runs(playbook_id: UUID, identity: Identity = Depends(authenticate)):
    person(identity)
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        await playbook_row(conn, playbook_id)
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.playbook_runs WHERE playbook_id=%s ORDER BY created_at DESC LIMIT 100",
                (playbook_id,),
            )
        ).fetchall()
        return {"items": [await safe_playbook(conn, row) for row in rows]}


@router.get("/playbook-runs/{run_id}")
async def playbook_run(run_id: UUID, identity: Identity = Depends(authenticate)):
    person(identity)
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute("SELECT * FROM atlas.playbook_runs WHERE id=%s", (run_id,))
        ).fetchone()
        if not row:
            raise HTTPException(404, "Run unavailable")
        return await safe_playbook(conn, row)


@router.patch("/playbook-runs/{run_id}/steps/{step_id}")
async def update_step(
    run_id: UUID, step_id: UUID, body: StepProgress, identity: Identity = Depends(authenticate)
):
    person(identity)
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM atlas.playbook_runs WHERE id=%s FOR UPDATE", (run_id,)
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "Run unavailable")
        row = await safe_playbook(conn, row)
        if row["needs_review"]:
            raise HTTPException(
                409, "Source versions changed; review the playbook and start a new run"
            )
        step = next((step for step in row["steps"] if step["id"] == str(step_id)), None)
        if step is None:
            raise HTTPException(404, "Step unavailable")
        if (
            step.get("assignee_user_id")
            and step["assignee_user_id"] != str(identity.user_id)
            and identity.role not in {"owner", "admin"}
        ):
            raise HTTPException(403, "This step is assigned to another member")
        if body.approved is not None:
            require(identity, "admin")
            step["approved"] = body.approved
            step["approved_by"] = str(identity.user_id) if body.approved else None
        if body.completed is not None:
            step["completed"] = body.completed
            if not body.completed:
                step["approved"] = False
                step["approved_by"] = None
        if body.note is not None:
            step["note"] = body.note
        status = (
            "completed"
            if all(
                s.get("completed") and (not s.get("requires_approval") or s.get("approved"))
                for s in row["steps"]
            )
            else "active"
        )
        return await (
            await conn.execute(
                "UPDATE atlas.playbook_runs SET steps=%s,status=%s,updated_at=now() WHERE id=%s RETURNING *",
                (Jsonb(row["steps"]), status, run_id),
            )
        ).fetchone()


async def validate_briefing_scope(conn, body):
    for space in body.space_ids:
        await accessible(conn, "space", space)
    for document in body.document_ids:
        await accessible(conn, "document", document)
    if not body.space_ids and not body.document_ids:
        raise HTTPException(422, "Follow at least one space or document")


@router.get("/briefings")
async def briefings(identity: Identity = Depends(authenticate)):
    person(identity)
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.briefing_subscriptions ORDER BY name,id LIMIT 100"
            )
        ).fetchall()
    return {"items": rows}


@router.post("/briefings", status_code=201)
async def create_briefing(body: BriefingBody, identity: Identity = Depends(authenticate)):
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        await validate_briefing_scope(conn, body)
        return await (
            await conn.execute(
                "INSERT INTO atlas.briefing_subscriptions(tenant_id,id,user_id,session_id,name,space_ids,document_ids,question,cadence,enabled) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    identity.tenant_id,
                    uuid4(),
                    person(identity),
                    identity.session_id,
                    body.name,
                    body.space_ids,
                    body.document_ids,
                    body.question,
                    body.cadence,
                    body.enabled,
                ),
            )
        ).fetchone()


async def briefing_row(conn, briefing_id, lock=False):
    row = await (
        await conn.execute(
            "SELECT * FROM atlas.briefing_subscriptions WHERE id=%s"
            + (" FOR UPDATE" if lock else ""),
            (briefing_id,),
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, "Briefing subscription unavailable")
    return row


@router.patch("/briefings/{briefing_id}")
async def edit_briefing(
    briefing_id: UUID, body: BriefingPatch, identity: Identity = Depends(authenticate)
):
    person(identity)
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        row = await briefing_row(conn, briefing_id, True)
        update = body.model_dump(exclude_unset=True)
        if any(value is None for value in update.values()):
            raise HTTPException(422, "Briefing settings cannot be null")
        row.update(update)
        validated = BriefingBody(**row)
        await validate_briefing_scope(conn, validated)
        return await (
            await conn.execute(
                "UPDATE atlas.briefing_subscriptions SET name=%s,space_ids=%s,document_ids=%s,question=%s,cadence=%s,enabled=%s,session_id=%s,last_error=NULL,next_run_at=now(),updated_at=now() WHERE id=%s RETURNING *",
                (
                    validated.name,
                    validated.space_ids,
                    validated.document_ids,
                    validated.question,
                    validated.cadence,
                    validated.enabled,
                    identity.session_id,
                    briefing_id,
                ),
            )
        ).fetchone()


@router.delete("/briefings/{briefing_id}")
async def delete_briefing(briefing_id: UUID, identity: Identity = Depends(authenticate)):
    person(identity)
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        await briefing_row(conn, briefing_id, True)
        await conn.execute("DELETE FROM atlas.briefing_subscriptions WHERE id=%s", (briefing_id,))
    return {"deleted": True}


async def safe_briefing_run(conn, row):
    for item in row["items"]:
        allowed = await (
            await conn.execute(
                "SELECT 1 FROM atlas.documents WHERE id=%s AND lifecycle='active'",
                (UUID(item["document_id"]),),
            )
        ).fetchone()
        if not allowed:
            return {
                **row,
                "status": "unavailable",
                "summary": "Evidence access has changed. Run this briefing again using current permissions.",
                "items": [],
            }
    return row


@router.get("/briefings/{briefing_id}/runs")
async def briefing_runs(briefing_id: UUID, identity: Identity = Depends(authenticate)):
    person(identity)
    require(identity, "read")
    async with transaction(identity.tenant_id) as conn:
        await briefing_row(conn, briefing_id)
        rows = await (
            await conn.execute(
                "SELECT * FROM atlas.briefing_runs WHERE briefing_id=%s ORDER BY created_at DESC LIMIT 100",
                (briefing_id,),
            )
        ).fetchall()
        return {"items": [await safe_briefing_run(conn, row) for row in rows]}


async def run_briefing(identity, briefing_id):
    person(identity)
    require(identity, "read")
    await workflow_request_limit(identity)
    run_id = uuid4()
    try:
        async with transaction(identity.tenant_id) as conn:
            briefing = await briefing_row(conn, briefing_id, True)
            await validate_briefing_scope(conn, BriefingBody(**briefing))
            charged = await (await conn.execute("SELECT atlas.charge_query() allowed")).fetchone()
            if not charged or not charged["allowed"]:
                raise HTTPException(429, "Workspace daily query quota reached")
            await conn.execute(
                "UPDATE atlas.briefing_runs SET status='failed',error_code='interrupted',completed_at=now() WHERE briefing_id=%s AND status='running' AND created_at<now()-interval '10 minutes'",
                (briefing_id,),
            )
            await conn.execute(
                "INSERT INTO atlas.briefing_runs(tenant_id,id,briefing_id,user_id) VALUES(%s,%s,%s,%s)",
                (identity.tenant_id, run_id, briefing_id, identity.user_id),
            )
            cutoff = briefing["last_run_at"] or datetime.now(UTC) - timedelta(days=7)
            rows = await (
                await conn.execute(
                    "SELECT d.id document_id,d.title,d.updated_at changed_at,v.id version_id,v.number,v.content,v.publication_status,v.effective_at FROM atlas.documents d JOIN atlas.document_versions v ON v.tenant_id=d.tenant_id AND v.id=d.current_version_id WHERE d.lifecycle='active' AND d.status='ready' AND v.publication_status='published' AND v.effective_at<=now() AND d.updated_at>%s AND (d.space_id=ANY(%s) OR d.id=ANY(%s)) AND (cardinality(%s::text[])=0 OR EXISTS(SELECT 1 FROM unnest(%s::text[]) topic WHERE to_tsvector('english',d.title||' '||v.content) @@ plainto_tsquery('english',topic))) ORDER BY d.updated_at DESC,d.id LIMIT 51",
                    (
                        cutoff,
                        briefing["space_ids"],
                        briefing["document_ids"],
                        briefing_terms(briefing["question"]),
                        briefing_terms(briefing["question"]),
                    ),
                )
            ).fetchall()
            items = [
                {
                    "document_id": str(row["document_id"]),
                    "version_id": str(row["version_id"]),
                    "title": row["title"],
                    "number": row["number"],
                    "excerpt": row["content"][:900],
                    "changed_at": row["changed_at"].isoformat(),
                    "effective_at": row["effective_at"].isoformat(),
                    "source_url": f"/library/{row['document_id']}?version={row['version_id']}",
                }
                for row in rows[:50]
            ]
            summary = (
                f"{len(items)} published document{'s' if len(items) != 1 else ''} changed since {cutoff.date().isoformat()}."
                if items
                else "No matching published document changes since the previous briefing."
            )
            if len(rows) > 50:
                summary += " Showing the 50 most recent changes."
            row = await (
                await conn.execute(
                    "UPDATE atlas.briefing_runs SET status='completed',summary=%s,items=%s,completed_at=now() WHERE id=%s RETURNING *",
                    (summary, Jsonb(items), run_id),
                )
            ).fetchone()
            await conn.execute(
                "UPDATE atlas.briefing_subscriptions SET last_run_at=now(),next_run_at=now()+make_interval(days=>%s),last_error=NULL WHERE id=%s",
                (1 if briefing["cadence"] == "daily" else 7, briefing_id),
            )
            await conn.execute(
                "INSERT INTO atlas.notifications(tenant_id,id,user_id,kind,title,body,link,dedupe_key) VALUES(%s,%s,%s,'briefing','Your briefing is ready','Open your briefing to view currently authorized changes.','/briefings',%s) ON CONFLICT(tenant_id,user_id,dedupe_key) DO NOTHING",
                (identity.tenant_id, uuid4(), identity.user_id, f"briefing:{run_id}"),
            )
            return row
    except UniqueViolation as exc:
        raise HTTPException(409, "This briefing is already running") from exc


@router.post("/briefings/{briefing_id}/run")
async def briefing_now(briefing_id: UUID, identity: Identity = Depends(authenticate)):
    return await run_briefing(identity, briefing_id)


async def workflow_tick():
    """Worker discovers IDs only; application-role execution rechecks the saved authority.

    Schedules pause after session expiry or revocation. Saving a schedule reauthorizes
    it with the current session. At most one small batch runs per maintenance tick.
    """
    async with transaction() as conn:
        due = await (await conn.execute("SELECT * FROM atlas.due_workflows()")).fetchall()
    for workflow in due:
        async with identity_transaction() as conn:
            member = await (
                await conn.execute(
                    "SELECT m.role,t.name,t.auth_revision FROM atlas.memberships m JOIN atlas.tenants t ON t.id=m.tenant_id JOIN atlas.users u ON u.id=m.user_id JOIN atlas.user_sessions s ON s.user_id=m.user_id WHERE m.tenant_id=%s AND m.user_id=%s AND m.status='active' AND t.status='active' AND u.disabled_at IS NULL AND u.email_verified_at IS NOT NULL AND s.id=%s AND s.revoked_at IS NULL AND s.expires_at>now()",
                    (workflow["tenant_id"], workflow["user_id"], workflow["session_id"]),
                )
            ).fetchone()
        if not member:
            async with transaction() as conn:
                await conn.execute(
                    "SELECT atlas.pause_workflow(%s,%s,%s)",
                    (workflow["kind"], workflow["tenant_id"], workflow["id"]),
                )
            continue
        identity = Identity(
            tenant_id=workflow["tenant_id"],
            key_id=workflow["session_id"],
            name=member["name"],
            scopes=ROLE_SCOPES[member["role"]],
            user_id=workflow["user_id"],
            role=member["role"],
            principal_kind="user",
            principal_id=workflow["user_id"],
            auth_revision=member["auth_revision"],
            session_id=workflow["session_id"],
        )
        token = bind_identity(identity)
        unavailable = False
        failure = None
        try:
            async with application_transactions():
                async with transaction(identity.tenant_id) as conn:
                    active = await (
                        await conn.execute("SELECT atlas.principal_active() active")
                    ).fetchone()
                    if not active or not active["active"]:
                        raise HTTPException(403, "Authorizing session has expired")
                if workflow["kind"] == "source":
                    await sync_source(identity, workflow["id"])
                else:
                    await run_briefing(identity, workflow["id"])
        except HTTPException as exc:
            unavailable = exc.status_code in {401, 403, 404}
            if not unavailable:
                failure = (
                    "quota_exceeded"
                    if exc.status_code == 429
                    else "already_running"
                    if exc.status_code == 409
                    else "workflow_failed"
                )
        except Exception:
            # Provider/database exceptions must not break maintenance for other
            # tenants or leave a due workflow repeatedly monopolizing the batch.
            failure = "workflow_failed"
        finally:
            access_context.reset(token)
        if unavailable:
            async with transaction() as conn:
                await conn.execute(
                    "SELECT atlas.pause_workflow(%s,%s,%s)",
                    (workflow["kind"], workflow["tenant_id"], workflow["id"]),
                )
        elif failure:
            async with transaction() as conn:
                await conn.execute(
                    "SELECT atlas.defer_workflow(%s,%s,%s,%s)",
                    (workflow["kind"], workflow["tenant_id"], workflow["id"], failure),
                )


@router.get("/sources/{source_id}/preview")
async def preview_source(source_id: UUID, identity: Identity = Depends(authenticate)):
    require(identity, "admin")
    await workflow_request_limit(identity)
    async with transaction(identity.tenant_id) as conn:
        source = await source_row(conn, source_id)
    async with asyncio.timeout(180):
        files = await source_files(source)
    async with transaction(identity.tenant_id) as conn:
        await source_row(conn, source_id)
    return {
        "items": [{"path": path, "byte_size": len(data)} for path, data in files],
        "total_bytes": sum(len(data) for _, data in files),
    }
