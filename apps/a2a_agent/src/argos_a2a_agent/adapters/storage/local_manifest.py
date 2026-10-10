"""Persistência atômica do manifesto técnico sem acesso aos PDFs de origem."""

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from argos_a2a_agent.domain.errors import ManifestStoreError
from argos_a2a_agent.domain.models import CorpusManifest, DocumentMetadata
from packages.contracts.a2a import DocumentType, LegalValidityStatus


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("manifesto contém chave JSON duplicada")
        result[key] = value
    return result


class LocalManifestRepository:
    """Carrega/salva apenas o estado técnico do índice documental."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path).resolve()

    async def load(self) -> CorpusManifest | None:
        if not self._path.exists():
            return None
        try:
            if self._path.is_symlink():
                raise ValueError("manifesto não pode ser link simbólico")
            raw = self._path.read_text(encoding="utf-8")
            payload = json.loads(raw, object_pairs_hook=_unique_object)
            return self._from_payload(payload)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            KeyError,
        ) as exc:
            raise ManifestStoreError("manifesto técnico local inválido ou indisponível") from exc

    async def save(self, manifest: CorpusManifest) -> None:
        temporary_name: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                self._to_payload(manifest),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self._path)
        except (OSError, TypeError, ValueError) as exc:
            raise ManifestStoreError("não foi possível salvar o manifesto técnico") from exc
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _to_payload(manifest: CorpusManifest) -> dict[str, Any]:
        documents = []
        for item in manifest.documents:
            documents.append(
                {
                    "document_id": str(item.document_id),
                    "source_key": item.source_key,
                    "title": item.title,
                    "document_type": item.document_type.value,
                    "origin": item.origin,
                    "revision_hash": item.revision_hash,
                    "size_bytes": item.size_bytes,
                    "source_etag": item.source_etag,
                    "source_modified_at": LocalManifestRepository._format(item.source_modified_at),
                    "jurisdiction": item.jurisdiction,
                    "document_date": item.document_date.isoformat() if item.document_date else None,
                    "uploaded_at": LocalManifestRepository._format(item.uploaded_at),
                    "updated_at": LocalManifestRepository._format(item.updated_at),
                    "legal_validity": item.legal_validity.value,
                }
            )
        return {
            "schema_version": 1,
            "corpus_revision": manifest.corpus_revision,
            "generated_at": LocalManifestRepository._format(manifest.generated_at),
            "documents": documents,
        }

    @staticmethod
    def _from_payload(payload: Any) -> CorpusManifest:
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "corpus_revision",
            "generated_at",
            "documents",
        }:
            raise ValueError("estrutura do manifesto inválida")
        if payload["schema_version"] != 1 or not isinstance(payload["documents"], list):
            raise ValueError("versão ou documentos do manifesto inválidos")
        fields = {
            "document_id",
            "source_key",
            "title",
            "document_type",
            "origin",
            "revision_hash",
            "size_bytes",
            "source_etag",
            "source_modified_at",
            "jurisdiction",
            "document_date",
            "uploaded_at",
            "updated_at",
            "legal_validity",
        }
        documents = []
        for item in payload["documents"]:
            if not isinstance(item, dict) or set(item) != fields:
                raise ValueError("metadados no manifesto inválidos")
            documents.append(
                DocumentMetadata(
                    document_id=UUID(item["document_id"]),
                    source_key=item["source_key"],
                    title=item["title"],
                    document_type=DocumentType(item["document_type"]),
                    origin=item["origin"],
                    revision_hash=item["revision_hash"],
                    size_bytes=item["size_bytes"],
                    source_etag=item["source_etag"],
                    source_modified_at=LocalManifestRepository._datetime(
                        item["source_modified_at"]
                    ),
                    jurisdiction=item["jurisdiction"],
                    document_date=LocalManifestRepository._date(item["document_date"]),
                    uploaded_at=LocalManifestRepository._datetime(item["uploaded_at"]),
                    updated_at=LocalManifestRepository._datetime(item["updated_at"]),
                    legal_validity=LegalValidityStatus(item["legal_validity"]),
                )
            )
        generated_at = LocalManifestRepository._datetime(payload["generated_at"])
        if generated_at is None:
            raise ValueError("generated_at é obrigatório")
        return CorpusManifest(
            corpus_revision=payload["corpus_revision"],
            documents=tuple(documents),
            generated_at=generated_at,
        )

    @staticmethod
    def _format(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _date(value: str | None) -> date | None:
        if value is None:
            return None
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("data deve usar YYYY-MM-DD")
        return parsed

    @staticmethod
    def _datetime(value: str | None) -> datetime | None:
        if value is None:
            raise ValueError("timestamp obrigatório ausente")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp deve incluir timezone")
        return parsed
