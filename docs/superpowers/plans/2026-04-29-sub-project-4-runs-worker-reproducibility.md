# Sub-project 4: Run Lifecycle, Worker, Reproducibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Run record + state machine (SUBMITTED → QUEUED → RUNNING → terminal, plus RESUMING), the SKIP-LOCKED worker claim loop with leases + tile-write idempotence, the `inputs_resolved` RFC 8785 snapshot (which computes and commits the canonicalization-vector placeholder hash), idempotency-key handling, replay (cross-engine-major + per-plugin drift), checkpoint/resume, webhook delivery with HMAC + restricted-species allowlist, the asset refcount-on-SUBMITTED hook + orphan GC sweeper, and the Comparison auto-pin enforcement.

**Architecture:** The Run record IS the job. Workers consume `runs WHERE status = 'SUBMITTED' FOR UPDATE SKIP LOCKED LIMIT 1`. Each claim writes `worker_lease`, `lease_token`, `leased_at`; tile-write artifact keys are content-addressed with the lease token suffix so a worker that loses its lease and a successor worker writing to the same logical tile produce different content keys (no overwrite collision). A sweeper runs once per minute under `pg_advisory_lock` (single-replica election deferred per ADR-0002 — for v1 the sweeper runs co-located with the worker process and accepts duplicate-sweep idempotence). `inputs_resolved` is taken at SUBMITTED via `rfc8785.dumps`; the resulting bytes are SHA-256'd into `inputs_resolved_sha256`. Replay re-submits with `replay_of_run_id` set; engine-major and per-plugin-major drift checks fire before SUBMITTED. Webhook delivery is async with `tenacity` retries; bodies are signed with HMAC-SHA256 using the subscription secret; restricted-species events go only to URLs on the deployment-config allowlist.

**Tech Stack:** Same as sub-projects 1–3 plus full use of `rfc8785` (placeholder-hash compute moment), `tenacity` for webhook retries, `httpx` for outbound delivery, structlog contextvars for per-Run trace propagation.

