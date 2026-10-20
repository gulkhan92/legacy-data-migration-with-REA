"""Upload validation and encrypted storage.

Uploaded software is data. Nothing in this module (or anywhere in the application) loads,
imports or executes an uploaded file. Files are validated, encrypted with AES-256-GCM and stored
under a random name; the original filename is kept only as sanitized metadata.
"""

import hashlib
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.crypto import decrypt_bytes, encrypt_bytes
from app.db.models.enums import SourceKind

SQLITE_MAGIC = b"SQLite format 3\x00"
EXTENSIONS: dict[str, SourceKind] = {
    ".sqlite": SourceKind.SQLITE,
    ".sqlite3": SourceKind.SQLITE,
    ".db": SourceKind.SQLITE,
    ".csv": SourceKind.CSV,
    ".tsv": SourceKind.CSV,
    ".txt": SourceKind.CSV,
    ".dll": SourceKind.DOTNET_BINARY,
    ".exe": SourceKind.DOTNET_BINARY,
    ".cdb": SourceKind.DOTNET_BINARY,
    ".dat": SourceKind.DOTNET_BINARY,
    ".bin": SourceKind.DOTNET_BINARY,
}
ASSEMBLY_EXTENSIONS = {".dll", ".exe"}
HEAD_SIZE = 8192


class UploadRejected(Exception):
    def __init__(self, reason: str, status_code: int = 400) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class StoredUpload:
    kind: SourceKind
    original_filename: str
    storage_path: str
    size_bytes: int
    sha256: str


def sanitize_filename(name: str) -> str:
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return base[:200] or "upload"


def _is_pe(head: bytes) -> bool:
    """A Windows portable executable: MZ header whose e_lfanew points at the PE signature."""
    if head[:2] != b"MZ" or len(head) < 0x40:
        return False
    offset = int.from_bytes(head[0x3C:0x40], "little")
    return offset + 4 <= len(head) and head[offset : offset + 4] == b"PE\x00\x00"


def _looks_executable(head: bytes) -> bool:
    return head[:2] == b"MZ" or head[:4] == b"\x7fELF" or head[:2] == b"#!" or head[:4] in (
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
    )


def _is_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    sample = head.decode("utf-8", errors="ignore") if head else ""
    if not sample:
        return len(head) > 0
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in sample)
    return printable / len(sample) > 0.95


def validate_upload(filename: str, head: bytes, size: int) -> SourceKind:
    """Check extension, size and magic bytes. Raises UploadRejected."""
    settings = get_settings()
    if size == 0:
        raise UploadRejected("The file is empty")
    if size > settings.upload_max_bytes:
        raise UploadRejected("The file is too large", status_code=413)
    ext = Path(filename).suffix.lower()
    kind = EXTENSIONS.get(ext)
    if kind is None:
        raise UploadRejected("This file type is not supported")

    if kind is SourceKind.SQLITE:
        if not head.startswith(SQLITE_MAGIC):
            raise UploadRejected("The content is not a SQLite database")
    elif kind is SourceKind.CSV:
        if not _is_text(head) or _looks_executable(head):
            raise UploadRejected("The content is not delimited text")
    elif ext in ASSEMBLY_EXTENSIONS:
        if not _is_pe(head):
            raise UploadRejected("The content is not a .NET or Windows assembly")
    else:  # proprietary data files must never be an executable in disguise
        if _looks_executable(head):
            raise UploadRejected("Executable content is not accepted as a data file")
    return kind


def read_limited(chunks: Iterator[bytes]) -> bytes:
    """Collect an upload stream, stopping as soon as the size cap is exceeded."""
    limit = get_settings().upload_max_bytes
    buffer = bytearray()
    for chunk in chunks:
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise UploadRejected("The file is too large", status_code=413)
    return bytes(buffer)


def store_upload(org_id: uuid.UUID, filename: str, data: bytes) -> StoredUpload:
    kind = validate_upload(filename, data[:HEAD_SIZE], len(data))
    storage_id = uuid.uuid4().hex
    relative = f"{org_id}/{storage_id}.enc"
    target = Path(get_settings().upload_dir) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encrypt_bytes(data, context=relative))
    return StoredUpload(
        kind=kind,
        original_filename=sanitize_filename(filename),
        storage_path=relative,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def load_upload(storage_path: str) -> bytes:
    """Decrypt a stored upload into memory. The caller must treat the bytes as untrusted data."""
    root = Path(get_settings().upload_dir).resolve()
    target = (root / storage_path).resolve()
    if root not in target.parents:
        raise ValueError("Invalid storage path")
    return decrypt_bytes(target.read_bytes(), context=storage_path)
