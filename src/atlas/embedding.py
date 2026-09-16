import asyncio
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from atlas.config import settings

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_lock = asyncio.Lock()


@lru_cache(maxsize=1)
def encoder():
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(4)

    if not Path(settings.embedding_path).exists():
        raise RuntimeError("Local embedding model is missing. Run scripts/download_models.py")
    return SentenceTransformer(settings.embedding_path, local_files_only=True, device="cpu")


def model_revision() -> str:
    path = Path(settings.embedding_path) / "config.json"
    manifest_path = Path("models/manifest.json")
    revision = (
        json.loads(manifest_path.read_text())["bge"]["revision"]
        if manifest_path.exists()
        else "unavailable"
    )
    # Full artifact revisions are captured during bootstrap; this fingerprints encoding settings.
    return (
        "bge-small-en-v1.5:"
        + hashlib.sha256(
            revision.encode()
            + (path.read_bytes() if path.exists() else b"")
            + b"normalize=true;passage=plain;query=instruction"
        ).hexdigest()[:16]
    )


async def embed(texts: list[str], query=False) -> list[list[float]]:
    async with _lock:
        values = [QUERY_PREFIX + text for text in texts] if query else texts
        return await asyncio.to_thread(
            lambda: (
                encoder()
                .encode(values, normalize_embeddings=True, batch_size=16, show_progress_bar=False)
                .tolist()
            )
        )


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in vector) + "]"
