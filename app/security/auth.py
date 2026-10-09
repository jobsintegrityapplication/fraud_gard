"""OIDC bearer-token validation and role-based authorization.

The identity provider owns login, MFA, password policy, and token issuance. This
service accepts RS256 access tokens and validates their signature and claims
against the provider's OIDC JWKS endpoint.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable
from urllib.request import urlopen

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..domain.redact import fingerprint

ROLES = frozenset({'ADMIN', 'FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'})
_ROLE_CACHE: dict[str, Any] = {'expires': 0.0, 'jwks': None, 'discovery': None}
_CACHE_LOCK = Lock()
bearer_scheme = HTTPBearer(auto_error=False, scheme_name='OIDC access token', description='RS256 bearer access token issued by the configured OpenID Connect provider.')


@dataclass(frozen=True)
class Principal:
    subject: str
    actor_id: str
    roles: frozenset[str]
    claims: dict[str, Any]


class TokenValidationError(ValueError):
    pass


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def _json_segment(value: str) -> dict[str, Any]:
    result = json.loads(_b64url(value))
    if not isinstance(result, dict):
        raise TokenValidationError('invalid token segment')
    return result


def _oidc_metadata() -> dict[str, Any]:
    issuer = os.getenv('OIDC_ISSUER', '').rstrip('/')
    if not issuer:
        raise TokenValidationError('OIDC_ISSUER is not configured')
    with _CACHE_LOCK:
        if _ROLE_CACHE['discovery'] and _ROLE_CACHE['expires'] > time.monotonic():
            return _ROLE_CACHE['discovery']
    configured_jwks = os.getenv('OIDC_JWKS_URL')
    if configured_jwks:
        metadata = {'issuer': issuer, 'jwks_uri': configured_jwks}
    else:
        with urlopen(f'{issuer}/.well-known/openid-configuration', timeout=5) as response:
            metadata = json.loads(response.read())
        if metadata.get('issuer', '').rstrip('/') != issuer:
            raise TokenValidationError('OIDC discovery issuer does not match configuration')
    if not metadata.get('jwks_uri'):
        raise TokenValidationError('OIDC JWKS endpoint is unavailable')
    with _CACHE_LOCK:
        _ROLE_CACHE.update(discovery=metadata, expires=time.monotonic() + 300)
    return metadata


def _jwks() -> dict[str, Any]:
    metadata = _oidc_metadata()
    with _CACHE_LOCK:
        if _ROLE_CACHE['jwks'] and _ROLE_CACHE['expires'] > time.monotonic():
            return _ROLE_CACHE['jwks']
    with urlopen(metadata['jwks_uri'], timeout=5) as response:
        value = json.loads(response.read())
    if not isinstance(value.get('keys'), list):
        raise TokenValidationError('OIDC signing keys are unavailable')
    with _CACHE_LOCK:
        _ROLE_CACHE.update(jwks=value, expires=time.monotonic() + 300)
    return value


def _verify_rs256(token: str) -> dict[str, Any]:
    try:
        encoded_header, encoded_claims, encoded_signature = token.split('.')
        header = _json_segment(encoded_header)
        claims = _json_segment(encoded_claims)
        if header.get('alg') != 'RS256' or not header.get('kid'):
            raise TokenValidationError('unsupported token signing algorithm')
        jwk = next((k for k in _jwks()['keys'] if k.get('kid') == header['kid'] and k.get('kty') == 'RSA'), None)
        if jwk is None:
            with _CACHE_LOCK:
                _ROLE_CACHE.update(jwks=None, expires=0.0)
            jwk = next((k for k in _jwks()['keys'] if k.get('kid') == header['kid'] and k.get('kty') == 'RSA'), None)
        if jwk is None:
            raise TokenValidationError('token signing key was not found')
        public_key = rsa.RSAPublicNumbers(
            int.from_bytes(_b64url(jwk['e']), 'big'), int.from_bytes(_b64url(jwk['n']), 'big')
        ).public_key()
        public_key.verify(
            _b64url(encoded_signature), f'{encoded_header}.{encoded_claims}'.encode('ascii'),
            padding.PKCS1v15(), hashes.SHA256(),
        )
    except TokenValidationError:
        raise
    except Exception as exc:
        raise TokenValidationError('invalid bearer token') from exc
    issuer = os.getenv('OIDC_ISSUER', '').rstrip('/')
    audience = os.getenv('OIDC_AUDIENCE', '')
    if not issuer or not audience:
        raise TokenValidationError('OIDC_ISSUER and OIDC_AUDIENCE must be configured')
    now = time.time()
    aud_claim = claims.get('aud')
    if claims.get('iss', '').rstrip('/') != issuer or audience not in (aud_claim if isinstance(aud_claim, list) else [aud_claim]):
        raise TokenValidationError('token issuer or audience is invalid')
    if not claims.get('sub') or not claims.get('exp') or float(claims['exp']) <= now:
        raise TokenValidationError('token is expired or missing required claims')
    if claims.get('nbf') is not None and float(claims['nbf']) > now + 30:
        raise TokenValidationError('token is not yet valid')
    return claims


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    roles_claim = os.getenv('OIDC_ROLES_CLAIM', 'roles')
    roles: Any = claims.get(roles_claim)
    if roles is None:
        roles = claims.get('realm_access', {}).get('roles', []) if isinstance(claims.get('realm_access'), dict) else []
    if isinstance(roles, str):
        roles = [roles]
    normalized = frozenset(str(role).upper() for role in (roles or []) if str(role).upper() in ROLES)
    subject = str(claims['sub'])
    actor = 'user:' + fingerprint(subject).split(':', 1)[-1]
    return Principal(subject=subject, actor_id=actor, roles=normalized, claims=claims)


def authenticate_token(token: str) -> Principal:
    return principal_from_claims(_verify_rs256(token))


async def require_principal(request: Request, _credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> Principal:
    principal = getattr(request.state, 'principal', None)
    if principal is None:
        raise HTTPException(status_code=401, detail='Authentication required', headers={'WWW-Authenticate': 'Bearer'})
    return principal


def require_roles(*roles: str) -> Callable:
    required = frozenset(roles)

    async def check(request: Request, _credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> Principal:
        principal = await require_principal(request, _credentials)
        if 'ADMIN' in principal.roles or principal.roles.intersection(required):
            return principal
        raise HTTPException(status_code=403, detail='Insufficient role')
    return check
