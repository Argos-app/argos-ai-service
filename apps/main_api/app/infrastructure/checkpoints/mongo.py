"""Adapter de checkpointer LangGraph sobre o MongoDB do serviço."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.settings import Settings


@dataclass(slots=True)
class MongoCheckpointerRuntime:
    client: Any
    saver: Any

    def close(self) -> None:
        self.client.close()


def create_mongo_checkpointer(settings: Settings) -> MongoCheckpointerRuntime:
    """Cria as collections do checkpointer sem expiração automática.

    A criação do saver prepara os índices do pacote oficial. O client é separado
    do Motor, pois MongoDBSaver usa PyMongo síncrono e expõe operações async por
    meio do executor do próprio LangGraph.
    """

    if not settings.mongo_uri:
        raise ValueError("MAIN_API_MONGO_URI é obrigatório para o checkpointer")
    try:
        from langgraph.checkpoint.mongodb import MongoDBSaver
        from pymongo import MongoClient
    except ImportError as exc:
        raise RuntimeError(
            "instale langgraph-checkpoint-mongodb para persistir o estado do grafo"
        ) from exc

    client = MongoClient(
        settings.mongo_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=settings.mongo_server_selection_timeout_ms,
        w="majority",
        wtimeoutMS=settings.mongo_write_timeout_ms,
    )
    try:
        client.admin.command("ping")
        saver = MongoDBSaver(
            client,
            db_name=settings.mongo_database,
            checkpoint_collection_name=settings.mongo_collections.langgraph_checkpoints,
            writes_collection_name=settings.mongo_collections.langgraph_checkpoint_writes,
            ttl=None,
        )
        return MongoCheckpointerRuntime(client=client, saver=saver)
    except Exception:
        client.close()
        raise
