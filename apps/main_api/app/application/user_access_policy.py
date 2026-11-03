"""Regras comuns para habilitar o acesso de um cadastro à API de IA."""

from datetime import UTC, datetime

from app.domain.user_access import UserAccess


def is_user_enabled_for_ai(user: UserAccess, *, now: datetime | None = None) -> bool:
    """Retorna se o cadastro está ativo e possui fazendas autorizadas."""
    if not user.active or not user.permission_active or not user.farms:
        return False
    if user.blocked_until is None:
        return True

    instant = now or datetime.now(UTC)
    blocked_until = user.blocked_until
    if blocked_until.tzinfo is None:
        blocked_until = blocked_until.replace(tzinfo=UTC)
    return blocked_until <= instant
