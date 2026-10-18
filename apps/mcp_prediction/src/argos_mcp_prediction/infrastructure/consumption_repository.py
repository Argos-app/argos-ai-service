"""Agregação PostgreSQL parametrizada, restrita ao escopo e sem paginação de eventos."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from argos_mcp_prediction.domain import ConsumptionGroup, ConsumptionUnit, MonthlyConsumptionRecord
from argos_mcp_prediction.domain.errors import (
    ConsumptionResultLimitError,
    ConsumptionSourceUnavailableError,
    InvalidConsumptionDataError,
)
from argos_mcp_prediction.ports import ConsumptionReadRepository, SqlReader
from packages.security.access import AuthorizedScope
from packages.security.errors import AccessDeniedError

_MAX_GROUPS = 1000
_MAX_MONTHLY_ROWS = _MAX_GROUPS * 12

# Joins: modulos/02_tabelas.sql e infrastructure/cattle_repository.py do MCP de dados.
# Consumo: dose * head_count_applied, conforme 06_triggers.sql e 07_procedures.sql.
_CONSUMPTION_SQL = """
    WITH scoped_applications AS (
        SELECT a.id, p.id AS property_id, m.id AS medicine_id,
               m.brand_name AS medicine_name, m.active_ingredient,
               a.application_date, a.unit, a.dose, a.head_count_applied
        FROM application AS a
        JOIN lot AS l ON l.id = a.lot_id
        JOIN herd AS h ON h.id = l.herd_id
        JOIN property AS p ON p.id = h.property_id
        JOIN medicine_batch AS mb ON mb.id = a.medicine_batch_id
        JOIN medicine AS m ON m.id = mb.medicine_id
        WHERE p.id = ANY($1::uuid[])
          AND p.active = TRUE
          AND a.active = TRUE
          AND a.application_date BETWEEN $2::date AND $3::date
          AND (
              $4::text[] IS NULL
              OR EXISTS (
                  SELECT 1
                  FROM unnest($4::text[]) AS requested(name)
                  WHERE position(lower(requested.name) in lower(m.brand_name)) > 0
                     OR position(lower(requested.name) in lower(m.active_ingredient)) > 0
              )
          )
    ), checked_applications AS (
        SELECT a.*,
               (
                   EXISTS (
                       SELECT 1 FROM application_animal AS aa
                       WHERE aa.application_id = a.id AND aa.dose_given <> a.dose
                   )
                   OR (
                       EXISTS (
                           SELECT 1 FROM application_animal AS aa
                           WHERE aa.application_id = a.id
                       )
                       AND (
                           SELECT COUNT(*) FROM application_animal AS aa
                           WHERE aa.application_id = a.id
                       ) <> a.head_count_applied
                   )
               ) AS inconsistent
        FROM scoped_applications AS a
    ), consumption_groups AS (
        SELECT property_id, medicine_id, medicine_name, active_ingredient, unit
        FROM scoped_applications
        GROUP BY property_id, medicine_id, medicine_name, active_ingredient, unit
    ), summary AS (
        SELECT (SELECT COUNT(*) FROM consumption_groups) AS group_count,
               EXISTS (
                   SELECT 1 FROM checked_applications WHERE inconsistent
               ) AS any_inconsistent
    )
    SELECT a.property_id, a.medicine_id, a.medicine_name, a.active_ingredient, a.unit,
           EXTRACT(MONTH FROM a.application_date)::integer AS month,
           SUM(a.dose * a.head_count_applied) AS quantity,
           COUNT(*)::bigint AS application_events,
           SUM(a.head_count_applied)::bigint AS head_administrations,
           s.group_count, s.any_inconsistent
    FROM checked_applications AS a
    CROSS JOIN summary AS s
    GROUP BY a.property_id, a.medicine_id, a.medicine_name, a.active_ingredient, a.unit,
             EXTRACT(MONTH FROM a.application_date), s.group_count, s.any_inconsistent
    ORDER BY a.property_id, a.medicine_id, a.unit, month
    LIMIT $5
