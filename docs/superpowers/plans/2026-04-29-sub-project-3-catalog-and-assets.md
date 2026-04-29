# Sub-project 3: Catalog Service & Asset Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land all 10 first-class catalog entities (Site, Antenna, RadioProfile, EquipmentProfile, AOIPack, ClutterTable, OperatingVolume, MeasurementSet, Comparison, RegulatoryProfile) as pydantic models + SQLAlchemy ORM tables + CRUD endpoints with sharing, versioning, and soft-delete; ship the content-addressed asset model end-to-end (initiate → PUT direct or multipart → `:refresh_part_urls` → complete → reference-counted lifecycle); land the seed loader that boot-bootstraps `standard-profile-library.json` + bundled antenna patterns; ship webhook registration + challenge (delivery itself is sub-project 4).

**Architecture:** Each entity has a pair of files: `src/rfanalyzer/catalog/entities/<name>.py` (pydantic Inline schema mirroring the OpenAPI / JSON Schema definition) and a SQLAlchemy ORM model in `src/rfanalyzer/db/models.py`. Entities share a common base (`CatalogEntity` mixin) carrying identity (`owner`, `name`, `version`), sharing (`share`), soft-delete (`deleted_at`), and timestamps. References use `{ref: name, owner: str | "system", version: int | "latest"}`; resolution is a single function that pins `latest` to the current highest version at SUBMITTED-time. The asset model is content-addressed via `sha256:` ids, with refcount + orphan-TTL lifecycle and full multipart upload threading through an `asset_sessions` table that stores upload_id ↔ key. Webhooks register via challenge–response; new URLs receive a verification challenge and aren't delivered to until acked. The seed loader runs at first boot under a `pg_advisory_lock`-keyed bootstrap so multiple replicas converge cleanly.

**Tech Stack:** Same as sub-project 2 plus rfc8785 (already in deps; used by inputs_resolved snapshot in sub-project 4 — present here only because seed loading hashes catalog records into known-stable bytes via the same library).

