"""Consultas SQL parametrizadas de detalhe, sempre limitadas pelo AuthorizedScope."""

from typing import Any, TypeVar

from pydantic import BaseModel

from argos_mcp_data.domain.access import AuthorizedScope
from argos_mcp_data.domain.cattle import (
    AnimalLotHistoryRecord,
    ApplicationAnimalRecord,
    ApplicationRecord,
    ExceptionAnimalRecord,
    HerdRecord,
    ListAnimalLotHistoryRequest,
    ListApplicationAnimalsRequest,
    ListApplicationsRequest,
    ListExceptionAnimalsRequest,
    ListHerdsRequest,
    ListLotsRequest,
    ListOccurrencesRequest,
    ListPropertiesRequest,
    LotRecord,
    OccurrenceRecord,
    Page,
    PropertyRecord,
    ScopedPageRequest,
)
from argos_mcp_data.ports.cattle_repository import CattleReadRepository
from argos_mcp_data.ports.sql_reader import SqlReader

RecordT = TypeVar("RecordT", bound=BaseModel)


class _BoundFilters:
    """Monta somente predicados estáticos; valores sempre viram parâmetros asyncpg."""

    def __init__(self, scope: AuthorizedScope, *, property_alias: str = "p") -> None:
        self.values: list[Any] = [list(scope.property_ids)]
        self.clauses = [f"{property_alias}.id = ANY($1::uuid[])"]

    def add(self, condition: str, value: Any) -> None:
        self.values.append(value)
        self.add_clause(condition, len(self.values))

    def add_many(self, condition: str, *values: Any) -> None:
        first_index = len(self.values) + 1
        self.values.extend(values)
        formatted = condition
        for index in range(len(values)):
            formatted = formatted.replace("?", f"${first_index + index}", 1)
        self.clauses.append(formatted)

    def add_clause(self, condition: str, index: int) -> None:
        self.clauses.append(condition.replace("?", f"${index}"))

    def where(self) -> str:
        return " AND ".join(self.clauses)