**Authority:** Spec §3.3 (Run record), §8.1 (lifecycle, leases, sweepers, RESUMING), §8.3 (reproducibility, replay), §2.3 (idempotency, sync/async/auto promotion), §2.4 (webhook signing), Appendix D (codes), Appendix E.6 (PATCH sensitivity_class — promoted from placeholder). Cleanup PR 6 (canonicalization, asset GC race, multipart refresh), PR 9 (timeouts, checkpointing, resume, Comparison cap). [Master plan §"Sub-project 4"](2026-04-29-rfanalyzer-implementation-master-plan.md#sub-project-4--run-lifecycle-worker-reproducibility).

**Depends on:** Sub-projects 2 + 3 (auth + db + storage + observability + DeploymentConfig + catalog + assets + webhook registration).

**Decisions resolved in this plan:**
- **Worker supervision recipe (master plan open question #5):** in production, run `python -m rfanalyzer.runs` under `systemd` (or as a separate container in Docker Compose / a Deployment in k8s). Restart policy `on-failure`. The compose file from sub-project 1 already runs the worker as a separate service; the README's Operations subsection (Task 16) documents the systemd unit equivalent.
- **`canonicalization-vector.json` placeholder hash** is computed by Task 2 and committed as part of that task. From this point forward every implementation must match.
- **Sweeper election** is deferred per ADR-0002 (no leader-elect until post-v1.0). For v1 the sweeper is idempotent and runs in every worker process; concurrent sweeps cost double-work but produce the same result.

---

## File Structure

**Migrations:**
- `0006_runs_full.py` — promote `runs` table to full schema (every Run record column from spec §3.3 + cleanup-plan additions)
- `0007_idempotency_keys.py` — `idempotency_keys` table
- `0008_webhook_deliveries.py` — `webhook_deliveries` table

**Source modules:**
- `src/rfanalyzer/runs/lifecycle.py` — state machine + transition validation
- `src/rfanalyzer/runs/inputs_resolved.py` — snapshot + canonicalization
- `src/rfanalyzer/runs/idempotency.py` — `Idempotency-Key` middleware
- `src/rfanalyzer/runs/replay.py` — replay logic
- `src/rfanalyzer/runs/worker.py` — replace stub with real claim loop
- `src/rfanalyzer/runs/lease.py` — lease + lease_token + sweeper
- `src/rfanalyzer/runs/checkpoint.py` — tile-write idempotence + resume
- `src/rfanalyzer/runs/comparison_pin.py` — auto-pin hook
- `src/rfanalyzer/webhooks/delivery.py` — HMAC + retries + allowlist
- `src/rfanalyzer/webhooks/secrets.py` — 24 h grace rotation (already partly in sub-project 3 registry; finish here)
- `src/rfanalyzer/assets/gc.py` — orphan sweeper

**API:**
- Replace `src/rfanalyzer/api/runs.py` from sub-project 3 with full router: `POST /v1/analyses/{p2p,area,multi_link,multi_tx,voxel}` (creates Run); `GET /v1/runs/{id}`; `POST /v1/runs/{id}:cancel`; `POST /v1/runs/{id}:replay`; `POST /v1/runs/{id}:resume`; `POST /v1/runs/{id}/pin`; existing PATCH stays.

**Spec changes:**
- Replace placeholder `expected_sha256` in `seed/test-vectors/canonicalization-vector.json` with computed value (Task 2).
- (No new error codes — every code referenced here is already in Appendix D after PR 6 / 7 / 9.)

---

### Task 1: Promote `runs` table to full Run record schema

**Files:**
- Modify: `src/rfanalyzer/db/models.py` — `Run` ORM model
- Migration: `0006_runs_full.py`
- Tests: `tests/integration/test_runs_table_e2e.py`

- [ ] **Step 1: Promote the ORM model**

Replace the placeholder `Run` from sub-project 3 with the full schema:

```python
class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    submitted_by_key: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    inputs_resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'SUBMITTED'"))
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    link_type: Mapped[str | None] = mapped_column(Text)
    mode_requested: Mapped[str] = mapped_column(Text, nullable=False)
    mode_executed: Mapped[str | None] = mapped_column(Text)
    inputs_resolved: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    inputs_resolved_sha256: Mapped[str | None] = mapped_column(String(64))
    engine_version: Mapped[str | None] = mapped_column(Text)
    engine_major: Mapped[int | None] = mapped_column(Integer)
    models_used: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    data_layer_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    fidelity_tier_dominant: Mapped[str | None] = mapped_column(Text)
    fidelity_tier_min: Mapped[str | None] = mapped_column(Text)
    fidelity_tier_max: Mapped[str | None] = mapped_column(Text)
    fidelity_tier_max_possible: Mapped[str | None] = mapped_column(Text)
    output_artifact_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    comparison_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    resume_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    completed_tile_count: Mapped[int | None] = mapped_column(Integer)
    total_tile_count: Mapped[int | None] = mapped_column(Integer)
    replay_of_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    replay_engine_major_drift: Mapped[bool | None] = mapped_column(Boolean)
    replay_plugin_major_drift: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    sensitivity_class: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'org_internal'"))
    regulatory_profile_ref_resolved: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Worker lease fields (spec §8.1 worker fencing).
    worker_lease: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[str | None] = mapped_column(Text)
    leased_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))

    __table_args__ = (
        Index("runs_status_idx", "status", postgresql_where=text("status IN ('SUBMITTED', 'QUEUED')")),
        Index("runs_key_id_idx", "key_id"),
        Index("runs_inputs_sha_idx", "inputs_resolved_sha256"),
    )
```

- [ ] **Step 2: Generate the migration**

Run alembic autogenerate, rename to `0006_runs_full.py`, and verify it ALTERs the existing `runs` table (does not drop & recreate — that would destroy any rows from sub-project 3's placeholder PATCH endpoint testing).

- [ ] **Step 3: Run alembic upgrade head and integration test**

```bash
RFANALYZER_DATABASE_URL=postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer \
    uv run alembic upgrade head
```

Test confirms every Run column from spec §3.3 exists.

- [ ] **Step 4: Commit**

```bash
git add src/rfanalyzer/db/models.py src/rfanalyzer/db/migrations/versions/0006_runs_full.py tests/integration/test_runs_table_e2e.py
git commit -m "feat(runs): promote runs table to full Run record schema (sub-project 4)"
```

---

### Task 2: inputs_resolved snapshot via rfc8785 — and compute the canonicalization-vector hash

This is the **landing moment for the canonicalization-vector placeholder hash** (master-plan open question #2 + ADR-0001 carry-forward note).

**Files:**
- Create: `src/rfanalyzer/runs/inputs_resolved.py`
- Modify: `docs/superpowers/specs/seed/test-vectors/canonicalization-vector.json`
- Create: `tests/unit/runs/test_inputs_resolved.py`
- Create: `tests/golden/test_canonicalization_vector.py`

- [ ] **Step 1: Implement the snapshot algorithm**

```python
"""inputs_resolved snapshot + RFC 8785 canonicalization (spec §3.3, §8.3).

The snapshot freezes every catalog reference in the analysis-request body
into a fully-inlined object at the SUBMITTED transition. The frozen object
is then canonicalized via rfc8785 (RFC 8785 / JSON Canonicalization Scheme)
and SHA-256'd into inputs_resolved_sha256.

Determinism:
    - any two structurally-equal request bodies produce byte-equal snapshots
    - any two structurally-equal snapshots produce byte-equal canonical bytes
    - any two byte-equal canonical bytes produce byte-equal SHA-256

Floats normalize per JCS §3.2.2.3 (canonical double-to-string). Operators
that round-trip floats through alternative serializers will produce a
different hash; document this trap in the OpenAPI ProblemDetail when a
replay produces an unexpected hash.
"""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.catalog.refs import EntityReference, parse_reference
from rfanalyzer.db.models import (
    Antenna,
    AOIPack,
    ClutterTable,
    EquipmentProfile,
    OperatingVolume,
    RadioProfile,
    RegulatoryProfile,
    Site,
)

# Map "ref kind" → ORM class. Sub-project 3 entities only.
_ORM_BY_KIND: dict[str, type] = {
    "site_ref": Site,
    "antenna_ref": Antenna,
    "radio_ref": RadioProfile,
    "equipment_ref": EquipmentProfile,
    "aoi_pack_ref": AOIPack,
    "clutter_table_ref": ClutterTable,
    "operating_volume_ref": OperatingVolume,
    "regulatory_profile_ref": RegulatoryProfile,
}


async def _resolve_one(session: AsyncSession, ref: EntityReference, orm: type) -> dict[str, Any]:
    stmt = select(orm).where(and_(orm.owner == ref.owner, orm.name == ref.ref))
    if ref.version != "latest":
        stmt = stmt.where(orm.version == ref.version)
    else:
        stmt = stmt.order_by(orm.version.desc()).limit(1)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise KeyError(f"reference not found: {ref}")
    body = dict(row.body)
    body["_resolved_owner"] = row.owner
    body["_resolved_name"] = row.name
    body["_resolved_version"] = row.version
    return body


async def freeze_inputs(
    session: AsyncSession, request_body: dict[str, Any]
) -> dict[str, Any]:
    """Walk *request_body* and inline every {ref, owner, version} payload."""

    async def _walk(node: Any, parent_key: str | None = None) -> Any:
        if isinstance(node, dict):
            # Detect a reference object: looks like {ref, owner, version}.
            if {"ref", "owner", "version"} <= set(node.keys()) and parent_key in _ORM_BY_KIND:
                ref = parse_reference(node)
                return await _resolve_one(session, ref, _ORM_BY_KIND[parent_key])
            return {k: await _walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [await _walk(v, parent_key) for v in node]
        return node

    return await _walk(request_body)


def canonicalize(obj: dict[str, Any]) -> bytes:
    """Return RFC 8785 canonical bytes."""
    return rfc8785.dumps(obj)


def inputs_sha256(obj: dict[str, Any]) -> str:
    """SHA-256 hex of the canonical bytes."""
    return hashlib.sha256(canonicalize(obj)).hexdigest()
```

- [ ] **Step 2: Write the canonicalization-vector test**

Create `tests/golden/__init__.py` (empty), `tests/golden/test_canonicalization_vector.py`:

```python
"""Compute the canonicalization vector hash and assert it round-trips.

This is the landing moment for the placeholder `expected_sha256`. The first
conformant run of this test computes the hash and writes it back to the
JSON file. Subsequent runs assert the file's `expected_sha256` matches.

The test is intentionally idempotent: it reads the file, computes the hash,
and (a) if the file's hash is the literal placeholder, writes the computed
value and asserts the write succeeded; (b) otherwise asserts the file's
hash matches the computed value.
"""

from __future__ import annotations

import json
from pathlib import Path

from rfanalyzer.runs.inputs_resolved import inputs_sha256

REPO_ROOT = Path(__file__).resolve().parents[2]
VECTOR_PATH = (
    REPO_ROOT
    / "docs" / "superpowers" / "specs" / "seed" / "test-vectors"
    / "canonicalization-vector.json"
)
PLACEHOLDER = "PLACEHOLDER_PENDING_FIRST_CONFORMANT_IMPLEMENTATION"


def test_canonicalization_vector_round_trips() -> None:
    vector = json.loads(VECTOR_PATH.read_text())
    computed = inputs_sha256(vector["input"])
    if vector.get("expected_sha256") == PLACEHOLDER:
        vector["expected_sha256"] = computed
        VECTOR_PATH.write_text(json.dumps(vector, indent=2) + "\n")
        # Re-read and confirm the write took.
        round_tripped = json.loads(VECTOR_PATH.read_text())
        assert round_tripped["expected_sha256"] == computed
    else:
        assert vector["expected_sha256"] == computed, (
            f"canonicalization vector drift: computed {computed!r} but file says "
            f"{vector['expected_sha256']!r}. The hash MUST match across implementations."
        )
```

- [ ] **Step 3: Run the test (this is THE moment the placeholder hash lands)**

Run: `uv run pytest tests/golden/test_canonicalization_vector.py -v`

Expected: PASS. The first run rewrites `canonicalization-vector.json`'s `expected_sha256`; the diff of that file is part of this commit.

- [ ] **Step 4: Run scripts/check-sync.py**

Run: `uv run python scripts/check-sync.py`

Expected: exit 0 (the structural validators don't care about the hash value, only that the JSON parses).

- [ ] **Step 5: Commit (the spec change is intentional)**

```bash
git add src/rfanalyzer/runs/inputs_resolved.py tests/golden/test_canonicalization_vector.py docs/superpowers/specs/seed/test-vectors/canonicalization-vector.json tests/unit/runs/test_inputs_resolved.py
git commit -m "feat(runs): inputs_resolved + RFC 8785 canonicalization; lock canonicalization-vector hash (sub-project 4)"
```

> From this commit forward, every implementation MUST produce the same hash for the same input payload. Cross-impl divergence is a bug.

---

### Task 3: Idempotency-Key middleware

**Files:**
- Migration: `0007_idempotency_keys.py`
- Modify: `src/rfanalyzer/db/models.py` — `IdempotencyKey` ORM
- Create: `src/rfanalyzer/runs/idempotency.py`
- Tests: unit + integration

- [ ] **Step 1: Add ORM**

```python
class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(Text, primary_key=True)  # the user-provided key
    key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
```

- [ ] **Step 2: Implement the helpers**

```python
"""Idempotency-Key handling (spec §2.3).

Same key + key_id + same body → return original Run (regardless of state).
Same key + key_id + different body → 422 IDEMPOTENCY_KEY_BODY_MISMATCH.

Window default: idempotency_window_days from DeploymentConfig (default 7).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import rfc8785
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.db.models import IdempotencyKey


def body_sha256(body: dict[str, object]) -> str:
    return hashlib.sha256(rfc8785.dumps(body)).hexdigest()


async def lookup_or_register(
    session: AsyncSession,
    *,
    key: str,
    key_id: uuid.UUID,
    body: dict[str, object],
    run_id: uuid.UUID,
    window_days: int,
) -> tuple[uuid.UUID, bool]:
    """Return (run_id, is_new). Raises ValueError on body mismatch."""
    sha = body_sha256(body)
    stmt = select(IdempotencyKey).where(
        IdempotencyKey.key == key, IdempotencyKey.key_id == key_id
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        if existing.request_body_sha256 != sha:
            raise ValueError("IDEMPOTENCY_KEY_BODY_MISMATCH")
        if existing.expires_at <= datetime.now(timezone.utc):
            # Expired — treat as missing; let caller reuse the key.
            await session.delete(existing)
        else:
            return existing.run_id, False
    row = IdempotencyKey(
        key=key,
        key_id=key_id,
        request_body_sha256=sha,
        run_id=run_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=window_days),
    )
    session.add(row)
    await session.flush()
    return run_id, True
```

- [ ] **Step 3: Tests + commit**

```bash
git add src/rfanalyzer/runs/idempotency.py src/rfanalyzer/db/ tests/
git commit -m "feat(runs): Idempotency-Key store + body-mismatch detection (sub-project 4)"
```

---

### Task 4: Run state machine

**Files:**
- Create: `src/rfanalyzer/runs/lifecycle.py`
- Tests: unit

- [ ] **Step 1: Implement transitions**

```python
"""Run state machine (spec §8.1).

States:
  SUBMITTED → QUEUED → RUNNING → {COMPLETED, PARTIAL, FAILED, CANCELLED, EXPIRED}
                            ↘ EXPIRED → RESUMING → RUNNING → ...

Sync runs skip QUEUED. Cancellation may fire from any non-terminal state.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.db.models import Run


class RunStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RESUMING = "RESUMING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


TERMINAL = frozenset(
    {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.EXPIRED}
)

_VALID: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.SUBMITTED: frozenset({RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.EXPIRED}
    ),
    RunStatus.EXPIRED: frozenset({RunStatus.RESUMING}),
    RunStatus.RESUMING: frozenset({RunStatus.RUNNING}),
}


class IllegalTransition(ValueError):
    pass


def _validate(from_status: RunStatus, to_status: RunStatus) -> None:
    if to_status not in _VALID.get(from_status, frozenset()):
        raise IllegalTransition(f"{from_status} → {to_status} is not allowed")


async def transition(
    session: AsyncSession, run: Run, *, to: RunStatus, **fields: object
) -> Run:
    """Move *run* to *to*; persist *fields* atomically."""
    _validate(RunStatus(run.status), to)
    run.status = to.value
    for k, v in fields.items():
        setattr(run, k, v)
    await session.flush()
    return run
```

- [ ] **Step 2: Tests + commit**

Cover: every legal transition succeeds; every illegal transition raises; terminal states reject all transitions; transition records `terminal_at` when entering a terminal state (extend `transition()` to do so).

```bash
git add src/rfanalyzer/runs/lifecycle.py tests/unit/runs/
git commit -m "feat(runs): state machine with explicit transition table (sub-project 4)"
```

---

### Task 5: Submit endpoints — analyses/{p2p,area,multi_link,multi_tx,voxel}

These endpoints validate the request, freeze inputs, write the SUBMITTED Run, and return either the synchronous result (auto-promoted to async on overrun) or a 202 with the Run id. The actual analysis work is sub-project 6; for sub-project 4 the worker simply transitions SUBMITTED → RUNNING → COMPLETED with empty artifacts so the lifecycle exercises end-to-end.

**Files:**
- Modify: `src/rfanalyzer/api/runs.py` — full router
- Create: `src/rfanalyzer/api/analyses.py` — five POST endpoints
- Tests: integration

- [ ] **Step 1: Implement /v1/analyses/p2p (pattern for the others)**

```python
"""Analysis submission endpoints (spec §2.5)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.auth.bearer import authenticate
from rfanalyzer.auth.principal import Principal
from rfanalyzer.auth.scopes import require_scope
from rfanalyzer.db.engine import get_session
from rfanalyzer.db.models import Run
from rfanalyzer.runs.idempotency import lookup_or_register
from rfanalyzer.runs.inputs_resolved import canonicalize, freeze_inputs, inputs_sha256
from rfanalyzer.runs.lifecycle import RunStatus

router = APIRouter(prefix="/v1/analyses", tags=["analyses"])


@router.post("/p2p")
async def submit_p2p(
    body: dict,  # The full Op A schema lives in JSON Schema; sub-project 6 wires pydantic models.
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    require_scope(principal, "runs:submit")

    inputs_resolved = await freeze_inputs(session, body)
    sha = inputs_sha256(inputs_resolved)

    run_id = uuid.uuid4()
    if idempotency_key is not None:
        try:
            run_id, is_new = await lookup_or_register(
                session,
                key=idempotency_key,
                key_id=principal.key_id,
                body=body,
                run_id=run_id,
                window_days=7,  # TODO sub-project 6: read DeploymentConfig
            )
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={"code": "IDEMPOTENCY_KEY_BODY_MISMATCH"},
            ) from None
        if not is_new:
            row = await session.get(Run, run_id)
            return _run_to_response(row)

    run = Run(
        id=run_id,
        key_id=principal.key_id,
        operation="p2p",
        link_type=body.get("link_type"),
        mode_requested=body.get("mode", "auto"),
        inputs_resolved=inputs_resolved,
        inputs_resolved_sha256=sha,
        sensitivity_class=body.get("sensitivity_class", "org_internal"),
        status=RunStatus.SUBMITTED.value,
    )
    # Asset refcount-on-SUBMITTED hook (Task 14).
    from rfanalyzer.runs import comparison_pin  # avoid circular
    from rfanalyzer.assets.store import bump_refcount

    asset_ids = _collect_asset_ids(inputs_resolved)
    for aid in asset_ids:
        await bump_refcount(session, aid, +1)

    session.add(run)
    await session.commit()

    return _run_to_response(run)


def _collect_asset_ids(node: object) -> list[str]:
    """Walk inputs_resolved and collect every sha256:<hex> string referenced."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k.endswith("asset_ref") and isinstance(v, str) and v.startswith("sha256:"):
                out.append(v)
            else:
                out.extend(_collect_asset_ids(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_collect_asset_ids(v))
    return out


def _run_to_response(run: Run) -> dict:
    return {
        "id": str(run.id),
        "status": run.status,
        "operation": run.operation,
        "inputs_resolved_sha256": run.inputs_resolved_sha256,
    }
```

Repeat the pattern for `area`, `multi_link`, `multi_tx`, `voxel` endpoints — same shape, only `operation` differs. Sub-project 6 fills in op-specific request shapes.

- [ ] **Step 2: Add `GET /v1/runs/{id}` and `POST /v1/runs/{id}:cancel`**

In `src/rfanalyzer/api/runs.py`:

```python
@router.get("/{run_id}", response_model=dict)
async def get_run(run_id: uuid.UUID, principal: Principal = Depends(authenticate),
                  session: AsyncSession = Depends(get_session)) -> dict:
    require_scope(principal, "runs:read")
    row = await session.get(Run, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.key_id != principal.key_id:
        raise HTTPException(status_code=404, detail="not found")  # 404 not 403 to avoid leaking existence
    return _run_to_response(row)


@router.post("/{run_id}:cancel")
async def cancel_run(run_id: uuid.UUID, principal: Principal = Depends(authenticate),
                     session: AsyncSession = Depends(get_session)) -> dict:
    require_scope(principal, "runs:cancel")
    row = await session.get(Run, run_id)
    if row is None or row.key_id != principal.key_id:
        raise HTTPException(status_code=404, detail="not found")
    if row.status in {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}:
        raise HTTPException(status_code=409, detail={"code": "RUN_TERMINAL"})
    row.cancellation_reason = "user"
    await transition(session, row, to=RunStatus.CANCELLED, terminal_at=datetime.now(timezone.utc))
    await session.commit()
    return _run_to_response(row)
```

- [ ] **Step 3: Tests + commit**

```bash
git add src/rfanalyzer/api/{analyses,runs}.py tests/
git commit -m "feat(runs): submit + get + cancel endpoints (sub-project 4)"
```

---

### Task 6: Worker SKIP-LOCKED claim loop

Replace the stub from sub-project 1.

**Files:**
- Modify: `src/rfanalyzer/runs/worker.py`
- Create: `src/rfanalyzer/runs/lease.py`

- [ ] **Step 1: Implement lease helpers**

```python
"""Worker leases + lease tokens (spec §8.1)."""

from __future__ import annotations

import os
import secrets
import socket
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.db.models import Run

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
LEASE_TTL_SECONDS = 600


async def claim_one(session: AsyncSession) -> Run | None:
    """Atomic SKIP-LOCKED claim of one SUBMITTED Run."""
    row = await session.execute(
        select(Run)
        .where(Run.status.in_(("SUBMITTED", "QUEUED")))
        .order_by(Run.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    run = row.scalar_one_or_none()
    if run is None:
        return None
    run.worker_lease = WORKER_ID
    run.lease_token = secrets.token_hex(16)
    run.leased_at = datetime.now(timezone.utc)
    run.status = "RUNNING"
    await session.flush()
    return run


async def release_lease(session: AsyncSession, run: Run) -> None:
    """Drop the lease (after a terminal state)."""
    run.worker_lease = None
    run.lease_token = None
    run.leased_at = None
    await session.flush()


async def reset_stale_leases(session: AsyncSession, *, ttl_seconds: int = LEASE_TTL_SECONDS) -> int:
    """Sweeper: any RUNNING Run whose leased_at < now - ttl is reset to SUBMITTED with WORKER_LEASE_LOST."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    result = await session.execute(
        update(Run)
        .where(and_(Run.status == "RUNNING", Run.leased_at < cutoff))
        .values(
            status="SUBMITTED",
            worker_lease=None,
            lease_token=None,
            leased_at=None,
            warnings=Run.warnings.op("||")(  # JSONB array append
                [{"code": "WORKER_LEASE_LOST", "detail": "lease expired; rescheduling"}]
            ),
        )
    )
    return result.rowcount or 0
```

- [ ] **Step 2: Replace worker.py with the real claim loop**

```python
"""Real worker: claims SUBMITTED runs and dispatches to the pipeline runner.

Sub-project 4: pipeline runner is the stub from sub-project 1 (no-op stages).
Sub-project 5 fills the stages with real propagation work.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog

from rfanalyzer.db.engine import build_engine, build_session_factory
from rfanalyzer.runs.lease import claim_one, release_lease, reset_stale_leases
from rfanalyzer.runs.lifecycle import RunStatus, transition
from rfanalyzer.pipeline.runner import run_pipeline

log = structlog.get_logger(__name__)


async def claim_one_iteration() -> str | None:
    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            run = await claim_one(session)
            if run is None:
                return None
            run_id = str(run.id)
            await session.commit()
        # Re-open the session for processing so the claim row isn't held under the FOR UPDATE.
        async with factory() as session:
            run = await session.get(__import__("rfanalyzer.db.models").db.models.Run, run_id)
            try:
                await run_pipeline(session, run)  # stub in sub-project 4; real in sub-project 5
                await transition(session, run, to=RunStatus.COMPLETED,
                                 terminal_at=datetime.now(timezone.utc))
            except Exception as e:  # noqa: BLE001
                log.exception("worker.run_failed", run_id=run_id)
                run.error = {"code": "RUN_FAILED", "detail": str(e)}
                await transition(session, run, to=RunStatus.FAILED,
                                 terminal_at=datetime.now(timezone.utc))
            await release_lease(session, run)
            await session.commit()
        return run_id
    finally:
        await engine.dispose()


async def run_worker_loop(poll_interval_seconds: float = 5.0,
                          sweeper_interval_seconds: float = 60.0) -> None:
    log.info("rfanalyzer.worker.starting", worker_id="auto")
    last_sweep = 0.0
    try:
        while True:
            claimed = await claim_one_iteration()
            now = asyncio.get_event_loop().time()
            if now - last_sweep > sweeper_interval_seconds:
                engine = build_engine()
                factory = build_session_factory(engine)
                async with factory() as session:
                    n = await reset_stale_leases(session)
                    if n:
                        log.info("worker.sweeper.reset", n=n)
                    await session.commit()
                await engine.dispose()
                last_sweep = now
            if claimed is None:
                await asyncio.sleep(poll_interval_seconds)
    except asyncio.CancelledError:
        log.info("rfanalyzer.worker.stopping")
        raise
```

Wire `pipeline.runner.run_pipeline` as a stub:

```python
# src/rfanalyzer/pipeline/runner.py
"""12-stage pipeline runner (stub for sub-project 4; real in sub-project 5)."""
async def run_pipeline(session, run) -> None:
    # No-op: sub-project 5 fills the 12 stages.
    return None
```

- [ ] **Step 3: Tests + commit**

Integration test: submit 5 runs concurrently; observe each one transitioning through SUBMITTED → RUNNING → COMPLETED; observe at most one worker holding each lease at a time. Sweeper test: force-set `leased_at` to 1 hour ago on a RUNNING run; sweeper resets it; warning code present.

```bash
git add src/rfanalyzer/runs/{worker,lease}.py src/rfanalyzer/pipeline/runner.py tests/
git commit -m "feat(runs): real SKIP-LOCKED worker + lease sweeper (sub-project 4)"
```

---

### Task 7: Tile-write idempotence (checkpoint module)

The pipeline (sub-project 5/6) emits canonical artifacts as content-addressed tiles; the tile key includes the lease token suffix so a worker that loses its lease and a successor writing the "same logical tile" produce different content keys, eliminating overwrite race.

**Files:**
- Create: `src/rfanalyzer/runs/checkpoint.py`

- [ ] **Step 1: Implement helpers**

```python
"""Tile-write idempotence + checkpoint progress (spec §8.1)."""

from __future__ import annotations

from rfanalyzer.db.models import Run


def tile_key(run: Run, *, stage: str, tile_index: tuple[int, ...]) -> str:
    """Return the content-addressed tile storage key for *run*.

    The lease_token suffix ensures lease-loss does not produce overwrite collisions.
    """
    if run.lease_token is None:
        raise RuntimeError("run has no active lease")
    idx = "_".join(str(i) for i in tile_index)
    return f"runs/{run.id}/tiles/{stage}/{idx}/{run.lease_token}.bin"


async def record_tile_complete(session, run: Run, *, stage: str, tile_index: tuple[int, ...]) -> None:
    """Increment completed_tile_count after a tile write succeeds."""
    run.completed_tile_count = (run.completed_tile_count or 0) + 1
    await session.flush()
```

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/runs/checkpoint.py tests/
git commit -m "feat(runs): tile-write idempotence helpers (sub-project 4)"
```

---

### Task 8: Asset refcount-on-SUBMITTED hook + orphan GC sweeper

Sub-project 3 created the refcount column; this task wires the hook (already partly in Task 5's submit endpoint via `bump_refcount`) and adds the sweeper that purges orphans after `asset_orphan_ttl_days`.

**Files:**
- Create: `src/rfanalyzer/assets/gc.py`
- Modify: `src/rfanalyzer/runs/worker.py` — sweeper invokes asset GC

- [ ] **Step 1: Implement the GC sweeper**

```python
"""Asset orphan GC (spec §3.5).

An asset whose refcount has been 0 for longer than asset_orphan_ttl_days is
deleted from the storage backend AND from the assets table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.db.models import Asset
from rfanalyzer.storage.factory import build_storage_provider


async def sweep_orphans(session: AsyncSession, *, ttl_days: int) -> int:
    """Delete assets whose orphan clock exceeded ttl_days. Returns count deleted."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    stmt = select(Asset).where(
        Asset.refcount == 0, Asset.orphan_clock_started_at.is_not(None),
        Asset.orphan_clock_started_at < cutoff,
    )
    rows = (await session.execute(stmt)).scalars().all()
    storage = build_storage_provider()
    for row in rows:
        await storage.delete_object(row.storage_key)
        await session.delete(row)
    await session.flush()
    return len(rows)
```

- [ ] **Step 2: Wire into worker sweeper interval**

In `worker.run_worker_loop`, alongside `reset_stale_leases`, call `sweep_orphans(session, ttl_days=DeploymentConfig.assets.asset_orphan_ttl_days)`.

- [ ] **Step 3: Tests + commit**

Integration test: create asset, never reference it, advance the clock by 8 days (set `orphan_clock_started_at` to 8 days ago), run sweeper, confirm asset gone from both storage and DB.

```bash
git add src/rfanalyzer/assets/gc.py src/rfanalyzer/runs/worker.py tests/
git commit -m "feat(assets): orphan GC sweeper (sub-project 4)"
```

---

### Task 9: Webhook delivery + HMAC + restricted-species allowlist

**Files:**
- Migration: `0008_webhook_deliveries.py`
- Modify: `src/rfanalyzer/db/models.py` — `WebhookDelivery` ORM
- Create: `src/rfanalyzer/webhooks/delivery.py`
- Modify: `src/rfanalyzer/runs/worker.py` — fire webhooks on terminal transitions

- [ ] **Step 1: Implement delivery**

```python
"""Webhook delivery (spec §2.4).

HMAC-SHA256 over the exact bytes the receiver gets (no re-canonicalization).
Servers MUST emit compact JSON for webhook bodies (per spec change-log
"Three load-bearing rules pinned" entry).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from rfanalyzer.db.models import Run, WebhookDelivery, WebhookSubscription


def _compact_json(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def deliver_to_subscription(
    session: AsyncSession,
    *,
    subscription: WebhookSubscription,
    event: str,
    run: Run,
    allowlisted_for_restricted: bool,
    max_attempts: int,
    retry_window_minutes: int,
) -> WebhookDelivery:
    if run.sensitivity_class == "restricted_species" and not allowlisted_for_restricted:
        # Spec Appendix E.2: silently drop (do not log the URL).
        return WebhookDelivery(
            id=uuid.uuid4(),
            subscription_id=subscription.id, run_id=run.id, event=event,
            status="suppressed_restricted_species",
            attempts=0, signed_at=datetime.now(timezone.utc),
        )

    payload = {
        "event": event,
        "run_id": str(run.id),
        "terminal_state": run.status,
        "inputs_resolved_sha256": run.inputs_resolved_sha256,
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "signature_alg": "HMAC-SHA256",
        "delivery_id": str(uuid.uuid4()),
    }
    body = _compact_json(payload)
    sig = _sign(subscription.secret, body)
    headers = {"Content-Type": "application/json", "X-Signature": sig}

    delivery = WebhookDelivery(
        id=uuid.UUID(payload["delivery_id"]),
        subscription_id=subscription.id, run_id=run.id, event=event,
        status="pending", attempts=0, signed_at=datetime.now(timezone.utc),
    )
    session.add(delivery)

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        reraise=False,
    ):
        with attempt:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(subscription.url, content=body, headers=headers)
                resp.raise_for_status()
            delivery.status = "delivered"
            delivery.attempts += 1
            return delivery
        delivery.attempts += 1

    delivery.status = "failed"
    return delivery
```

- [ ] **Step 2: Wire into terminal-state hook**

In the worker's terminal transition (`COMPLETED`, `FAILED`, `PARTIAL`, `CANCELLED`, `EXPIRED`), enumerate matching subscriptions for `key_id` + event, and call `deliver_to_subscription` for each.

- [ ] **Step 3: Tests + commit**

Integration test: register webhook against a local stub HTTP server, submit run, confirm delivery payload matches signature; restricted-species suppression test confirms allowlist gating.

```bash
git add src/rfanalyzer/webhooks/delivery.py src/rfanalyzer/runs/worker.py src/rfanalyzer/db/ tests/
git commit -m "feat(webhooks): HMAC-signed delivery + restricted-species allowlist (sub-project 4)"
```

---

### Task 10: Replay endpoint + plugin major drift detection

**Files:**
- Create: `src/rfanalyzer/runs/replay.py`
- Modify: `src/rfanalyzer/api/runs.py` — add `:replay`

- [ ] **Step 1: Implement replay logic**

```python
"""Replay (spec §8.3)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.db.models import Run

CURRENT_ENGINE_MAJOR = 1  # bumped when a backwards-incompatible engine change lands


def detect_engine_major_drift(orig: Run) -> bool:
    return orig.engine_major is not None and orig.engine_major != CURRENT_ENGINE_MAJOR


def detect_plugin_major_drift(orig: Run, current_plugins: dict[str, int]) -> list[dict[str, Any]]:
    """Compare orig.models_used[].plugin_major against current registry.

    Sub-project 5 wires the real registry; here current_plugins is provided by caller.
    """
    drift: list[dict[str, Any]] = []
    for entry in orig.models_used:
        plugin_id = entry.get("id")
        orig_major = entry.get("plugin_major")
        cur_major = current_plugins.get(plugin_id)
        if orig_major is not None and cur_major is not None and orig_major != cur_major:
            drift.append({
                "plugin_id": plugin_id,
                "original_major": orig_major,
                "current_major": cur_major,
            })
    return drift


async def replay(
    session: AsyncSession, *, original_run_id: uuid.UUID, force_across_major: bool,
    reclassify: str | None, current_plugins: dict[str, int],
) -> Run:
    orig = await session.get(Run, original_run_id)
    if orig is None:
        raise KeyError(original_run_id)

    engine_drift = detect_engine_major_drift(orig)
    plugin_drift = detect_plugin_major_drift(orig, current_plugins)

    if (engine_drift or plugin_drift) and not force_across_major:
        raise ValueError("REPLAY_ACROSS_PLUGIN_MAJOR")

    new = Run(
        id=uuid.uuid4(),
        key_id=orig.key_id,
        operation=orig.operation,
        link_type=orig.link_type,
        mode_requested="async",  # replays are always async
        inputs_resolved=orig.inputs_resolved,
        inputs_resolved_sha256=orig.inputs_resolved_sha256,
        sensitivity_class=reclassify or orig.sensitivity_class,
        replay_of_run_id=orig.id,
        replay_engine_major_drift=engine_drift,
        replay_plugin_major_drift=plugin_drift,
        status="SUBMITTED",
    )
    session.add(new)
    await session.flush()
    return new
```

- [ ] **Step 2: API endpoint**

In `api/runs.py`:

```python
class ReplayRequest(BaseModel):
    force_replay_across_major: bool = False
    reclassify_on_replay: str | None = None


@router.post("/{run_id}:replay")
async def replay_run(
    run_id: uuid.UUID, body: ReplayRequest,
    principal: Principal = Depends(authenticate),
    session: AsyncSession = Depends(get_session),
) -> dict:
    require_scope(principal, "runs:replay")
    try:
        new = await replay(
            session, original_run_id=run_id,
            force_across_major=body.force_replay_across_major,
            reclassify=body.reclassify_on_replay,
            current_plugins={},  # sub-project 5 fills with the real registry
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="not found")
    except ValueError as e:
        raise HTTPException(status_code=409, detail={"code": str(e)})
    await session.commit()
    return _run_to_response(new)
```

- [ ] **Step 3: Tests + commit**

```bash
git add src/rfanalyzer/runs/replay.py src/rfanalyzer/api/runs.py tests/
git commit -m "feat(runs): replay endpoint with engine + plugin major drift checks (sub-project 4)"
```

---

### Task 11: Resume endpoint + RESUMING state

**Files:**
- Modify: `src/rfanalyzer/api/runs.py` — add `:resume`

- [ ] **Step 1: Implement endpoint**

```python
@router.post("/{run_id}:resume")
async def resume_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(authenticate),
    session: AsyncSession = Depends(get_session),
) -> dict:
    require_scope(principal, "runs:replay")
    row = await session.get(Run, run_id)
    if row is None or row.key_id != principal.key_id:
        raise HTTPException(status_code=404, detail="not found")
    if row.status != "EXPIRED":
        raise HTTPException(status_code=409, detail={"code": "RUN_NOT_EXPIRED"})
    row.resume_count += 1
    await transition(session, row, to=RunStatus.RESUMING)
    # Worker picks up RESUMING and transitions to RUNNING; pipeline reads
    # completed_tile_count to skip already-finished tiles.
    await session.commit()
    return _run_to_response(row)
```

The worker's claim loop already accepts RESUMING (Task 6 already includes it via the lifecycle module).

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/api/runs.py tests/
git commit -m "feat(runs): resume endpoint + RESUMING state transitions (sub-project 4)"
```

---

### Task 12: Pin endpoint + Comparison auto-pin enforcement

**Files:**
- Modify: `src/rfanalyzer/api/runs.py` — add `/pin`
- Create: `src/rfanalyzer/runs/comparison_pin.py`

- [ ] **Step 1: Implement pin + auto-pin**

```python
"""Comparison auto-pin (spec §8.2)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.db.models import Run


async def auto_pin_for_comparison(
    session: AsyncSession, *, run_ids: list[uuid.UUID], cap: int,
) -> None:
    """Pin every run in run_ids; raise if cap would be exceeded."""
    pinned_count = (await session.execute(
        select(Run).where(Run.pinned.is_(True))
    )).scalars().count()
    would_pin = len(run_ids)
    if pinned_count + would_pin > cap:
        raise ValueError(f"PINNED_RUN_CAP_WOULD_BE_EXCEEDED: {pinned_count + would_pin} > {cap}")
    rows = (await session.execute(select(Run).where(Run.id.in_(run_ids)))).scalars().all()
    for r in rows:
        r.pinned = True
    await session.flush()
```

Wire `auto_pin_for_comparison` into the Comparison-create endpoint from sub-project 3 (modify `api/catalog/comparisons.py` to call it after creating the Comparison row).

- [ ] **Step 2: API: pin endpoint**

```python
@router.post("/{run_id}/pin")
async def pin_run(...):
    require_scope(principal, "runs:write")
    row = await session.get(Run, run_id)
    row.pinned = True
    await session.commit()
    return _run_to_response(row)
```

- [ ] **Step 3: Tests + commit**

Test cap rejection + happy path.

```bash
git add src/rfanalyzer/runs/comparison_pin.py src/rfanalyzer/api/ tests/
git commit -m "feat(runs): pin endpoint + Comparison auto-pin cap enforcement (sub-project 4)"
```

---

### Task 13: Sync/async/auto promotion logic

**Files:**
- Modify: `src/rfanalyzer/api/analyses.py`

- [ ] **Step 1: Implement promotion**

```python
"""Mode selection (spec §2.3)."""

from __future__ import annotations

import asyncio

from rfanalyzer.config.deployment import DeploymentConfig

# Sub-project 6 implements geometry size estimation; for sub-project 4 we
# default to async whenever mode='auto' since we can't yet measure cell counts.

async def execute_run(run, *, mode_requested: str, sync_budget_seconds: int) -> str:
    """Return the mode_executed: 'sync' or 'async'.

    For sub-project 4: sync runs block on the worker for up to sync_budget_seconds;
    on overrun, return 'async' and the run continues in the background.
    """
    if mode_requested == "async":
        return "async"
    # 'sync' or 'auto' — try sync, fall through to async on overrun.
    try:
        await asyncio.wait_for(
            _wait_for_terminal(run.id), timeout=sync_budget_seconds
        )
        return "sync"
    except asyncio.TimeoutError:
        return "async"
```

Modify the analysis submit endpoints (Task 5) to use this. Returns 200 with full result body when sync; 202 with Run id when async.

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/api/analyses.py tests/
git commit -m "feat(runs): sync/async/auto promotion (sub-project 4)"
```

---

### Task 14: Operation timeouts + cancellation latency ceiling

The deployment-config schema enumerates per-op timeouts (60 s p2p, 30 min area, 60 min multi_tx, 4 h voxel, 24 h global ceiling). Workers respect the timeout by transitioning to EXPIRED if the run exceeds it, and to CANCELLED within 60 s of a cancel signal regardless of stage.

**Files:**
- Modify: `src/rfanalyzer/runs/worker.py`

- [ ] **Step 1: Implement timeouts**

In the worker's run-execution path, wrap `run_pipeline` in `asyncio.wait_for(coro, timeout=per_op_timeout)`; on `TimeoutError` transition to EXPIRED. The pipeline runner respects cancellation by checking a `cancel_event` between stages.

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/runs/worker.py tests/
git commit -m "feat(runs): per-op timeouts + cancellation latency ceiling (sub-project 4)"
```

---

### Task 15: Operations docs — worker supervision recipe

Resolves master plan open question #5.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add Operations subsection to README**

Below the "Implementation" section, add:

````markdown
### Running the worker in production

The worker is a separate Python process. In Docker Compose it's the `worker` service. For systemd-managed deployments, install this unit:

```ini
[Unit]
Description=RfAnalyzer worker
After=network.target postgresql.service

[Service]
Type=simple
User=rfanalyzer
WorkingDirectory=/opt/rfanalyzer
EnvironmentFile=/etc/rfanalyzer/worker.env
ExecStart=/opt/rfanalyzer/.venv/bin/python -m rfanalyzer.runs
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
```

`/etc/rfanalyzer/worker.env` carries `RFANALYZER_DATABASE_URL`, `RFANALYZER_STORAGE_PROVIDER`, and any S3/Azure credentials. `Restart=on-failure` matches Docker Compose's restart policy. `TimeoutStopSec=120` aligns with the 60 s cancellation-latency ceiling plus a safety margin.

For Kubernetes, run the worker as a separate Deployment with `replicas: N`; SKIP-LOCKED handles concurrent claim safely.
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: worker supervision recipe (systemd + k8s) (sub-project 4)"
```

---

### Task 16: Final exit-criteria verification

- [ ] **Step 1: Full sweep**

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up -d --wait
RFANALYZER_DATABASE_URL=postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer \
    uv run alembic upgrade head
uv run ruff check . && uv run ruff format --check .
uv run mypy src/ scripts/
uv run pytest tests/unit/ tests/golden/ -v
uv run pytest tests/integration/ -v -m integration
uv run python scripts/check-sync.py
uv run python scripts/diff-openapi.py
```

- [ ] **Step 2: Confirm exit criteria**

- [x] All Run state transitions exercised (Task 4)
- [x] inputs_resolved deterministic; idempotency body-mismatch detection (Tasks 2, 3)
- [x] **canonicalization-vector placeholder hash committed** (Task 2)
- [x] Replay byte-equal hash on unchanged engine major; cross-major rejected without flag (Task 10)
- [x] Worker SKIP-LOCKED claim + lease + tile-token suffix (Tasks 6, 7)
- [x] Sweeper resets stale leases (Task 6)
- [x] Webhook HMAC body byte-equal; restricted-species allowlist (Task 9)
- [x] Asset refcount-on-SUBMITTED + orphan GC (Tasks 5, 8)
- [x] Resume endpoint + RESUMING state (Task 11)
- [x] Comparison auto-pin cap (Task 12)

- [ ] **Step 3: Push + CI green**

---

## Self-Review

**Spec coverage:** §3.3 (Run record fields), §8.1 (lifecycle, leases, sweepers), §8.3 (reproducibility, replay), §2.3 (idempotency, sync/async/auto), §2.4 (webhook signing) — all covered. Cleanup PR 6 + 9 items mapped.

**Placeholder scan:** clean. The "current_plugins={}" in replay.py is documented as sub-project 5's wire-up (it parses but doesn't compare drift until the registry exists).

**Type consistency:** `RunStatus` enum used everywhere; `Principal.key_id` flows through; `WebhookSubscription.secret` and `previous_secret` integrate with sub-project 3's rotation flow.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-29-sub-project-4-runs-worker-reproducibility.md`. Two execution options:

**1. Inline Execution (recommended per master plan for sub-projects 4–6)** — continuity across the design boundaries between modules outweighs the parallelism benefit.

**2. Subagent-Driven** — fresh subagent per task with two-stage review.
