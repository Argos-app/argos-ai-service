"""Populate the local MCP business-document collections with synthetic data."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

SEED_MARKER = "argos-local-test-v1"
LOCAL_PROPERTY_IDS = (
    UUID("00000000-0000-4000-8000-000000000101"),
    UUID("00000000-0000-4000-8000-000000000102"),
)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} é obrigatório para carregar os dados Mongo locais")
    return value


async def _load_postgres_rows(dsn: str) -> tuple[list[dict], list[dict]]:
    import asyncpg

    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        property_rows = await connection.fetch(
            """
            SELECT p.id AS property_id, p.name AS property_name,
                   h.id AS herd_id, h.herd_name,
                   l.id AS lot_id, l.lot_code
            FROM property AS p
            JOIN herd AS h ON h.property_id = p.id
            JOIN lot AS l ON l.herd_id = h.id
            WHERE p.id = ANY($1::uuid[])
            ORDER BY p.id, h.id, l.id
            """,
            list(LOCAL_PROPERTY_IDS),
        )
        medicine_rows = await connection.fetch(
            """
            SELECT m.id AS medicine_id, m.brand_name, m.active_ingredient,
                   m.mapa_registration, m.indication, m.description,
                   m.needs_prescription, m.active, m.meat_withdrawal_days,
                   m.dose, m.unit, m.route,
                   c.id AS category_id, c.name AS category_name,
                   c.description AS category_description,
                   s.id AS supplier_id, s.company_name AS supplier_name,
                   s.cnpj AS supplier_cnpj, s.email AS supplier_email,
                   s.phone AS supplier_phone, s.address AS supplier_address
            FROM medicine AS m
            JOIN therapeutic_category AS c ON c.id = m.therapeutic_category_id
            JOIN supplier AS s ON s.id = m.supplier_id
            ORDER BY m.brand_name, m.id
            """
        )
        return [dict(row) for row in property_rows], [dict(row) for row in medicine_rows]
    finally:
        await connection.close()


def _cattle_documents(rows: list[dict], now: datetime) -> list[dict]:
    by_property: dict[UUID, dict] = {}
    for row in rows:
        property_id = row["property_id"]
        document = by_property.setdefault(
            property_id,
            {
                "_id": f"argos-local-cattle-{property_id}",
                "_argos_local_seed": SEED_MARKER,
                "relational_property_id": str(property_id),
                "property_name": row["property_name"],
                "herd": {
                    "herd_id": str(row["herd_id"]),
                    "herd_name": row["herd_name"],
                },
                "lots": [],
                "report_created_at": now,
                "last_updated_at": now,
            },
        )
        if len(document["lots"]) < 3:
            document["lots"].append(
                {
                    "lot_id": str(row["lot_id"]),
                    "lot_code": row["lot_code"],
                    "is_exception_lot": False,
                    "applications": [],
                    "animal": [],
                }
            )

    documents = list(by_property.values())
    if len(documents) != len(LOCAL_PROPERTY_IDS):
        raise RuntimeError(
            "A massa PostgreSQL local não contém os dois imóveis de demonstração "
            "com rebanhos e lotes; recrie o volume PostgreSQL e tente novamente"
        )
    for document in documents:
        document["history_summary"] = {
            "total_lots": len(document["lots"]),
            "total_exception_lots": 0,
            "total_exception_animals": 0,
            "total_lot_applications": 0,
            "total_exception_applications": 0,
            "total_occurrences": 0,
            "open_occurrence_count": 0,
        }
    return documents


def _medicine_documents(rows: list[dict], now: datetime) -> list[dict]:
    documents = []
    for row in rows:
        medicine_id = row["medicine_id"]
        documents.append(
            {
                "_id": f"argos-local-medicine-{medicine_id}",
                "_argos_local_seed": SEED_MARKER,
                "relational_medicine_id": medicine_id,
                "report_created_at": now,
                "last_updated_at": now,
                "brand_name": row["brand_name"],
                "active_ingredient": row["active_ingredient"],
                "mapa_registration": row["mapa_registration"],
                "indication": row["indication"],
                "description": row["description"],
                "therapeutic_category": {
                    "therapeutic_category_id": row["category_id"],
                    "name": row["category_name"],
                    "description": row["category_description"],
                },
                "supplier": {
                    "supplier_id": row["supplier_id"],
                    "company_name": row["supplier_name"],
                    "cnpj": row["supplier_cnpj"],
                    "email": row["supplier_email"],
                    "phone": row["supplier_phone"],
                    "address": row["supplier_address"],
                },
                "needs_prescription": row["needs_prescription"],
                "active": row["active"],
                "meat_withdrawal_days": row["meat_withdrawal_days"],
                "dose": str(row["dose"]),
                "unit": row["unit"],
                "route": row["route"],
            }
        )
    if not documents:
        raise RuntimeError("A massa PostgreSQL local não contém medicamentos")
    return documents


async def _replace_seeded_documents(collection, documents: list[dict]) -> None:
    ids = [document["_id"] for document in documents]
    for document in documents:
        await collection.replace_one({"_id": document["_id"]}, document, upsert=True)
    await collection.delete_many({"_argos_local_seed": SEED_MARKER, "_id": {"$nin": ids}})


async def main() -> None:
    if os.getenv("ARGOS_LOCAL_MONGO_SEED_ENABLED", "").casefold() != "true":
        raise RuntimeError("A carga só pode ser executada com ARGOS_LOCAL_MONGO_SEED_ENABLED=true")

    mongo_uri = _required("MCP_DATA_MONGO_URI")
    database_name = _required("MCP_DATA_MONGO_DATABASE")
    cattle_collection_name = "cattle_documents"
    medicine_collection_name = "medicine_documents"
    property_rows, medicine_rows = await _load_postgres_rows(_required("MCP_DATA_SQL_DSN"))
    now = datetime.now(UTC)
    cattle_documents = _cattle_documents(property_rows, now)
    medicine_documents = _medicine_documents(medicine_rows, now)

    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(
        mongo_uri,
        serverSelectionTimeoutMS=10_000,
        uuidRepresentation="standard",
    )
    try:
        await client.admin.command("ping")
        database = client[database_name]
        await _replace_seeded_documents(database[cattle_collection_name], cattle_documents)
        await _replace_seeded_documents(database[medicine_collection_name], medicine_documents)
    finally:
        client.close()

    print(
        f"Carga Mongo local concluída: {len(cattle_documents)} históricos bovinos e "
        f"{len(medicine_documents)} documentos de medicamentos em {database_name}."
    )


if __name__ == "__main__":
    asyncio.run(main())