class PostgresCattleRepository(CattleReadRepository):
    def __init__(self, sql_reader: SqlReader) -> None:
        self._sql_reader = sql_reader

    async def _page(
        self,
        scope: AuthorizedScope,
        request: ScopedPageRequest,
        *,
        model: type[RecordT],
        sql: str,
        values: list[Any],
    ) -> Page[RecordT]:
        values.extend((request.page_size + 1, request.offset))
        rows = await self._sql_reader.fetch_all(sql, *values)
        has_more = len(rows) > request.page_size
        items = tuple(model.model_validate(row) for row in rows[: request.page_size])
        next_offset = None
        if has_more and request.offset + request.page_size <= 10_000:
            next_offset = request.offset + request.page_size
        return Page[model](
            items=items,
            page_size=request.page_size,
            next_offset=next_offset,
            has_more=has_more,
        )

    async def list_properties(
        self, scope: AuthorizedScope, request: ListPropertiesRequest
    ) -> Page[PropertyRecord]:
        filters = _BoundFilters(scope)
        if request.name:
            filters.add("position(lower(?) in lower(p.name)) > 0", request.name)
        if request.active is not None:
            filters.add("p.active = ?", request.active)
        return await self._page(
            scope,
            request,
            model=PropertyRecord,
            values=filters.values,
            sql=f"""
                SELECT p.id AS property_id, p.name AS property_name,
                       p.city, p.state, p.active
                FROM property AS p
                WHERE {filters.where()}
                ORDER BY p.name, p.id
                LIMIT ${len(filters.values) + 1} OFFSET ${len(filters.values) + 2}
            """,
        )

    async def list_herds(
        self, scope: AuthorizedScope, request: ListHerdsRequest
    ) -> Page[HerdRecord]:
        filters = _BoundFilters(scope)
        if request.name:
            filters.add("position(lower(?) in lower(h.herd_name)) > 0", request.name)
        if request.breed:
            filters.add("position(lower(?) in lower(h.breed)) > 0", request.breed)
        if request.active is not None:
            filters.add("h.active = ?", request.active)
        return await self._page(
            scope,
            request,
            model=HerdRecord,
            values=filters.values,
            sql=f"""
                SELECT h.id AS herd_id, p.id AS property_id, p.name AS property_name,
                       h.herd_name, h.breed, h.head_count, h.active
                FROM herd AS h
                JOIN property AS p ON p.id = h.property_id
                WHERE {filters.where()}
                ORDER BY p.id, h.herd_name, h.id
                LIMIT ${len(filters.values) + 1} OFFSET ${len(filters.values) + 2}
            """,
        )

    async def list_lots(self, scope: AuthorizedScope, request: ListLotsRequest) -> Page[LotRecord]:
        filters = _BoundFilters(scope)
        optional = (
            ("l.herd_id = ?", request.herd_id),
            ("position(lower(?) in lower(l.lot_code)) > 0", request.lot_code),
            ("l.category = ?", request.category),
            ("l.is_exception_lot = ?", request.is_exception_lot),
            ("l.active = ?", request.active),
            ("l.head_count >= ?", request.head_count_min),
            ("l.head_count <= ?", request.head_count_max),
            ("l.open_date >= ?", request.date_from),
            ("l.open_date <= ?", request.date_to),
        )
        for condition, value in optional:
            if value is not None:
                filters.add(condition, value)
        return await self._page(
            scope,
            request,
            model=LotRecord,
            values=filters.values,
            sql=f"""
                SELECT l.id AS lot_id, p.id AS property_id, p.name AS property_name,
                       h.id AS herd_id, h.herd_name, l.lot_code, l.category,
                       l.is_exception_lot, l.exception_reason, l.head_count,
                       l.slaughter_release_date, l.fit_for_slaughter,
                       l.open_date, l.close_date, l.active
                FROM lot AS l
                JOIN herd AS h ON h.id = l.herd_id
                JOIN property AS p ON p.id = h.property_id
                WHERE {filters.where()}
                ORDER BY p.id, h.id, l.open_date DESC, l.id
                LIMIT ${len(filters.values) + 1} OFFSET ${len(filters.values) + 2}
            """,
        )

    async def list_exception_animals(
        self, scope: AuthorizedScope, request: ListExceptionAnimalsRequest
    ) -> Page[ExceptionAnimalRecord]:
        filters = _BoundFilters(scope)
        filters.clauses.append("origin_p.id = ANY($1::uuid[])")
        optional = (
            ("ae.lot_id = ?", request.lot_id),
            ("position(lower(?) in lower(ae.ear_tag)) > 0", request.ear_tag),
            ("ae.category = ?", request.category),
            ("ae.status = ANY(?::varchar[])", list(request.statuses) if request.statuses else None),
            ("ae.exception_date >= ?", request.date_from),
            ("ae.exception_date <= ?", request.date_to),
        )
        for condition, value in optional:
            if value is not None:
                filters.add(condition, value)
        return await self._page(
            scope,
            request,
            model=ExceptionAnimalRecord,
            values=filters.values,
            sql=f"""
                SELECT ae.id AS animal_id, p.id AS property_id, p.name AS property_name,
                       l.id AS current_lot_id, l.lot_code AS current_lot_code,
                       origin_l.id AS origin_lot_id, origin_l.lot_code AS origin_lot_code,
                       ae.ear_tag, ae.category, ae.birth_date, ae.current_weight,
                       ae.exception_date, ae.exception_end_date, ae.exception_reason,
                       ae.slaughter_release_date, ae.fit_for_slaughter, ae.status
                FROM animal_exception AS ae
                JOIN lot AS l ON l.id = ae.lot_id
                JOIN herd AS h ON h.id = l.herd_id
                JOIN property AS p ON p.id = h.property_id
                JOIN lot AS origin_l ON origin_l.id = ae.origin_lot_id
                JOIN herd AS origin_h ON origin_h.id = origin_l.herd_id
                JOIN property AS origin_p ON origin_p.id = origin_h.property_id
                WHERE {filters.where()}
                ORDER BY p.id, l.id, ae.ear_tag, ae.id
                LIMIT ${len(filters.values) + 1} OFFSET ${len(filters.values) + 2}
            """,
        )

    async def list_applications(
        self, scope: AuthorizedScope, request: ListApplicationsRequest
    ) -> Page[ApplicationRecord]:
        filters = _BoundFilters(scope)
        optional = (
            ("a.lot_id = ?", request.lot_id),
            ("m.id = ?", request.medicine_id),
            (
                "(position(lower(?) in lower(m.brand_name)) > 0 "
                "OR position(lower(?) in lower(m.active_ingredient)) > 0)",
                request.medicine_name,
            ),
            ("a.active = ?", request.active),
            ("a.application_date >= ?", request.date_from),
            ("a.application_date <= ?", request.date_to),
        )
        for condition, value in optional:
            if value is None:
                continue
            if request.medicine_name is not None and "brand_name" in condition:
                filters.add_many(condition, value, value)
            else:
                filters.add(condition, value)
        if request.application_kind == "INDIVIDUAL":
            filters.clauses.append("si.application_id IS NOT NULL")
        elif request.application_kind == "COLETIVA":
            filters.clauses.append("si.application_id IS NULL")
        return await self._page(
            scope,
            request,
            model=ApplicationRecord,
            values=filters.values,
            sql=f"""
                WITH visible_individual AS (
                    SELECT DISTINCT aa.application_id
                    FROM application_animal AS aa
                    JOIN application AS source_a ON source_a.id = aa.application_id
                    JOIN lot AS source_l ON source_l.id = source_a.lot_id
                    JOIN herd AS source_h ON source_h.id = source_l.herd_id
                    WHERE source_h.property_id = ANY($1::uuid[])
                )
                SELECT a.id AS application_id, p.id AS property_id,
                       p.name AS property_name, l.id AS lot_id, l.lot_code,
                       CASE WHEN si.application_id IS NULL THEN 'COLETIVA'
                            ELSE 'INDIVIDUAL' END AS application_kind,
                       m.id AS medicine_id, m.brand_name AS medicine_name,
                       m.active_ingredient, mb.id AS medicine_batch_id,
                       mb.batch_number AS medicine_batch_number,
                       a.application_date, a.application_time, a.dose, a.unit,
                       a.route, a.head_count_applied, a.meat_withdrawal_end,
                       a.goal, a.active
                FROM application AS a
                JOIN lot AS l ON l.id = a.lot_id
                JOIN herd AS h ON h.id = l.herd_id
                JOIN property AS p ON p.id = h.property_id
                JOIN medicine_batch AS mb ON mb.id = a.medicine_batch_id
                JOIN medicine AS m ON m.id = mb.medicine_id
                LEFT JOIN visible_individual AS si ON si.application_id = a.id
                WHERE {filters.where()}
                ORDER BY p.id, a.application_date DESC, a.id
                LIMIT ${len(filters.values) + 1} OFFSET ${len(filters.values) + 2}
            """,
        )

    async def list_application_animals(
        self, scope: AuthorizedScope, request: ListApplicationAnimalsRequest
    ) -> Page[ApplicationAnimalRecord]:
        filters = _BoundFilters(scope)
        filters.clauses.extend(
            [
                "animal_p.id = ANY($1::uuid[])",
                "origin_p.id = ANY($1::uuid[])",
            ]
        )
        optional = (
            ("a.id = ?", request.application_id),
            ("a.lot_id = ?", request.lot_id),
            ("m.id = ?", request.medicine_id),
            ("position(lower(?) in lower(ae.ear_tag)) > 0", request.ear_tag),
            ("a.application_date >= ?", request.date_from),
            ("a.application_date <= ?", request.date_to),
        )
        for condition, value in optional:
            if value is not None:
                filters.add(condition, value)
        return await self._page(
            scope,
            request,
            model=ApplicationAnimalRecord,
            values=filters.values,
            sql=f"""
                SELECT a.id AS application_id, p.id AS property_id,
                       a.application_date, l.id AS lot_id, l.lot_code,
                       m.id AS medicine_id, m.brand_name AS medicine_name,
                       ae.id AS animal_id, ae.ear_tag, ae.status AS animal_status,
                       aa.dose_given, aa.weight_at_time, aa.meat_withdrawal_end,
                       aa.released_for_slaughter
                FROM application_animal AS aa
                JOIN application AS a ON a.id = aa.application_id
                JOIN lot AS l ON l.id = a.lot_id
                JOIN herd AS h ON h.id = l.herd_id
                JOIN property AS p ON p.id = h.property_id
                JOIN medicine_batch AS mb ON mb.id = a.medicine_batch_id
                JOIN medicine AS m ON m.id = mb.medicine_id
                JOIN animal_exception AS ae ON ae.id = aa.animal_exception_id
                JOIN lot AS animal_l ON animal_l.id = ae.lot_id
                JOIN herd AS animal_h ON animal_h.id = animal_l.herd_id
                JOIN property AS animal_p ON animal_p.id = animal_h.property_id
                JOIN lot AS origin_l ON origin_l.id = ae.origin_lot_id
                JOIN herd AS origin_h ON origin_h.id = origin_l.herd_id
                JOIN property AS origin_p ON origin_p.id = origin_h.property_id
                WHERE {filters.where()}
                ORDER BY p.id, a.application_date DESC, a.id, ae.ear_tag, ae.id
                LIMIT ${len(filters.values) + 1} OFFSET ${len(filters.values) + 2}
            """,
        )

    async def list_occurrences(
        self, scope: AuthorizedScope, request: ListOccurrencesRequest
    ) -> Page[OccurrenceRecord]:
        filters = _BoundFilters(scope)
        filters.clauses.extend(["origin_p.id = ANY($1::uuid[])", "p.id = ANY($1::uuid[])"])
        optional = (
            ("ae.id = ?", request.animal_id),
            ("position(lower(?) in lower(ae.ear_tag)) > 0", request.ear_tag),
            ("o.type = ?", request.occurrence_type),
            ("o.resolved = ?", request.resolved),
            ("o.occurrence_date >= ?", request.date_from),
            ("o.occurrence_date <= ?", request.date_to),
        )
        for condition, value in optional:
            if value is not None:
                filters.add(condition, value)
        return await self._page(
            scope,
            request,
            model=OccurrenceRecord,
            values=filters.values,
            sql=f"""
                SELECT o.id AS occurrence_id, ae.id AS animal_id, ae.ear_tag,
                       ae.status AS animal_status, p.id AS current_property_id,
                       p.name AS current_property_name, l.id AS current_lot_id,
                       l.lot_code AS current_lot_code, o.type AS occurrence_type,
                       o.occurrence_date, o.description, o.diagnosis, o.resolved,
                       o.resolved_at
                FROM occurrence AS o
                JOIN animal_exception AS ae ON ae.id = o.animal_exception_id
                JOIN lot AS l ON l.id = ae.lot_id
                JOIN herd AS h ON h.id = l.herd_id
                JOIN property AS p ON p.id = h.property_id
                JOIN lot AS origin_l ON origin_l.id = ae.origin_lot_id
                JOIN herd AS origin_h ON origin_h.id = origin_l.herd_id
                JOIN property AS origin_p ON origin_p.id = origin_h.property_id
                WHERE {filters.where()}
                ORDER BY p.id, o.occurrence_date DESC, o.id
                LIMIT ${len(filters.values) + 1} OFFSET ${len(filters.values) + 2}
            """,
        )

    async def list_animal_lot_history(
        self, scope: AuthorizedScope, request: ListAnimalLotHistoryRequest
    ) -> Page[AnimalLotHistoryRecord]:
        filters = _BoundFilters(scope, property_alias="history_p")
        filters.clauses.extend(["current_p.id = ANY($1::uuid[])", "origin_p.id = ANY($1::uuid[])"])
        optional = (
            ("ae.id = ?", request.animal_id),
            ("position(lower(?) in lower(ae.ear_tag)) > 0", request.ear_tag),
            ("history_l.id = ?", request.lot_id),
            ("history.entry_date >= ?", request.date_from),
            ("history.entry_date <= ?", request.date_to),
        )
        for condition, value in optional:
            if value is not None:
                filters.add(condition, value)
        return await self._page(
            scope,
            request,
            model=AnimalLotHistoryRecord,
            values=filters.values,
            sql=f"""
                SELECT history.id AS history_id, ae.id AS animal_id, ae.ear_tag,
                       ae.status AS animal_status, history_p.id AS property_id,
                       history_p.name AS property_name, history_l.id AS lot_id,
                       history_l.lot_code, history.entry_date, history.exit_date,
                       history.reason
                FROM animal_lot_history AS history
                JOIN animal_exception AS ae ON ae.id = history.animal_exception_id
                JOIN lot AS history_l ON history_l.id = history.lot_id
                JOIN herd AS history_h ON history_h.id = history_l.herd_id
                JOIN property AS history_p ON history_p.id = history_h.property_id
                JOIN lot AS current_l ON current_l.id = ae.lot_id
                JOIN herd AS current_h ON current_h.id = current_l.herd_id
                JOIN property AS current_p ON current_p.id = current_h.property_id
                JOIN lot AS origin_l ON origin_l.id = ae.origin_lot_id
                JOIN herd AS origin_h ON origin_h.id = origin_l.herd_id
                JOIN property AS origin_p ON origin_p.id = origin_h.property_id
                WHERE {filters.where()}
                ORDER BY history_p.id, ae.ear_tag, history.entry_date DESC, history.id
                LIMIT ${len(filters.values) + 1} OFFSET ${len(filters.values) + 2}
            """,
        )
