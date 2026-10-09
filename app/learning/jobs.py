from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import uuid

from .workflow import get_job, run_learning_workflow

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='learning-job')
_lock = threading.Lock()
_inflight: dict[str, object] = {}


def schedule_learning_job(*, wait: bool = False, min_rows: int = 5, actor_id: str = 'system') -> dict:
    """Enqueue async learning workflow. Never trains inside the HTTP request path when wait=False."""
    job_id = 'LJ-' + uuid.uuid4().hex[:10].upper()
    if wait:
        return run_learning_workflow(job_id, min_rows=min_rows, actor_id=actor_id)

    def _run():
        try:
            run_learning_workflow(job_id, min_rows=min_rows, actor_id=actor_id)
        finally:
            with _lock:
                _inflight.pop(job_id, None)

    with _lock:
        fut = _executor.submit(_run)
        _inflight[job_id] = fut
    return {'job_id': job_id, 'status': 'SCHEDULED', 'async': True}
