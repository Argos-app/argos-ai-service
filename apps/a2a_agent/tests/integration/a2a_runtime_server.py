"""Processo A2A isolado usado somente pela homologação de integração."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from uuid import UUID

from argos_a2a_agent.bootstrap import RuntimeDependencies, run_server
from argos_a2a_agent.domain.agent_answers import GeneratedDocumentAnswer

from packages.contracts.a2a import DocumentAnswerStatus


class DeterministicEmbeddings:
    async def embed(self, texts):
        return tuple((float(len(text)), 1.0, 0.5) for text in texts)


class DeterministicAnswerGenerator:
    async def generate(
        self,
        *,
        system_prompt,
        question,
        jurisdiction,
        reference_date: date | None,
        evidence,
        specialist_answers=(),
    ):
        citations = tuple(item.evidence_id for item in evidence[:8])
        if not citations:
            return GeneratedDocumentAnswer(
                answer="",
                status=DocumentAnswerStatus.ABSTAIN,
                limitations=("Sem evidências no corpus sintético.",),
            )
        answer = "Consulta homologada com evidências " + " ".join(
            f"[[{citation}]]" for citation in citations
        )
        return GeneratedDocumentAnswer(
            answer=answer,
            status=DocumentAnswerStatus.SUCCESS,
            cited_evidence_ids=citations,
        )


class FilePermissionDirectory:
    """Lista dinâmica de usuários autorizados controlada pelo teste pai."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def has_active_documents_read_permission(self, user_id: UUID) -> bool:
        try:
            authorized = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return False
        return str(user_id) in authorized


async def main() -> None:
    fingerprint = hashlib.sha256(b"a2a-integration-embeddings-v1").hexdigest()
    dependencies = RuntimeDependencies(
        embeddings=DeterministicEmbeddings(),
        embedding_fingerprint=fingerprint,
        embedding_vector_size=3,
        answer_generator=DeterministicAnswerGenerator(),
        permission_directory=FilePermissionDirectory(Path(os.environ["A2A_TEST_PERMISSION_FILE"])),
    )
    await run_server(dependencies=dependencies)


if __name__ == "__main__":
    asyncio.run(main())
