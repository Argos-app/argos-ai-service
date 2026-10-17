"""Verificações de prontidão para o índice Qdrant efetivamente consultado."""

from typing import Any


async def check_qdrant_collection(
    client: Any,
    *,
    collection: str,
    vector_size: int,
    distance: str,
) -> None:
    """Exige que a coleção/alias ativo tenha o vetor configurado pelo runtime."""

    info = await client.get_collection(collection_name=collection)
    vectors = info.config.params.vectors
    actual_size = getattr(vectors, "size", None)
    actual_distance = getattr(vectors, "distance", None)
    actual_distance = getattr(actual_distance, "value", actual_distance)
    if actual_size != vector_size or str(actual_distance).casefold() != distance.casefold():
        raise RuntimeError("coleção Qdrant ativa diverge da dimensão ou distância configurada")
