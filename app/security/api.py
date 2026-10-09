from fastapi import APIRouter, Depends

from .audit import record_event
from .auth import Principal, require_principal

router = APIRouter(prefix='/api/v1/auth', tags=['authentication'])


@router.get('/session')
def session(principal: Principal = Depends(require_principal)):
    """Validate an existing OIDC session and record a successful login event."""
    record_event(principal.actor_id, 'LOGIN', 'Identity', detail={'roles': sorted(principal.roles)})
    return {'authenticated': True, 'actor_id': principal.actor_id, 'roles': sorted(principal.roles)}


@router.get('/me')
def current_user(principal: Principal = Depends(require_principal)):
    return {'authenticated': True, 'actor_id': principal.actor_id, 'roles': sorted(principal.roles)}