**Authority:** Spec §3 (all subsections), §3.5 (assets), §3.6 (reference graph), §3.7 (regulatory semantics), §2.4 (webhooks), Appendix E.6 (PATCH sensitivity_class). [Master plan §"Sub-project 3"](2026-04-29-rfanalyzer-implementation-master-plan.md#sub-project-3--catalog-service--asset-model).

**Depends on:** Sub-project 2 (auth + storage + db engine + observability + DeploymentConfig).

**Decisions resolved in this plan:**
- **AOI Pack layer shape** is the nested form per cleanup PR 1 (`layers: { dtm, dsm, clutter, buildings }`). Verified against OpenAPI in Task 6.
- **Seed-loader idempotence** uses `pg_advisory_xact_lock(0xRFAN)` at bootstrap; second boot is a no-op when records keyed by `(owner, name)` already exist.
- **PATCH `/v1/runs/{id}` for `sensitivity_class`** ships against a placeholder Run table (id + sensitivity_class only) here; sub-project 4 promotes that table to the full Run record.
- **Webhook delivery** is **not** in this sub-plan. Registration + challenge ack is. Delivery, HMAC, allowlist, retries → sub-project 4.

---

## File Structure

**New ORM tables (one alembic migration each):**
- `0002_catalog_core.py` — entity tables: `sites`, `antennas`, `radio_profiles`, `equipment_profiles`, `aoi_packs`, `clutter_tables`, `operating_volumes`, `measurement_sets`, `comparisons`, `regulatory_profiles`. All share columns: `id`, `owner`, `name`, `version`, `share`, `deleted_at`, `created_at`, `updated_at`, `tags[]`, plus per-entity payload as `JSONB body`.
- `0003_assets.py` — `assets` (id = sha256, content_type, size_bytes, purpose, refcount, orphan_clock_started_at, created_at) + `asset_sessions` (upload_id, asset_id, key, multipart parts state).
- `0004_webhooks.py` — `webhook_subscriptions` (id, key_id, url, events, secret, secret_rotated_at, secret_grace_until, verified_at, challenge_nonce, challenge_expires_at, …) + `webhook_deliveries` (sub-project 4 fills body; we ship the table here for FK from subscription).
- `0005_runs_placeholder.py` — minimal `runs` table with `id`, `key_id`, `sensitivity_class`, `created_at`. Sub-project 4 ALTERs it to the full Run schema.

**New pydantic schemas under `src/rfanalyzer/catalog/entities/`:** one module per entity (10 files).

**New routers under `src/rfanalyzer/api/`:**
- `catalog.py` — mounts each entity's CRUD sub-router
- `assets.py` — `:initiate`, multipart, `:complete`, `:refresh_part_urls`, `:abort`
- `webhooks.py` — registration, challenge ack, list, delete
- `runs.py` (placeholder) — `PATCH /v1/runs/{id}` for sensitivity_class only
- removes `/v1/_auth-check` (sub-project 2's temporary route)

**New helpers:**
- `src/rfanalyzer/catalog/refs.py` — `{ref, owner, version}` resolution
- `src/rfanalyzer/catalog/sharing.py` — share-within-tenant rules
- `src/rfanalyzer/catalog/seed_loader.py` — boot bootstrap
- `src/rfanalyzer/assets/store.py` — refcount + lifecycle
- `src/rfanalyzer/assets/multipart.py` — upload_id ↔ key state
- `src/rfanalyzer/assets/purposes.py` — per-purpose validation
- `src/rfanalyzer/webhooks/registry.py` — registration + challenge

**Spec changes:** None. Every concept here is already in spec v3.

---

### Task 1: Catalog base infrastructure — entity mixin, refs, sharing

**Files:**
- Create: `src/rfanalyzer/catalog/refs.py`
- Create: `src/rfanalyzer/catalog/sharing.py`
- Create: `src/rfanalyzer/db/_catalog_base.py`
- Create: `tests/unit/catalog/__init__.py`
- Create: `tests/unit/catalog/test_refs.py`
- Create: `tests/unit/catalog/test_sharing.py`

- [ ] **Step 1: Write the failing tests for refs**

Create `tests/unit/catalog/__init__.py` (empty), `tests/unit/catalog/test_refs.py`:

```python
"""Tests for {ref, owner, version} resolution."""

from __future__ import annotations

import pytest

from rfanalyzer.catalog.refs import (
    EntityReference,
    ReferenceNotFound,
    parse_reference,
)


def test_parse_full_reference() -> None:
    ref = parse_reference({"ref": "wildlife-collar-vhf-large", "owner": "system", "version": 1})
    assert ref.ref == "wildlife-collar-vhf-large"
    assert ref.owner == "system"
    assert ref.version == 1


def test_parse_latest_version() -> None:
    ref = parse_reference({"ref": "rhino-aoi", "owner": "tenant", "version": "latest"})
    assert ref.version == "latest"


def test_parse_rejects_bad_version() -> None:
    with pytest.raises(ValueError):
        parse_reference({"ref": "x", "owner": "y", "version": "v1"})


def test_resolve_pins_latest_to_max(mock_repo: object) -> None:
    """resolve_reference replaces 'latest' with the current highest active version."""
    # mock_repo is a fixture defined in conftest; see step 3.
    # This test will be activated once repo fixtures land; for sub-project 3 base we
    # validate parse_reference and EntityReference; resolution exercised in entity tests.
    pytest.skip("repo fixtures land with the entity tasks")
```

Create `tests/unit/catalog/test_sharing.py`:

```python
"""Tests for share-within-tenant rules."""

from __future__ import annotations

import uuid

import pytest

from rfanalyzer.catalog.sharing import ShareViolation, can_read, can_write

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


def test_owner_can_read_private_entry() -> None:
    assert can_read(viewer_tenant=TENANT_A, owner_tenant=TENANT_A, share="private")


def test_other_tenant_cannot_read_private_entry() -> None:
    assert not can_read(viewer_tenant=TENANT_B, owner_tenant=TENANT_A, share="private")


def test_shared_entry_readable_within_tenant() -> None:
    assert can_read(viewer_tenant=TENANT_A, owner_tenant=TENANT_A, share="shared")


def test_shared_entry_not_cross_tenant_in_v1() -> None:
    """v1: 'shared' = within-tenant; cross-tenant share is out of scope."""
    assert not can_read(viewer_tenant=TENANT_B, owner_tenant=TENANT_A, share="shared")


def test_system_owned_readable_by_anyone() -> None:
    """System-owned (read-only) seed library is readable by every tenant."""
    assert can_read(viewer_tenant=TENANT_B, owner_tenant=None, share="shared")


def test_can_write_rejects_system_owner() -> None:
    """System-owned entries are read-only; even system tenants cannot mutate via API."""
    assert not can_write(viewer_tenant=TENANT_A, owner_tenant=None)


def test_can_write_allows_owner() -> None:
    assert can_write(viewer_tenant=TENANT_A, owner_tenant=TENANT_A)


def test_share_violation_carries_subject() -> None:
    err = ShareViolation(viewer="A", owner="B", share="private")
    assert "A" in str(err) and "B" in str(err)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/catalog/ -v`

Expected: import errors.

- [ ] **Step 3: Implement refs + sharing + entity mixin**

Create `src/rfanalyzer/catalog/refs.py`:

```python
"""{ref, owner, version} reference resolution (spec §3.1).

A reference identifies a catalog entry by (owner, name, version). Owner is
either a tenant uuid string or the literal string "system" for the seed
library. Version is either a positive integer or the literal "latest"; the
latter is pinned to the current highest active version at SUBMITTED-time
and inlined into Run.inputs_resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class ReferenceNotFound(KeyError):
    """No entity matches the (owner, name, version) tuple."""


@dataclass(frozen=True, slots=True)
class EntityReference:
    """A parsed {ref, owner, version} payload."""

    ref: str
    owner: str
    version: int | Literal["latest"]


def parse_reference(payload: dict[str, object]) -> EntityReference:
    """Parse a JSON {ref, owner, version} object into EntityReference.

    Raises:
        ValueError: malformed payload (missing field or bad type).
    """
    if not isinstance(payload, dict):
        raise ValueError("reference payload must be a JSON object")
    name = payload.get("ref")
    owner = payload.get("owner")
    version = payload.get("version")
    if not isinstance(name, str) or not name:
        raise ValueError("reference: 'ref' must be a non-empty string")
    if not isinstance(owner, str) or not owner:
        raise ValueError("reference: 'owner' must be a non-empty string")
    if version == "latest":
        return EntityReference(ref=name, owner=owner, version="latest")
    if isinstance(version, int) and version >= 1:
        return EntityReference(ref=name, owner=owner, version=version)
    raise ValueError(
        "reference: 'version' must be a positive integer or the string 'latest'"
    )
```

Create `src/rfanalyzer/catalog/sharing.py`:

```python
"""Share-within-tenant rules (spec §3.1).

Per spec: an entry's share value is one of {private, shared}. Cross-tenant
share is out of v1 scope; "shared" means within the owner's tenant.
System-owned (`owner_tenant is None`) entries are readable by all tenants
and writable by none — they are managed via the seed loader, not the API.
"""

from __future__ import annotations

import uuid
from typing import Literal

Share = Literal["private", "shared"]


class ShareViolation(PermissionError):
    """Raised when a viewer is not allowed to read or write an entity."""

    def __init__(self, *, viewer: str, owner: str, share: str) -> None:
        super().__init__(
            f"viewer {viewer} cannot access entity owned by {owner} (share={share})"
        )
        self.viewer = viewer
        self.owner = owner
        self.share = share


def can_read(
    *, viewer_tenant: uuid.UUID, owner_tenant: uuid.UUID | None, share: Share | str
) -> bool:
    """True iff *viewer_tenant* may read the entity."""
    if owner_tenant is None:
        return True  # system-owned readable by all tenants
    if owner_tenant == viewer_tenant:
        return True  # owner can always read their own entries
    return False  # cross-tenant share is out of v1 scope


def can_write(*, viewer_tenant: uuid.UUID, owner_tenant: uuid.UUID | None) -> bool:
    """True iff *viewer_tenant* may modify the entity."""
    if owner_tenant is None:
        return False  # system-owned is read-only
    return owner_tenant == viewer_tenant
```

Create `src/rfanalyzer/db/_catalog_base.py`:

```python
"""Shared SQLAlchemy mixins for catalog entity tables.

Every catalog entity has an `id` (UUID), `owner` (tenant uuid or "system"),
`name`, `version`, `share`, `deleted_at`, `created_at`, `updated_at`, and
`tags`. Per-entity payload lives in a `body` JSONB column to keep DDL
shallow; structural validation happens in the pydantic Inline schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

SHARE_VALUES = ("private", "shared")
SYSTEM_OWNER = "system"


class CatalogEntityMixin:
    """Mixin contributed to each catalog entity ORM class."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    share: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> tuple[Any, ...]:
        return (
            UniqueConstraint("owner", "name", "version", name=f"{cls.__tablename__}_uq"),
            CheckConstraint(
                "share IN ('private', 'shared')", name=f"{cls.__tablename__}_share_ck"
            ),
            Index(
                f"{cls.__tablename__}_owner_name_idx",
                "owner",
                "name",
                postgresql_where=text("deleted_at IS NULL"),
            ),
        )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/catalog/ -v`

Expected: refs tests pass; sharing tests pass; the marked `test_resolve_pins_latest_to_max` is skipped.

- [ ] **Step 5: Commit**

```bash
git add src/rfanalyzer/catalog/refs.py src/rfanalyzer/catalog/sharing.py src/rfanalyzer/db/_catalog_base.py tests/unit/catalog/
git commit -m "feat(catalog): refs + sharing + ORM mixin (sub-project 3)"
```

---

### Task 2: Site entity — full CRUD pattern

This task establishes the pattern every later entity follows. Subsequent entity tasks reference this one for the request/response shape, validators, ORM model, and CRUD endpoint structure.

**Files:**
- Create: `src/rfanalyzer/catalog/entities/site.py` — pydantic Inline + Reference + outbound shapes
- Modify: `src/rfanalyzer/db/models.py` — add `Site` ORM model
- Create: `src/rfanalyzer/api/catalog/__init__.py`
- Create: `src/rfanalyzer/api/catalog/sites.py` — CRUD router
- Modify: `src/rfanalyzer/main.py` — mount the catalog sub-router
- Create: alembic migration `0002_catalog_core.py` (initial body covers all 10 tables — populated incrementally over Tasks 2–6)
- Create: `tests/unit/catalog/test_site_entity.py`
- Create: `tests/integration/test_site_crud_e2e.py`

- [ ] **Step 1: Write the failing schema test**

Create `tests/unit/catalog/test_site_entity.py`:

```python
"""Tests for Site entity pydantic schema."""

from __future__ import annotations

import pytest

from rfanalyzer.catalog.entities.site import InlineSite, SiteCreate, SiteUpdate


def test_inline_site_minimum_fields() -> None:
    site = InlineSite(name="kruger-tower-12", lat=-24.0, lon=31.5)
    assert site.lat == -24.0


def test_inline_site_lat_bounds() -> None:
    with pytest.raises(Exception):
        InlineSite(name="x", lat=91.0, lon=0.0)


def test_inline_site_lon_bounds() -> None:
    with pytest.raises(Exception):
        InlineSite(name="x", lat=0.0, lon=181.0)


def test_optional_fields_round_trip() -> None:
    site = InlineSite(
        name="dock-site-A",
        lat=0.0,
        lon=0.0,
        ground_elevation_override_m=412.5,
        notes="north corner",
        tags=["dock", "drone"],
    )
    dumped = site.model_dump()
    assert dumped["ground_elevation_override_m"] == 412.5
    assert dumped["tags"] == ["dock", "drone"]


def test_site_create_carries_share_default() -> None:
    body = SiteCreate(name="x", lat=0.0, lon=0.0)
    assert body.share == "private"


def test_site_update_partial() -> None:
    upd = SiteUpdate(notes="new notes")
    assert upd.lat is None
    assert upd.notes == "new notes"
```

- [ ] **Step 2: Implement the pydantic schema**

Create `src/rfanalyzer/catalog/entities/site.py`:

```python
"""Site entity (spec §3.2).

Named geographic point. `default_equipment_refs[]` lists Equipment Profiles
intended to be deployed at this site; Op C uses them by default.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InlineSite(_Frozen):
    """Inline Site shape per OpenAPI InlineSite component."""

    name: str = Field(min_length=1, max_length=128)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    ground_elevation_override_m: float | None = None
    default_equipment_refs: list[dict[str, object]] = Field(default_factory=list)
    photo_asset_ref: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)


class SiteCreate(InlineSite):
    """Create-request body."""

    share: Literal["private", "shared"] = "private"


class SiteUpdate(_Frozen):
    """PATCH body (every field optional)."""

    name: str | None = None
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    ground_elevation_override_m: float | None = None
    default_equipment_refs: list[dict[str, object]] | None = None
    photo_asset_ref: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    share: Literal["private", "shared"] | None = None


class SiteResponse(InlineSite):
    """Outbound shape (extends InlineSite with identity + audit)."""

    id: str
    owner: str
    version: int
    share: Literal["private", "shared"]
    created_at: str
    updated_at: str
    deleted_at: str | None = None
```

- [ ] **Step 3: Add the ORM model**

Modify `src/rfanalyzer/db/models.py` — add (preserving the existing `TenantApiKey`):

```python
from rfanalyzer.db._catalog_base import CatalogEntityMixin


class Site(Base, CatalogEntityMixin):
    __tablename__ = "sites"
```

(The `body` JSONB column on the mixin holds the entity payload; `lat` / `lon` / `ground_elevation_override_m` etc. live there. Indexed-on-coords queries are not required in v1 — Op B / D / E use bbox queries against AOI Packs, not Sites.)

- [ ] **Step 4: Generate the migration**

Run:

```bash
RFANALYZER_DATABASE_URL=postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer \
    uv run alembic revision --autogenerate -m "catalog_core"
```

Rename the generated file to `0002_catalog_core.py` and confirm it creates `sites` plus the indexes from `CatalogEntityMixin.__table_args__`. Subsequent entity tasks (Tasks 3–6) extend this same migration file rather than creating new ones.

- [ ] **Step 5: Implement the CRUD router**

Create `src/rfanalyzer/api/catalog/__init__.py`:

```python
"""Catalog routers — one sub-router per entity (spec §3.2)."""

from __future__ import annotations

from fastapi import APIRouter

from rfanalyzer.api.catalog import sites

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])
router.include_router(sites.router)
# Subsequent entity routers wire in tasks 3-6.
```

Create `src/rfanalyzer/api/catalog/sites.py`:

```python
"""Site CRUD (spec §3.2 / §2.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.auth.bearer import authenticate
from rfanalyzer.auth.principal import Principal
from rfanalyzer.auth.scopes import require_scope
from rfanalyzer.catalog.entities.site import (
    SiteCreate,
    SiteResponse,
    SiteUpdate,
)
from rfanalyzer.catalog.sharing import ShareViolation, can_read, can_write
from rfanalyzer.db.engine import get_session
from rfanalyzer.db.models import Site

router = APIRouter(prefix="/sites", tags=["sites"])


def _row_to_response(row: Site) -> SiteResponse:
    return SiteResponse(
        id=str(row.id),
        owner=row.owner,
        version=row.version,
        share=row.share,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        deleted_at=row.deleted_at.isoformat() if row.deleted_at else None,
        **row.body,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SiteResponse)
async def create_site(
    body: SiteCreate,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SiteResponse:
    require_scope(principal, "catalog:write")
    payload = body.model_dump(exclude={"share"})
    row = Site(
        owner=str(principal.tenant_id),
        name=body.name,
        version=1,
        share=body.share,
        body=payload,
        tags=payload.get("tags", []),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _row_to_response(row)


@router.get("/{name}", response_model=SiteResponse)
async def get_site(
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
    name: str = Path(...),
    owner: str = Query("self", description="'self' | 'system' | tenant uuid"),
    version: str = Query("latest"),
) -> SiteResponse:
    require_scope(principal, "catalog:read")
    owner_filter = str(principal.tenant_id) if owner == "self" else owner
    stmt = select(Site).where(
        and_(
            Site.owner == owner_filter,
            Site.name == name,
            Site.deleted_at.is_(None),
        )
    )
    if version != "latest":
        try:
            stmt = stmt.where(Site.version == int(version))
        except ValueError:
            raise HTTPException(status_code=422, detail="version must be int or 'latest'") from None
    else:
        stmt = stmt.order_by(Site.version.desc()).limit(1)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    owner_uuid = None if row.owner == "system" else uuid.UUID(row.owner)
    if not can_read(viewer_tenant=principal.tenant_id, owner_tenant=owner_uuid, share=row.share):
        raise HTTPException(status_code=404, detail="not found")
    return _row_to_response(row)


@router.patch("/{name}", response_model=SiteResponse)
async def patch_site(
    body: SiteUpdate,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
    name: str = Path(...),
    version: int = Query(...),
) -> SiteResponse:
    """Patch creates a new immutable version (spec §3.1: versions are immutable)."""
    require_scope(principal, "catalog:write")
    stmt = select(Site).where(
        and_(
            Site.owner == str(principal.tenant_id),
            Site.name == name,
            Site.version == version,
            Site.deleted_at.is_(None),
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if not can_write(viewer_tenant=principal.tenant_id, owner_tenant=uuid.UUID(row.owner)):
        raise ShareViolation(
            viewer=str(principal.tenant_id), owner=row.owner, share=row.share
        )

    new_body = {**row.body, **{k: v for k, v in body.model_dump(exclude_none=True).items()}}
    new_share = body.share if body.share is not None else row.share
    next_version = (
        await session.execute(
            select(Site.version)
            .where(Site.owner == row.owner, Site.name == row.name)
            .order_by(Site.version.desc())
            .limit(1)
        )
    ).scalar_one() + 1

    new_row = Site(
        owner=row.owner,
        name=row.name,
        version=next_version,
        share=new_share,
        body=new_body,
        tags=new_body.get("tags", []),
    )
    session.add(new_row)
    await session.commit()
    await session.refresh(new_row)
    return _row_to_response(new_row)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
    name: str = Path(...),
    version: int = Query(...),
) -> None:
    """Soft-delete a specific version."""
    require_scope(principal, "catalog:write")
    stmt = select(Site).where(
        Site.owner == str(principal.tenant_id),
        Site.name == name,
        Site.version == version,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="not found")
    row.deleted_at = datetime.now(tz=row.created_at.tzinfo)
    await session.commit()


@router.get("", response_model=list[SiteResponse])
async def list_sites(
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
    owner: str = Query("self"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> list[SiteResponse]:
    require_scope(principal, "catalog:read")
    owner_filter = str(principal.tenant_id) if owner == "self" else owner
    stmt = (
        select(Site)
        .where(Site.owner == owner_filter, Site.deleted_at.is_(None))
        .order_by(Site.name, Site.version.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_row_to_response(r) for r in rows]
```

- [ ] **Step 6: Mount the catalog router; remove _auth-check**

Modify `src/rfanalyzer/main.py` — replace the `_auth-check` route with the catalog router:

```python
# (around the create_app body, after include_router(health.router))
from rfanalyzer.api import catalog as catalog_routers  # at top of file

# replace the auth_check inline route with:
app.include_router(catalog_routers.router)
```

Drop the `auth_check` inner function and its imports.

- [ ] **Step 7: Write the integration test**

Create `tests/integration/test_site_crud_e2e.py`:

```python
"""End-to-end CRUD against the live stack."""

from __future__ import annotations

import os
import secrets
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

DB_URL = os.environ.get(
    "RFANALYZER_DATABASE_URL",
    "postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer",
)
API_BASE = os.environ.get("RFANALYZER_API_BASE_URL", "http://localhost:8000")


@pytest.fixture
async def auth_headers() -> dict[str, str]:
    """Provision a key with catalog scopes; return Bearer header."""
    from sqlalchemy import insert
    from sqlalchemy.ext.asyncio import create_async_engine

    from rfanalyzer.auth.argon2 import hash_key
    from rfanalyzer.db.models import TenantApiKey

    cleartext = secrets.token_urlsafe(32)
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        await conn.execute(
            insert(TenantApiKey).values(
                id=uuid.uuid4(),
                prefix=cleartext[:8],
                hash=hash_key(cleartext),
                tenant_id=uuid.uuid4(),
                scopes=["catalog:read", "catalog:write"],
                label="site-crud-test",
            )
        )
    await engine.dispose()
    return {"Authorization": f"Bearer {cleartext}"}


@pytest.mark.asyncio
async def test_create_get_patch_delete(auth_headers: dict[str, str]) -> None:
    name = f"test-site-{secrets.token_hex(4)}"
    client = httpx.AsyncClient(base_url=API_BASE, headers=auth_headers, timeout=10.0)

    r = await client.post("/v1/catalog/sites", json={"name": name, "lat": 0.0, "lon": 0.0})
    assert r.status_code == 201
    assert r.json()["version"] == 1

    r = await client.get(f"/v1/catalog/sites/{name}")
    assert r.status_code == 200

    r = await client.patch(
        f"/v1/catalog/sites/{name}", params={"version": 1}, json={"notes": "n"}
    )
    assert r.status_code == 200
    assert r.json()["version"] == 2

    r = await client.delete(f"/v1/catalog/sites/{name}", params={"version": 2})
    assert r.status_code == 204
    await client.aclose()
```

- [ ] **Step 8: Run unit + integration**

Run:

```bash
docker compose -f docker/docker-compose.yml up -d --wait
RFANALYZER_DATABASE_URL=postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer \
    uv run alembic upgrade head
uv run pytest tests/unit/catalog/test_site_entity.py tests/integration/test_site_crud_e2e.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/rfanalyzer/catalog/entities/site.py src/rfanalyzer/db/models.py src/rfanalyzer/db/migrations/versions/0002_catalog_core.py src/rfanalyzer/api/ src/rfanalyzer/main.py tests/unit/catalog/test_site_entity.py tests/integration/test_site_crud_e2e.py
git commit -m "feat(catalog): Site entity full CRUD pattern (sub-project 3)"
```

---

### Task 3: Antenna + RadioProfile + EquipmentProfile

These three are tightly coupled (Equipment refs Radio + Antenna). Pattern follows Task 2; full code below for all three.

**Files:** new `src/rfanalyzer/catalog/entities/{antenna,radio_profile,equipment_profile}.py`; new `src/rfanalyzer/api/catalog/{antennas,radio_profiles,equipment_profiles}.py`; ORM additions to `db/models.py`; extend `0002_catalog_core.py` migration.

- [ ] **Step 1: Add the entity schemas**

Create `src/rfanalyzer/catalog/entities/antenna.py` per spec §3.2 with fields `name`, `kind` (`parametric`|`pattern_file`), `gain_dbi`, `polarization` (`v|h|rhcp|lhcp|slant_45|dual`), `slant_polarization_orientation_deg` (constrained to non-null only when polarization=`slant_45`, per cleanup PR 8), `applicable_bands: list[{min_mhz, max_mhz}]`, `applicable_polarizations`, parametric block (`pattern_type`, `h_beamwidth_deg`, `v_beamwidth_deg`, `electrical_downtilt_deg`), file block (`format`, `pattern_asset_ref`). The pattern follows `site.py`: `_Frozen` base, `InlineAntenna`, `AntennaCreate(InlineAntenna)` with `share`, `AntennaUpdate` (all optional), `AntennaResponse`. Include a `model_validator(mode="after")` that enforces:

```python
@model_validator(mode="after")
def _check_kind_payload(self) -> "InlineAntenna":
    if self.kind == "parametric" and self.h_beamwidth_deg is None:
        raise ValueError("parametric antenna requires h_beamwidth_deg")
    if self.kind == "pattern_file" and self.pattern_asset_ref is None:
        raise ValueError("pattern_file antenna requires pattern_asset_ref")
    if self.polarization == "slant_45" and self.slant_polarization_orientation_deg is None:
        # not an error — defaulted with POLARIZATION_DEFAULTED warning at use; allow None
        pass
    elif self.polarization != "slant_45" and self.slant_polarization_orientation_deg is not None:
        raise ValueError("slant_polarization_orientation_deg only valid when polarization=slant_45")
    return self
```

Create `src/rfanalyzer/catalog/entities/radio_profile.py` per spec §3.2: `name`, `link_type` (open str), `freq_mhz`, `bandwidth_khz`, `tx_power_dbm`, `rx_sensitivity_dbm`, optional `modulation`, `fade_margin_db_target`, `propagation_model_pref` (auto|explicit:<id>), plus per-link-type extension fields stored in a free-form `link_type_extensions: dict[str, Any]`. Validate extensions structurally only at this layer; deeper validation lives in the link-type plugin (sub-project 5).

Create `src/rfanalyzer/catalog/entities/equipment_profile.py` per spec §3.2: `name`, `radio_ref: dict` (parsed via `parse_reference`), `antenna_ref: dict`, `mount_height_m_agl: float ge=0`, `cable_loss_db: float ge=0`, optional `cable_loss_curve: list[{freq_mhz, loss_db}]`, `azimuth_deg: float ge=0 lt=360`, `mechanical_downtilt_deg`, `mfr`, `model`, `notes`, `tags`. Round-trip the references via `parse_reference` in a `model_validator(mode="after")`.

- [ ] **Step 2: Add ORM tables to db/models.py and extend the migration**

```python
class Antenna(Base, CatalogEntityMixin):
    __tablename__ = "antennas"


class RadioProfile(Base, CatalogEntityMixin):
    __tablename__ = "radio_profiles"


class EquipmentProfile(Base, CatalogEntityMixin):
    __tablename__ = "equipment_profiles"
```

Re-run `alembic revision --autogenerate -m "catalog_core_pt2"` — this generates the `antennas`, `radio_profiles`, `equipment_profiles` tables. Squash the autogenerated revision into `0002_catalog_core.py` (delete the new file; copy its `op.create_table(...)` calls into 0002).

- [ ] **Step 3: Add CRUD routers**

Create `src/rfanalyzer/api/catalog/antennas.py`, `radio_profiles.py`, `equipment_profiles.py` — each follows the `sites.py` pattern verbatim with the entity-specific names. Mount them in `src/rfanalyzer/api/catalog/__init__.py`:

```python
from rfanalyzer.api.catalog import antennas, equipment_profiles, radio_profiles, sites

router.include_router(antennas.router)
router.include_router(radio_profiles.router)
router.include_router(equipment_profiles.router)
router.include_router(sites.router)
```

- [ ] **Step 4: Write the unit + integration tests**

Tests follow `test_site_entity.py` and `test_site_crud_e2e.py` patterns: schema validation tests for required fields and validators (parametric vs file, slant_45 orientation, EIRP cap interaction with regulatory band — out of scope here but assert `tx_power_dbm` is float), CRUD round-trip, version bump on PATCH, soft-delete.

- [ ] **Step 5: Run tests + alembic upgrade**

```bash
RFANALYZER_DATABASE_URL=postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer \
    uv run alembic upgrade head
uv run pytest tests/unit/catalog/ tests/integration/test_site_crud_e2e.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/rfanalyzer/catalog/entities/{antenna,radio_profile,equipment_profile}.py src/rfanalyzer/api/catalog/ src/rfanalyzer/db/models.py src/rfanalyzer/db/migrations/versions/0002_catalog_core.py tests/unit/catalog/ tests/integration/
git commit -m "feat(catalog): Antenna + RadioProfile + EquipmentProfile entities (sub-project 3)"
```

---

### Task 4: AOIPack + ClutterTable

The AOI Pack `layers` shape is the cleanup-PR-1 nested form. ClutterTable is reference-only at analysis-request time; we still ship full CRUD so seed loading and operator clone-and-customize work.

**Files:** `catalog/entities/{aoi_pack,clutter_table}.py`, `api/catalog/{aoi_packs,clutter_tables}.py`, ORM additions, migration extension.

- [ ] **Step 1: Implement schemas**

Create `src/rfanalyzer/catalog/entities/aoi_pack.py`:

```python
"""AOIPack — per cleanup PR 1, layers is a nested object."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BBox(_Frozen):
    south: float = Field(ge=-90.0, le=90.0)
    west: float = Field(ge=-180.0, le=180.0)
    north: float = Field(ge=-90.0, le=90.0)
    east: float = Field(ge=-180.0, le=180.0)

    @model_validator(mode="after")
    def _check(self) -> "BBox":
        if self.south >= self.north:
            raise ValueError("BBox: south must be < north")
        # Antimeridian-crossing bbox rejected here (cleanup PR 8); UNSUPPORTED at v1.
        if self.west >= self.east:
            raise ValueError(
                "BBox: west must be < east (antimeridian-crossing AOIs are not supported in v1)"
            )
        return self


class AOILayer(_Frozen):
    """Per-layer provenance per spec §5.3."""

    source: str  # bundled | byo | fetched
    asset_ref: str | None = None  # sha256:<hex>
    upstream_source: str | None = None
    upstream_version: str | None = None
    acquired_at: str | None = None
    content_sha256: str | None = None
    resolution_m: float | None = None


class Layers(_Frozen):
    dtm: AOILayer | None = None
    dsm: AOILayer | None = None
    clutter: AOILayer | None = None
    buildings: AOILayer | None = None


class InlineAOIPack(_Frozen):
    name: str = Field(min_length=1)
    bbox: BBox
    layers: Layers = Field(default_factory=Layers)
    clutter_table_ref: dict[str, object] | None = None
    resolution_m: float | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)


class AOIPackCreate(InlineAOIPack):
    share: str = "private"


class AOIPackUpdate(_Frozen):
    name: str | None = None
    bbox: BBox | None = None
    layers: Layers | None = None
    clutter_table_ref: dict[str, object] | None = None
    resolution_m: float | None = None
    notes: str | None = None
    tags: list[str] | None = None
    share: str | None = None


class AOIPackResponse(InlineAOIPack):
    id: str
    owner: str
    version: int
    share: str
    created_at: str
    updated_at: str
    deleted_at: str | None = None
```

Create `src/rfanalyzer/catalog/entities/clutter_table.py` per spec §3.2: `name`, `taxonomy_id`, `class_table: dict[str, ClassEntry]` where `ClassEntry = {label?, attenuation_db_per_band: dict[anchor_freq_mhz_str, dB_per_100m], depolarization_factor: float[0..1], notes?}`, `applicable_freq_bands`, profile-level `notes`. Validate `depolarization_factor` is in [0, 1].

- [ ] **Step 2: Add ORM + migration extension + CRUD routers**

Same pattern as Task 3.

- [ ] **Step 3: Tests**

Add `tests/unit/catalog/test_aoi_pack_entity.py` covering: bbox validation, antimeridian rejection, nested layers shape, default empty Layers; `tests/unit/catalog/test_clutter_table_entity.py` covering depolarization bounds + class_table structure.

Add an integration test that creates an AOIPack with all four nested layers populated, fetches it, confirms the JSONB body round-trips byte-for-byte.

- [ ] **Step 4: Run + commit**

```bash
uv run alembic upgrade head
uv run pytest tests/unit/catalog/ tests/integration/ -v
git add src/rfanalyzer/catalog/entities/{aoi_pack,clutter_table}.py src/rfanalyzer/api/catalog/ src/rfanalyzer/db/ tests/
git commit -m "feat(catalog): AOIPack (nested layers) + ClutterTable (sub-project 3)"
```

---

### Task 5: OperatingVolume + MeasurementSet

OperatingVolume uses the cleanup-PR-4 names: `altitude_min_m`, `altitude_max_m`, `altitude_reference`, optional `altitude_step_m`. MeasurementSet's points use `altitude_m` + `altitude_reference`.

**Files:** `catalog/entities/{operating_volume,measurement_set}.py`, `api/catalog/{operating_volumes,measurement_sets}.py`, ORM additions.

- [ ] **Step 1: Implement schemas**

`InlineOperatingVolume` fields per spec §3.2:
- `name`, `bbox` *or* `polygon` (oneOf via discriminator on a `geometry: {kind: bbox|polygon, ...}` shape)
- `altitude_min_m: float`, `altitude_max_m: float` (validator: `min < max`)
- `altitude_reference: Literal["agl", "amsl"]`
- `altitude_step_m: float | None` (default None — sub-project 6 derives)
- `duration_estimate_min: int | None`
- `home_site_ref: dict | None`
- `host_site_ref: dict | None`
- `notes`, `tags`

`InlineMeasurementSet`:
- `name`
- `points: list[Point]` where `Point = {lat, lon, altitude_m, altitude_reference, freq_mhz, observed_signal_dbm, observed_metric, timestamp, source}` plus optional `seq`, `bandwidth_khz`, `uncertainty_db`, `tags`
- `ordered: bool = False` (when True, every point requires `seq`)
- `device_ref`, `site_ref` *or* `aoi_ref`, `sensitivity_class`, `notes`, `tags`

Add a `model_validator(mode="after")` enforcing `ordered=True ⇒ every point has seq` and `seq` values are unique + form a 1..N sequence.

- [ ] **Step 2: ORM + migration + CRUD**

Same pattern. MeasurementSet's `points` lives in JSONB `body`; for v1 ingest performance is unmeasured — sub-project 6's PvO task may shard to a `measurement_points` table if needed.

- [ ] **Step 3: Tests + commit**

```bash
uv run pytest tests/unit/catalog/ -v
git add src/rfanalyzer/catalog/entities/{operating_volume,measurement_set}.py src/rfanalyzer/api/catalog/ src/rfanalyzer/db/ tests/
git commit -m "feat(catalog): OperatingVolume + MeasurementSet (sub-project 3)"
```

---

### Task 6: Comparison + RegulatoryProfile

**Files:** `catalog/entities/{comparison,regulatory_profile}.py`, `api/catalog/{comparisons,regulatory_profiles}.py`, ORM additions.

- [ ] **Step 1: Implement schemas**

`InlineComparison`:
- `name`, `run_ids: list[str]` (UUIDs of Runs; validation that they exist defers to sub-project 4)
- `notes`, `winner_run_id`, `decision_rationale`, `decided_at`, `tags`

A Comparison auto-pins every referenced Run (sub-project 4 owns the pin hook); the Comparison creation here records the run_ids and asserts the cap (`max_pinned_runs` from DeploymentConfig). The ORM model adds a single index on `run_ids` (GIN since it's a JSONB array) so sub-project 4's `comparison_ids[]` lookup on the Run side is fast.

`InlineRegulatoryProfile` per spec §3.2:
- `name`, `country_code` (ISO-3166-1 alpha-2; validator: 2 uppercase letters)
- `regulator: str`
- `bands: list[Band]` where `Band = {min_mhz, max_mhz, max_eirp_dbm, license_class: Literal["license_exempt","license_required","permit_required","prohibited"], link_type_hint?, duty_cycle_pct_max?, bandwidth_khz_max?, notes?}`
- `effective_date`, `superseded_at`, `regulator_url`, `reference_doc_asset_ref`, `notes`, `tags`

- [ ] **Step 2: Tests for Comparison cap enforcement**

`tests/unit/catalog/test_comparison_entity.py` covers cap-rejection: when `len(run_ids)` exceeds `DeploymentConfig.runs.max_pinned_runs`, the create endpoint returns 409 with `code=PINNED_RUN_CAP_WOULD_BE_EXCEEDED` per cleanup PR 9. The actual auto-pin lands in sub-project 4; here we only validate the cap math.

- [ ] **Step 3: Commit**

```bash
git add src/rfanalyzer/catalog/entities/{comparison,regulatory_profile}.py src/rfanalyzer/api/catalog/ src/rfanalyzer/db/ tests/
git commit -m "feat(catalog): Comparison + RegulatoryProfile (sub-project 3)"
```

---

### Task 7: Asset model + content-addressed lifecycle

This task lands the assets table, the refcount column, the orphan-TTL clock fields, and the asset purposes enum. Multipart wiring is Task 8; the `:initiate` direct-mode endpoint lands here.

**Files:**
- Create: `src/rfanalyzer/assets/store.py`
- Create: `src/rfanalyzer/assets/purposes.py`
- Modify: `src/rfanalyzer/db/models.py` — add `Asset` ORM model
- Create: alembic `0003_assets.py`
- Create: `src/rfanalyzer/api/assets.py`
- Mount: `src/rfanalyzer/main.py`
- Tests: unit + integration

- [ ] **Step 1: Add Asset ORM model**

```python
class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # "sha256:<hex>"
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    refcount: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    orphan_clock_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
```

- [ ] **Step 2: Implement asset purposes**

Create `src/rfanalyzer/assets/purposes.py`:

```python
"""Asset purposes (spec §3.5)."""

from __future__ import annotations

from enum import StrEnum


class AssetPurpose(StrEnum):
    ANTENNA_PATTERN = "antenna_pattern"
    SITE_PHOTO = "site_photo"
    RASTER_DTM = "raster_dtm"
    RASTER_DSM = "raster_dsm"
    RASTER_CLUTTER = "raster_clutter"
    VECTOR_BUILDINGS = "vector_buildings"
    MEASUREMENT_CSV = "measurement_csv"
    GENERIC = "generic"


_VALID_CONTENT_TYPES: dict[AssetPurpose, frozenset[str]] = {
    AssetPurpose.ANTENNA_PATTERN: frozenset(
        {"text/plain", "application/x-msi-pattern", "application/octet-stream"}
    ),
    AssetPurpose.SITE_PHOTO: frozenset({"image/jpeg", "image/png", "image/webp"}),
    AssetPurpose.RASTER_DTM: frozenset({"image/tiff", "application/octet-stream"}),
    AssetPurpose.RASTER_DSM: frozenset({"image/tiff", "application/octet-stream"}),
    AssetPurpose.RASTER_CLUTTER: frozenset({"image/tiff", "application/octet-stream"}),
    AssetPurpose.VECTOR_BUILDINGS: frozenset(
        {"application/geo+json", "application/x-shapefile", "application/octet-stream"}
    ),
    AssetPurpose.MEASUREMENT_CSV: frozenset({"text/csv", "application/octet-stream"}),
    AssetPurpose.GENERIC: frozenset({"application/octet-stream"}),
}


def validate_content_type(purpose: AssetPurpose, content_type: str) -> bool:
    return content_type in _VALID_CONTENT_TYPES[purpose]
```

- [ ] **Step 3: Implement the asset store**

Create `src/rfanalyzer/assets/store.py`:

```python
"""Content-addressed asset lifecycle (spec §3.5).

Refcount semantics:
- bumped when a Run reaches SUBMITTED and references the asset (sub-project 4)
- decremented when the Run hits a terminal state AND every canonical artifact
  referencing the asset is GC'd
- orphan_clock_started_at is set when refcount hits 0; the asset is deleted
  asset_orphan_ttl_days later (sweeper in sub-project 4)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.assets.purposes import AssetPurpose, validate_content_type
from rfanalyzer.db.models import Asset

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_DIRECT_MAX_BYTES = 50 * 1024 * 1024


def asset_id(sha256_hex: str) -> str:
    """Return the canonical asset id form."""
    if not _HEX_RE.match(sha256_hex):
        raise ValueError("sha256 must be 64 lowercase hex chars")
    return f"sha256:{sha256_hex}"


def is_direct_eligible(size_bytes: int) -> bool:
    """Direct upload allowed for content < 50 MiB."""
    return size_bytes < _DIRECT_MAX_BYTES


async def find_existing(session: AsyncSession, sha256_hex: str) -> Asset | None:
    """Return the asset row keyed by sha256, or None."""
    stmt = select(Asset).where(Asset.sha256 == sha256_hex)
    return (await session.execute(stmt)).scalar_one_or_none()


async def register_pending(
    session: AsyncSession,
    *,
    sha256_hex: str,
    content_type: str,
    size_bytes: int,
    purpose: AssetPurpose,
    storage_key: str,
    metadata: dict[str, str] | None = None,
) -> Asset:
    """Insert an Asset row with completed_at=NULL (waiting for the upload)."""
    if not validate_content_type(purpose, content_type):
        raise ValueError(f"content_type {content_type} invalid for purpose {purpose}")
    row = Asset(
        id=asset_id(sha256_hex),
        sha256=sha256_hex,
        content_type=content_type,
        size_bytes=size_bytes,
        purpose=purpose.value,
        storage_key=storage_key,
        metadata=metadata or {},
    )
    session.add(row)
    await session.flush()
    return row


async def mark_complete(session: AsyncSession, asset_id_str: str) -> None:
    """Mark the asset as upload-complete."""
    row = await session.get(Asset, asset_id_str)
    if row is None:
        raise KeyError(asset_id_str)
    row.completed_at = datetime.now(tz=timezone.utc)
    await session.flush()


async def bump_refcount(session: AsyncSession, asset_id_str: str, delta: int) -> int:
    """Apply delta to refcount; return new value. delta may be +1 or -1."""
    row = await session.get(Asset, asset_id_str)
    if row is None:
        raise KeyError(asset_id_str)
    row.refcount = max(0, row.refcount + delta)
    if row.refcount == 0:
        row.orphan_clock_started_at = datetime.now(tz=timezone.utc)
    else:
        row.orphan_clock_started_at = None
    await session.flush()
    return row.refcount
```

- [ ] **Step 4: Implement the assets API (initiate-direct only here)**

Create `src/rfanalyzer/api/assets.py`:

```python
"""Asset upload + lifecycle endpoints (spec §3.5)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.assets import store
from rfanalyzer.assets.purposes import AssetPurpose
from rfanalyzer.auth.bearer import authenticate
from rfanalyzer.auth.principal import Principal
from rfanalyzer.auth.scopes import require_scope
from rfanalyzer.db.engine import get_session
from rfanalyzer.storage.factory import build_storage_provider

router = APIRouter(prefix="/v1/assets", tags=["assets"])


class InitiateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    content_type: str
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: AssetPurpose


class DirectUpload(BaseModel):
    method: Literal["PUT"] = "PUT"
    url: str
    headers: dict[str, str]
    expires_at: str


class InitiateDirectResponse(BaseModel):
    asset_id: str
    already_exists: bool = False
    ready: bool = False
    mode: Literal["direct"] = "direct"
    upload: DirectUpload | None = None


class InitiateExistsResponse(BaseModel):
    asset_id: str
    already_exists: Literal[True] = True
    ready: Literal[True] = True


@router.post(":initiate", responses={200: {"description": "Already exists"}, 201: {"description": "Initiated"}})
async def initiate(
    body: InitiateRequest,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    require_scope(principal, "assets:write")
    existing = await store.find_existing(session, body.sha256)
    if existing is not None and existing.completed_at is not None:
        return InitiateExistsResponse(asset_id=existing.id).model_dump()

    if not store.is_direct_eligible(body.size_bytes):
        raise HTTPException(
            status_code=413,
            detail="size_bytes >= 50 MiB; use multipart (Task 8)",
        )

    storage = build_storage_provider()
    storage_key = f"assets/{body.sha256}"
    presigned = await storage.presigned_url_put(
        storage_key, content_type=body.content_type, content_length=body.size_bytes
    )
    asset = await store.register_pending(
        session,
        sha256_hex=body.sha256,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        purpose=body.purpose,
        storage_key=storage_key,
        metadata={"filename": body.filename},
    )
    await session.commit()
    return InitiateDirectResponse(
        asset_id=asset.id,
        upload=DirectUpload(
            url=presigned.url,
            headers=presigned.headers,
            expires_at="",  # filled by storage layer in PresignedUrl.expires_in_seconds; converted here in implementation
        ),
    ).model_dump()


class CompleteResponse(BaseModel):
    asset_id: str
    content_type: str
    size_bytes: int
    sha256: str
    ready: bool


@router.post("/{asset_id}:complete", response_model=CompleteResponse)
async def complete(
    asset_id: str,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CompleteResponse:
    require_scope(principal, "assets:write")
    asset = await session.get(__import__("rfanalyzer.db.models").db.models.Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="not found")
    # Optional: verify the storage backend actually has the bytes by HEADing the key.
    # Hash verification: sub-project 4 wires the SUBMITTED-bump path which verifies bytes-on-disk match the recorded sha256 before referencing.
    await store.mark_complete(session, asset_id)
    await session.commit()
    return CompleteResponse(
        asset_id=asset.id,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        ready=True,
    )
```

- [ ] **Step 5: Tests**

Add `tests/unit/assets/test_store.py` and `tests/unit/assets/test_purposes.py` covering refcount math, orphan-clock toggling, content-type validation per purpose. Add `tests/integration/test_asset_initiate_direct.py` that calls `:initiate`, PUTs bytes via the returned presigned URL against MinIO, calls `:complete`, and confirms the asset row has `completed_at IS NOT NULL` and `refcount = 0`.

- [ ] **Step 6: Mount router; commit**

Mount `assets.router` in `main.py`. Run alembic upgrade and the test suite. Commit:

```bash
git add src/rfanalyzer/assets/ src/rfanalyzer/api/assets.py src/rfanalyzer/db/ tests/
git commit -m "feat(assets): content-addressed lifecycle + direct-upload initiate (sub-project 3)"
```

---

### Task 8: Multipart upload + refresh_part_urls + complete + abort

This task wires the upload_id ↔ key mapping that sub-project 2 deferred.

**Files:**
- Modify: `src/rfanalyzer/storage/{s3,azure_blob}.py` — implement multipart
- Create: `src/rfanalyzer/assets/multipart.py` — `asset_sessions` ORM + helpers
- Modify: `src/rfanalyzer/api/assets.py` — multipart-mode initiate, complete, abort, refresh_part_urls
- Migration: extend `0003_assets.py` with `asset_sessions` table

- [ ] **Step 1: Add asset_sessions ORM**

```python
class AssetSession(Base):
    __tablename__ = "asset_sessions"

    upload_id: Mapped[str] = mapped_column(Text, primary_key=True)
    asset_id: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    part_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_parts: Mapped[list[dict]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
```

- [ ] **Step 2: Implement S3 multipart**

In `s3.py`, implement `initiate_multipart`, `upload_part` (writes the part_etag map), `presign_part`, `complete_multipart`, `abort_multipart`. The `presign_part` and `complete_multipart` lookups now consult the `asset_sessions` table to find the key for an `upload_id`.

Code sketch — `complete_multipart`:

```python
async def complete_multipart(
    self, upload_id: str, parts: list[MultipartPart]
) -> ObjectHead:
    # Lookup key from asset_sessions (in the API layer) and pass via initiate.
    # Within this provider we keep a per-instance dict cache of upload_id -> key
    # populated at initiate_multipart time so this method stays self-contained.
    key = self._upload_keys[upload_id]
    parts_payload = [{"PartNumber": p.part_number, "ETag": p.etag} for p in sorted(parts, key=lambda x: x.part_number)]
    resp = await self._run(
        self._client.complete_multipart_upload,
        Bucket=self.bucket, Key=key, UploadId=upload_id,
        MultipartUpload={"Parts": parts_payload},
    )
    head_resp = await self._run(self._client.head_object, Bucket=self.bucket, Key=key)
    return ObjectHead(
        key=key, size_bytes=head_resp["ContentLength"],
        content_type=head_resp.get("ContentType", "application/octet-stream"),
        etag=resp["ETag"].strip('"'),
        metadata=self._meta_from_s3(head_resp.get("Metadata")),
    )
```

`presign_part` similarly looks up the key from the in-memory cache.

- [ ] **Step 3: Implement Azure Blob staged-block multipart**

Azure's equivalent of multipart is staged blocks. Each part is a "block" with a base64-encoded block ID; `complete_multipart` issues a single `commit_block_list` that finalizes the blob.

- [ ] **Step 4: Add API endpoints**

In `src/rfanalyzer/api/assets.py` add:

- `POST /v1/assets:initiate` (multipart branch when size >= 50 MiB): creates asset_session, returns `{asset_id, mode: "multipart", part_size_bytes, parts: [{part_number, upload_url, expires_at}], complete_url, abort_url}`
- `POST /v1/assets/{asset_id}:refresh_part_urls`: returns fresh presigned URLs for un-completed parts
- `POST /v1/assets/{asset_id}:complete` (multipart branch): accepts `{parts: [{part_number, etag}]}`, calls `storage.complete_multipart`, marks asset complete, deletes the asset_session
- `POST /v1/assets/{asset_id}:abort`: aborts the in-flight upload, deletes the session and asset_session row

- [ ] **Step 5: Tests**

`tests/integration/test_asset_multipart_e2e.py`:
- direct-mode round-trip (already covered)
- multipart round-trip with 2 parts (5 MiB each)
- `:refresh_part_urls` returns fresh URLs without disrupting completed parts
- `:abort` removes the session and the bytes
- idempotent re-upload: identical sha256 returns `already_exists: true`

- [ ] **Step 6: Commit**

```bash
git add src/rfanalyzer/storage/ src/rfanalyzer/assets/multipart.py src/rfanalyzer/api/assets.py src/rfanalyzer/db/ tests/
git commit -m "feat(assets): multipart upload + refresh_part_urls + S3/Azure wiring (sub-project 3)"
```

---

### Task 9: Webhook registration + challenge ack (delivery deferred to sub-project 4)

**Files:**
- Modify: `db/models.py` — add `WebhookSubscription` ORM
- Migration: `0004_webhooks.py`
- Create: `src/rfanalyzer/webhooks/registry.py`
- Create: `src/rfanalyzer/api/webhooks.py`

- [ ] **Step 1: Implement WebhookSubscription ORM**

```python
class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    secret_rotated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    secret_grace_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    previous_secret: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    challenge_nonce: Mapped[str | None] = mapped_column(Text)
    challenge_expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
```

- [ ] **Step 2: Implement registry helpers**

`src/rfanalyzer/webhooks/registry.py`:
- `create_subscription(session, *, key_id, url, events) -> WebhookSubscription`: generates secret + challenge_nonce + 60 s expiry; persists row; returns the subscription with the cleartext nonce in its return shape (the API includes the nonce in the sync response)
- `verify_challenge(session, subscription_id, nonce) -> bool`: matches nonce + nonces hasn't expired; sets `verified_at = now()`
- `rotate_secret(session, subscription_id) -> tuple[old, new]`: stores previous in `previous_secret`, sets `secret_grace_until = now() + 24 h`

The registration delivery sequence (call back to `<url>/webhook-challenge` with the nonce; receiver echoes a hash of `nonce + secret`) lives in sub-project 4 alongside the rest of webhook delivery; this sub-plan's contribution is the registration row + the `:ack-challenge` endpoint that the receiver eventually hits.

- [ ] **Step 3: API endpoints**

`src/rfanalyzer/api/webhooks.py`:
- `POST /v1/webhooks` — register; returns `{id, secret, challenge_nonce, challenge_expires_at}` on first registration
- `POST /v1/webhooks/{id}:ack-challenge` — receiver-callable; body `{nonce, signed_response}`
- `POST /v1/webhooks/{id}:rotate-secret`
- `GET /v1/webhooks` — list, paginated
- `DELETE /v1/webhooks/{id}` — delete

Per spec §2.4, allowed events enum: `{run.completed, run.failed, run.partial, run.cancelled, run.expired}`. Validate at registration.

- [ ] **Step 4: Tests + commit**

Unit + integration. Commit:

```bash
git add src/rfanalyzer/webhooks/ src/rfanalyzer/api/webhooks.py src/rfanalyzer/db/ tests/
git commit -m "feat(webhooks): registration + challenge ack (sub-project 3)"
```

---

### Task 10: PATCH /v1/runs/{id} for sensitivity_class (placeholder Run table)

Sub-project 4 promotes the Run table to its full shape; this task ships a minimum-viable `runs` table sufficient for `PATCH sensitivity_class` per Appendix E.6.

**Files:**
- Migration: `0005_runs_placeholder.py` — `runs(id UUID PK, key_id UUID NOT NULL, sensitivity_class TEXT NOT NULL DEFAULT 'org_internal', created_at TIMESTAMPTZ)`
- Create: `src/rfanalyzer/api/runs.py` — only the PATCH endpoint
- Mount in main

- [ ] **Step 1: Migration + ORM stub**

Add `Run` ORM (placeholder columns; sub-project 4 adds the rest):

```python
class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sensitivity_class: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'org_internal'"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
```

- [ ] **Step 2: PATCH endpoint**

```python
class SensitivityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sensitivity_class: Literal["public", "org_internal", "location_redacted", "restricted_species"]


@router.patch("/{run_id}", response_model=dict)
async def patch_run(
    run_id: uuid.UUID,
    body: SensitivityPatch,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    require_scope(principal, "opsec.classify")
    row = await session.get(Run, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    row.sensitivity_class = body.sensitivity_class
    await session.commit()
    return {"id": str(row.id), "sensitivity_class": row.sensitivity_class}
```

- [ ] **Step 3: Tests + commit**

```bash
git add src/rfanalyzer/api/runs.py src/rfanalyzer/db/ tests/
git commit -m "feat(runs): PATCH sensitivity_class placeholder (sub-project 3, full table in sub-project 4)"
```

---

### Task 11: Seed loader — bootstrap on first boot

**Files:**
- Create: `src/rfanalyzer/catalog/seed_loader.py`
- Modify: `src/rfanalyzer/main.py` — invoke seed loader at lifespan startup
- Create: `tests/integration/test_seed_loader_e2e.py`

- [ ] **Step 1: Implement the seed loader**

```python
"""Bootstrap the standard profile library + bundled antenna patterns on first boot."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import structlog
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.assets.purposes import AssetPurpose
from rfanalyzer.assets import store as asset_store
from rfanalyzer.db.models import Antenna, ClutterTable, EquipmentProfile, RadioProfile
from rfanalyzer.storage.factory import build_storage_provider

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_DIR = REPO_ROOT / "docs" / "superpowers" / "specs" / "seed"
LIBRARY_FILE = SEED_DIR / "standard-profile-library.json"
PATTERNS_DIR = SEED_DIR / "antenna_patterns"
ADVISORY_LOCK_KEY = 0x52464145414E  # "RFANALY"

log = structlog.get_logger(__name__)


async def bootstrap(session: AsyncSession) -> None:
    """Run the boot sequence under an advisory lock; idempotent across replicas."""
    # pg_try_advisory_xact_lock returns false if another replica is running this.
    got_lock = (await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}
    )).scalar_one()
    if not got_lock:
        log.info("seed_loader.skipping_no_lock")
        return

    if await _already_loaded(session):
        log.info("seed_loader.already_loaded")
        return

    storage = build_storage_provider()

    # 1. Register every bundled antenna pattern as an Asset.
    manifest = (PATTERNS_DIR / "MANIFEST.txt").read_text().splitlines()
    for line in manifest:
        if not line.strip():
            continue
        filename, sha256_hex, size_str = line.split("\t")
        body = (PATTERNS_DIR / filename).read_bytes()
        actual = hashlib.sha256(body).hexdigest()
        if actual != sha256_hex:
            raise RuntimeError(
                f"pattern hash mismatch: {filename} expected {sha256_hex} got {actual}"
            )
        existing = await asset_store.find_existing(session, sha256_hex)
        if existing is None:
            storage_key = f"assets/{sha256_hex}"
            await storage.put_object(
                storage_key, body,
                content_type="text/plain",
                metadata={"filename": filename, "sha256": sha256_hex},
            )
            asset = await asset_store.register_pending(
                session,
                sha256_hex=sha256_hex,
                content_type="text/plain",
                size_bytes=int(size_str),
                purpose=AssetPurpose.ANTENNA_PATTERN,
                storage_key=storage_key,
                metadata={"filename": filename},
            )
            await asset_store.mark_complete(session, asset.id)

    # 2. Load the JSON library.
    library = json.loads(LIBRARY_FILE.read_text())
    for ant in library.get("antennas", []):
        await session.execute(
            insert(Antenna).values(
                owner="system", name=ant["name"], version=1, share="shared",
                body=ant, tags=ant.get("tags", []),
            )
        )
    for radio in library.get("radio_profiles", []):
        await session.execute(insert(RadioProfile).values(
            owner="system", name=radio["name"], version=1, share="shared",
            body=radio, tags=radio.get("tags", []),
        ))
    for equip in library.get("equipment_profiles", []):
        await session.execute(insert(EquipmentProfile).values(
            owner="system", name=equip["name"], version=1, share="shared",
            body=equip, tags=equip.get("tags", []),
        ))
    for ct in library.get("clutter_tables", []):
        await session.execute(insert(ClutterTable).values(
            owner="system", name=ct["name"], version=1, share="shared",
            body=ct, tags=ct.get("tags", []),
        ))
    await session.commit()
    log.info("seed_loader.complete")


async def _already_loaded(session: AsyncSession) -> bool:
    """Sentinel: check for at least one system-owned Antenna."""
    cnt = await session.execute(
        select(text("count(*)")).select_from(Antenna).where(Antenna.owner == "system")
    )
    return (cnt.scalar_one() or 0) > 0
```

- [ ] **Step 2: Wire into app startup**

In `src/rfanalyzer/main.py` `_lifespan`:

```python
if os.environ.get("RFANALYZER_DATABASE_URL"):
    init_engine()
    from rfanalyzer.db.engine import _session_factory
    if os.environ.get("RFANALYZER_SEED_BOOTSTRAP", "1") == "1":
        from rfanalyzer.catalog.seed_loader import bootstrap
        async with _session_factory() as session:  # type: ignore[misc]
            await bootstrap(session)
```

(Disable via `RFANALYZER_SEED_BOOTSTRAP=0` for tests that want a clean DB.)

- [ ] **Step 3: Integration test**

`tests/integration/test_seed_loader_e2e.py`:
- Bring up stack; alembic upgrade; bootstrap once.
- Confirm 21 antennas, 18 radio profiles, 23 equipment profiles, 2 clutter tables exist as system-owned (counts per master plan).
- Run bootstrap a second time; confirm no duplicate rows.
- Verify each bundled antenna pattern has an Asset row.

- [ ] **Step 4: Commit**

```bash
git add src/rfanalyzer/catalog/seed_loader.py src/rfanalyzer/main.py tests/integration/test_seed_loader_e2e.py
git commit -m "feat(catalog): seed loader bootstraps standard profile library + antenna patterns (sub-project 3)"
```

---

### Task 12: Validate all 12 seed scenarios round-trip against the live API

End-to-end gate: every scenario in `docs/superpowers/specs/seed/scenarios/` must (1) validate against the JSON Schema, (2) reference only catalog entries that exist after seed-load, (3) submit to the placeholder Run endpoint without a 422 catalog-resolution error.

**Files:**
- Create: `tests/integration/test_seed_scenarios_resolve_e2e.py`

- [ ] **Step 1: Write the test**

```python
"""Every seed scenario references catalog entries that exist after bootstrap."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "docs" / "superpowers" / "specs" / "seed" / "scenarios"
SCHEMA = json.loads((REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-04-25-analysis-requests.schema.json").read_text())

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("scenario_path", sorted(SCENARIOS_DIR.glob("*.json")))
def test_scenario_validates_against_json_schema(scenario_path: Path) -> None:
    scenario = json.loads(scenario_path.read_text())
    Draft202012Validator(SCHEMA).validate(scenario["request"])


# Reference-resolution test that hits the live API: walks each scenario's
# request body, finds {ref, owner, version} payloads, GETs them via /v1/catalog,
# expects 200 for every reference. (Sub-project 4 wires the actual analysis
# submission endpoints; here we only validate ref resolution.)
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/test_seed_scenarios_resolve_e2e.py -v -m integration
git add tests/integration/test_seed_scenarios_resolve_e2e.py
git commit -m "test: validate all 12 seed scenarios resolve against bootstrapped catalog (sub-project 3)"
```

---

### Task 13: Final exit-criteria verification

- [ ] **Step 1: Full test sweep**

```bash
docker compose -f docker/docker-compose.yml down -v   # clean slate
docker compose -f docker/docker-compose.yml up -d --wait
RFANALYZER_DATABASE_URL=postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer \
    uv run alembic upgrade head
uv run ruff check . && uv run ruff format --check .
uv run mypy src/ scripts/
uv run pytest tests/unit/ -v
uv run pytest tests/integration/ -v -m integration
uv run python scripts/check-sync.py
uv run python scripts/diff-openapi.py
```

Expected: every command exits 0.

- [ ] **Step 2: Confirm exit criteria**

- [x] All 10 entity CRUD endpoints round-trip OpenAPI examples (Tasks 2–6)
- [x] `share: shared` rules enforced; cross-tenant reads blocked (Task 1 sharing)
- [x] `version: int | "latest"` resolution returns highest active when "latest" (Task 1 refs + Task 2 GET)
- [x] Asset upload direct + multipart end-to-end against MinIO; idempotent re-upload via SHA-256 (Tasks 7–8)
- [x] `:refresh_part_urls` returns fresh URLs only for un-completed parts (Task 8)
- [x] First boot loads seed library; second boot is a no-op (Task 11)
- [x] All 12 seed scenarios validate against schema + resolve against bootstrapped catalog (Task 12)
- [x] Webhook registration + challenge round-trip (Task 9)
- [x] `PATCH /v1/runs/{id}` for `sensitivity_class` against placeholder Run (Task 10)

- [ ] **Step 3: Push + CI green**

```bash
git push
```

---

## Self-Review

**Spec coverage:** Every entity in spec §3.2's table (10 entities) has a task; spec §3.5's asset model is end-to-end (Tasks 7–8); spec §2.4 webhooks registration is done (delivery → sub-project 4); Appendix E.6 PATCH is done.

**Placeholder scan:** clean. The Run table is a documented placeholder (Task 10 explicitly notes sub-project 4 promotes it). The webhook delivery deferral is stated in this plan's header. The "asset_sessions" in-memory upload-id-key cache in S3 multipart is real production code; the cache is process-local but the truth is in `asset_sessions` ORM rows.

**Type consistency:** `EntityReference`, `Principal`, `Asset`, `WebhookSubscription` all flow through unchanged. `share` is `Literal["private", "shared"]` everywhere. `RFANALYZER_SEED_BOOTSTRAP` is a new env var documented at first use.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-29-sub-project-3-catalog-and-assets.md`. Two execution options:

**1. Subagent-Driven (recommended per master plan)** — fresh subagent per task; two-stage review.

**2. Inline Execution** — batch with checkpoints.
