from datetime import UTC, datetime

from packages.observability.mongo_repository import (
    _documents_equal,
    _same_attempt_identity,
)


def test_mongo_naive_utc_timestamps_match_aware_observations():
    instant = datetime(2026, 10, 29, 12, 30, tzinfo=UTC)
    existing = {
        "_id": "observation",
        "started_at": instant.replace(tzinfo=None),
        "updated_at": instant.replace(tzinfo=None),
        "status": "started",
        "service": "main_api",
    }
    incoming = {**existing, "started_at": instant, "updated_at": instant, "status": "started"}

    assert _same_attempt_identity(existing, incoming)
    assert _documents_equal(existing, incoming)


def test_timestamp_comparison_preserves_different_instants():
    instant = datetime(2026, 10, 29, 12, 30, tzinfo=UTC)
    existing = {"_id": "observation", "started_at": instant.replace(tzinfo=None)}
    incoming = {"_id": "observation", "started_at": instant}
    incoming["started_at"] = incoming["started_at"].replace(minute=31)

    assert not _same_attempt_identity(existing, incoming)
