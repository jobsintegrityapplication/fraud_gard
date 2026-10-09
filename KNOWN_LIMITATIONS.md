# Known limitations

This repository is a local development MVP, not a production telecom fraud
deployment.

- Operational API workflows persist to SQLite (`data/fraud.db`). PostgreSQL
  currently hosts MLflow metadata and the canonical Alembic schema; the active
  case, evidence, decision, audit, and learning queue paths have not migrated.
- The default Compose profile disables OIDC and runs as a development ADMIN.
  Published ports bind to localhost, but enabling access from other machines
  requires OIDC, configured roles, and production secrets.
- Learning jobs run in a process-local, single-worker executor. In-flight jobs
  are not resumed after process restart, and multiple backend replicas are not
  coordinated by a durable job lease system.
- Dataset and feature lineage are not fully immutable. A version string and
  feature-set version do not guarantee reconstruction of the exact source data,
  dependency environment, or runtime hardware.
- Joblib model files must be treated as trusted executable artifacts.
- Several API payloads accept free-form JSON structures and lack comprehensive
  body-size/depth limits.
- The frontend lockfile/package state and current third-party vulnerability
  status need verification in a network-enabled clean build.
- No end-to-end runtime result is available for this review: Docker Desktop's
  Linux daemon is unavailable. The test suite is present but could not start
  because the installed Python runtimes lack pytest and/or a working `_sqlite3`
  extension. `docker compose config --quiet` did pass.
- Optional Neo4j and local-LLM services are not required by the default local
  MVP; the graph defaults to NetworkX, and Ollama does not download a model.

Review and maintain these items as the service is deployed to each environment.
