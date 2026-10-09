from __future__ import annotations

from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config
from .service import generate_investigation_report, list_reports, load_report
from ..security.audit import record_event
from ..security.auth import Principal, require_roles

router = APIRouter(prefix='/api/v1/reports', tags=['reports'])


class GenerateReportBody(BaseModel):
    state: dict
    report_version: str | None = None


def _validate_version(value: str | None) -> None:
    if value is not None and not re.fullmatch(r'RPT-[A-Za-z0-9_-]{1,64}', value):
        raise HTTPException(400, 'invalid report version')


@router.get('')
def api_list_reports(limit: int = 50, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'AUDITOR'))):
    return {'reports': list_reports(limit=max(1, min(limit, 200)))}


@router.post('/generate')
def api_generate(body: GenerateReportBody, principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    _validate_version(body.report_version)
    try:
        out = generate_investigation_report(body.state, report_version=body.report_version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record_event(principal.actor_id, 'REPORT_GENERATION', 'InvestigationReport', out['report_version'], {'case_id': out.get('case_id')})
    return {
        'report_version': out['report_version'],
        'case_id': out['case_id'],
        'transaction_id': out['transaction_id'],
        'invented': False,
        'html_path': out['html_path'],
        'pdf_path': out['pdf_path'],
        'json_path': out['json_path'],
        'claims': out['document']['claims'],
        'unavailable': out['document'].get('unavailable') or [],
    }


@router.get('/{report_version}')
def api_get_report(report_version: str, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    _validate_version(report_version)
    doc = load_report(report_version)
    if doc is None:
        raise HTTPException(404, 'report not found')
    return doc


@router.get('/{report_version}/html')
def api_report_html(report_version: str, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    _validate_version(report_version)
    html = Path(config.REPORT_DIR) / f'{report_version}.html'
    if not html.exists():
        raise HTTPException(404, 'report HTML not found')
    return FileResponse(html, media_type='text/html', filename=html.name)


@router.get('/{report_version}/pdf')
def api_report_pdf(report_version: str, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    _validate_version(report_version)
    pdf = Path(config.REPORT_DIR) / f'{report_version}.pdf'
    if not pdf.exists():
        raise HTTPException(404, 'report PDF not found')
    return FileResponse(pdf, media_type='application/pdf', filename=pdf.name)
