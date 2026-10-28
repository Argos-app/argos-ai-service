"""Contexto de correlação e parentesco entre observações aninhadas."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

_correlation_id: ContextVar[UUID | None] = ContextVar("argos_correlation_id", default=None)
_parent_observation_id: ContextVar[UUID | None] = ContextVar(
    "argos_parent_observation_id", default=None
)
_user_id: ContextVar[UUID | None] = ContextVar("argos_user_id", default=None)
_session_id: ContextVar[UUID | None] = ContextVar("argos_session_id", default=None)
_interaction_id: ContextVar[UUID | None] = ContextVar("argos_interaction_id", default=None)


def current_correlation_id() -> UUID | None:
    return _correlation_id.get()


def current_parent_observation_id() -> UUID | None:
    return _parent_observation_id.get()


def current_user_id() -> UUID | None:
    return _user_id.get()


def current_session_id() -> UUID | None:
    return _session_id.get()


def current_interaction_id() -> UUID | None:
    return _interaction_id.get()


def correlation_key(correlation_id: UUID | str) -> str:
    """Gera uma chave estável não UUID para correlacionar traces após anonimizar UUIDs."""
    value = str(correlation_id)
    try:
        value = str(UUID(value))
    except ValueError:
        pass
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def bind_correlation(correlation_id: UUID) -> Iterator[None]:
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


@contextmanager
def observation_scope(observation_id: UUID) -> Iterator[None]:
    token: Token[UUID | None] = _parent_observation_id.set(observation_id)
    try:
        yield
    finally:
        _parent_observation_id.reset(token)


@contextmanager
def bind_user_id(user_id: UUID) -> Iterator[None]:
    token: Token[UUID | None] = _user_id.set(user_id)
    try:
        yield
    finally:
        _user_id.reset(token)


@contextmanager
def bind_interaction(session_id: UUID, interaction_id: UUID) -> Iterator[None]:
    """Vincula os IDs funcionais a cada observação técnica da interação."""
    session_token = _session_id.set(session_id)
    interaction_token = _interaction_id.set(interaction_id)
    try:
        yield
    finally:
        _interaction_id.reset(interaction_token)
        _session_id.reset(session_token)
