"""Persistência MongoDB das operações de envio e da idempotência."""

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from argos_mcp_email.domain.models import DispatchRecord, DispatchState


class DispatchCollection(Protocol):
    async def create_index(self, keys: list[tuple[str, int]], **options: Any) -> Any: ...

    async def insert_one(self, document: dict[str, Any]) -> Any: ...

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None: ...

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
    ) -> Any: ...


class MongoDispatchRepository:
    """Implementa ``DispatchRepository`` sobre uma collection Mongo."""

    def __init__(self, collection: DispatchCollection) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        await self._collection.create_index(
            [("idempotency_key", 1)],
            unique=True,
            name="uq_dispatch_idempotency_key",
        )

    async def find_by_idempotency_key(self, key: str) -> DispatchRecord | None:
        document = await self._collection.find_one({"idempotency_key": key})
        return self._from_document(document) if document else None

    async def create_pending(
        self,
        *,
        operation_id: UUID,
        report_id: UUID,
        requested_by: UUID,
        idempotency_key: str,
    ) -> DispatchRecord:
        now = datetime.now(UTC)
        document = {
            "_id": str(operation_id),
            "report_id": str(report_id),
            "requested_by": str(requested_by),
            "idempotency_key": idempotency_key,
            "state": DispatchState.PENDING.value,
            "provider_message_id": None,
            "error_code": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self._collection.insert_one(document)
        except Exception as exc:
            if getattr(exc, "code", None) != 11000:
                raise
            existing = await self.find_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            return existing
        return self._from_document(document)

    async def update_state(
        self,
        operation_id: UUID,
        state: DispatchState,
        *,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> DispatchRecord:
        updated_at = datetime.now(UTC)
        await self._collection.update_one(
            {"_id": str(operation_id)},
            {
                "$set": {
                    "state": state.value,
                    "provider_message_id": provider_message_id,
                    "error_code": error_code,
                    "updated_at": updated_at,
                }
            },
        )
        result = await self.get(operation_id)
        if result is None:
            raise KeyError(f"operação não encontrada: {operation_id}")
        return result

    async def get(self, operation_id: UUID) -> DispatchRecord | None:
        document = await self._collection.find_one({"_id": str(operation_id)})
        return self._from_document(document) if document else None

    @staticmethod
    def _from_document(document: dict[str, Any]) -> DispatchRecord:
        return DispatchRecord(
            operation_id=UUID(str(document["_id"])),
            report_id=UUID(str(document["report_id"])),
            requested_by=UUID(str(document["requested_by"])),
            idempotency_key=document["idempotency_key"],
            state=DispatchState(document["state"]),
            provider_message_id=document.get("provider_message_id"),
            error_code=document.get("error_code"),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
        )
