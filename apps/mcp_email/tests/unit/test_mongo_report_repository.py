from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from argos_mcp_email.adapters.storage.mongo_reports import MongoReportRepository
from argos_mcp_email.domain import (
    PreparedReportRecord,
    ReportAlreadyExistsError,
    ReportAttachmentRecord,
    ResolvedRecipient,
)


class DuplicateError(Exception):
    code = 11000


class FakeCollection:
    def __init__(self):
        self.document = None

    async def insert_one(self, document):
        if self.document is not None:
            raise DuplicateError()
        self.document = document

    async def find_one(self, query):
        if self.document and self.document["_id"] == query["_id"]:
            return self.document
        return None


def report() -> PreparedReportRecord:
    now = datetime.now(UTC)
    return PreparedReportRecord(
        report_id=uuid4(),
        requested_by=uuid4(),
        recipients=(ResolvedRecipient(uuid4(), "Matheus Fazan", "matheus@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        source_payload_hash="sha256:abc",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        attachments=(
            ReportAttachmentRecord(
                filename="relatorio.txt",
                content_type="text/plain",
                content=b"relatorio sintetico",
                sha256=sha256(b"relatorio sintetico").hexdigest(),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_serializes_and_recovers_report_from_mongo_document() -> None:
    collection = FakeCollection()
    repository = MongoReportRepository(collection)
    prepared = report()

    await repository.save(prepared)
    recovered = await repository.get(prepared.report_id)

    assert recovered == prepared
    assert collection.document["_id"] == str(prepared.report_id)


@pytest.mark.asyncio
async def test_maps_mongo_duplicate_key_to_domain_error() -> None:
    collection = FakeCollection()
    repository = MongoReportRepository(collection)
    prepared = report()
    await repository.save(prepared)

    with pytest.raises(ReportAlreadyExistsError):
        await repository.save(prepared)
