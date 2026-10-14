"""Schema versionado para persistir uma observação técnica por tentativa."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class _ObservabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class OperationKind(StrEnum):
    HTTP = "http"
    INTERACTION = "interaction"
    GRAPH = "graph"
    AGENT = "agent"
    MODEL = "model"
    EMBEDDING = "embedding"
    TOOL = "tool"
    MCP = "mcp"
    A2A = "a2a"
    WORKFLOW = "workflow"
    STORAGE = "storage"
    EXTERNAL = "external"


class OperationPhase(StrEnum):
    INITIALIZATION = "initialization"
    EXECUTION = "execution"
    VALIDATION = "validation"
    RECOVERY = "recovery"


class ObservationLevel(StrEnum):
    LOCAL_OPERATION = "local_operation"
    SDK_INVOCATION = "sdk_invocation"
    TRANSPORT_ATTEMPT = "transport_attempt"


class ExecutionMode(StrEnum):
    REAL = "real"
    MOCK = "mock"
    UNAVAILABLE = "unavailable"


class ObservationStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ErrorOrigin(StrEnum):
    APPLICATION = "application"
    PROVIDER = "provider"
    TRANSPORT = "transport"
    PROTOCOL = "protocol"
    VALIDATION = "validation"
    STORAGE = "storage"
    UNKNOWN = "unknown"


class AttemptReason(StrEnum):
    INITIAL = "initial"
    RETRY_TRANSIENT = "retry_transient"
    FALLBACK = "fallback"
    REPAIR = "repair"
    JUDGE_REVISION = "judge_revision"
    SOURCE_RENEWAL = "source_renewal"
    UNKNOWN = "unknown"


class AttemptVisibility(StrEnum):
    INDIVIDUAL = "individual"
    INTERNAL_RETRIES_UNKNOWN = "internal_retries_unknown"


class CancellationState(StrEnum):
    REQUESTED = "requested"
    OBSERVED = "observed"
    CONFIRMED = "confirmed"


class UsageAvailability(StrEnum):
    RETURNED = "returned"
    PARTIAL = "partial"
    ABSENT = "absent"
    NOT_APPLICABLE = "not_applicable"


class UsageSource(StrEnum):
    PROVIDER = "provider"
    SDK = "sdk"


NonNegativeCount = Annotated[StrictInt, Field(ge=0)]
NonNegativeDuration = Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]
BoundedName = Annotated[StrictStr, Field(min_length=1, max_length=120)]
OptionalCode = StrictInt | Annotated[StrictStr, Field(min_length=1, max_length=120)]


def _require_utc_millisecond(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} deve incluir timezone UTC")
    if value.microsecond % 1000:
        raise ValueError(f"{field_name} deve ter precisão de milissegundos")
    return value.astimezone(UTC)


class HttpMetadata(_ObservabilityModel):
    method: Annotated[StrictStr, Field(min_length=1, max_length=12)] | None = None
    route: Annotated[StrictStr, Field(min_length=1, max_length=200)] | None = None
    status_code: Annotated[StrictInt, Field(ge=100, le=599)] | None = None


class ProtocolMetadata(_ObservabilityModel):
    name: Literal["mcp", "a2a"]
    operation: BoundedName
    skill: BoundedName | None = None
    task_state: Annotated[StrictStr, Field(min_length=1, max_length=80)] | None = None
    error_code: OptionalCode | None = None


class ResultMetadata(_ObservabilityModel):
    source: BoundedName | None = None
    code: BoundedName | None = None
    confirmed: StrictBool | None = None
    recovered: StrictBool | None = None


class ErrorMetadata(_ObservabilityModel):
    type: BoundedName
    origin: ErrorOrigin
    code: OptionalCode | None = None
    message_sanitized: Annotated[StrictStr, Field(min_length=1, max_length=512)] | None = None


class Usage(_ObservabilityModel):
    availability: UsageAvailability
    source: UsageSource | None = None
    input_tokens: NonNegativeCount | None = None
    output_tokens: NonNegativeCount | None = None
    total_tokens: NonNegativeCount | None = None
    cache_read_tokens: NonNegativeCount | None = None
    cache_write_tokens: NonNegativeCount | None = None
    reasoning_tokens: NonNegativeCount | None = None
    # Nomes originais do provedor para categorias adicionais, com valores apenas numéricos.
    details: dict[BoundedName, NonNegativeCount] = Field(default_factory=dict, max_length=32)


class Billing(_ObservabilityModel):
    reported_cost: Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=10)] | None = None
    currency: Annotated[StrictStr, Field(pattern=r"^[A-Z]{3}$")] | None = None
    cost_source: Literal["provider"] | None = None
    service_tier: Annotated[StrictStr, Field(min_length=1, max_length=80)] | None = None
    price_reference: Annotated[StrictStr, Field(min_length=1, max_length=120)] | None = None

    @model_validator(mode="after")
    def validate_reported_cost(self) -> Billing:
        if self.reported_cost is not None and not all((self.currency, self.cost_source)):
            raise ValueError("custo informado exige moeda e origem provider")
        if self.reported_cost is None and (
            self.currency is not None or self.cost_source is not None
        ):
            raise ValueError("moeda e origem só podem aparecer com custo informado")
        return self


class ResourceUsage(_ObservabilityModel):
    name: BoundedName
    quantity: Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=10)]
    unit: BoundedName
    source: Literal["provider", "sdk", "application"]


class Observation(_ObservabilityModel):
    """Documento atual de uma operação/tentativa, atualizado ao seu término.

    Datas são UTC com precisão de milissegundos. Duração usa milissegundos
    monotônicos locais e não é reconstruída a partir de timestamps.
    """

    id: str = Field(alias="_id", min_length=36, max_length=36)
    schema_version: Literal["1"] = "1"
    correlation_id: UUID | None = None
    parent_observation_id: UUID | None = None
    service: BoundedName
    environment: BoundedName
    service_version: Annotated[StrictStr, Field(min_length=1, max_length=120)] | None = None
    operation_kind: OperationKind
    operation_name: BoundedName
    operation_phase: OperationPhase | None = None
    observation_level: ObservationLevel
    agent: BoundedName | None = None
    execution_mode: ExecutionMode
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: NonNegativeDuration | None = None
    updated_at: datetime
    status: ObservationStatus
    http: HttpMetadata | None = None
    protocol: ProtocolMetadata | None = None
    result: ResultMetadata | None = None
    error: ErrorMetadata | None = None
    provider: BoundedName | None = None
    model_requested: BoundedName | None = None
    model_returned: BoundedName | None = None
    model_revision: BoundedName | None = None
    usage: Usage | None = None
    billing: Billing | None = None
    resources: tuple[ResourceUsage, ...] = Field(default=(), max_length=32)
    timeout_ms: Annotated[StrictFloat, Field(gt=0, allow_inf_nan=False)] | None = None
    timeout_phase: BoundedName | None = None
    cancellation_state: CancellationState | None = None
    attempt_number: Annotated[StrictInt, Field(ge=1)] | None = None
    attempt_reason: AttemptReason | None = None
    attempt_visibility: AttemptVisibility | None = None
    replayed_result: StrictBool | None = None

    @field_validator("id")
    @classmethod
    def validate_uuid_id(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise ValueError("_id deve ser UUID") from exc
        if str(parsed) != value.lower():
            raise ValueError("_id deve usar formato UUID canônico")
        return str(parsed)

    @field_validator("started_at", "ended_at", "updated_at")
    @classmethod
    def validate_utc_timestamps(cls, value: datetime | None, info) -> datetime | None:
        if value is None:
            return None
        return _require_utc_millisecond(value, info.field_name)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Observation:
        if self.status == ObservationStatus.STARTED:
            if self.ended_at is not None or self.duration_ms is not None:
                raise ValueError("observação started não pode ter término ou duração")
        elif self.ended_at is None or self.duration_ms is None:
            raise ValueError("estado terminal exige fim e duração")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at não pode anteceder started_at")
        if self.updated_at < self.started_at:
            raise ValueError("updated_at não pode anteceder started_at")
        if self.ended_at is not None and self.updated_at < self.ended_at:
            raise ValueError("updated_at não pode anteceder ended_at")
        if (
            self.status
            in (
                ObservationStatus.FAILED,
                ObservationStatus.TIMEOUT,
            )
            and self.error is None
        ):
            raise ValueError("falha e timeout exigem metadados de erro")
        if self.status != ObservationStatus.STARTED and self.usage is not None:
            if self.usage.availability == UsageAvailability.ABSENT:
                if (
                    any(
                        value is not None
                        for value in (
                            self.usage.input_tokens,
                            self.usage.output_tokens,
                            self.usage.total_tokens,
                            self.usage.cache_read_tokens,
                            self.usage.cache_write_tokens,
                            self.usage.reasoning_tokens,
                        )
                    )
                    or self.usage.details
                ):
                    raise ValueError("uso ausente não pode conter contagens")
        return self
