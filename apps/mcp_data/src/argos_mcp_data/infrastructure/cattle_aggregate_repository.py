"""Agregações PostgreSQL parametrizadas e restritas ao escopo recebido."""

from typing import Any

from argos_mcp_data.domain.access import AuthorizedScope
from argos_mcp_data.domain.cattle_aggregates import (
    AggregatePage,
    ExceptionSummaryRequest,
    FarmSummaryRequest,
)
from argos_mcp_data.ports.cattle_aggregate_repository import CattleAggregateReadRepository
from argos_mcp_data.ports.sql_reader import SqlReader


class PostgresCattleAggregateRepository(CattleAggregateReadRepository):
    def __init__(self, sql_reader: SqlReader) -> None:
        self._sql_reader = sql_reader

    @staticmethod
    def _page(rows: Any, page_size: int, offset: int) -> AggregatePage:
        has_more = len(rows) > page_size
        return AggregatePage(
            items=tuple(dict(row) for row in rows[:page_size]),
            page_size=page_size,
            has_more=has_more,
            next_offset=offset + page_size if has_more and offset + page_size <= 10_000 else None,
        )

    async def farm_summary(
        self, scope: AuthorizedScope, request: FarmSummaryRequest
    ) -> AggregatePage:
        group_columns = {
            "property": ("p.id", "p.name"),
            "herd": ("h.id", "h.herd_name"),
            "lot": ("l.id", "l.lot_code"),
            "lot_category": ("l.category", "l.category"),
            "exception_lot": ("l.is_exception_lot", "l.is_exception_lot"),
            "active": ("l.active", "l.active"),
        }
        id_expr, name_expr = group_columns[request.group_by]
        predicates = ["p.id = ANY($1::uuid[])"]
        values: list[Any] = [list(scope.property_ids)]
        if request.herd_id is not None:
            values.append(request.herd_id)
            predicates.append(f"h.id = ${len(values)}")
        if request.lot_id is not None:
            values.append(request.lot_id)
            predicates.append(f"l.id = ${len(values)}")
        if request.lot_category is not None:
            values.append(request.lot_category)
            predicates.append(f"l.category = ${len(values)}")
        if request.active is not None:
            values.append(request.active)
            predicates.append(f"l.active = ${len(values)}")
        if request.is_exception_lot is not None:
            values.append(request.is_exception_lot)
            predicates.append(f"l.is_exception_lot = ${len(values)}")
        if request.head_count_min is not None:
            values.append(request.head_count_min)
            predicates.append(f"l.head_count >= ${len(values)}")
        if request.head_count_max is not None:
            values.append(request.head_count_max)
            predicates.append(f"l.head_count <= ${len(values)}")
        values.extend((request.page_size + 1, request.offset))
        sql = f"""
            SELECT {id_expr} AS group_value, {name_expr} AS group_label,
                   COUNT(DISTINCT h.id)::integer AS herd_count,
                   COUNT(DISTINCT l.id)::integer AS lot_count,
                   COALESCE(SUM(l.head_count), 0)::bigint AS head_count
            FROM lot AS l
            JOIN herd AS h ON h.id = l.herd_id
            JOIN property AS p ON p.id = h.property_id
            WHERE {" AND ".join(predicates)}
            GROUP BY {id_expr}, {name_expr}
            ORDER BY {id_expr}, {name_expr}
            LIMIT ${len(values) - 1} OFFSET ${len(values)}
        """
        rows = await self._sql_reader.fetch_all(sql, *values)
        return self._page(
            tuple(
                {
                    "dimensions": {
                        request.group_by: row["group_value"],
                        "label": row["group_label"],
                    },
                    "herd_count": row["herd_count"],
                    "lot_count": row["lot_count"],
                    "head_count": row["head_count"],
                }
                for row in rows
            ),
            request.page_size,
            request.offset,
        )

    async def exception_summary(
        self, scope: AuthorizedScope, request: ExceptionSummaryRequest
    ) -> AggregatePage:
        group_columns = {
            "property": ("p.id", "p.name"),
            "herd": ("h.id", "h.herd_name"),
            "lot": ("l.id", "l.lot_code"),
            "status": ("ae.status", "ae.status"),
            "category": ("ae.category", "ae.category"),
            "reason": ("ae.exception_reason", "ae.exception_reason"),
        }
        group_expr, label_expr = group_columns[request.group_by]
        predicates = [
            "p.id = ANY($1::uuid[])",
            "origin_p.id = ANY($1::uuid[])",
        ]
        values: list[Any] = [list(scope.property_ids)]
        for expression, value in (
            ("h.id =", request.herd_id),
            ("l.id =", request.lot_id),
            ("ae.category =", request.category),
            ("ae.exception_date >=", request.date_from),
            ("ae.exception_date <=", request.date_to),
        ):
            if value is not None:
                values.append(value)
                predicates.append(f"{expression} ${len(values)}")
        if request.statuses:
            values.append(list(request.statuses))
            predicates.append(f"ae.status = ANY(${len(values)}::varchar[])")
        if request.reason:
            values.append(request.reason)
            predicates.append(f"position(lower(${len(values)}) in lower(ae.exception_reason)) > 0")
        if request.ear_tag is not None:
            values.append(request.ear_tag)
            predicates.append(f"position(lower(${len(values)}) in lower(ae.ear_tag)) > 0")
        values.extend((request.page_size + 1, request.offset))
        sql = f"""
            SELECT {group_expr} AS group_value, {label_expr} AS group_label,
                   COUNT(DISTINCT ae.id)::integer AS animal_count,
                   COUNT(ae.current_weight)::integer AS known_weight_count,
                   (COUNT(*) - COUNT(ae.current_weight))::integer AS missing_weight_count,
                   SUM(ae.current_weight) AS known_weight_sum
            FROM animal_exception AS ae
            JOIN lot AS l ON l.id = ae.lot_id
            JOIN herd AS h ON h.id = l.herd_id
            JOIN property AS p ON p.id = h.property_id
            JOIN lot AS origin_l ON origin_l.id = ae.origin_lot_id
            JOIN herd AS origin_h ON origin_h.id = origin_l.herd_id
            JOIN property AS origin_p ON origin_p.id = origin_h.property_id
            WHERE {" AND ".join(predicates)}
            GROUP BY {group_expr}, {label_expr}
            ORDER BY {group_expr} NULLS LAST, {label_expr} NULLS LAST
            LIMIT ${len(values) - 1} OFFSET ${len(values)}
        """
        rows = await self._sql_reader.fetch_all(sql, *values)
        return self._page(
            tuple(
                {
                    "dimensions": {
                        request.group_by: row["group_value"],
                        "label": row["group_label"],
                    },
                    "animal_count": row["animal_count"],
                    "known_weight_count": row["known_weight_count"],
                    "missing_weight_count": row["missing_weight_count"],
                    "known_weight_sum": row["known_weight_sum"],
                }
                for row in rows
            ),
            request.page_size,
            request.offset,
        )
