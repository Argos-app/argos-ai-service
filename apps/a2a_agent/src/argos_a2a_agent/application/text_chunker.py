"""Divisão reprodutível de texto por página para indexação documental."""

import re
from uuid import uuid5

from argos_a2a_agent.domain.errors import PdfExtractionError
from argos_a2a_agent.domain.models import DocumentChunk, ParsedDocument


class TextChunker:
    """Cria chunks limitados, com sobreposição e identidade estável por revisão."""

    def __init__(
        self,
        *,
        max_chars: int = 1_200,
        overlap_chars: int = 150,
        max_chunks: int = 2_000,
    ) -> None:
        if not 100 <= max_chars <= 8_000:
            raise ValueError("max_chars deve estar entre 100 e 8000")
        if not 0 <= overlap_chars < max_chars:
            raise ValueError("overlap_chars deve ser menor que max_chars")
        if not 1 <= max_chunks <= 10_000:
            raise ValueError("max_chunks deve estar entre 1 e 10000")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars
        self._max_chunks = max_chunks

    def split(self, document: ParsedDocument) -> tuple[DocumentChunk, ...]:
        chunks: list[DocumentChunk] = []
        for page in document.pages:
            text = re.sub(r"[ \t]+", " ", page.text).strip()
            if not text:
                continue
            for piece in self._split_text(text):
                index = len(chunks)
                if index >= self._max_chunks:
                    raise PdfExtractionError("PDF excede o limite de chunks por documento")
                identity = f"{document.metadata.revision_hash}:{page.page_number}:{index}"
                chunks.append(
                    DocumentChunk(
                        chunk_id=uuid5(document.metadata.document_id, identity),
                        document_id=document.metadata.document_id,
                        revision_hash=document.metadata.revision_hash,
                        chunk_index=index,
                        page_from=page.page_number,
                        page_to=page.page_number,
                        text=piece,
                    )
                )
        if not chunks:
            raise PdfExtractionError("PDF não produziu texto indexável")
        return tuple(chunks)

    def _split_text(self, text: str) -> tuple[str, ...]:
        pieces: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + self._max_chars, len(text))
            if end < len(text):
                boundary = text.rfind(" ", start + self._max_chars // 2, end)
                if boundary > start:
                    end = boundary
            piece = text[start:end].strip()
            if piece:
                pieces.append(piece)
            if end >= len(text):
                break
            next_start = max(start + 1, end - self._overlap_chars)
            while next_start < len(text) and text[next_start].isspace():
                next_start += 1
            start = next_start
        return tuple(pieces)
