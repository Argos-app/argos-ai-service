"""Extração limitada de texto por página de PDFs allowlisted."""

from hashlib import sha256
from io import BytesIO

from argos_a2a_agent.domain.errors import PdfExtractionError
from argos_a2a_agent.domain.models import DocumentMetadata, ParsedDocument, ParsedPage


class PyPdfParser:
    """Parser sem OCR; PDFs escaneados sem camada de texto falham de forma segura."""

    def __init__(self, *, max_pdf_bytes: int = 20 * 1024 * 1024, max_pages: int = 300) -> None:
        if not 1 <= max_pdf_bytes <= 20 * 1024 * 1024:
            raise ValueError("max_pdf_bytes deve estar entre 1 byte e 20 MiB")
        if not 1 <= max_pages <= 300:
            raise ValueError("max_pages deve estar entre 1 e 300")
        self._max_pdf_bytes = max_pdf_bytes
        self._max_pages = max_pages

    async def parse(self, content: bytes, metadata: DocumentMetadata) -> ParsedDocument:
        if not content or len(content) > self._max_pdf_bytes:
            raise PdfExtractionError("PDF vazio ou acima do limite de bytes")
        if (
            len(content) != metadata.size_bytes
            or sha256(content).hexdigest() != metadata.revision_hash
        ):
            raise PdfExtractionError("bytes do PDF não correspondem à revisão allowlisted")
        if not content.startswith(b"%PDF-"):
            raise PdfExtractionError("arquivo não possui assinatura PDF")

        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise PdfExtractionError("PDF protegido por senha não pode ser indexado")
            if len(reader.pages) == 0 or len(reader.pages) > self._max_pages:
                raise PdfExtractionError("PDF sem páginas ou acima do limite de páginas")

            pages = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text()
                normalized = "\n".join(
                    line.rstrip() for line in (text or "").replace("\x00", "").splitlines()
                ).strip()
                pages.append(ParsedPage(page_number=page_number, text=normalized))
            if not any(page.text.strip() for page in pages):
                raise PdfExtractionError("PDF não contém texto extraível; OCR não está habilitado")
            return ParsedDocument(metadata=metadata, pages=tuple(pages))
        except PdfExtractionError:
            raise
        except Exception as exc:
            raise PdfExtractionError("não foi possível extrair o texto do PDF") from exc
