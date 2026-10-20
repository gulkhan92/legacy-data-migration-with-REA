import struct
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import DecryptionError, decrypt_bytes, encrypt_bytes
from app.db import models
from app.services.uploads import load_upload, sanitize_filename, validate_upload, UploadRejected
from tests.conftest import login_as

SQLITE = b"SQLite format 3\x00" + b"\x01\x02\x03" * 100


def _pe() -> bytes:
    data = bytearray(256)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = struct.pack("<I", 0x80)
    data[0x80:0x84] = b"PE\x00\x00"
    return bytes(data)


def _upload(client, name: str, content: bytes):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/sources/upload", files={"file": (name, content)})


def test_valid_sqlite_upload_is_encrypted_at_rest_and_recorded(client, admin_engine) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    response = _upload(client, "legacy.sqlite", SQLITE)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "sqlite" and body["size_bytes"] == len(SQLITE) and len(body["sha256"]) == 64
    with Session(admin_engine) as session:
        source = session.get(models.Source, body["id"])
    stored = Path(get_settings().upload_dir) / source.storage_path
    raw = stored.read_bytes()
    assert b"SQLite format 3" not in raw and raw.startswith(b"LLE1")
    assert load_upload(source.storage_path) == SQLITE
    assert source.encrypted is True and source.storage_path.endswith(".enc")


def test_assembly_and_data_file_are_accepted(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    assert _upload(client, "ClinicDesk.dll", _pe()).status_code == 201
    assert _upload(client, "clinic.cdb", b"CDB1" + bytes(range(256))).status_code == 201
    assert _upload(client, "export.csv", b"id,name\n1,Ada\n2,Grace\n").status_code == 201


@pytest.mark.parametrize(
    "name,content",
    [
        ("evil.csv", _pe()),  # executable renamed to csv
        ("evil.cdb", _pe()),  # executable renamed to a data file
        ("evil.bin", b"\x7fELF" + b"\x00" * 64),
        ("fake.db", b"this is not sqlite at all"),
        ("fake.dll", b"MZ" + b"\x00" * 100),  # MZ without a PE header
        ("binary.csv", bytes(range(256)) * 4),
        ("archive.zip", b"PK\x03\x04" + b"\x00" * 50),
        ("script.sh", b"#!/bin/sh\nrm -rf /\n"),
        ("noextension", b"id,name\n1,2\n"),
        ("empty.csv", b""),
    ],
)
def test_bad_uploads_are_rejected_and_nothing_is_stored(client, name, content) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    response = _upload(client, name, content)
    assert response.status_code == 400
    upload_dir = Path(get_settings().upload_dir)
    assert not upload_dir.exists() or not list(upload_dir.rglob("*.enc"))


def test_oversized_upload_is_rejected(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "upload_max_bytes", 1000)
    login_as(client, "analyst@acme.test")
    response = _upload(client, "big.csv", b"a,b\n" * 2000)
    assert response.status_code == 413


def test_rejected_upload_is_audited(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "admin@acme.test")
    assert _upload(client, "fake.db", b"nope").status_code == 400
    entries = client.get("/api/v1/admin/audit-log", params={"action": "source.upload_rejected"}).json()
    assert entries and entries[0]["detail"]["reason"] == "The content is not a SQLite database"


def test_accepted_upload_is_audited(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "admin@acme.test")
    source_id = _upload(client, "ok.csv", b"a,b\n1,2\n").json()["id"]
    entries = client.get("/api/v1/admin/audit-log", params={"action": "source.upload"}).json()
    assert source_id in {e["entity_id"] for e in entries}


def test_sources_are_isolated_between_organizations(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    mine = _upload(client, "mine.csv", b"a,b\n1,2\n").json()["id"]
    client.cookies.clear()
    login_as(client, "admin@globex.test")
    assert mine not in {s["id"] for s in client.get("/api/v1/sources").json()}


def test_filenames_are_sanitized() -> None:
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\Windows\\system32\\a b.dll") == "a b.dll"
    assert sanitize_filename("<script>.csv") == "_script_.csv"
    assert sanitize_filename("...") == "upload"


def test_validate_upload_rejects_wrong_content_for_extension() -> None:
    with pytest.raises(UploadRejected):
        validate_upload("a.sqlite", b"not sqlite", 10)
    assert validate_upload("a.sqlite", SQLITE, len(SQLITE)).value == "sqlite"


def test_storage_path_cannot_escape_the_upload_directory() -> None:
    with pytest.raises(ValueError):
        load_upload("../../secret.enc")


def test_encryption_round_trip_and_tamper_detection() -> None:
    blob = encrypt_bytes(b"secret data", context="a/b.enc")
    assert b"secret data" not in blob
    assert decrypt_bytes(blob, context="a/b.enc") == b"secret data"
    assert encrypt_bytes(b"secret data", context="a/b.enc") != blob  # fresh nonce each time
    flipped = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(DecryptionError):
        decrypt_bytes(flipped, context="a/b.enc")
    with pytest.raises(DecryptionError):
        decrypt_bytes(blob, context="other/path.enc")  # ciphertext bound to its location
    with pytest.raises(DecryptionError):
        decrypt_bytes(b"plain", context="a/b.enc")


def test_upload_never_leaves_plaintext_on_disk(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    _upload(client, "marker.csv", b"a,b\nUNIQUE-MARKER-VALUE,2\n")
    for path in Path(get_settings().upload_dir).rglob("*"):
        if path.is_file():
            assert b"UNIQUE-MARKER-VALUE" not in path.read_bytes()
