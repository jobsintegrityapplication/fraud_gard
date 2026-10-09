from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .schema import SUPPORTED_FORMATS
from .service import get_ingestion, ingest_bytes, list_quarantine
from ..security.auth import Principal, require_roles

router = APIRouter(prefix='/api/v1/ingestion', tags=['ingestion'])


@router.get('/formats')
def formats(_principal: Principal = Depends(require_roles('ML_ENGINEER', 'DATA_SCIENTIST'))):
    return {'formats': list(SUPPORTED_FORMATS), 'statuses': ['PENDING', 'VALIDATING', 'PROCESSING', 'COMPLETED', 'FAILED']}


@router.post('/upload')
async def upload(file: UploadFile = File(...), _principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    payload = await file.read()
    upload_id = 'ING-' + uuid.uuid4().hex[:12].upper()
    try:
        return ingest_bytes(payload, file.filename or 'upload.bin', file.content_type, upload_id)
    except ValueError as exc:
        job = get_ingestion(upload_id)
        if job:
            return job
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        job = get_ingestion(upload_id)
        if job and job.get('status') == 'FAILED':
            return job
        raise HTTPException(400, str(exc)) from exc


@router.get('/{upload_id}')
def get_upload(upload_id: str, _principal: Principal = Depends(require_roles('ML_ENGINEER', 'DATA_SCIENTIST'))):
    job = get_ingestion(upload_id)
    if not job:
        raise HTTPException(404, 'upload not found')
    return job


@router.get('/{upload_id}/quarantine')
def get_quarantine(upload_id: str, _principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    job = get_ingestion(upload_id)
    if not job:
        raise HTTPException(404, 'upload not found')
    return {'upload_id': upload_id, 'records': list_quarantine(upload_id)}
