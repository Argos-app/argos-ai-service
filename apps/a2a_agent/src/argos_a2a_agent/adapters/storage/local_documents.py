"""Fonte local allowlisted, exclusivamente de leitura, para PDFs globais."""

import json
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from argos_a2a_agent.domain.errors import SourceReadError
from argos_a2a_agent.domain.models import (
    DocumentMetadata,
    SourceDocument,
    SourceDocumentListing,
)
from packages.contracts.a2a import DocumentType, LegalValidityStatus


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("catálogo contém chave JSON duplicada")
        result[key] = value
    return result


class LocalDocumentSource:
    """Lê somente PDFs declarados em catálogo local confiável.

    O caminho do catálogo é injetado pelo bootstrap; chamadas do agente nunca
    escolhem diretórios, nomes de arquivo, classificação ou origem.
    """

    _CATALOG_FIELDS = {"documents"}
    _DOCUMENT_FIELDS = {
        "document_id",
        "file_name",
        "title",
        "document_type",
        "origin",
        "jurisdiction",
        "document_date",
        "source_modified_at",
        "uploaded_at",
        "updated_at",
        "legal_validity",
    }

    def __init__(self, root: Path, catalog_name: str = "catalog.json") -> None:
        self._root = Path(root).resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("a raiz da fonte local deve ser um diretório")
        catalog = Path(catalog_name)
        if catalog.is_absolute() or len(catalog.parts) != 1 or catalog.name != catalog_name:
            raise ValueError("o catálogo deve ser um nome de arquivo dentro da raiz")
        self._catalog_path = self._root / catalog

    async def list_documents(self, *, cursor: str | None = None) -> SourceDocumentListing:
        if cursor is not None:
            raise SourceReadError("a fonte local não oferece paginação")
        entries = self._load_catalog()
        documents = tuple(self._read_entry(entry).metadata for entry in entries)
        return SourceDocumentListing(
            documents=tuple(sorted(documents, key=lambda item: str(item.document_id))),
            complete=True,
        )

    async def read_pdf(self, document_id: UUID) -> SourceDocument | None:
        entries = self._load_catalog()
        matches = [entry for entry in entries if entry["document_id"] == str(document_id)]
        if not matches:
            return None
        if len(matches) != 1:
            raise SourceReadError("catálogo repete a identidade do documento")
        return self._read_entry(matches[0])

    def _load_catalog(self) -> list[dict[str, Any]]:
        try:
            if self._catalog_path.is_symlink():
                raise ValueError("catálogo não pode ser link simbólico")
            raw = self._catalog_path.read_text(encoding="utf-8")
            catalog = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(catalog, dict) or set(catalog) != self._CATALOG_FIELDS:
                raise ValueError("estrutura do catálogo inválida")
            entries = catalog["documents"]
            if not isinstance(entries, list):
                raise ValueError("documents deve ser uma lista")
            if len(entries) > 10_000:
                raise ValueError("catálogo excede o limite de documentos")
            identifiers: set[str] = set()
            file_names: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != self._DOCUMENT_FIELDS:
                    raise ValueError("metadados de documento inválidos")
                document_id = entry.get("document_id")
                if not isinstance(document_id, str):
                    raise ValueError("document_id inválido")
                parsed_id = UUID(document_id)
                if str(parsed_id) != document_id or document_id in identifiers:
                    raise ValueError("document_id inválido ou duplicado")
                identifiers.add(document_id)
                if entry.get("document_type") not in {item.value for item in DocumentType}:
                    raise ValueError("tipo documental não permitido")
                file_name = entry.get("file_name")
                if file_name in file_names:
                    raise ValueError("catálogo associa mais de uma identidade ao mesmo PDF")
                file_names.add(file_name)
                self._resolve_pdf(file_name)
            return entries
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            raise SourceReadError("catálogo local indisponível ou inválido") from exc

    def _resolve_pdf(self, file_name: Any) -> Path:
        if (
            not isinstance(file_name, str)
            or not file_name.lower().endswith(".pdf")
            or Path(file_name).name != file_name
            or file_name in {".", ".."}
        ):
            raise ValueError("arquivo deve ser um nome PDF simples")
        path = self._root / file_name
        if path.is_symlink():
            raise ValueError("PDF não pode ser link simbólico")
        resolved = path.resolve(strict=True)
        if resolved.parent != self._root or not resolved.is_file():
            raise ValueError("PDF fora da raiz ou não é arquivo regular")
        return resolved

    def _read_entry(self, entry: dict[str, Any]) -> SourceDocument:
        try:
            path = self._resolve_pdf(entry["file_name"])
            pdf = path.read_bytes()
            if not pdf.startswith(b"%PDF-"):
                raise ValueError("conteúdo não possui assinatura PDF")
            metadata = self._metadata(entry, pdf)
            return SourceDocument(metadata=metadata, content=pdf)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SourceReadError("PDF allowlisted indisponível ou inválido") from exc

    @staticmethod
    def _metadata(entry: dict[str, Any], pdf: bytes) -> DocumentMetadata:
        document_date = entry["document_date"]
        return DocumentMetadata(
            document_id=UUID(entry["document_id"]),
            source_key=entry["file_name"],
            title=entry["title"],
            document_type=DocumentType(entry["document_type"]),
            origin=entry["origin"],
            revision_hash=sha256(pdf).hexdigest(),
            size_bytes=len(pdf),
            source_modified_at=LocalDocumentSource._datetime(entry["source_modified_at"]),
            jurisdiction=entry["jurisdiction"],
            document_date=LocalDocumentSource._date(document_date),
            uploaded_at=LocalDocumentSource._datetime(entry["uploaded_at"]),
            updated_at=LocalDocumentSource._datetime(entry["updated_at"]),
            legal_validity=LegalValidityStatus(entry["legal_validity"]),
        )

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
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp deve incluir timezone")
        return parsed
