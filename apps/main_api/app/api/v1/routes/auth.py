"""Rotas de autenticação do serviço principal."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.api.v1.models.auth import AccessTokenResponse
from app.application.user_access_policy import is_user_enabled_for_ai
from app.infrastructure.firebase_auth import (
    FirebaseVerificationUnavailable,
    InvalidFirebaseIdToken,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _bearer_token(authorization: str | None) -> str:
    scheme, separator, token = (authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or not separator or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token obrigatório",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


@router.post("/firebase", response_model=AccessTokenResponse)
async def authenticate_firebase(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenResponse:
    """Troca Firebase ID token válido por JWT mínimo do serviço de IA."""
    token = _bearer_token(authorization)
    authenticator = getattr(request.app.state, "firebase_identity_authenticator", None)
    codec = getattr(request.app.state, "access_token_codec", None)
    if authenticator is None or codec is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticação Firebase não configurada",
        )
    try:
        result = await authenticator.authenticate(token)
    except InvalidFirebaseIdToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firebase ID token inválido, expirado ou revogado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except FirebaseVerificationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível validar a identidade no Firebase",
        ) from exc
    except Exception as exc:
        # O autenticador também consulta o banco; falhas da dependência devem
        # falhar fechadas sem expor detalhes de conexão ou SQL.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível consultar o cadastro de acesso",
        ) from exc

    user = result.user
    if user is None or not is_user_enabled_for_ai(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário sem cadastro habilitado ou sem fazendas autorizadas",
        )

    return AccessTokenResponse(access_token=codec.issue(user.user_id, result.identity.firebase_uid))
