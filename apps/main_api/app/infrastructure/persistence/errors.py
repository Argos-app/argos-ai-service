"""Erros de consistência na persistência Mongo."""


class PersistenceConflictError(RuntimeError):
    """O identificador já existe com conteúdo incompatível."""
