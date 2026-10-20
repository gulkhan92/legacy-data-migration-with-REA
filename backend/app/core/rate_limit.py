from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

_settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_settings.default_rate_limit],
    enabled=_settings.rate_limit_enabled,
    storage_uri="memory://",
)
