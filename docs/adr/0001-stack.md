# ADR-0001: Implementation stack

**Status:** Accepted
**Date:** 2026-04-25
**Deciders:** Larry Watkins (project owner)

## Context

RfAnalyzer is a self-hosted, single-tenant API for RF propagation analysis. The design spec is feature-complete (Draft v3 — ready to implement); implementation has not started. The stack chosen here governs every subsequent technical decision, so it needs to fit the spec's hard requirements before code lands.

The constraints that shape the choice:

- **Compute-heavy.** Five analysis ops (§4.0) including voxel scans and area sweeps that run minutes-to-hours. Job state machine (§8.1) must support pause/resume conceptually and survive process restarts.
- **Numerical & raster-heavy.** Adaptive geo-data fidelity tiers (§5.4), DSM ingest, GeoTIFF/NetCDF artifacts (§6.1), per-stage propagation math across the 12-stage pipeline (§4.1). First-class numerical libraries are a must.
- **Pluggable model registry + pluggable link-type registry** (§4.2, §4.6). The runtime must expose stable extension points and version them.
- **Spec-first contract.** The design spec markdown is canonical; OpenAPI and JSON Schema are derived (per `CLAUDE.md`). The framework must accept this — code-first OpenAPI generators that invert the relationship are out.
- **Reproducible runs.** `inputs_resolved` snapshots (§3.1, §8.3) demand deterministic serialization and validation. The framework's request-binding pipeline must be inspectable, not magic.
- **Single-tenant Docker-Compose deployment** (§2.2, §8.5) on a single operator's hardware. Maintenance burden weighs heavily. Avoid platforms that require fleets of services to operate.
- **Downstream consumer.** [argus-flight-center](https://github.com/wildlifeprotection/argus-flight-center) is a TypeScript / Next.js / Prisma application that will consume this API. The contract between the two must support a generated TS client and a shared logging/observability shape.

## Decision

**Python 3.12, FastAPI, pydantic v2.** Run the API process under uvicorn; run a separate worker process consuming SUBMITTED runs from Postgres via SKIP LOCKED. Generate a TypeScript client from the OpenAPI for argus-flight-center to import.

| Concern | Choice | Rationale |
|---|---|---|
| Language / runtime | **Python 3.12** | First-class numerical/raster ecosystem (numpy, scipy, rasterio, pyproj, shapely, xarray, netCDF4). Same language as ITU-R reference implementations. |
| Web framework | **FastAPI + uvicorn** | Native async, OpenAPI emission from pydantic, request-binding is inspectable. |
| Worker model | **Separate process, polls Postgres `runs` table via `SELECT … FOR UPDATE SKIP LOCKED`** | The Run record IS the job (§8.1); no external queue infrastructure to keep in sync with Run state. |
| Validation | **pydantic v2**, **rfc8785** for JCS canonicalization | Models double as runtime types and OpenAPI source. The hand-maintained spec markdown / JSON Schema / OpenAPI artifacts in `docs/superpowers/specs/` remain canonical until implementation; once implementation begins, pydantic-emitted OpenAPI is checked against the spec-derived OpenAPI in CI. The `inputs_resolved_sha256` canonicalization (spec §3.3) MUST use the `rfc8785` Python library (or a binary-equivalent strict RFC 8785 implementation); hand-rolled JCS encoders are not permitted. |
| Package manager | **uv** | Fast resolver, lockfile-pinned, single binary, virtualenv-managed. |
| Lint / format | **ruff** (lint + format) | One tool, fast, replaces black + isort + flake8 + most of pylint. |
| Type-check | **mypy --strict** on `src/`, **basedpyright** as fallback for IDE | Strict typing catches the kind of polymorphic-request-body bugs §4.0's Op A–E shapes invite. |
| Tests | **pytest + pytest-asyncio + hypothesis + Schemathesis** | hypothesis for property tests over Op A–E request shapes; Schemathesis to fuzz the live API against the published OpenAPI (catches drift between spec-derived OpenAPI and implementation behavior). |
| Logging | **structlog**, JSON in prod; explicit key-redaction set per [ADR-0002](0002-argus-alignment-and-auth.md) (case-insensitive exact match, recurse 5 levels, replace with `[REDACTED]`) | Same field shape across both services makes a single log aggregator viable. |
| Observability | **OpenTelemetry** (traces + metrics) | One span per pipeline stage (§4.1) is the minimum useful granularity for debugging long Runs. |
| State DB | **`postgis/postgis:16-3.4`** (PostGIS extension mandatory — see [ADR-0002](0002-argus-alignment-and-auth.md)) | Same image as argus; well-understood operationally; SKIP LOCKED is the queue. |
| Object store | **Filesystem (dev/local)** ↔ **S3/Azure Blob (prod)** behind a `StorageProvider` interface, switched by `STORAGE_PROVIDER` env var | Pattern copied verbatim from argus-flight-center's `src/lib/storage.ts`. Local-mode parity per §8.5. |
| Auth | **`Authorization: Bearer <api-key>`**, hashed at rest with argon2id, prefix-indexed — see [ADR-0002](0002-argus-alignment-and-auth.md). Per-operation scope checks (§8.4). | Single-tenant compute API; OAuth/JWT/cookie session is the wrong model; bearer wire format matches argus. |
| HTTP outbound (webhook delivery, plugin fetch) | **httpx** with explicit timeouts + tenacity for retries | No bare `fetch`-style "fail silent and return null" calls. |
| Plugin loading | **Python entry points** (`importlib.metadata`) for both propagation models and link-type plugins | Stdlib mechanism, no custom plugin loader to maintain. Sandboxing deferred to a future ADR. |
| Containerization | Multi-stage Dockerfile (slim base, no compile toolchain in runtime layer); Docker Compose for dev with services: api, worker, postgres, minio | Mirrors argus's compose layout where the services overlap (postgres, minio). |
| CI | GitHub Actions: lint → typecheck → unit → integration → Schemathesis-fuzz-against-OpenAPI | Integration tests use real Postgres + MinIO via service containers, matching argus's pattern. |
| Client SDK | **`openapi-typescript` + `openapi-fetch`** generates a typed TS client; the generated artifact is committed directly into argus-flight-center (no registry, no package publication) | Hand-written clients drift; generated clients track the OpenAPI. Vendoring the artifact rather than publishing avoids running a private registry for a two-service ecosystem. |

## Alternatives considered

### Mirror argus-flight-center (TypeScript + Next.js App Router + Prisma)
Rejected. Five disqualifiers:

1. **No comparable numerical/raster ecosystem.** Argus offloads its geo work to PostGIS and Turf.js (vector geometry). RfAnalyzer is raster-first.
2. **Code-first OpenAPI inverts our contract.** Argus's `scripts/generate-openapi.ts` walks route files to *produce* the OpenAPI. RfAnalyzer's working agreement is the opposite.
3. **App Router fights long-running work.** Argus already had to bolt a custom `server.ts` onto Next.js for Socket.io. Adding multi-hour voxel jobs to the same model multiplies that friction.
4. **Hand-rolled validation does not scale to polymorphic Op A–E shapes.** Argus has no schema library; `if (!body.x) return 400` per route. Our request bodies are too structured for that.
5. **Reproducibility model.** Argus's "fire-and-forget `void process().catch(log)`" is the antithesis of `inputs_resolved`-snapshotted Runs.

### Rust (axum + serde) or Go (Echo / Fiber)
Rejected. Faster runtimes, but:

- **Geo / RF library gap.** Rust's georust ecosystem and Go's geo libraries are smaller than Python's; ITU-R reference implementations exist in Python and MATLAB, not in either.
- **Iteration speed > runtime speed at this stage.** The hot path inside propagation models can be moved to numba or a native extension later; the API and pipeline glue benefit more from a fast feedback loop.
- **Single-operator team.** No second engineer to absorb a less familiar stack.

### Same Python framework, different web framework (Litestar, Starlette + custom, Flask)
Rejected. FastAPI is the de-facto standard for pydantic-backed Python APIs; the others either give up OpenAPI emission, give up async ergonomics, or are smaller communities. None of those costs are worth paying.

## Trade-offs

- **We accept Python's runtime overhead** in exchange for ecosystem fit and iteration speed. Hot loops in propagation models can be optimized with numba/Cython/native extensions later if profiling demands it; the pipeline glue (§4.1) does not need it.
- **We accept that the TS client argus consumes is generated, not handwritten.** Generated clients are uglier but track the OpenAPI; a handwritten client would silently drift.
- **We accept Postgres SKIP LOCKED as the queue** rather than Redis Streams or RabbitMQ. SKIP LOCKED is well-understood, requires no extra infrastructure, and the Run record (§3.3) carries enough state to act as both work item and audit log. If/when a second worker tier is needed (e.g., a fast lane for sync-mode Op A), revisit.
- **We accept that PostGIS is deferred.** No endpoint in v1 demands spatial queries; storing Site geometry as JSONB is sufficient. Adding PostGIS later is a single migration.

## Consequences

**Easier:**
- Spec-driven workflow continues unchanged; pydantic models are the implementation projection of the spec.
- ITU-R model implementations (P.1812, P.528, P.530, etc.) can be ported directly from existing Python references.
- Argus consumes a generated client — no contract drift between the two services.
- Logs and traces from both services share a field shape and can flow into one aggregator.

**Harder:**
- Two languages on Larry's workstation (Python here, TypeScript in argus). Mitigated by sharp boundary: one OpenAPI artifact, one generated client.
- CPython's GIL means worker concurrency is process-based, not thread-based. Workers are already separate processes, so this is a non-issue for run execution; matters only for per-process I/O concurrency, which is async anyway.
- Pluggable models live in-process (entry points). A misbehaving third-party plugin can crash a worker. Sandboxing is a known gap in the spec (see audit) and will be addressed in a follow-up ADR before plugin onboarding opens to outside contributors.

## Action items

1. [ ] Add `pyproject.toml` with the dependency set above (including `rfc8785` for JCS canonicalization); pin the toolchain in `.python-version`.
2. [ ] Stand up the repo skeleton: `src/rfanalyzer/{api,pipeline,models,plugins,storage,auth}/`, `tests/`, `docker/`.
3. [ ] Wire CI: lint → typecheck → unit → integration → Schemathesis fuzz.
4. [ ] Create the `StorageProvider` interface with filesystem and S3 implementations (mirror argus's `src/lib/storage.ts` shape).
5. [ ] Set up the OpenAPI emission check: pydantic-emitted OpenAPI is diffed against the spec-derived OpenAPI; CI fails on divergence.
6. [ ] Generate the first TS client from the published OpenAPI and vendor it into argus-flight-center (commit the generated source under `argus-flight-center/src/lib/rfanalyzer-client/`); no registry, no package publication.
7. [ ] Plugin sandboxing ADR — open once the first third-party plugin candidate appears (note: the auth, Postgres-image, and logging-redaction items previously listed here are answered by [ADR-0002](0002-argus-alignment-and-auth.md); see ADR-0002's own action items for the implementation tasks).
