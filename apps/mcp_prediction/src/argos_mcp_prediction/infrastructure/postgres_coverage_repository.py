"""Leitura somente leitura de uma view operacional de cobertura validada."""

import re
from datetime import UTC, date, datetime, timedelta

from argos_mcp_prediction.domain import CoverageRecord, CoverageState
from argos_mcp_prediction.ports import CoverageReadRepository, SqlReader
from packages.security.access import AuthorizedScope
from packages.security.errors import AccessDeniedError

_RELATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")


class PostgresCoverageRepository(CoverageReadRepository):
    """Consome uma view provisionada por pipeline operacional confiável.

    A view deve expor somente evidências validadas com property_id,
    coverage_start, coverage_end e evidence_source. O serviço não infere
    completude pelos eventos de aplicação.
    """

    def __init__(self, sql_reader: SqlReader, relation: str) -> None:
        if not _RELATION.fullmatch(relation):
            raise ValueError("relação de cobertura inválida")
        self._sql_reader = sql_reader
        self._relation = ".".join(f'"{part}"' for part in relation.split("."))

    async def get_coverage(
        self,
        scope: AuthorizedScope,
        *,
        start: date,
        end: date,
    ) -> tuple[CoverageRecord, ...]:
        if (
            not isinstance(scope, AuthorizedScope)
            or scope.agent_id != "analista"
            or scope.operation != "prediction.read"
            or not 1 <= len(scope.property_ids) <= 100
            or len(set(scope.property_ids)) != len(scope.property_ids)
            or scope.expires_at.tzinfo is None
            or scope.expires_at <= datetime.now(UTC)
        ):
            raise AccessDeniedError("escopo autorizado inválido")
        if type(start) is not date or type(end) is not date or start > end:
            raise ValueError("janela de cobertura inválida")

        rows = await self._sql_reader.fetch_all(
            f"""
            SELECT property_id, coverage_start, coverage_end, evidence_source
            FROM {self._relation}
            WHERE property_id = ANY($1::uuid[])
              AND coverage_start <= $3::date
              AND coverage_end >= $2::date
            ORDER BY property_id, coverage_start, coverage_end
            LIMIT 1001
            """,
            list(scope.property_ids),
            start,
            end,
        )
        if len(rows) > 1_000:
            raise ValueError("fonte de cobertura excedeu o limite de intervalos")
        by_property: dict[object, list[tuple[date, date, str]]] = {
            property_id: [] for property_id in scope.property_ids
        }
        for row in rows:
            property_id = row.get("property_id")
            if property_id not in by_property:
                raise ValueError("fonte de cobertura retornou propriedade fora do escopo")
            row_start = row.get("coverage_start")
            row_end = row.get("coverage_end")
            source = row.get("evidence_source")
            if (
                type(row_start) is not date
                or type(row_end) is not date
                or row_start > row_end
                or not isinstance(source, str)
                or not source.strip()
            ):
                raise ValueError("fonte de cobertura retornou evidência inválida")
            by_property[property_id].append((row_start, row_end, source.strip()))

        result = []
        for property_id, intervals in by_property.items():
            if not intervals:
                result.append(
                    CoverageRecord(
                        property_id,
                        CoverageState.UNKNOWN,
                        reason="não há evidência de cobertura para o período solicitado",
                    )
                )
                continue
            cursor = start
            sources: list[str] = []
            for row_start, row_end, source in intervals:
                if row_end < cursor:
                    continue
                if row_start > cursor:
                    break
                cursor = max(cursor, row_end + timedelta(days=1))
                sources.append(source)
                if cursor > end:
                    break
            if cursor > end:
                source = "; ".join(dict.fromkeys(sources))
                if len(source) > 200:
                    result.append(
                        CoverageRecord(
                            property_id,
                            CoverageState.INCOMPLETE,
                            reason="fontes de cobertura excedem o limite de resposta",
                        )
                    )
                    continue
                result.append(
                    CoverageRecord(
                        property_id,
                        CoverageState.CONFIRMED,
                        start=start,
                        end=end,
                        source=source,
                    )
                )
            else:
                result.append(
                    CoverageRecord(
                        property_id,
                        CoverageState.INCOMPLETE,
                        start=min(item[0] for item in intervals),
                        end=max(item[1] for item in intervals),
                        reason="evidências possuem lacuna na janela histórica solicitada",
                    )
                )
        return tuple(result)
