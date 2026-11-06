"""Renderização determinística de relatórios de lote."""

from importlib import resources
from io import BytesIO
from string import Template
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from argos_mcp_email.ports.report_renderer import RenderedReport
from packages.contracts import LotReportData, RecipientRef


class TemplateReportRenderer:
    """Preenche um template controlado sem gerar texto por LLM."""

    def __init__(self, template_text: str | None = None) -> None:
        self._template = Template(template_text or self._load_default_template())

    def render(
        self,
        report: LotReportData,
        recipients: tuple[RecipientRef, ...],
        title: str,
    ) -> RenderedReport:
        """destinatários pertencem ao envelope do e-mail, não ao conteúdo."""
        del recipients
        lot = report.requested_lot
        applications = (
            "\n".join(
                (
                    f"- {item.application_date.isoformat()} ({item.application_kind.lower()}): "
                    f"{item.medicine_name} "
                    f"(lote do medicamento {item.medicine_batch_number}); "
                    f"({item.dose} {item.unit}, {item.route}); "
                    f"{item.head_count_applied} cabeças; carência até "
                    f"{
                        item.meat_withdrawal_end.isoformat()
                        if item.meat_withdrawal_end
                        else 'não informada'
                    }; "
                    f"registro {'ativo' if item.active else 'inativo'}."
                )
                for item in report.applications
            )
            or "- Nenhuma aplicação registrada."
        )
        exception_animals = (
            "\n".join(
                (
                    f"- {animal.ear_tag}: {animal.category}, status {animal.status}; "
                    f"motivo: {animal.exception_reason}; apto para abate: "
                    f"{'sim' if animal.fit_for_slaughter else 'não'}."
                )
                for animal in report.exception_animals
            )
            or "- Nenhum animal em exceção registrado."
        )

        body = self._template.substitute(
            lot_code=lot.lot_code,
            property_name=lot.property_name,
            herd_name=lot.herd_name,
            herd_breed=lot.herd_breed,
            lot_category=lot.lot_category,
            head_count=lot.lot_head_count,
            open_date=lot.lot_open_date.isoformat(),
            slaughter_release_date=(
                lot.slaughter_release_date.isoformat()
                if lot.slaughter_release_date
                else "não informada"
            ),
            fit_for_slaughter="sim" if lot.fit_for_slaughter else "não",
            applications=applications,
            exception_animals=exception_animals,
            queried_at=report.queried_at.isoformat(),
        )
        subject = f"{title.strip()} — lote {lot.lot_code}"
        body = body.strip()
        return RenderedReport(subject=subject, body=body, pdf=_render_pdf(subject, body))

    @staticmethod
    def _load_default_template() -> str:
        return (
            resources.files("argos_mcp_email.adapters.rendering.templates")
            .joinpath("lot_report.txt")
            .read_text(encoding="utf-8")
        )


def _render_pdf(subject: str, body: str) -> bytes:
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=27 * mm,
        bottomMargin=22 * mm,
    )
    title_style = ParagraphStyle(
        "title",
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=26,
        textColor=colors.HexColor("#183b2c"),
        spaceAfter=12,
    )
    text_style = ParagraphStyle(
        "body",
        fontName="Helvetica",
        fontSize=9.5,
        leading=15,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#24332b"),
        spaceAfter=3,
    )
    section_style = ParagraphStyle(
        "section",
        parent=text_style,
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=15,
        textColor=colors.HexColor("#234b39"),
        spaceBefore=12,
        spaceAfter=5,
        keepWithNext=True,
    )
    content = [
        Paragraph(escape(subject), title_style),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dce7df")),
        Spacer(1, 8),
    ]
    for line in body.splitlines():
        if line and set(line) <= {"-", "="}:
            continue
        if line == "ARGOS | RELATÓRIO DE LOTE":
            continue
        style = (
            section_style
            if line in {"IDENTIFICAÇÃO", "APLICAÇÕES", "ANIMAIS EM EXCEÇÃO"}
            else text_style
        )
        content.append(Paragraph(escape(line), style) if line else Spacer(1, 3))

    def draw_frame(canvas, doc):
        canvas.saveState()
        width, _ = A4
        canvas.setFillColor(colors.HexColor("#234b39"))
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(doc.leftMargin, A4[1] - 17 * mm, "ARGOS")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#718078"))
        canvas.drawRightString(width - doc.rightMargin, A4[1] - 17 * mm, "RELATÓRIO DE LOTE")
        canvas.setStrokeColor(colors.HexColor("#dce7df"))
        canvas.line(doc.leftMargin, 16 * mm, width - doc.rightMargin, 16 * mm)
        canvas.drawString(doc.leftMargin, 11 * mm, "Argos | Informações da consulta")
        canvas.drawRightString(width - doc.rightMargin, 11 * mm, f"Página {doc.page}")
        canvas.restoreState()

    document.build(content, onFirstPage=draw_frame, onLaterPages=draw_frame)
    return output.getvalue()
