"""Source hashes recorded with every benchmark run."""

import hashlib
from pathlib import Path


def source_fingerprint() -> dict[str, str]:
    source_root = Path(__file__).resolve().parents[2]
    sources = {}
    for package in ("vista",):
        for path in sorted((source_root / package).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".md"}:
                with path.open("rb") as stream:
                    sources[str(path.relative_to(source_root))] = hashlib.file_digest(
                        stream, "sha256"
                    ).hexdigest()
    return sources
