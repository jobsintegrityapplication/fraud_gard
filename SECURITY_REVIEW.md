# Security review

Review date: 2026-10-09. Scope: repository source, deployment configuration,
authentication/authorization, data handling, model lifecycle, and available
local checks. This is a source review, not a penetration test or a production
certification.

## Findings

| Severity | Area | Finding and disposition |
| --- | --- | --- |
| High | Local deployment | Compose disables authentication by default and assigns the `ADMIN` development role. All host-published ports now bind to `127.0.0.1`; this limits the default local-only mode to the workstation. Set up OIDC and enable authentication before any shared or production deployment. |
| High | Container | The backend image previously ran as root. It now creates UID/GID 10001 and runs the backend and MLflow processes as that user. Verify volume ownership when upgrading an existing deployment with old root-owned bind mounts. |
| High | Case concurrency | Case decisions previously had a read/check/write race. Decision evidence version and state are now rechecked inside `BEGIN IMMEDIATE`, and the update is conditional. Stale/concurrent decisions return a conflict. |
| High | Learning concurrency | Queue claiming previously selected and updated labels one row at a time. Claims now occur in one SQLite write transaction with a conditional status update, preventing duplicate claims between local workers. Multi-host Postgres queue claiming is not implemented because operational queue storage remains SQLite. |
| High | Model label leakage | Challenger training previously filled unreviewed transaction labels from the source table (including synthetic ground truth), treating missing outcomes as negatives. Training now maps only analyst-reviewed queue labels while retaining the full transaction history for feature context; unlabeled rows are excluded from model fitting/evaluation. |
| Medium | Authentication | OIDC validation supports RS256, issuer/audience, expiry, not-before, and JWKS rotation. It does not implement login/MFA/token issuance, logout/revocation, refresh tokens, or provider-specific assurance policy; those remain identity-provider responsibilities. |
| Medium | Sensitive data | Response redaction is role-based; analyst roles can view raw identifiers, while non-analyst roles receive masked identifiers. Audit details are sanitized/fingerprinted. Free-text evidence/reason fields still need domain-specific review before storing arbitrary external content. |
| Medium | Logging/errors | Learning enqueue errors are now returned as a generic message rather than raw exception text. Review remaining exception handlers and proxy/application logs before production; ensure request bodies and authorization headers are never captured by upstream logging. |
| Medium | SQL | Reviewed SQL uses bound parameters for values. Dynamic schema fragments in migrations and ingestion are selected from code-defined column lists. Continue to keep identifiers allowlisted; SQL identifiers cannot be protected by value parameterization. |
| Medium | Model artifacts | Joblib artifacts are executable pickle content. Only load artifacts from trusted, access-controlled storage. Do not accept arbitrary user-uploaded model files or make the model volume writable by untrusted processes. |
| Medium | Model lifecycle | Temporal train/validation/test splits, fixed random seeds, feature-set metadata, model registry records, gates, deployment states, and rollback exist. Reproducibility is limited by dependency/runtime/hardware versions and incomplete immutable dataset/feature lineage. |
| Medium | Agent/evidence | Investigation output is assembled from deterministic tools and report claims mark unavailable evidence; no autonomous fraud decision is authorized by the agent. Validate source provenance and ensure each production evidence item has durable source/version/hash metadata before relying on reports for regulated decisions. |
| Medium | API validation | Core request bodies use Pydantic, parameters are bounded on key list endpoints, and report versions are regex-validated. Several free-form dictionaries/lists (case evidence, report state, model gate payloads) do not have strict size/depth/field limits. Add request-size limits and per-field schemas before exposure to untrusted clients. |
| Medium | Frontend | Angular uses a same-origin Nginx API proxy. No frontend secret should be shipped in the bundle. Validate CSP/security headers, XSS behavior for server-provided text, and auth token storage/refresh when a real OIDC UI is integrated. |
| Medium | Configuration/secrets | `.env.example` contains placeholders only. Compose fallback values are explicitly development-only and are not production secrets. Use an external secret manager and rotate credentials for any shared environment. |
| Medium | Data store | Operational app routes use SQLite in a persistent volume; PostgreSQL is initialized for the canonical schema and MLflow metadata. This split is not suitable for horizontally scaled production decisions, queue ownership, or high write concurrency. |
| Low | Audit | Login, case access/decision, pattern/model lifecycle, report, and policy events are represented across security and domain audit paths. Confirm retention, tamper resistance, access separation, and alerting requirements with the deployment owner. |
| Not verified | Dependencies | `pip-audit` is not installed, npm audit cannot be run without a complete installed dependency tree/network, and Docker Scout is not available against a running build. Dependency vulnerability status is therefore unverified, not clean. |

## Fixes made in this review

- Bind published Compose ports to localhost while preserving configurable host
  ports.
- Run Python services as non-root UID/GID 10001.
- Serialize case decisions and check the case evidence version atomically.
- Claim learning labels atomically.
- Train challengers only from analyst-reviewed labels; unlabeled transactions
  remain feature context and are no longer coerced to negative examples.
- Avoid returning raw exception strings from learning enqueue failures.

## Verification limits

`docker compose config --quiet` passed. The test suite could not start: the
workspace Python environment fails loading `_sqlite3`, and the available
Python 3.14 installation has no pytest; the project targets Python 3.11.
Docker Compose runtime tests could not run because the Docker Desktop Linux
engine pipe is unavailable. These are environment blockers, not passing tests.
