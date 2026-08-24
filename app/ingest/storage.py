"""Content-addressed storage for the uploaded bills.

Files are stored under their SHA-256 with the original extension, sharded two
levels deep so a directory never fills up:

    data/files/9f/7a/9f7ab3...pdf

Naming by content means re-uploading the same bill — a forward of the same
email, a second scan of the same paper — resolves to the same stored file and
the same document row, instead of quietly creating a duplicate invoice.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}

MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def storage_path(digest: str, suffix: str) -> Path:
    return settings.files_dir / digest[:2] / digest[2:4] / f"{digest}{suffix.lower()}"


def store_bytes(data: bytes, filename: str) -> tuple[str, Path, str]:
    """Save an upload. Returns (sha256, stored path, mime type)."""
    suffix = Path(filename).suffix.lower() or ".pdf"
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"'{suffix}' files are not supported. Upload a PDF or an image "
            f"({', '.join(sorted(ALLOWED_SUFFIXES))})."
        )

    digest = sha256_bytes(data)
    target = storage_path(digest, suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # Write to a temp name first so a crash mid-write cannot leave a
        # truncated file sitting at the address of valid content.
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        log.info("stored %s as %s", filename, target.name)

    return digest, target, MIME_BY_SUFFIX.get(suffix, "application/octet-stream")


def store_file(path: Path) -> tuple[str, Path, str]:
    return store_bytes(path.read_bytes(), path.name)


def pages_dir_for(digest: str) -> Path:
    return settings.pages_dir / digest[:2] / digest[2:4] / digest


def delete_stored(digest: str, suffix: str = ".pdf") -> None:
    """Remove a stored file and its rendered pages."""
    target = storage_path(digest, suffix)
    if target.exists():
        target.unlink()
    pages = pages_dir_for(digest)
    if pages.exists():
        shutil.rmtree(pages, ignore_errors=True)
