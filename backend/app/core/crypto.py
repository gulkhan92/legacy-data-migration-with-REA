"""AES-256-GCM encryption for uploaded files at rest.

File layout: 4 byte magic, 12 byte random nonce, ciphertext with the 16 byte GCM tag appended.
The authenticated associated data binds the ciphertext to its storage identifier, so a file
cannot be swapped for another one without detection.
"""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

MAGIC = b"LLE1"
NONCE_SIZE = 12


class DecryptionError(Exception):
    pass


def _key() -> bytes:
    key = base64.b64decode(get_settings().upload_encryption_key)
    if len(key) != 32:
        raise ValueError("UPLOAD_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def encrypt_bytes(plaintext: bytes, *, context: str) -> bytes:
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(_key()).encrypt(nonce, plaintext, context.encode())
    return MAGIC + nonce + ciphertext


def decrypt_bytes(blob: bytes, *, context: str) -> bytes:
    if blob[:4] != MAGIC or len(blob) < 4 + NONCE_SIZE + 16:
        raise DecryptionError("Not an encrypted upload")
    nonce = blob[4 : 4 + NONCE_SIZE]
    try:
        return AESGCM(_key()).decrypt(nonce, blob[4 + NONCE_SIZE :], context.encode())
    except InvalidTag as exc:
        raise DecryptionError("Authentication failed") from exc
