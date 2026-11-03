"""Erros seguros de autenticação delegada e autorização."""


class AccessContextError(Exception):
    """Contexto de usuário ausente, inválido ou expirado."""


class AccessDeniedError(Exception):
    """Operação ou propriedade fora do escopo autorizado."""
