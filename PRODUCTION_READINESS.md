# Production readiness assessment

Review date: 2026-10-09. Overall status: **not production-ready**. The repository
is a useful local MVP/reference stack, but key persistence, identity, operational
and validation controls must be completed before handling real customer or
telecom data.

## Summary by area

| Area | Status | Assessment |
| --- | --- | --- |
| Architecture | Needs work | Services are separated and Compose provides local dependencies. Operational API data still uses SQLite while PostgreSQL hosts MLflow metadata/canonical schema. |
| Authentication/RBAC | Partial | OIDC RS256 validation and role checks exist. Local Compose deliberately runs unauthenticated as ADMIN and now binds only to localhost. Production OIDC configuration, identity lifecycle, and role mapping need deployment validation. |
| Sensitive data | Partial | Role-based masking and audit sanitization exist. Review all nested/free-text payloads, data retention, encryption, and least-privilege role access against actual policy. |
| Validation/SQL | Partial | Parameterized SQL and Pydantic validation are widely used. Arbitrary dictionary payloads and uploaded data require stricter limits, schemas, and fuzz testing. |
| Logging/errors | Partial | HTTP access logs are disabled and secrets are not intended to be logged. Add structured, redacted logs, correlation IDs, retention policy, and centralized alerting. |
| Models | Partial | Temporal splits, deterministic seeds, feature metadata, promotion gates and rollback mechanisms are present. Immutable data snapshots, feature lineage, artifact signatures, and cross-runtime reproducibility are incomplete. |
| Champion/challenger | Partial | Registry and shadow/canary/rollback APIs exist. Validate gates with representative data, protect deployment actions with approvals, and test crash/concurrency behavior. |
| Agents/evidence | Partial | Tools provide investigation evidence and reports distinguish unavailable facts. Require source IDs, timestamps, versions, and integrity hashes for evidentiary workflows; keep agent output advisory. |
| Observability | Partial | Prometheus metrics, OTel configuration, and Grafana definitions exist. Tracing exporter and production dashboards/alerts need environment-specific setup. |
| Docker | Improved for local | Health checks, named volumes and localhost-only publishing are configured. Python containers now run non-root. Pin/scan image digests, test upgrades and volume permissions before production. |
| Dependencies | Unverified | Locking/pinning exists for Python, but vulnerability scanning could not execute. Frontend lock/install state and image SBOM need a clean network-enabled build and audit. |
| Tests | Unverified here | 24 test modules are present. Full suite was requested and attempted, but no tests executed because the available Python runtime lacks a working SQLite module/pytest. |
| Performance/concurrency | Partial | Some model execution is constrained and key case/queue writes now serialize safely in SQLite. SQLite and in-process background jobs limit throughput and multi-replica operation. |
| Idempotency/indexes | Needs work | Some ingestions use checksums and entity IDs. Decision/job/report retry semantics and database index coverage should be formalized and load-tested. |
| Secrets/config | Local-safe only | `.env.example` uses placeholders. Compose fallback credentials, disabled auth, and ADMIN identity are strictly local development defaults. |

## Release blockers

1. Move operational transactions, cases, evidence, decisions, queues, audits,
   policies, and model-registry state to PostgreSQL with migrations, constraints,
   indexes, transactions, and multi-worker concurrency tests.
2. Require a real OIDC provider in all shared environments; provision and test
   role claims, token lifetimes, MFA/assurance policy, key rotation, revocation,
   and access-denial behavior.
3. Add strict schemas and request/upload limits for free-form API payloads;
   verify all sensitive response shapes by role.
4. Add durable background job execution/leases and restart recovery; do not rely
   on the process-local executor for production retraining.
5. Establish signed/immutable model artifacts, complete dataset/feature lineage,
   reproducible build/runtime metadata, and approval controls for promotion.
6. Run the full tests, dependency audit, frontend production build, image scan,
   and end-to-end workflows in a clean Docker-enabled CI runner.
7. Define retention, backup/restore, encryption, incident response, audit-log
   immutability, and operational SLOs for the deployment.

## Changes made

This review restricted Docker host publication to loopback, changed the Python
image to non-root, made decision writes evidence-version/state conditional in a
write transaction, made label claiming atomic, and removed raw learning error
text from API output. See [SECURITY_REVIEW.md](SECURITY_REVIEW.md) for findings
and verification details.
