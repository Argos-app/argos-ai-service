from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.user_memory_service import UserMemoryService
from app.domain.memory import (
    MemoryFieldAction,
    UserMemory,
    UserMemoryChange,
    UserMemoryFieldChange,
)
from app.infrastructure.persistence.mongo import _memory_document, _memory_from_document


class FakeMemoryRepository:
    def __init__(self, initial=None):
        self.memories = {initial.user_id: initial} if initial else {}
        self.saved = []

    async def get(self, user_id):
        return self.memories.get(user_id)

    async def save_if_current(self, memory, *, expected_revision):
        current = self.memories.get(memory.user_id)
        revision = current.revision if current else None
        if revision != expected_revision:
            return False
        self.memories[memory.user_id] = memory
        self.saved.append(memory)
        return True


class FakeExtractor:
    def __init__(self, change):
        self.change = change
        self.calls = []

    async def extract(self, **kwargs):
        self.calls.append(kwargs)
        return self.change


def _profile(user_id, session_id, interaction_id, updated_at):
    return UserMemory(
        user_id=user_id,
        tone="simples e acolhedor",
        general_goal="entender medicamentos e prevenir perdas",
        tone_source_session_id=session_id,
        tone_source_interaction_id=interaction_id,
        goal_source_session_id=session_id,
        goal_source_interaction_id=interaction_id,
        revision=1,
        updated_at=updated_at,
        tone_source_excerpt="Prefiro um tom simples e acolhedor.",
        goal_source_excerpt="Meu objetivo é entender medicamentos e prevenir perdas.",
        tone_updated_at=updated_at,
        goal_updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_memory_updates_only_declared_fields_and_keeps_provenance():
    user_id, session_id, interaction_id = uuid4(), uuid4(), uuid4()
    created_at = datetime.now(UTC)
    source = "Prefiro um tom simples. Meu objetivo é entender medicamentos."
    extractor = FakeExtractor(
        UserMemoryChange(
            tone=UserMemoryFieldChange(
                MemoryFieldAction.SET,
                "simples e acolhedor",
                "Prefiro um tom simples.",
            ),
            general_goal=UserMemoryFieldChange(
                MemoryFieldAction.SET,
                "entender medicamentos",
                "Meu objetivo é entender medicamentos.",
            ),
        )
    )
    repository = FakeMemoryRepository()
    service = UserMemoryService(repository=repository, extractor_provider=lambda: extractor)

    memory = await service.update_from_user_entry(
        user_id=user_id,
        session_id=session_id,
        interaction_id=interaction_id,
        source_created_at=created_at,
        sanitized_user_input=source,
    )

    assert memory is not None
    assert memory.tone == "simples e acolhedor"
    assert memory.general_goal == "entender medicamentos"
    assert memory.tone_source_session_id == session_id
    assert memory.tone_source_interaction_id == interaction_id
    assert memory.tone_source_excerpt == "Prefiro um tom simples."
    assert memory.tone_updated_at == created_at
    assert memory.summary_text == (
        "Tom de comunicação: simples e acolhedor; Objetivo geral: entender medicamentos"
    )
    assert extractor.calls[0]["sanitized_user_input"] == source


@pytest.mark.asyncio
async def test_question_or_unverifiable_excerpt_does_not_create_profile_memory():
    user_id, session_id, interaction_id = uuid4(), uuid4(), uuid4()
    extractor = FakeExtractor(
        UserMemoryChange(
            general_goal=UserMemoryFieldChange(
                MemoryFieldAction.SET,
                "entender medicamentos",
                "Meu objetivo geral é entender medicamentos.",
            )
        )
    )
    repository = FakeMemoryRepository()
    service = UserMemoryService(repository=repository, extractor_provider=lambda: extractor)

    result = await service.update_from_user_entry(
        user_id=user_id,
        session_id=session_id,
        interaction_id=interaction_id,
        source_created_at=datetime.now(UTC),
        sanitized_user_input="Quais medicamentos foram usados no lote?",
    )

    assert result is None
    assert repository.saved == []


@pytest.mark.asyncio
async def test_explicit_forget_removes_only_requested_field_and_leaves_tombstone():
    user_id, session_id, source_interaction_id, interaction_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    old = datetime.now(UTC) - timedelta(minutes=1)
    created_at = datetime.now(UTC)
    current = _profile(user_id, session_id, source_interaction_id, old)
    repository = FakeMemoryRepository(current)
    extractor = FakeExtractor(UserMemoryChange())
    service = UserMemoryService(repository=repository, extractor_provider=lambda: extractor)

    memory = await service.update_from_user_entry(
        user_id=user_id,
        session_id=session_id,
        interaction_id=interaction_id,
        source_created_at=created_at,
        sanitized_user_input="Por favor, esqueça meu tom de comunicação.",
    )

    assert memory is not None
    assert memory.tone is None
    assert memory.tone_source_excerpt is None
    assert memory.tone_source_session_id is None
    assert memory.tone_updated_at == created_at
    assert memory.general_goal == current.general_goal
    assert memory.goal_source_interaction_id == source_interaction_id
    assert memory.revision == 2


@pytest.mark.asyncio
async def test_old_extraction_cannot_restore_deleted_field_or_overwrite_newer_value():
    user_id, session_id, newer_interaction_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    current = _profile(user_id, session_id, newer_interaction_id, now)
    repository = FakeMemoryRepository(current)
    extractor = FakeExtractor(
        UserMemoryChange(
            tone=UserMemoryFieldChange(
                MemoryFieldAction.SET,
                "formal",
                "Prefiro um tom formal.",
            )
        )
    )
    service = UserMemoryService(repository=repository, extractor_provider=lambda: extractor)

    result = await service.update_from_user_entry(
        user_id=user_id,
        session_id=uuid4(),
        interaction_id=uuid4(),
        source_created_at=now - timedelta(seconds=1),
        sanitized_user_input="Prefiro um tom formal.",
    )

    assert result is current
    assert repository.saved == []
    assert result.tone == "simples e acolhedor"


@pytest.mark.asyncio
async def test_extractor_cannot_delete_a_field_without_explicit_user_request():
    user_id, session_id, interaction_id = uuid4(), uuid4(), uuid4()
    current = _profile(user_id, session_id, interaction_id, datetime.now(UTC))
    repository = FakeMemoryRepository(current)
    extractor = FakeExtractor(
        UserMemoryChange(tone=UserMemoryFieldChange(action=MemoryFieldAction.DELETE))
    )
    service = UserMemoryService(repository=repository, extractor_provider=lambda: extractor)

    result = await service.update_from_user_entry(
        user_id=user_id,
        session_id=uuid4(),
        interaction_id=uuid4(),
        source_created_at=datetime.now(UTC) + timedelta(seconds=1),
        sanitized_user_input="Quero entender melhor os dados.",
    )

    assert result is current
    assert repository.saved == []


def test_explicit_deletion_parser_does_not_treat_negated_forget_as_delete():
    from app.application.user_memory_service import _explicit_deletions

    assert _explicit_deletions("Não esqueça meu objetivo geral.").general_goal.action == (
        MemoryFieldAction.UNCHANGED
    )
    assert _explicit_deletions("Não considere mais meu objetivo geral.").general_goal.action == (
        MemoryFieldAction.DELETE
    )


def test_explicit_deletion_parser_supports_clearing_both_profile_fields():
    from app.application.user_memory_service import _explicit_deletions

    change = _explicit_deletions("Apague minhas preferências da memória.")
    assert change.tone.action == MemoryFieldAction.DELETE
    assert change.general_goal.action == MemoryFieldAction.DELETE


def test_mongo_memory_document_round_trips_field_sources_and_deletion_timestamps():
    user_id, session_id, interaction_id = uuid4(), uuid4(), uuid4()
    updated_at = datetime.now(UTC)
    memory = _profile(user_id, session_id, interaction_id, updated_at)

    restored = _memory_from_document(_memory_document(memory))

    assert restored == memory
