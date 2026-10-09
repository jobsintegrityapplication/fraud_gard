from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..agents.policy import EVIDENCE_UNAVAILABLE

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(['html', 'xml', 'j2']),
    )


def render_html(report: dict[str, Any]) -> str:
    """Generate HTML first via Jinja2."""
    tmpl = _env().get_template('investigation_report.html.j2')
    return tmpl.render(report=report, unavailable_token=EVIDENCE_UNAVAILABLE)


def render_pdf(report: dict[str, Any], dest: Path) -> Path:
    """Generate PDF second from the same report document (no invented content)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(dest), pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch)
    styles = getSampleStyleSheet()
    title = ParagraphStyle('TitleCustom', parent=styles['Heading1'], fontSize=14, spaceAfter=8)
    h2 = ParagraphStyle('H2Custom', parent=styles['Heading2'], fontSize=11, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle('BodyCustom', parent=styles['Normal'], fontSize=9, leading=12)
    note = ParagraphStyle('NoteCustom', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#555555'), leading=10)

    story = []
    story.append(Paragraph(f"Fraud Investigation Report — {report.get('report_version')}", title))
    story.append(Paragraph(
        f"Case: {report.get('case_id')} &nbsp;|&nbsp; Transaction: {report.get('transaction_id')} "
        f"&nbsp;|&nbsp; Generated: {report.get('generated_at')}",
        body,
    ))
    story.append(Paragraph(
        f"Invented content: {report.get('invented')}. "
        f"Missing evidence is marked {EVIDENCE_UNAVAILABLE} and is never fabricated.",
        note,
    ))
    story.append(Spacer(1, 8))

    for claim in report.get('claims') or []:
        story.append(Paragraph(str(claim.get('label') or claim.get('field')), h2))
        status = claim.get('status')
        value = claim.get('value')
        if status != 'OK' or value == EVIDENCE_UNAVAILABLE:
            story.append(Paragraph(f"<b>{EVIDENCE_UNAVAILABLE}</b>", body))
            if claim.get('note'):
                story.append(Paragraph(str(claim['note']), note))
        else:
            text = _format_value_for_pdf(value)
            story.append(Paragraph(text, body))
        eids = claim.get('evidence_ids') or []
        eid_text = ', '.join(str(e) for e in eids) if eids else EVIDENCE_UNAVAILABLE
        story.append(Paragraph(f"Evidence IDs: {eid_text}", note))
        story.append(Spacer(1, 4))

    unavailable = report.get('unavailable') or []
    if unavailable:
        story.append(Paragraph('Unavailable evidence log', h2))
        rows = [['Kind', 'Reason']]
        for u in unavailable:
            rows.append([str(u.get('kind') or ''), str(u.get('reason') or '')])
        table = Table(rows, colWidths=[1.5 * inch, 5 * inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eeeeee')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(table)

    doc.build(story)
    return dest


def _format_value_for_pdf(value: Any) -> str:
    if value is None:
        return EVIDENCE_UNAVAILABLE
    if isinstance(value, dict):
        parts = [f"<b>{_esc(k)}</b>: {_esc(v)}" for k, v in value.items()]
        return '<br/>'.join(parts)
    if isinstance(value, list):
        if not value:
            return '<i>(none observed)</i>'
        lines = []
        for i, item in enumerate(value[:50]):
            lines.append(f"{i + 1}. {_esc(item)}")
        if len(value) > 50:
            lines.append(f"… {len(value) - 50} more")
        return '<br/>'.join(lines)
    return _esc(value)


def _esc(value: Any) -> str:
    text = str(value)
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )
