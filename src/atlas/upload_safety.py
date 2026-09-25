"""Fail-closed ClamAV scanning before parsing or storing untrusted uploads."""

import asyncio
import hashlib
import struct
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from atlas.config import settings
from atlas.db import access_context, transaction


async def _command(command: bytes, payload: bytes | None = None) -> str:
    reader, writer = await asyncio.open_connection(
        settings.upload_scanner_host, settings.upload_scanner_port, limit=4096
    )
    try:
        writer.write(command)
        if payload is not None:
            for start in range(0, len(payload), 65536):
                block = payload[start : start + 65536]
                writer.write(struct.pack("!I", len(block)) + block)
                await writer.drain()
            writer.write(struct.pack("!I", 0))
        await writer.drain()
        return (await reader.readuntil(b"\0")).rstrip(b"\0").decode("utf-8", "replace")
    finally:
        writer.close()
        await writer.wait_closed()


async def scanner_status() -> dict:
    async with asyncio.timeout(settings.upload_scanner_timeout):
        version = await _command(b"zVERSION\0")
    parts = version.split("/", 2)
    if len(parts) != 3:
        raise ValueError("Scanner signature database is unavailable")
    updated = datetime.strptime(parts[2].strip(), "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
    age = (datetime.now(UTC) - updated).total_seconds()
    if age < -86400 or age > settings.upload_scanner_max_age_days * 86400:
        raise ValueError("Scanner signatures are outdated")
    return {"engine": parts[0], "signatures": parts[1], "updated_at": updated.isoformat()}


async def record_rejection(filename: str, data: bytes, status: str):
    actor = access_context.get()
    if not actor:
        return
    # Quarantine records contain a digest and reason, never uploaded bytes. Rejected
    # files are discarded instead of filling disk with untrusted retained content.
    async with transaction(actor.tenant_id) as conn:
        await conn.execute(
            """INSERT INTO atlas.upload_checks(tenant_id,id,principal_id,filename,sha256,byte_size,status)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (
                actor.tenant_id,
                uuid4(),
                actor.principal_id,
                Path(filename).name[:250],
                hashlib.sha256(data).hexdigest(),
                len(data),
                status,
            ),
        )


async def scan_upload(filename: str, data: bytes) -> dict:
    if not data or len(data) > settings.max_file_bytes:
        raise HTTPException(413, "Upload must be nonempty and within the configured file limit")
    if not settings.upload_scan_required:
        return {"status": "development_bypass"}
    try:
        async with asyncio.timeout(settings.upload_scanner_timeout):
            version = await scanner_status()
            result = await _command(b"zINSTREAM\0", data)
    except (
        OSError,
        TimeoutError,
        ValueError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
    ) as exc:
        await record_rejection(filename, data, "scanner_unavailable")
        raise HTTPException(
            503,
            "Upload quarantined: malware scanning is unavailable or signatures are outdated. Ask an operator to restore the scanner, then upload again.",
            headers={"Retry-After": "30"},
        ) from exc
    if result.endswith(" FOUND"):
        await record_rejection(filename, data, "unsafe")
        raise HTTPException(422, "Upload quarantined: the malware scanner rejected this file")
    if result != "stream: OK":
        await record_rejection(filename, data, "scanner_unavailable")
        raise HTTPException(
            503, "Upload quarantined: the malware scanner could not safely inspect this file"
        )
    return {"status": "clean", **version}
