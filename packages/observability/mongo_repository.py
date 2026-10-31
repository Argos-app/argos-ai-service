"""Adapter Mongo para documentos brutos de observabilidade."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from bson import BSON, Decimal128
from pymongo.errors import DuplicateKeyError

from packages.observability.models import Observation, ObservationStatus


class ObservationConflictError(RuntimeError):
    """O mesmo ID de tentativa foi reutilizado para conteúdo terminal divergente."""


class MongoObservationRepository:
    """Serializa e atualiza observações sem regredir estados terminais."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    @staticmethod
    def document_for(observation: Observation) -> tuple[dict[str, Any], int]:
        """Gera documento BSON e seu tamanho exato para impor limites da fila."""
        document = observation.model_dump(mode="python", by_alias=True, exclude_none=True)
        document = _bson_value(document)
        document["updated_at"] = max(document["updated_at"], _utcnow_millisecond())
        return document, len(BSON.encode(document))

    async def ensure_indexes(self) -> None:
        await self._collection.create_index(
            [("correlation_id", 1), ("started_at", 1)],
            name="ix_observability_correlation_started",
        )
        await self._collection.create_index(
            [("interaction_id", 1), ("started_at", 1)],
            name="ix_observability_interaction_started",
        )
        await self._collection.create_index(
            [("session_id", 1), ("started_at", 1)],
            name="ix_observability_session_started",
        )
        await self._collection.create_index(
            [("started_at", 1), ("_id", 1)], name="ix_observability_started_id"
        )
        await self._collection.create_index(
            [("updated_at", 1), ("_id", 1)], name="ix_observability_updated_id"
        )

    async def write_document(self, document: dict[str, Any]) -> None:
        """Grava início ou término; inserção terminal cobre início perdido.

        Outra gravação do mesmo ID venceu; reler para decidir idempotência."""
        for _ in range(3):
            existing = await self._collection.find_one({"_id": document["_id"]})
            if existing is None:
                try:
                    await self._collection.insert_one(document)
                except DuplicateKeyError:
                    continue
                return

            incoming_status = document["status"]
            existing_status = existing.get("status")
            if incoming_status == ObservationStatus.STARTED.value:
                if existing_status != ObservationStatus.STARTED.value:
                    return
                if _without_updated_at(existing) == _without_updated_at(document):
                    return
                raise ObservationConflictError("início divergente para o mesmo ID")

            if existing_status != ObservationStatus.STARTED.value:
                if _without_updated_at(existing) == _without_updated_at(document):
                    return
                raise ObservationConflictError("término divergente para o mesmo ID")

            if not _same_attempt_identity(existing, document):
                raise ObservationConflictError("identidade divergente para o mesmo ID")

            result = await self._collection.replace_one(
                {"_id": document["_id"], "status": ObservationStatus.STARTED.value},
                document,
            )
            if result.matched_count == 1:
                return

        raise RuntimeError("estado mudou durante a atualização da observação")


def _utcnow_millisecond() -> datetime:
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _bson_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Decimal):
        return Decimal128(value)
    if isinstance(value, dict):
        return {key: _bson_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_bson_value(item) for item in value]
    return value


def _without_updated_at(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "updated_at"}


def _same_attempt_identity(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    identity_fields = (
        "_id",
        "correlation_id",
        "parent_observation_id",
        "session_id",
        "interaction_id",
        "schema_version",
        "service",
        "environment",
        "service_version",
        "operation_kind",
        "operation_name",
        "operation_phase",
        "observation_level",
        "agent",
        "execution_mode",
        "started_at",
    )
    return all(existing.get(field) == incoming.get(field) for field in identity_fields)
