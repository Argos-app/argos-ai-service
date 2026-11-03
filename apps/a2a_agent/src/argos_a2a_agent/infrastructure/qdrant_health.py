"""Verificações de prontidão para o índice Qdrant efetivamente consultado."""

from typing import Any


async def check_qdrant_collection(
    client: Any,
    *,
    collection: str,
    vector_size: int,
    distance: str,
    voyage_vector_size: int | None = None,
) -> None:
    """Valida o Qdrant e a coleção ativa, quando o corpus já foi inicializado.

        Em uma instalação limpa, o alias é criado pela primeira consulta documental
        autorizada, que sincroniza a fonte antes de ler. A ausência do alias, por si
        só, não torna o serviço incapaz de atender; falhas de conexão e coleções
        existentes incompatíveis continuam impedindo a prontidão.

    A coleção single-vector existente pode continuar servindo como origem do
    rollback enquanto o fingerprint novo prepara e publica o índice dual."""

    if not await client.collection_exists(collection_name=collection):
        return

    info = await client.get_collection(collection_name=collection)
    vectors = info.config.params.vectors
    if voyage_vector_size is not None and isinstance(vectors, dict):
        expected = {"openai": vector_size, "voyage": voyage_vector_size}
        matches = set(vectors) == set(expected) and all(
            getattr(vectors[name], "size", None) == size
            and str(
                getattr(
                    getattr(vectors[name], "distance", None),
                    "value",
                    getattr(vectors[name], "distance", None),
                )
            ).casefold()
            == distance.casefold()
            for name, size in expected.items()
        )
        if matches:
            return

    actual_size = getattr(vectors, "size", None)
    actual_distance = getattr(vectors, "distance", None)
    actual_distance = getattr(actual_distance, "value", actual_distance)
    if actual_size != vector_size or str(actual_distance).casefold() != distance.casefold():
        raise RuntimeError("coleção Qdrant ativa diverge da dimensão ou distância configurada")
