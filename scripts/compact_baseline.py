"""Losslessly compress an existing measured baseline and update its artifact checksum."""

import gzip
import hashlib
import json
from pathlib import Path

base = Path(__file__).resolve().parents[1] / "data/baseline"
manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
info = manifest["files"]["logs"]
source = (base / info["path"]).resolve()
if not source.is_relative_to(base.resolve()) or source.suffix != ".jsonl":
    raise ValueError("Expected an uncompressed baseline JSONL within data/baseline")
data = source.read_bytes()
if hashlib.sha256(data).hexdigest() != info["sha256"]:
    raise ValueError("Source checksum mismatch")
target = source.with_suffix(".jsonl.gz")
encoded = gzip.compress(data, mtime=0)
target.write_bytes(encoded)
assert gzip.decompress(target.read_bytes()) == data
info.update(
    path=target.relative_to(base).as_posix(),
    sha256=hashlib.sha256(encoded).hexdigest(),
    size_bytes=len(encoded),
)
(base / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
source.unlink()
