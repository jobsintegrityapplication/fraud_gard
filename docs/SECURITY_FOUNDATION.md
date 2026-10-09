# Security foundation

## OIDC configuration

The API does not collect or validate passwords. Configure an OpenID Connect provider to issue RS256 bearer access tokens and set:

- `OIDC_ISSUER` to the exact issuer URL from the provider's discovery document.
- `OIDC_AUDIENCE` to this API's audience.
- `OIDC_JWKS_URL` only when the provider's signing-key URL should be pinned; otherwise discovery supplies it.
- `OIDC_ROLES_CLAIM` when roles are not in the `roles` claim. Keycloak `realm_access.roles` is also recognized.
- `PII_MASK_SECRET` to a high-entropy deployment secret so restricted-role pseudonyms remain stable across restarts.

Set the browser's public `issuer`, `clientId`, optional `audience`, and `scope` in `frontend/fraud-console/src/assets/runtime-config.js` (or replace it during deployment). Register `https://<console-origin>/auth/callback` as an allowed redirect URI. The browser uses authorization code with PKCE and does not use a client secret; access tokens stay in session storage and are sent only to the same-origin `/api/` endpoints.

Authentication is enabled by default and fails closed when OIDC configuration is missing. `AUTH_ENABLED=false` and `DEV_AUTH_ROLE` exist for local development only; do not use them in a deployed environment. The API supports the external provider's authorization-code/PKCE login flow by accepting and validating its bearer token. `GET /api/v1/auth/session` validates the authenticated session and writes a `LOGIN` audit event.

## Roles

| Role | Main permissions |
| --- | --- |
| `ADMIN` | Full access; policy changes and administrative operations |
| `FRAUD_ANALYST` | Read alerts/cases, investigate, decide cases, and review patterns |
| `SENIOR_ANALYST` | Analyst permissions plus pattern activation/deprecation |
| `DATA_SCIENTIST` | Read masked data, score/investigate, discover patterns, evaluate models, and manage learning inputs |
| `ML_ENGINEER` | Model training/deployment/rollback, ingestion, learning jobs, and candidate features |
| `AUDITOR` | Read audit, cases, alerts, patterns, and report metadata with sensitive fields masked |

Only `ADMIN`, `FRAUD_ANALYST`, and `SENIOR_ANALYST` receive full IMEI, MSISDN, IP, customer, account, SIM, and device identifier values. Other roles receive masked identifiers or stable keyed pseudonyms. Report file downloads are limited to full-access analyst roles because generated PDF/HTML files are not transformed at response time.

Audit actors are one-way pseudonymous IDs derived from the OIDC subject. Free-text audit values are omitted; credential-like fields are omitted and sensitive structured values are keyed fingerprints. Authorization headers and bearer tokens are not written to audit events.
