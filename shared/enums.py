"""Phase 1 枚举。"""
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCESS = "success"
    SUCCESS_WITH_PARTIAL_FAILURE = "success_with_partial_failure"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DocWriteStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    WRITING = "writing"
    SUCCESS = "success"
    FAILED = "failed"
    ORPHANED = "orphaned"
    CANCELLED = "cancelled"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    CLOSED = "closed"


class RiskLevel(str, Enum):
    L0_READ = "L0_read"
    L1_COMPUTE = "L1_compute"
    L2_SIDE_EFFECT = "L2_side_effect"


class ApprovalMode(str, Enum):
    BIND_SCOPE = "bind_scope"
    EXPLICIT_CARD = "explicit_card"