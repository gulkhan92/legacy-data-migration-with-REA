"""The last line of defence before a prompt leaves the system.

The payload builders already send metadata only (profile views hold no values of a PII column). This guard does not trust
them: it refuses a payload that contains row-like structures, values that look like personal data, or too many tokens.
Free tier providers may use prompts to improve their products, so nothing identifying may be sent.
"""

import json
import re
from typing import Any

from app.llm.errors import PayloadRejected
from app.llm.tokens import estimate_json_tokens

MAX_PAYLOAD_TOKENS = 5_000  # keeps every request under the Groq per minute limit of 8,000 with room for the answer
FORBIDDEN_KEYS = {"rows", "row", "sample_rows", "values", "records", "raw_row", "raw_json", "password", "api_key", "token", "secret"}
PATTERNS = {
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
    "social security number": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "payment card number": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "phone number": re.compile(r"(?<!\d)\+?\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]\d{3,4}[\s.-]\d{3,4}(?!\d)"),
    "long digit run": re.compile(r"\d{9,}"),
}


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(str(k).lower())
            keys |= _walk_keys(v)
    elif isinstance(value, list):
        for item in value:
            keys |= _walk_keys(item)
    return keys


def check_payload(payload: Any) -> None:
    """Raise PayloadRejected when the payload must not be sent."""
    forbidden = _walk_keys(payload) & FORBIDDEN_KEYS
    if forbidden:
        raise PayloadRejected(f"The payload contains fields that are never sent: {sorted(forbidden)}")
    # Digit runs that the profile view already masked as 9999... are not data.
    text = re.sub(r"9{6,}", "#", json.dumps(payload, ensure_ascii=False))
    for kind, pattern in PATTERNS.items():
        if pattern.search(text):
            raise PayloadRejected(f"The payload contains what looks like a {kind}")
    if estimate_json_tokens(payload) > MAX_PAYLOAD_TOKENS:
        raise PayloadRejected(f"The payload is larger than {MAX_PAYLOAD_TOKENS} tokens")
