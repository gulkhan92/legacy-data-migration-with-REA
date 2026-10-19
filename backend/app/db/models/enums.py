import enum


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:
        return self.value


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class SourceKind(StrEnum):
    SQLITE = "sqlite"
    DOTNET_BINARY = "dotnet_binary"
    CSV = "csv"


class JobStatus(StrEnum):
    CREATED = "CREATED"
    UPLOADED = "UPLOADED"
    DISCOVERING = "DISCOVERING"
    PROFILED = "PROFILED"
    MAPPED = "MAPPED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    DRY_RUN = "DRY_RUN"
    READY = "READY"
    LOADING = "LOADING"
    RECONCILING = "RECONCILING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class MappingMethod(StrEnum):
    MEMORY = "memory"
    EMBEDDING = "embedding"
    LLM = "llm"
    HUMAN = "human"


class MappingStatus(StrEnum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    OVERRIDDEN = "overridden"
    REJECTED = "rejected"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class InvoiceStatus(StrEnum):
    PAID = "paid"
    OPEN = "open"
    OVERDUE = "overdue"
    VOID = "void"


class InvoiceLineKind(StrEnum):
    BASE = "base"
    OVERAGE = "overage"


class LlmCallStatus(StrEnum):
    OK = "ok"
    CACHE_HIT = "cache_hit"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    SCHEMA_ERROR = "schema_error"
    ERROR = "error"
