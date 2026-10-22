"""Token estimation. No tokenizer is installed, so the estimate is characters divided by four, rounded up, plus a margin.

It is used before a call (to check budgets and rate limits) and never for billing: the provider's own usage figures are what
gets recorded.
"""

import json
import math
from typing import Any

SAFETY_MARGIN = 1.1


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4 * SAFETY_MARGIN)


def estimate_json_tokens(value: Any) -> int:
    return estimate_tokens(json.dumps(value, separators=(",", ":"), ensure_ascii=False))
