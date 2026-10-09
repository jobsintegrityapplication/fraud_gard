from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config
from ..agents.policy import EVIDENCE_UNAVAILABLE
from .assemble import assemble_report_document
from .render import render_html, render_pdf


def _report_dir() -> Path:
    dest = Path(config.REPORT_DIR)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def generate_investigation_report(state: dict[str, Any], *, report_version: str | None = None) -> dict:
    """
    Build report document → HTML (Jinja2) first → PDF second.
    Never fabricates scores, entities, or decisions.
    """
    document = assemble_report_document(state, report_version=report_version)
    version = document['report_version']
    base = _report_dir() / version

    html = render_html(document)
    html_path = Path(str(base) + '.html')
    html_path.write_text(html, encoding='utf-8')

    pdf_path = Path(str(base) + '.pdf')
    render_pdf(document, pdf_path)

    json_path = Path(str(base) + '.json')
    json_path.write_text(json.dumps(document, indent=2, default=str), encoding='utf-8')

    return {
        'report_version': version,
        'case_id': document.get('case_id'),
        'transaction_id': document.get('transaction_id'),
        'invented': False,
        'html_path': str(html_path),
        'pdf_path': str(pdf_path),
        'json_path': str(json_path),
        'document': document,
        'unavailable_token': EVIDENCE_UNAVAILABLE,
    }


def load_report(report_version: str) -> dict | None:
    path = _report_dir() / f'{report_version}.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def list_reports(limit: int = 50) -> list[dict]:
    rows = []
    for path in sorted(_report_dir().glob('RPT-*.json'), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({
            'report_version': data.get('report_version') or path.stem,
            'case_id': data.get('case_id'),
            'transaction_id': data.get('transaction_id'),
            'generated_at': data.get('generated_at'),
            'html_path': str(path.with_suffix('.html')),
            'pdf_path': str(path.with_suffix('.pdf')),
        })
    return rows
