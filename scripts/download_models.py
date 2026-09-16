"""Download only the selected local models and record immutable source revisions."""

import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

models = {
    "bge": "BAAI/bge-small-en-v1.5",
    "reranker": "cross-encoder/ms-marco-MiniLM-L6-v2",
}
manifest_path = Path("models/manifest.json")
manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
for name, repo in models.items():
    revision = manifest.get(name, {}).get("revision") or HfApi().model_info(repo).sha
    snapshot_download(
        repo,
        revision=revision,
        local_dir=f".models/{name}",
        allow_patterns=["*.json", "*.txt", "*.safetensors", "*.model", "1_Pooling/*"],
    )
    manifest[name] = {"repository": repo, "revision": revision}
Path("models").mkdir(exist_ok=True)
if not manifest_path.exists() or json.loads(manifest_path.read_text()) != manifest:
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
print("Local embedding and reranking models downloaded; revisions recorded")
