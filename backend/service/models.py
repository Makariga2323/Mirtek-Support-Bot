from dataclasses import dataclass
from enum import Enum

class MessageStatus(str, Enum):
    PROCESSING = "processing"
    ANSWERED = "answered"
    TOO_COMPLEX = "too_complex"
    FAILED = "failed"

class FailReason(str, Enum):
    TIMEOUT = "timeout"
    CORE_ERROR = "core_error"
    INTERRUPTED = "interuppted"

@dataclass(frozen=True)
class CoreResult:
    status:MessageStatus
    answer: str = ""
@dataclass(frozen=True)
class MessageResult:
    status:MessageStatus
    answer: str = ""
    fail_reason: FailReason | None = None
    latency_ms: int | None = None