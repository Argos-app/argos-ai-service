"""Adapter Qdrant com superfície exclusivamente de leitura."""

import math
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID


class ReadOnlyQdrantReader:
    """Coleção fixa por configuração; não expõe cliente ou operações de escrita.

    Filtros pertencem ao código dos repositórios, nunca ao payload do agente.
    Esta camada valida formato/limites; o repositório injeta a revisão publicada.
    """

    def __init__(self, client: Any, collection: str) -> None:
        if not collection or not collection.strip():
            raise ValueError("a coleção Qdrant deve ser configurada")
        self._client = client
        self._collection = collection

    @staticmethod
    def _validated_filter(query_filter: Mapping[str, Any]) -> Any:
        from qdrant_client.models import Filter

        validated = Filter.model_validate(dict(query_filter))
        if not any((validated.must, validated.should, validated.must_not, validated.min_should)):
            raise ValueError("consultas Qdrant exigem um filtro não vazio")
        return validated

    async def query_points(
        self,
        vector: Sequence[float],
        *,
        query_filter: Mapping[str, Any],
        limit: int,
        vector_name: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit deve ser um inteiro entre 1 e 20")
        if vector_name is not None and vector_name not in {"openai", "voyage"}:
            raise ValueError("vector_name deve ser openai ou voyage")
        if not vector or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            raise ValueError("a consulta vetorial deve conter números finitos")
        kwargs = {"using": vector_name} if vector_name is not None else {}
        result = await self._client.query_points(
            collection_name=self._collection,
            query=list(vector),
            query_filter=self._validated_filter(query_filter),
            limit=limit,
            with_payload=True,
            with_vectors=False,
            **kwargs,
        )
        return tuple(point.model_dump(mode="json") for point in result.points)

    async def retrieve(self, point_ids: Sequence[str | int]) -> Sequence[Mapping[str, Any]]:
        if not 1 <= len(point_ids) <= 100:
            raise ValueError("informe de 1 a 100 IDs de pontos")
        validated_ids = []
        for point_id in point_ids:
            if isinstance(point_id, int) and not isinstance(point_id, bool):
                if not 0 <= point_id < 2**64:
                    raise ValueError("ID numérico deve ser um inteiro sem sinal de 64 bits")
                validated_ids.append(point_id)
            elif isinstance(point_id, str):
                try:
                    validated_ids.append(str(UUID(point_id)))
                except ValueError as exc:
                    raise ValueError("ID textual deve ser um UUID") from exc
            else:
                raise ValueError("ID de ponto deve ser UUID ou inteiro sem sinal de 64 bits")
        from qdrant_client.models import PointRequest

        request = PointRequest(ids=validated_ids, with_payload=True, with_vector=False)
        points = await self._client.retrieve(
            collection_name=self._collection,
            ids=request.ids,
            with_payload=True,
            with_vectors=False,
        )
        return tuple(point.model_dump(mode="json") for point in points)

    async def count(self, *, query_filter: Mapping[str, Any]) -> int:
        result = await self._client.count(
            collection_name=self._collection,
            count_filter=self._validated_filter(query_filter),
            exact=True,
        )
        return result.count

    async def scroll(
        self,
        *,
        query_filter: Mapping[str, Any],
        limit: int,
        offset: str | int | None = None,
    ) -> tuple[Sequence[Mapping[str, Any]], str | int | None]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit deve ser um inteiro entre 1 e 100")
        if offset is not None and not (
            isinstance(offset, int)
            and not isinstance(offset, bool)
            and 0 <= offset < 2**64
            or isinstance(offset, str)
            and _is_uuid(offset)
        ):
            raise ValueError("offset deve ser UUID ou inteiro sem sinal de 64 bits")
        points, next_offset = await self._client.scroll(
            collection_name=self._collection,
            scroll_filter=self._validated_filter(query_filter),
            limit=limit,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        records = tuple(point.model_dump(mode="json") for point in points)
        if next_offset is not None:
            next_offset = str(next_offset) if isinstance(next_offset, UUID) else next_offset
        return records, next_offset


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True
