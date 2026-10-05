from uuid import uuid4

import pytest
from argos_mcp_email.adapters.storage import MongoDispatchRepository
from argos_mcp_email.domain import DispatchState


class DuplicateError(Exception):
    code = 11000


class FakeCollection:
    def __init__(self):
        self.documents = {}
        self.indexes = []

    async def create_index(self, keys, **options):
        self.indexes.append((keys, options))

    async def insert_one(self, document):
        if any(
            value["idempotency_key"] == document["idempotency_key"]
            for value in self.documents.values()
        ):
            raise DuplicateError()
        self.documents[document["_id"]] = document

    async def find_one(self, query):
        if "_id" in query:
            return self.documents.get(query["_id"])
        return next(
            (
                document
                for document in self.documents.values()
                if document["idempotency_key"] == query["idempotency_key"]
            ),
            None,
        )

    async def update_one(self, query, update):
        self.documents[query["_id"]].update(update["$set"])


@pytest.mark.asyncio
async def test_creates_unique_idempotency_index_and_reuses_existing_operation():
    collection = FakeCollection()
    repository = MongoDispatchRepository(collection)
    operation_id = uuid4()
    report_id = uuid4()
    requester = uuid4()

    await repository.ensure_indexes()
    first = await repository.create_pending(
        operation_id=operation_id,
        report_id=report_id,
        requested_by=requester,
        idempotency_key="send-1",
    )
    second = await repository.create_pending(
        operation_id=uuid4(),
        report_id=report_id,
        requested_by=requester,
        idempotency_key="send-1",
    )

    assert collection.indexes[0][1]["unique"] is True
    assert first.operation_id == second.operation_id
    assert first.state == DispatchState.PENDING


@pytest.mark.asyncio
async def test_updates_dispatch_state():
    collection = FakeCollection()
    repository = MongoDispatchRepository(collection)
    operation_id = uuid4()
    await repository.create_pending(
        operation_id=operation_id,
        report_id=uuid4(),
        requested_by=uuid4(),
        idempotency_key="send-2",
    )

    result = await repository.update_state(operation_id, DispatchState.UNKNOWN)

    assert result.state == DispatchState.UNKNOWN
