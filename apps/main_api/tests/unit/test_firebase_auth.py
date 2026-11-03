"""Fronteira Admin SDK e associação exclusivamente pelo UID, sem serviços externos."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.infrastructure.firebase_auth import (
    FirebaseAdminIdTokenVerifier,
    FirebaseIdentity,
    FirebaseIdentityAuthenticator,
    FirebaseVerificationUnavailable,
    InvalidFirebaseIdToken,
)


class _FirebaseError(Exception):
    pass


class _InvalidIdTokenError(_FirebaseError):
    pass


class _ExpiredIdTokenError(_InvalidIdTokenError):
    pass


class _RevokedIdTokenError(_InvalidIdTokenError):
    pass


class _UserDisabledError(_FirebaseError):
    pass


def _verifier(*, claims=None, error=None):
    sdk = SimpleNamespace(
        verify_id_token=Mock(return_value=claims, side_effect=error),
        InvalidIdTokenError=_InvalidIdTokenError,
        UserDisabledError=_UserDisabledError,
    )
    app = object()
    verifier = FirebaseAdminIdTokenVerifier(
        app, auth_module=sdk, firebase_error_type=_FirebaseError
    )
    return verifier, sdk, app


@pytest.mark.parametrize(
    "email,verified,expected_email,expected_verified",
    [
        (None, False, None, False),
        ("", False, None, False),
        ("  identity@example.test ", True, "identity@example.test", True),
        ("identity@example.test", False, "identity@example.test", False),
        ("identity@example.test", "true", "identity@example.test", False),
    ],
)
def test_admin_verifies_revocation_and_extracts_optional_identity(
    email, verified, expected_email, expected_verified
):
    verifier, sdk, app = _verifier(
        claims={"uid": "firebase-uid", "email": email, "email_verified": verified}
    )
    identity = verifier.verify("firebase-id-token")
    sdk.verify_id_token.assert_called_once_with("firebase-id-token", app=app, check_revoked=True)
    assert identity == FirebaseIdentity("firebase-uid", expected_email, expected_verified)


@pytest.mark.parametrize("claims", [None, [], {}, {"uid": None}, {"uid": 1}, {"uid": " "}])
def test_admin_rejects_invalid_claims_or_missing_uid(claims):
    verifier, _, _ = _verifier(claims=claims)
    with pytest.raises(InvalidFirebaseIdToken):
        verifier.verify("firebase-id-token")


@pytest.mark.parametrize("token", [None, "", "  ", "x" * 16_385])
def test_admin_rejects_invalid_input_without_calling_sdk(token):
    verifier, sdk, _ = _verifier()
    with pytest.raises(InvalidFirebaseIdToken):
        verifier.verify(token)
    sdk.verify_id_token.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        _InvalidIdTokenError(),
        _ExpiredIdTokenError(),
        _RevokedIdTokenError(),
        _UserDisabledError(),
        ValueError(),
    ],
)
def test_admin_normalizes_rejected_tokens(error):
    verifier, _, _ = _verifier(error=error)
    with pytest.raises(InvalidFirebaseIdToken):
        verifier.verify("firebase-id-token")


def test_admin_reports_dependency_outage_separately_from_rejected_identity():
    verifier, _, _ = _verifier(error=_FirebaseError("internal SDK detail"))
    with pytest.raises(FirebaseVerificationUnavailable):
        verifier.verify("firebase-id-token")


@pytest.mark.parametrize(
    "email,verified", [(None, False), ("other@example.test", False), ("other@example.test", True)]
)
@pytest.mark.asyncio
async def test_authenticator_looks_up_uid_without_email_association(auth_user, email, verified):
    verifier = Mock()
    verifier.verify.return_value = FirebaseIdentity(auth_user.firebase_uid, email, verified)
    users = SimpleNamespace(find_by_firebase_uid=AsyncMock(return_value=auth_user))
    result = await FirebaseIdentityAuthenticator(verifier, users).authenticate("id-token")
    verifier.verify.assert_called_once_with("id-token")
    users.find_by_firebase_uid.assert_awaited_once_with(auth_user.firebase_uid)
    assert result.user == auth_user
    assert result.user.email == "database@example.test"


@pytest.mark.asyncio
async def test_authenticator_does_not_search_database_after_rejected_token():
    verifier = Mock()
    verifier.verify.side_effect = InvalidFirebaseIdToken()
    users = SimpleNamespace(find_by_firebase_uid=AsyncMock())
    with pytest.raises(InvalidFirebaseIdToken):
        await FirebaseIdentityAuthenticator(verifier, users).authenticate("bad-token")
    users.find_by_firebase_uid.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_uid_is_not_associated_by_matching_email(auth_user):
    verifier = Mock()
    verifier.verify.return_value = FirebaseIdentity("unknown-uid", auth_user.email, True)
    users = SimpleNamespace(find_by_firebase_uid=AsyncMock(return_value=None))
    result = await FirebaseIdentityAuthenticator(verifier, users).authenticate("id-token")
    assert result.user is None
    users.find_by_firebase_uid.assert_awaited_once_with("unknown-uid")
