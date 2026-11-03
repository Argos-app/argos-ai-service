"""Leitura paginada e parametrizada do catálogo relacional de medicamentos."""

from typing import Any

from argos_mcp_data.domain.cattle import Page
from argos_mcp_data.domain.medicine import ListMedicinesRequest, MedicineCatalogRecord
from argos_mcp_data.ports.medicine_repository import MedicineReadRepository
from argos_mcp_data.ports.sql_reader import SqlReader


class PostgresMedicineRepository(MedicineReadRepository):
    """Consulta somente as tabelas globais de catálogo, sem estoque ou uso por fazenda."""

    def __init__(self, reader: SqlReader) -> None:
        self._reader = reader

    async def list_medicines(self, request: ListMedicinesRequest) -> Page[MedicineCatalogRecord]:
        clauses: list[str] = []
        parameters: list[Any] = []

        def add(expression: str, value: Any) -> None:
            parameters.append(value)
            clauses.append(expression.replace("?", f"${len(parameters)}"))

        if request.medicine_id is not None:
            add("m.id = ?", request.medicine_id)
        if request.brand_name is not None:
            add("position(lower(?) in lower(m.brand_name)) > 0", request.brand_name)
        if request.active_ingredient is not None:
            add(
                "position(lower(?) in lower(m.active_ingredient)) > 0",
                request.active_ingredient,
            )
        if request.search is not None:
            brand_index = len(parameters) + 1
            ingredient_index = brand_index + 1
            parameters.extend((request.search, request.search))
            clauses.append(
                "(position(lower($"
                f"{brand_index}"
                ") in lower(m.brand_name)) > 0 OR position(lower($"
                f"{ingredient_index}"
                ") in lower(m.active_ingredient)) > 0)"
            )
        if request.therapeutic_category is not None:
            add("position(lower(?) in lower(tc.name)) > 0", request.therapeutic_category)
        if request.needs_prescription is not None:
            add("m.needs_prescription = ?", request.needs_prescription)
        if request.active is not None:
            add("m.active = ?", request.active)

        where = " AND ".join(clauses) if clauses else "TRUE"
        limit_index = len(parameters) + 1
        offset_index = limit_index + 1
        rows = await self._reader.fetch_all(
            f"""
            SELECT m.id AS medicine_id,
                   m.supplier_id,
                   s.company_name AS supplier_name,
                   tc.id AS therapeutic_category_id,
                   tc.name AS therapeutic_category,
                   tc.description AS therapeutic_category_description,
                   m.brand_name,
                   m.active_ingredient,
                   m.mapa_registration,
                   m.indication,
                   m.description,
                   m.needs_prescription,
                   m.meat_withdrawal_days,
                   m.dose,
                   m.unit,
                   m.route,
                   m.active
            FROM medicine AS m
            JOIN supplier AS s ON s.id = m.supplier_id
            JOIN therapeutic_category AS tc ON tc.id = m.therapeutic_category_id
            WHERE {where}
            ORDER BY lower(m.brand_name), m.id
            LIMIT ${limit_index} OFFSET ${offset_index}
            """,
            *parameters,
            request.page_size + 1,
            request.offset,
        )
        records = tuple(MedicineCatalogRecord.model_validate(dict(row)) for row in rows)
        has_more = len(records) > request.page_size
        return Page[MedicineCatalogRecord](
            items=records[: request.page_size],
            page_size=request.page_size,
            has_more=has_more,
            next_offset=request.offset + request.page_size if has_more else None,
        )
