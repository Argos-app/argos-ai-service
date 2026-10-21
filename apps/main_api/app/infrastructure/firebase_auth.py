"""Validação de Firebase ID tokens e lookup por UID Firebase."""

import asyncio
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.user_access import UserAccess
from app.ports.user_access_repository import UserAccessRepository
from app.settings import Settings

_MAX_FIREBASE_ID_TOKEN_BYTES = 16_384


class FirebaseAuthenticationError(Exception):
    """Base para falhas de autenticação Firebase sem expor o token recebido."""


class InvalidFirebaseIdToken(FirebaseAuthenticationError):
    """Token inválido, expirado, revogado ou pertencente a usuário desativado."""


class FirebaseVerificationUnavailable(FirebaseAuthenticationError):
    """Firebase Auth não pôde concluir a validação do token."""


class FirebaseConfigurationError(RuntimeError):
    """Configuração Admin SDK ausente ou incompatível com o projeto definido."""


@dataclass(frozen=True, slots=True)
class FirebaseIdentity:
    firebase_uid: str
    email: str | None
    email_verified: bool


@dataclass(frozen=True, slots=True)
class FirebaseAuthenticationResult:
    identity: FirebaseIdentity
    user: UserAccess | None


class FirebaseTokenVerifier(Protocol):
    def verify(self, id_token: str) -> FirebaseIdentity: ...


class FirebaseAdminIdTokenVerifier:
    """Invoca o Admin SDK com checagem obrigatória de revogação."""

    def __init__(
        self,
        firebase_app: Any,
        auth_module: Any | None = None,
        firebase_error_type: type[Exception] | None = None,
    ) -> None:
        if auth_module is None:
            from firebase_admin import auth

            auth_module = auth
        if firebase_error_type is None:
            from firebase_admin.exceptions import FirebaseError

            firebase_error_type = FirebaseError
        self._app = firebase_app
        self._auth = auth_module
        self._firebase_error_type = firebase_error_type

    def verify(self, id_token: str) -> FirebaseIdentity:
        if (
            not isinstance(id_token, str)
            or not id_token.strip()
            or len(id_token.encode("utf-8")) > _MAX_FIREBASE_ID_TOKEN_BYTES
        ):
            raise InvalidFirebaseIdToken("Firebase ID token inválido")

        try:
            claims = self._auth.verify_id_token(
                id_token,
                app=self._app,
                check_revoked=True,
            )
        except (self._auth.InvalidIdTokenError, self._auth.UserDisabledError) as exc:
            raise InvalidFirebaseIdToken("Firebase ID token inválido") from exc
        except ValueError as exc:
            raise InvalidFirebaseIdToken("Firebase ID token inválido") from exc
        except self._firebase_error_type as exc:
            raise FirebaseVerificationUnavailable(
                "Firebase Auth indisponível para validar o token"
            ) from exc

        if not isinstance(claims, Mapping):
            raise InvalidFirebaseIdToken("Firebase ID token inválido")
        uid = claims.get("uid")
        if not isinstance(uid, str) or not uid.strip():
            raise InvalidFirebaseIdToken("Firebase ID token sem UID válido")

        raw_email = claims.get("email")
        email = raw_email.strip() if isinstance(raw_email, str) and raw_email.strip() else None
        return FirebaseIdentity(
            firebase_uid=uid,
            email=email,
            email_verified=claims.get("email_verified") is True,
        )


class FirebaseIdentityAuthenticator:
    """Valida Firebase ID token e busca cadastro pelo UID Firebase."""

    def __init__(
        self,
        verifier: FirebaseTokenVerifier,
        user_access_repository: UserAccessRepository,
    ) -> None:
        self._verifier = verifier
        self._users = user_access_repository

    async def authenticate(self, id_token: str) -> FirebaseAuthenticationResult:
        identity = await asyncio.to_thread(self._verifier.verify, id_token)
        # Email e email_verified são metadados de identidade; nenhum deles é
        # usado para associar ou localizar cadastros legados.
        user = await self._users.find_by_firebase_uid(identity.firebase_uid)
        return FirebaseAuthenticationResult(identity=identity, user=user)


def create_firebase_id_token_verifier(settings: Settings) -> FirebaseAdminIdTokenVerifier:
    """Inicializa o Admin SDK com credencial montada ou Application Default Credentials."""
    project_id = settings.firebase_project_id
    if not project_id:
        raise FirebaseConfigurationError("MAIN_API_FIREBASE_PROJECT_ID não configurado")

    try:
        import firebase_admin
        from firebase_admin import credentials

        app_name = "argos-main-api-" + hashlib.sha256(project_id.encode()).hexdigest()[:12]
        try:
            firebase_app = firebase_admin.get_app(app_name)
        except ValueError:
            credential = (
                credentials.Certificate(settings.firebase_credentials_file)
                if settings.firebase_credentials_file
                else credentials.ApplicationDefault()
            )
            firebase_app = firebase_admin.initialize_app(
                credential,
                options={"projectId": project_id},
                name=app_name,
            )
    except Exception as exc:
        raise FirebaseConfigurationError(
            "não foi possível inicializar o Firebase Admin SDK"
        ) from exc

    if firebase_app.project_id != project_id:
        raise FirebaseConfigurationError("credencial Firebase pertence a outro projeto")
    return FirebaseAdminIdTokenVerifier(firebase_app)
