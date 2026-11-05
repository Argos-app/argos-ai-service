"""Renderização determinística de relatórios de lote."""

from importlib import resources
from io import BytesIO
from string import Template
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

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
    document = SimpleDocTemplate(output, leftMargin=20 * mm, rightMargin=20 * mm)
    title_style = ParagraphStyle(
        "title",
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=24,
        textColor=colors.HexColor("#234b39"),
        spaceAfter=16,
    )
    text_style = ParagraphStyle(
        "body",
        fontName="Helvetica",
        fontSize=10,
        leading=15,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#24332b"),
    )
    content = [Paragraph(escape(subject), title_style)]
    for line in body.splitlines():
        content.append(Paragraph(escape(line), text_style) if line else Spacer(1, 7))
    document.build(content)
    return output.getvalue()
