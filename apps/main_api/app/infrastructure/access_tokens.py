"""Emissão e validação de JWTs próprios da API principal."""

import base64
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from app.settings import Settings

_MAX_TOKEN_BYTES = 16_384


class AccessTokenConfigurationError(ValueError):
    """Configuração da chave emissora incompleta ou inválida."""


class InvalidAccessToken(ValueError):
    """JWT próprio inválido, expirado ou incompatível com o contrato."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: UUID
    firebase_uid: str
    token_id: UUID
    issued_at: int


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_segment(value: str) -> bytes:
    valid_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    if not value or any(char not in valid_characters for char in value):
        raise InvalidAccessToken("token inválido")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise InvalidAccessToken("token inválido") from exc


def _decode_object(value: bytes) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise InvalidAccessToken("token inválido")
            result[key] = item
        return result

    try:
        result = json.loads(value, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise InvalidAccessToken("token inválido") from exc
    if not isinstance(result, dict):
        raise InvalidAccessToken("token inválido")
    return result


class MainApiAccessTokenCodec:
    """Emite JWT mínimo; o escopo cadastral é recarregado do banco por requisição."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not settings.access_token_key_id or not settings.access_token_private_key:
            raise AccessTokenConfigurationError(
                "MAIN_API_ACCESS_TOKEN_KEY_ID e MAIN_API_ACCESS_TOKEN_PRIVATE_KEY são obrigatórios"
            )
        if not settings.access_token_issuer.strip() or not settings.access_token_audience.strip():
            raise AccessTokenConfigurationError("issuer e audience do JWT são obrigatórios")
        self._issuer = settings.access_token_issuer
        self._audience = settings.access_token_audience
        self._key_id = settings.access_token_key_id
        self._ttl_seconds = settings.access_token_ttl_seconds
        self._clock = clock
        self._private_key = self._load_private_key(settings.access_token_private_key)
        self._public_key = self._private_key.public_key()

    @staticmethod
    def _load_private_key(value: str) -> Ed25519PrivateKey:
        try:
            key = serialization.load_der_private_key(
                base64.b64decode(value, validate=True), password=None
            )
        except (ValueError, TypeError) as exc:
            raise AccessTokenConfigurationError(
                "MAIN_API_ACCESS_TOKEN_PRIVATE_KEY deve ser PKCS#8 DER Ed25519 em Base64"
            ) from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise AccessTokenConfigurationError(
                "MAIN_API_ACCESS_TOKEN_PRIVATE_KEY deve usar Ed25519"
            )
        return key

    def issue(self, user_id: UUID, firebase_uid: str) -> str:
        if not isinstance(firebase_uid, str) or not firebase_uid.strip() or len(firebase_uid) > 128:
            raise ValueError("firebase_uid inválido")
        issued_at = int(self._clock())
        token_id = uuid4()
        header = {"alg": "EdDSA", "kid": self._key_id, "typ": "JWT"}
        claims = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(user_id),
            "firebase_uid": firebase_uid,
            "jti": str(token_id),
            "iat": issued_at,
            "nbf": issued_at,
            "exp": issued_at + self._ttl_seconds,
            "v": 1,
        }
        encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
        encoded_claims = _b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        return f"{encoded_header}.{encoded_claims}.{_b64url(self._private_key.sign(signing_input))}"

    def verify(self, token: str) -> AccessTokenClaims:
        if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise InvalidAccessToken("token inválido")
        parts = token.split(".")
        if len(parts) != 3:
            raise InvalidAccessToken("token inválido")
        header = _decode_object(_decode_segment(parts[0]))
        if (
            header.get("alg") != "EdDSA"
            or header.get("kid") != self._key_id
            or header.get("typ") != "JWT"
            or "crit" in header
            or "b64" in header
        ):
            raise InvalidAccessToken("token inválido")
        try:
            self._public_key.verify(
                _decode_segment(parts[2]), f"{parts[0]}.{parts[1]}".encode("ascii")
            )
        except (InvalidSignature, ValueError) as exc:
            raise InvalidAccessToken("token inválido") from exc

        claims = _decode_object(_decode_segment(parts[1]))
        if set(claims) != {
            "iss",
            "aud",
            "sub",
            "firebase_uid",
            "jti",
            "iat",
            "nbf",
            "exp",
            "v",
        }:
            raise InvalidAccessToken("claims incompatíveis")
        issued_at = claims.get("iat")
        not_before = claims.get("nbf")
        expires_at = claims.get("exp")
        now = int(self._clock())
        if (
            claims.get("iss") != self._issuer
            or claims.get("aud") != self._audience
            or claims.get("v") != 1
            or type(claims.get("v")) is not int
            or any(type(value) is not int for value in (issued_at, not_before, expires_at))
            or issued_at > now + 30
            or not_before > now + 30
            or not_before < issued_at
            or expires_at <= now
            or expires_at <= issued_at
            or expires_at - issued_at > self._ttl_seconds
        ):
            raise InvalidAccessToken("token inválido ou expirado")
        try:
            user_id = UUID(claims["sub"])
            token_id = UUID(claims["jti"])
            firebase_uid = claims["firebase_uid"]
            if (
                not isinstance(firebase_uid, str)
                or not firebase_uid.strip()
                or len(firebase_uid) > 128
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise InvalidAccessToken("token inválido") from exc
        return AccessTokenClaims(
            user_id=user_id,
            firebase_uid=firebase_uid,
            token_id=token_id,
            issued_at=issued_at,
        )
