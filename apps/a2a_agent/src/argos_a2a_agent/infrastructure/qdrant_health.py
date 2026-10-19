"""Verificações de prontidão para o índice Qdrant efetivamente consultado."""

from typing import Any


async def check_qdrant_collection(
    client: Any,
    *,
    collection: str,
    vector_size: int,
    distance: str,
) -> None:
    """Valida o Qdrant e a coleção ativa, quando o corpus já foi inicializado.

    Em uma instalação limpa, o alias é criado pela primeira consulta documental
    autorizada, que sincroniza a fonte antes de ler. A ausência do alias, por si
    só, não torna o serviço incapaz de atender; falhas de conexão e coleções
    existentes incompatíveis continuam impedindo a prontidão.
    """

    if not await client.collection_exists(collection_name=collection):
        return

    info = await client.get_collection(collection_name=collection)
    vectors = info.config.params.vectors
    actual_size = getattr(vectors, "size", None)
    actual_distance = getattr(vectors, "distance", None)
    actual_distance = getattr(actual_distance, "value", actual_distance)
    if actual_size != vector_size or str(actual_distance).casefold() != distance.casefold():
        raise RuntimeError("coleção Qdrant ativa diverge da dimensão ou distância configurada")