"""


class PostgresConsumptionRepository(ConsumptionReadRepository):
    def __init__(self, sql_reader: SqlReader) -> None:
        self._sql_reader = sql_reader

    @staticmethod
    def _validate_scope(scope: AuthorizedScope) -> None:
        if not isinstance(scope, AuthorizedScope):
            raise AccessDeniedError("escopo autorizado obrigatório")
        if (
            scope.agent_id != "analista"
            or scope.operation != "prediction.read"
            or not 1 <= len(scope.property_ids) <= 100
            or any(not isinstance(value, UUID) for value in scope.property_ids)
            or len(set(scope.property_ids)) != len(scope.property_ids)
        ):
            raise AccessDeniedError("escopo autorizado inválido")
        if scope.expires_at.tzinfo is None or scope.expires_at <= datetime.now(UTC):
            raise AccessDeniedError("contexto de acesso expirado")

    async def aggregate_monthly(
        self,
        scope: AuthorizedScope,
        *,
        start: date,
        end: date,
        medicine_names: tuple[str, ...] | None = None,
    ) -> tuple[MonthlyConsumptionRecord, ...]:
        self._validate_scope(scope)
        if (
            type(start) is not date
            or type(end) is not date
            or start > end
            or end >= date.today()
            or (end - start).days >= 366
        ):
            raise ValueError("janela histórica inválida: até 366 dias encerrados")
        if medicine_names is not None and (
            not isinstance(medicine_names, tuple)
            or not 1 <= len(medicine_names) <= 100
            or any(
                not isinstance(value, str) or not 1 <= len(value.strip()) <= 80
                for value in medicine_names
            )
            or len({value.strip().casefold() for value in medicine_names}) != len(medicine_names)
        ):
            raise ValueError("filtro por nome de medicamento inválido")
        try:
            rows = await self._sql_reader.fetch_all(
                _CONSUMPTION_SQL,
                list(scope.property_ids),
                start,
                end,
                [name.strip() for name in medicine_names] if medicine_names is not None else None,
                _MAX_MONTHLY_ROWS + 1,
            )
        except Exception as exc:
            raise ConsumptionSourceUnavailableError("fonte de consumo indisponível") from exc
        self._validate_scope(scope)
        if len(rows) > _MAX_MONTHLY_ROWS:
            raise ConsumptionResultLimitError("selecione menos propriedades ou medicamentos")
        results = []
        keys = set()
        group_counts = set()
        try:
            for row in rows:
                group_count = row["group_count"]
                if type(group_count) is not int or group_count < 1:
                    raise ValueError("contagem de grupos inválida")
                if group_count > _MAX_GROUPS:
                    raise ConsumptionResultLimitError(
                        "selecione menos propriedades ou medicamentos"
                    )
                if row["any_inconsistent"]:
                    raise InvalidConsumptionDataError(
                        "detalhes individuais divergem da aplicação-base"
                    )
                group_counts.add(group_count)
                group = ConsumptionGroup(
                    UUID(str(row["property_id"])),
                    UUID(str(row["medicine_id"])),
                    ConsumptionUnit(row["unit"]),
                    row["medicine_name"],
                    row["active_ingredient"],
                )
                if group.property_id not in scope.property_ids:
                    raise ValueError("registro externo à seleção")
                if (
                    not isinstance(group.medicine_name, str)
                    or not group.medicine_name.strip()
                    or not isinstance(group.active_ingredient, str)
                    or not group.active_ingredient.strip()
                ):
                    raise ValueError("identificação de medicamento inválida")
                if medicine_names is not None and not any(
                    name.strip().casefold() in group.medicine_name.casefold()
                    or name.strip().casefold() in group.active_ingredient.casefold()
                    for name in medicine_names
                ):
                    raise ValueError("repositório ignorou o filtro por nome de medicamento")
                month, quantity = row["month"], row["quantity"]
                events, heads = row["application_events"], row["head_administrations"]
                if (
                    type(month) is not int
                    or not 1 <= month <= 12
                    or not isinstance(quantity, Decimal)
                    or not quantity.is_finite()
                    or quantity <= 0
                    or type(events) is not int
                    or events < 1
                    or type(heads) is not int
                    or heads < events
                ):
                    raise ValueError("agregado de consumo inválido")
                key = (group, month)
                if key in keys:
                    raise ValueError("agregado mensal duplicado")
                keys.add(key)
                results.append(MonthlyConsumptionRecord(group, month, quantity, events, heads))
            if rows and (
                len(group_counts) != 1
                or len({item.group for item in results}) != next(iter(group_counts))
            ):
                raise ValueError("agregação incompleta")
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidConsumptionDataError("agregados de consumo inconsistentes") from exc
        return tuple(results)
