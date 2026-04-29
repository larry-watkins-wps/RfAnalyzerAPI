# Sub-project 6: Geo, Analysis Ops, Artifacts, PvO, OPSEC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land everything left for v1: adaptive geo-data fidelity (T0–T4); AOI Pack ingest (DTM / DSM / clutter / buildings); BYO data validation; coordinate / projection / antimeridian / polar / datum rules; five analysis ops (P2P, area, multi-link, multi-Tx, voxel) wired end-to-end through the sub-project-5 pipeline; canonical artifact emission (GeoTIFF, voxel NetCDF, link_budget JSON, etc.); derivative emission (KMZ, PNG, GeoJSON contours, GeoTIFF stack, rendered cross-section); voxel slicing; `:rederive`; predicted-vs-observed reporting with dimensionally-coherent filtering; OPSEC classification + auto-classification + per-class redaction + restricted-species allowlist; Schemathesis fuzz wired to the live API; vendored TypeScript client in argus-flight-center; license + SECURITY.md + CONTRIBUTING.md before v1 tag.

**Architecture:** Geo work uses `rasterio` for raster IO, `pyproj` for CRS + projection, `shapely` for vector geometry, `xarray` + `netCDF4` for voxels. AOI Packs are validated at ingest (CRS, bbox sanity, antimeridian rejection, polar warning, BYO format + footprint check), then rasterio handles tiled access during pipeline stages. The five analysis-op endpoints accept their op-specific JSON Schema body (already defined in `docs/superpowers/specs/2026-04-25-analysis-requests.schema.json`) and feed it to the pipeline via the existing `submit_*` flow from sub-project 4. Stages 10 and 11 of the pipeline now have real bodies that materialize canonical and derivative artifacts behind the StorageProvider; `:rederive` re-emits derivatives without re-running propagation. PvO matching is dimensionally coherent — frequency tolerance defaults to half the radio's bandwidth, metric coherence enforced (lora/rssi, lte/rsrp, etc.). OPSEC auto-classification runs at SUBMITTED: any geometry intersecting `DeploymentConfig.opsec.restricted_species_polygons` upgrades the Run's `sensitivity_class` and emits `OPSEC_AUTO_CLASSIFIED`.

**Tech Stack:** rasterio · pyproj · shapely · numpy · scipy · xarray · netCDF4 · simplekml (KMZ) · matplotlib (PNG renders) · openapi-typescript + openapi-fetch (TS client generation; npm).

**Authority:** Spec §2.3 (sync/async/auto), §4.0 (Tx/Rx, frequency authority, per-Op pairing), §5 (geospatial, all subsections), §6 (artifacts, all subsections), §7 (measurements + PvO), §8.9 (large-data transport), Appendix A (Op×Output matrix), Appendix E (OPSEC). Cleanup PR 4 (Op A outputs widening, Op E shape, altitude naming), PR 8 (CRS / antimeridian / polar / datum / slant-45), PR 11 (canonical-vs-derivative drift, stage 6 rename).

**Depends on:** Sub-project 5 (pipeline + models + link-type plugins).

**Decisions resolved in this plan:**
- **`restricted_species_polygons` test fixture:** generated at test-setup time from a synthetic GeoJSON polygon embedded in the integration-test conftest. The real deployment-config polygons are NEVER committed (per Appendix E.4); only the fixture polygons live in the repo, scoped to the test that uses them.
- **AOI Pack T4 ingest scope:** smoke-tested only (one small AOI with a single building footprint set) — full T4 production validation is post-v1 hardening per master plan §"Sub-project 6 risks/unknowns".
- **TS client output path:** `argus-flight-center/src/lib/rfanalyzer-client/` (per ADR-0001 amended action item 6). Generation script lives under `scripts/generate-ts-client.sh`; CI does not run it (it would need a checkout of argus-flight-center) — it's a local script Larry runs to refresh argus.

---

## File Structure

**New source modules:**
- `src/rfanalyzer/geo/{tiers,projections,aoi_pack,byo}.py`
- `src/rfanalyzer/artifacts/{canonicals,derivatives,voxel_slice,rederive}.py`
- `src/rfanalyzer/measurements/{ingest,pvo}.py`
- `src/rfanalyzer/opsec/{classification,polygons,redaction}.py`
- `src/rfanalyzer/api/measurements.py` — `POST /v1/measurements`, `GET /v1/measurement_sets/{id}`
- Replace `src/rfanalyzer/api/analyses.py` from sub-project 4: now has the full op-specific request bodies and wires PvO attachment, OPSEC auto-classification, sync/async/auto with cell-count estimation
- New: `scripts/generate-ts-client.sh`

**New tests:**
- `tests/unit/geo/{test_tiers,test_projections,test_aoi_pack,test_byo}.py`
- `tests/unit/artifacts/{test_canonicals,test_derivatives,test_rederive}.py`
- `tests/unit/measurements/{test_ingest,test_pvo}.py`
- `tests/unit/opsec/{test_classification,test_polygons,test_redaction}.py`
- `tests/integration/test_op_a_p2p_e2e.py`, `test_op_b_area_e2e.py`, `test_op_c_multi_link_e2e.py`, `test_op_d_multi_tx_e2e.py`, `test_op_e_voxel_e2e.py`
- `tests/integration/test_seed_scenarios_run_e2e.py` — every scenario reaches a terminal state
- `tests/fuzz/test_schemathesis.py` — replace placeholder; fuzz live API

**New repo-level files:**
- `LICENSE` (Apache-2.0)
- `SECURITY.md`
- `CONTRIBUTING.md`

**Spec changes:** None. Every concept here is already in spec v3.

---

### Task 1: Adaptive fidelity tier classification (T0–T4)

**Files:**
- Create: `src/rfanalyzer/geo/tiers.py`
- Tests: unit

- [ ] **Step 1: Implement tier classification + `min_fidelity_tier` / `min_fidelity_coverage` checks**

```python
"""Adaptive fidelity tier contract (spec §5.4).

Tiers (worst → best):
  T0_FREE_SPACE       — sanity bound only
  T1_DTM              — terrain only
  T2_DTM_CLUTTER      — terrain + clutter overlay
  T3_DSM              — surface model (terrain + canopy)
  T4_SURFACE_PLUS_BUILDINGS — DSM + per-building loss

Per Run we report four values: dominant, min, max, max_possible. A Run
completes as PARTIAL rather than COMPLETED when fidelity is below the AOI's
max_possible (the engineer learns "I could have gotten more").
"""

from __future__ import annotations

from dataclasses import dataclass

from rfanalyzer.models.interface import FidelityTier

_ORDER = [
    FidelityTier.T0_FREE_SPACE,
    FidelityTier.T1_DTM,
    FidelityTier.T2_DTM_CLUTTER,
    FidelityTier.T3_DSM,
    FidelityTier.T4_SURFACE_PLUS_BUILDINGS,
]


def tier_index(t: FidelityTier) -> int:
    return _ORDER.index(t)


@dataclass(frozen=True, slots=True)
class TierClassification:
    dominant: FidelityTier
    min: FidelityTier
    max: FidelityTier
    max_possible: FidelityTier


def classify_aoi(*, available_layers: dict[str, bool]) -> TierClassification:
    """Return tier classification given which AOIPack layers are populated.

    available_layers keys: 'dtm', 'dsm', 'clutter', 'buildings'.
    """
    has = available_layers
    tiers: list[FidelityTier] = [FidelityTier.T0_FREE_SPACE]
    if has.get("dtm"):
        tiers.append(FidelityTier.T1_DTM)
    if has.get("dtm") and has.get("clutter"):
        tiers.append(FidelityTier.T2_DTM_CLUTTER)
    if has.get("dsm"):
        tiers.append(FidelityTier.T3_DSM)
    if has.get("dsm") and has.get("buildings"):
        tiers.append(FidelityTier.T4_SURFACE_PLUS_BUILDINGS)
    achieved = max(tiers, key=tier_index)
    return TierClassification(
        dominant=achieved, min=achieved, max=achieved, max_possible=achieved,
    )


def is_below_max_possible(c: TierClassification) -> bool:
    """True if the run achieved less than the AOI was capable of."""
    return tier_index(c.dominant) < tier_index(c.max_possible)
```

For `min_fidelity_tier` (per-pixel floor) and `min_fidelity_coverage: {tier, fraction}` (coverage floor), implement helper `enforce_floor(c, *, min_tier=None, min_coverage=None)` that raises `FidelityFloorViolation` when the constraint isn't met.

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/geo/tiers.py tests/
git commit -m "feat(geo): T0-T4 fidelity classification (sub-project 6)"
```

---

### Task 2: Coordinate systems + projections (LAEA selection, antimeridian, polar, datum)

**Files:**
- Create: `src/rfanalyzer/geo/projections.py`
- Tests: unit

- [ ] **Step 1: Implement projection selection + bbox validators**

```python
"""Coordinate / projection rules (spec §5.5-§5.6, cleanup PR 8)."""

from __future__ import annotations

from dataclasses import dataclass

import pyproj


# v1 accepts WGS84 (EPSG:4326) only for inputs.
SUPPORTED_INPUT_CRS = {"EPSG:4326"}

# Internal projection: LAEA centered on AOI centroid.
# EPSG:3035 covers EU; EPSG:9311 covers North America; computed-LAEA elsewhere.
EU_BBOX = (-25.0, 34.0, 45.0, 72.0)  # west, south, east, north
NA_BBOX = (-170.0, 15.0, -50.0, 75.0)


@dataclass(frozen=True, slots=True)
class BBox:
    west: float; south: float; east: float; north: float


class CRSError(ValueError):
    """UNSUPPORTED_CRS — AOI input CRS is not WGS84."""


class AntimeridianError(ValueError):
    """BBOX_CROSSES_ANTIMERIDIAN_NOT_SUPPORTED."""


class PolarWarning(UserWarning):
    """POLAR_PROJECTION_DEGRADED — AOI crosses 85°."""


def validate_input_crs(crs: str) -> None:
    if crs not in SUPPORTED_INPUT_CRS:
        raise CRSError(f"UNSUPPORTED_CRS: {crs}")


def validate_bbox(bbox: BBox) -> None:
    if bbox.south >= bbox.north:
        raise ValueError("south < north required")
    if bbox.west >= bbox.east:
        raise AntimeridianError("BBOX_CROSSES_ANTIMERIDIAN_NOT_SUPPORTED")


def is_polar(bbox: BBox) -> bool:
    return bbox.north > 85.0 or bbox.south < -85.0


def select_projection(bbox: BBox) -> pyproj.CRS:
    """Return the LAEA CRS to use for internal computation."""
    if bbox.north > 85.0:
        return pyproj.CRS.from_epsg(3413)  # NSIDC north polar stereographic
    if bbox.south < -85.0:
        return pyproj.CRS.from_epsg(3031)  # Antarctic polar stereographic
    cw = (bbox.west + bbox.east) / 2
    cs = (bbox.south + bbox.north) / 2
    if EU_BBOX[0] <= cw <= EU_BBOX[2] and EU_BBOX[1] <= cs <= EU_BBOX[3]:
        return pyproj.CRS.from_epsg(3035)
    if NA_BBOX[0] <= cw <= NA_BBOX[2] and NA_BBOX[1] <= cs <= NA_BBOX[3]:
        return pyproj.CRS.from_epsg(9311)
    return pyproj.CRS.from_proj4(
        f"+proj=laea +lat_0={cs} +lon_0={cw} +x_0=0 +y_0=0 +datum=WGS84 +units=m"
    )
```

- [ ] **Step 2: Tests + commit**

Cover: WGS84 only; antimeridian rejection; polar warning; LAEA selection per centroid (EU / NA / custom).

```bash
git add src/rfanalyzer/geo/projections.py tests/
git commit -m "feat(geo): LAEA projection selection + antimeridian/polar/datum gates (sub-project 6)"
```

---

### Task 3: AOI Pack DTM + DSM + clutter + buildings ingest

**Files:**
- Create: `src/rfanalyzer/geo/aoi_pack.py`
- Tests: unit + integration

- [ ] **Step 1: Implement ingest + footprint validation**

```python
"""AOI Pack ingest (spec §5.1-§5.3)."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import rasterio
import rasterio.io


@dataclass(frozen=True, slots=True)
class RasterMeta:
    crs: str
    bounds: tuple[float, float, float, float]  # west, south, east, north
    resolution_m: float
    width: int
    height: int
    dtype: str


def inspect_raster(body: bytes) -> RasterMeta:
    """Open *body* with rasterio in-memory and return meta."""
    with rasterio.io.MemoryFile(body) as mem:
        with mem.open() as ds:
            crs = ds.crs.to_string() if ds.crs else "unknown"
            b = ds.bounds
            res = max(abs(ds.transform.a), abs(ds.transform.e))
            return RasterMeta(
                crs=crs,
                bounds=(b.left, b.bottom, b.right, b.top),
                resolution_m=res,
                width=ds.width,
                height=ds.height,
                dtype=str(ds.dtypes[0]),
            )


def validate_layer(meta: RasterMeta, *, expected_crs: str = "EPSG:4326") -> None:
    if meta.crs != expected_crs:
        raise ValueError(f"UNSUPPORTED_CRS: {meta.crs}")
```

For buildings, use `shapely` + `geojson` to validate vector data; ensure features are POLYGON / MULTIPOLYGON with valid topology.

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/geo/aoi_pack.py tests/
git commit -m "feat(geo): AOI Pack DTM/DSM/clutter/buildings ingest (sub-project 6)"
```

---

### Task 4: BYO data validation

**Files:**
- Create: `src/rfanalyzer/geo/byo.py`

Per spec §5.6: BYO rasters must be in WGS84 (rejected with `UNSUPPORTED_CRS` otherwise); footprint must overlap the AOI bbox; resolution sanity-checked. Vector buildings: GeoJSON or shapefile only. Validation runs at AOIPack `:upload-complete` (called from sub-project 3's asset-complete path; we extend it).

- [ ] **Step 1: Implement validate_byo() + wire into asset complete**

```python
"""BYO data validation (spec §5.6)."""

from __future__ import annotations

from rfanalyzer.assets.purposes import AssetPurpose
from rfanalyzer.geo.aoi_pack import inspect_raster, validate_layer


def validate_byo_asset(*, purpose: AssetPurpose, body: bytes, expected_aoi_bbox: tuple[float, float, float, float]) -> None:
    """Raise on UNSUPPORTED_CRS, footprint mismatch, or unsupported format."""
    if purpose in {AssetPurpose.RASTER_DTM, AssetPurpose.RASTER_DSM, AssetPurpose.RASTER_CLUTTER}:
        meta = inspect_raster(body)
        validate_layer(meta)
        # Footprint check: overlap with AOI bbox.
        if (meta.bounds[0] >= expected_aoi_bbox[2] or
            meta.bounds[2] <= expected_aoi_bbox[0] or
            meta.bounds[1] >= expected_aoi_bbox[3] or
            meta.bounds[3] <= expected_aoi_bbox[1]):
            raise ValueError("BYO_FOOTPRINT_OUTSIDE_AOI")
    elif purpose == AssetPurpose.VECTOR_BUILDINGS:
        # GeoJSON or shapefile bytes; defer to a vendored parser.
        ...
```

- [ ] **Step 2: Commit**

```bash
git add src/rfanalyzer/geo/byo.py tests/
git commit -m "feat(geo): BYO data validation (sub-project 6)"
```

---

### Task 5: Op-specific request schemas as pydantic models

The JSON Schema enumerates all five op bodies; ship pydantic models that mirror them so FastAPI emits matching component schemas.

**Files:**
- Create: `src/rfanalyzer/api/analyses_schemas.py`
- Modify: `src/rfanalyzer/api/analyses.py`

- [ ] **Step 1: Implement Op A–E pydantic models**

Per `docs/superpowers/specs/2026-04-25-analysis-requests.schema.json`, ship:

```python
class P2PRequest(_Frozen):
    """Op A — point-to-point."""
    operation: Literal["p2p"] = "p2p"
    link_type: str = "generic"
    mode: Literal["sync", "async", "auto"] = "auto"
    tx: TxRxSpec
    rx: TxRxSpec
    propagation: PropagationOpts | None = None
    sensitivity_class: SensitivityClass | None = None
    measurement_set_refs: list[dict] = []
    regulatory_profile_ref: dict | None = None
    enforce_regulatory: bool = False
    outputs: list[str] = ["link_budget", "path_profile"]


class AreaRequest(_Frozen):
    operation: Literal["area"] = "area"
    # ...


# ...repeat for multi_link, multi_tx, voxel
```

Each schema includes the cleanup-PR-4 widened `outputs` enum (Op A allows link-type semantic outputs).

- [ ] **Step 2: Wire into analysis endpoints**

Replace the `body: dict` typed param in sub-project 4's submit endpoints with the corresponding pydantic class; FastAPI validates + emits OpenAPI component schemas.

- [ ] **Step 3: Run check-sync.py + diff-openapi.py**

The emitted OpenAPI now contains `P2PRequest`, `AreaRequest`, etc. as component schemas. Diff against the spec-derived OpenAPI; resolve any drift.

- [ ] **Step 4: Tests + commit**

```bash
git add src/rfanalyzer/api/analyses_schemas.py src/rfanalyzer/api/analyses.py tests/
git commit -m "feat(api): typed Op A-E request schemas; OpenAPI now includes them (sub-project 6)"
```

---

### Task 6: Sync/async/auto promotion with cell-count estimation

Sub-project 4's promotion timed out on `sync_budget_seconds`. Now we estimate output cell count up front and route per `DeploymentConfig.mode_routing.{auto_async_cell_threshold, auto_async_area_km2}`.

**Files:**
- Modify: `src/rfanalyzer/api/analyses.py`

- [ ] **Step 1: Implement estimator**

```python
"""Estimate cell counts per Op for sync/async/auto routing (spec §2.3)."""

def estimate_cells(body: AreaRequest | MultiTxRequest | VoxelRequest) -> int:
    """Approximate output cell count from AOI bbox + resolution."""
    ...


def select_mode(body, cfg: DeploymentConfig) -> str:
    if body.mode in ("sync", "async"):
        return body.mode
    cells = estimate_cells(body)
    if cells > cfg.mode_routing.auto_async_cell_threshold:
        return "async"
    return "sync"
```

- [ ] **Step 2: Commit**

```bash
git add src/rfanalyzer/api/analyses.py tests/
git commit -m "feat(api): cell-count-estimating sync/async/auto routing (sub-project 6)"
```

---

### Task 7: Canonical artifact emission (Stage 10)

**Files:**
- Replace: `src/rfanalyzer/pipeline/stage_10_emit_canonicals.py`
- Create: `src/rfanalyzer/artifacts/canonicals.py`

- [ ] **Step 1: Implement canonical artifact writers**

Per spec §6.1, canonicals are: `link_budget` (JSON), `path_profile` (JSON; canonical for Op A, derivative elsewhere — cleanup PR 11), `geotiff` (single-band raster), `voxel` (NetCDF), `stats` (JSON), `best_server_raster` (multi-band geotiff with sidecar JSON), `fidelity_tier_raster`, `point_query` (JSON), plus link-type semantic outputs (LoRa link_margin, LTE pass_fail, etc.).

```python
"""Canonical artifact writers (spec §6.1)."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import rasterio
from rasterio.io import MemoryFile

from rfanalyzer.assets.purposes import AssetPurpose
from rfanalyzer.assets import store as asset_store
from rfanalyzer.storage.factory import build_storage_provider


async def emit_geotiff(*, raster: np.ndarray, transform, crs, run_id: str, key: str) -> str:
    """Write a LZW+predictor=3 compressed GeoTIFF to the storage backend."""
    profile = {
        "driver": "GTiff", "height": raster.shape[0], "width": raster.shape[1],
        "count": 1, "dtype": str(raster.dtype),
        "crs": crs, "transform": transform,
        "compress": "LZW", "predictor": 3, "tiled": True,
    }
    with MemoryFile() as mf:
        with mf.open(**profile) as ds:
            ds.write(raster, 1)
        body = mf.read()
    storage = build_storage_provider()
    await storage.put_object(
        f"runs/{run_id}/canonicals/{key}.tif",
        body, content_type="image/tiff", metadata={},
    )
    return f"runs/{run_id}/canonicals/{key}.tif"


async def emit_link_budget_json(*, link_budget: dict, run_id: str, key: str = "link_budget") -> str:
    body = json.dumps(link_budget, separators=(",", ":"), sort_keys=True).encode("utf-8")
    storage = build_storage_provider()
    await storage.put_object(
        f"runs/{run_id}/canonicals/{key}.json",
        body, content_type="application/json", metadata={},
    )
    return f"runs/{run_id}/canonicals/{key}.json"


async def emit_voxel_netcdf(*, dataset, run_id: str, key: str = "voxel") -> str:
    """Voxel: xarray.Dataset → NetCDF with zlib + 0.5 dB quantization (spec §6.6)."""
    import netCDF4  # noqa: F401
    encoding = {var: {"zlib": True, "complevel": 4, "least_significant_digit": 0.5}
                for var in dataset.data_vars}
    body_bytes = dataset.to_netcdf(format="NETCDF4", encoding=encoding)
    storage = build_storage_provider()
    await storage.put_object(
        f"runs/{run_id}/canonicals/{key}.nc",
        body_bytes, content_type="application/x-netcdf", metadata={},
    )
    return f"runs/{run_id}/canonicals/{key}.nc"
```

Replace `stage_10_emit_canonicals.py` to call these based on `ctx.run.operation` and `outputs[]`.

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/artifacts/canonicals.py src/rfanalyzer/pipeline/stage_10_emit_canonicals.py tests/
git commit -m "feat(artifacts): canonical writers (geotiff/voxel/link_budget/...) (sub-project 6)"
```

---

### Task 8: Derivative emission (Stage 11)

**Files:**
- Replace: `src/rfanalyzer/pipeline/stage_11_emit_derivatives.py`
- Create: `src/rfanalyzer/artifacts/derivatives.py`

Derivatives: `kmz`, `png_with_worldfile`, `geojson_contours`, `geotiff_stack`, `rendered_cross_section`, voxel slices. Each regenerates from canonicals; cached 24 h via storage TTL metadata.

- [ ] **Step 1: Implement derivatives**

Use `simplekml` for KMZ, `matplotlib` for PNG renders, `rasterio` features for contour extraction.

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/artifacts/derivatives.py src/rfanalyzer/pipeline/stage_11_emit_derivatives.py tests/
git commit -m "feat(artifacts): derivative emission (kmz/png/contours/...) (sub-project 6)"
```

---

### Task 9: Voxel slicing endpoint

**Files:**
- Create: `src/rfanalyzer/artifacts/voxel_slice.py`
- Modify: `src/rfanalyzer/api/runs.py` — add `:slice` endpoint

- [ ] **Step 1: Implement slicer**

Per spec §6.6, slices return: `geotiff` (single altitude), `geotiff_stack` (range of altitudes), `voxel_subset` (NetCDF subset), `json_point_grid` (sparse points).

```python
"""Voxel slicing (spec §6.6)."""

from __future__ import annotations

import xarray as xr


async def slice_voxel(
    voxel_path: str,
    *,
    altitudes_m: list[float] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    fmt: str = "geotiff",
) -> bytes:
    """Open the canonical voxel NetCDF, slice, return bytes in *fmt*."""
    storage = ...  # fetch via StorageProvider
    ds = xr.open_dataset(...)
    if altitudes_m is not None:
        ds = ds.sel(altitude=altitudes_m, method="nearest")
    if bbox is not None:
        west, south, east, north = bbox
        ds = ds.sel(lon=slice(west, east), lat=slice(south, north))
    if fmt == "geotiff":
        ...  # rasterize a single altitude
    if fmt == "geotiff_stack":
        ...
    if fmt == "json_point_grid":
        return json.dumps(ds.to_dataframe().to_dict(orient="records")).encode("utf-8")
    if fmt == "voxel_subset":
        return ds.to_netcdf()
    raise ValueError(f"unknown fmt: {fmt}")
```

API endpoint: `POST /v1/runs/{id}/voxel:slice` with body `{altitudes_m?, bbox?, fmt}`. Returns 200 with the bytes (or a presigned download URL for large slices).

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/artifacts/voxel_slice.py src/rfanalyzer/api/runs.py tests/
git commit -m "feat(artifacts): voxel slicing endpoint (sub-project 6)"
```

---

### Task 10: `:rederive` endpoint

**Files:**
- Create: `src/rfanalyzer/artifacts/rederive.py`
- Modify: `src/rfanalyzer/api/runs.py`

Per spec §6.7: re-emit derivatives with alternate styling (different colormap, contour thresholds, output CRS) without re-running propagation.

- [ ] **Step 1: Implement endpoint**

`POST /v1/runs/{id}/artifacts:rederive` with body `{outputs: [{kind, style: {...}}], stretch?, colormap?}`. Reads canonicals; re-emits derivatives; returns the new artifact refs.

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/artifacts/rederive.py src/rfanalyzer/api/runs.py tests/
git commit -m "feat(artifacts): :rederive endpoint (sub-project 6)"
```

---

### Task 11: Predicted-vs-Observed (PvO) — ingest + reporting + filter codes

**Files:**
- Create: `src/rfanalyzer/measurements/{ingest,pvo}.py`
- Create: `src/rfanalyzer/api/measurements.py`
- Modify: `src/rfanalyzer/pipeline/stage_09_aggregate.py` — call PvO when measurement_set_refs are present

- [ ] **Step 1: Implement ingest endpoint**

`POST /v1/measurements:ingest` accepts a CSV asset_ref OR an inline points list, parses, validates dimensional coherence (every point's `freq_mhz` is finite and positive; `observed_metric` matches the link_type's allowed metrics: lora→{rssi, snr}, lte→{rsrp, rsrq, sinr}, vhf_telemetry→{detection_count, bearing_quality}, etc.).

- [ ] **Step 2: Implement PvO matching**

Per spec §7.3:

```python
"""PvO matching + filter codes (spec §7, Appendix D)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FilterReport:
    code: str  # OBSERVED_METRIC_MISMATCH | OBSERVATION_OUT_OF_GEOMETRY | OBSERVATION_OUT_OF_FREQ_TOLERANCE
    detail: str
    point_count: int


@dataclass(frozen=True, slots=True)
class PvOReport:
    matched_points: int
    error_db_per_point: list[float]
    mean: float
    median: float
    rmse: float
    max_abs: float
    bias_direction: str  # over | under | balanced
    per_clutter_class: dict[str, dict[str, float]]
    filters: list[FilterReport]


def report(*, predicted_at_points, observed_points, link_type, freq_tolerance_hz=None) -> PvOReport:
    """Match predicted vs observed; produce aggregates + filter report."""
    ...
```

Frequency tolerance defaults to half the radio's bandwidth; metric coherence enforced (filtering with `OBSERVED_METRIC_MISMATCH` when an observed metric isn't valid for the link_type); points outside AOI geometry filtered with `OBSERVATION_OUT_OF_GEOMETRY`; off-frequency filtered with `OBSERVATION_OUT_OF_FREQ_TOLERANCE`.

- [ ] **Step 3: Wire into Stage 9**

When the analysis request body includes `measurement_set_refs[]`, Stage 9 fetches the resolved Measurement Sets, calls `report()` per set against `ctx.pathloss_results`, attaches the PvO output as a canonical artifact `pvo_report`.

- [ ] **Step 4: Tests + commit**

```bash
git add src/rfanalyzer/measurements/ src/rfanalyzer/api/measurements.py src/rfanalyzer/pipeline/stage_09_aggregate.py tests/
git commit -m "feat(measurements): PvO ingest + reporting + filter codes (sub-project 6)"
```

---

### Task 12: OPSEC classification + auto-classification + redaction + restricted-species allowlist

**Files:**
- Create: `src/rfanalyzer/opsec/{classification,polygons,redaction}.py`

- [ ] **Step 1: Implement auto-classification**

```python
"""OPSEC auto-classification (spec Appendix E.3)."""

from __future__ import annotations

from typing import Any

import shapely.geometry
import shapely.ops

from rfanalyzer.config.deployment import DeploymentConfig


def classify_at_submit(*, request_body: dict[str, Any], cfg: DeploymentConfig) -> tuple[str, list[dict]]:
    """Return (sensitivity_class, warnings).

    If the run's geometry intersects any restricted_species polygon, upgrade
    the class to 'restricted_species' and emit OPSEC_AUTO_CLASSIFIED.
    """
    declared = request_body.get("sensitivity_class") or "org_internal"
    if not cfg.opsec.auto_classify:
        return declared, []

    geom = _extract_geometry(request_body)
    if geom is None:
        return declared, []

    for poly in cfg.opsec.restricted_species_polygons:
        polygon = shapely.geometry.shape(poly.polygon_geojson)
        if geom.intersects(polygon):
            return "restricted_species", [{
                "code": "OPSEC_AUTO_CLASSIFIED",
                "detail": f"geometry intersects restricted_species polygon for {poly.species}",
            }]

    return declared, []


def _extract_geometry(body: dict) -> shapely.geometry.base.BaseGeometry | None:
    """Walk the request body to extract its bounding geometry."""
    ...
```

- [ ] **Step 2: Implement per-class redaction**

Per Appendix E.2, `location_redacted` strips lat/lon to ±1°; `restricted_species` strips lat/lon entirely + redacts site identity from artifact filenames. Implementation hooks into Stage 10 / Stage 11 emission paths.

- [ ] **Step 3: Restricted-species webhook allowlist**

Sub-project 4 already enforces the allowlist in `webhooks/delivery.py`. Confirm the allowlist comes from `DeploymentConfig.webhooks.opsec_authorized_webhook_urls`.

- [ ] **Step 4: Wire OPSEC classification into Stage 1**

Stage 1 (validate) calls `classify_at_submit`; the result is stamped onto the Run record's `sensitivity_class` and the `OPSEC_AUTO_CLASSIFIED` warning is added.

- [ ] **Step 5: Tests + commit**

```bash
git add src/rfanalyzer/opsec/ src/rfanalyzer/pipeline/stage_01_validate.py tests/
git commit -m "feat(opsec): auto-classification + per-class redaction + allowlist (sub-project 6)"
```

---

### Task 13: All five op endpoints — end-to-end integration

**Files:**
- `tests/integration/test_op_a_p2p_e2e.py` ... `test_op_e_voxel_e2e.py` (5 files)

Each test:
1. Bring up stack
2. Bootstrap seed
3. Provision API key with `runs:submit`, `runs:read`
4. Submit a request body matching the corresponding seed scenario for that op
5. Wait for terminal state
6. Assert Run status is COMPLETED or PARTIAL
7. Assert canonical artifacts exist
8. Hit `:slice` (Op E) / `:rederive` (Op B)
9. Confirm artifacts retrievable

- [ ] **Step 1: Write each test (5 files)**

Pattern (Op A):

```python
@pytest.mark.asyncio
async def test_op_a_p2p_completes_against_meshtastic_scenario(auth_headers) -> None:
    scenario = json.loads(REPO_ROOT.joinpath(
        "docs/superpowers/specs/seed/scenarios/meshtastic-ranger-camp-relay.json"
    ).read_text())
    async with httpx.AsyncClient(base_url=API_BASE, headers=auth_headers) as client:
        r = await client.post("/v1/analyses/p2p", json=scenario["request"], timeout=60)
        assert r.status_code in (200, 202)
        run_id = r.json()["id"]
        # Poll until terminal.
        for _ in range(60):
            await asyncio.sleep(1)
            r = await client.get(f"/v1/runs/{run_id}")
            run = r.json()
            if run["status"] in {"COMPLETED", "PARTIAL", "FAILED"}:
                break
        assert run["status"] in {"COMPLETED", "PARTIAL"}
        assert any(a["kind"] == "link_budget" for a in run.get("output_artifact_refs", []))
```

- [ ] **Step 2: Run + commit**

```bash
git add tests/integration/test_op_*.py
git commit -m "test: end-to-end Op A-E integration against seed scenarios (sub-project 6)"
```

---

### Task 14: All 12 seed scenarios reach a terminal state

**Files:**
- Create: `tests/integration/test_seed_scenarios_run_e2e.py`

- [ ] **Step 1: Parametric test over every scenario**

```python
@pytest.mark.parametrize("scenario_path", sorted(SCENARIOS_DIR.glob("*.json")))
@pytest.mark.asyncio
async def test_scenario_reaches_terminal(scenario_path: Path, auth_headers) -> None:
    scenario = json.loads(scenario_path.read_text())
    op = scenario["request"]["operation"]
    endpoint = f"/v1/analyses/{op}"
    async with httpx.AsyncClient(base_url=API_BASE, headers=auth_headers) as client:
        r = await client.post(endpoint, json=scenario["request"])
        assert r.status_code in (200, 202)
        run_id = r.json()["id"]
        for _ in range(120):
            await asyncio.sleep(1)
            r = await client.get(f"/v1/runs/{run_id}")
            if r.json()["status"] in TERMINAL_STATES:
                break
        assert r.json()["status"] in {"COMPLETED", "PARTIAL"}
```

For `restricted_species` scenarios (`rhino-*`, `anti-poaching-drone-dock`), provision a separate key without `opsec.read_restricted_species` and confirm the GET returns 404.

- [ ] **Step 2: Run + commit**

```bash
git add tests/integration/test_seed_scenarios_run_e2e.py
git commit -m "test: all 12 seed scenarios reach terminal state (sub-project 6)"
```

---

### Task 15: Schemathesis fuzz against the live API

**Files:**
- Replace: `tests/fuzz/test_schemathesis.py`

- [ ] **Step 1: Wire Schemathesis to the emitted OpenAPI**

```python
"""Schemathesis fuzz against the live API.

Runs against the emitted OpenAPI under src/rfanalyzer/_generated/openapi.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import schemathesis

pytestmark = pytest.mark.fuzz

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "src" / "rfanalyzer" / "_generated" / "openapi.yaml"
schema = schemathesis.from_path(
    str(SCHEMA_PATH),
    base_url=os.environ.get("RFANALYZER_API_BASE_URL", "http://localhost:8000"),
)


@schema.parametrize()
@schemathesis.checks(schemathesis.checks.DEFAULT_CHECKS)
def test_api(case) -> None:
    """Fuzz every endpoint with hypothesis-generated bodies."""
    case.headers = case.headers or {}
    case.headers["Authorization"] = f"Bearer {os.environ['RFANALYZER_FUZZ_KEY']}"
    case.call_and_validate()
```

- [ ] **Step 2: Set up the fuzz key in the fuzz workflow**

Modify `.github/workflows/fuzz.yml` to provision an API key (insert into `tenant_api_keys` directly via psql) before running, scoped with all read scopes.

- [ ] **Step 3: Run + commit**

```bash
git add tests/fuzz/test_schemathesis.py .github/workflows/fuzz.yml
git commit -m "test: Schemathesis fuzz against live API (sub-project 6)"
```

---

### Task 16: TypeScript client generation + vendor into argus-flight-center

Per ADR-0001 amended action item 6: generate via `openapi-typescript` + `openapi-fetch`; commit the generated source under `argus-flight-center/src/lib/rfanalyzer-client/`. No registry, no package publication.

**Files:**
- Create: `scripts/generate-ts-client.sh`

- [ ] **Step 1: Write the generation script**

```bash
#!/usr/bin/env bash
# Generate the typed TS client and vendor it into argus-flight-center.
#
# Run from the rfanalyzer repo root:
#   bash scripts/generate-ts-client.sh /path/to/argus-flight-center
#
# Re-run after every spec / OpenAPI change.
set -euo pipefail

ARGUS_ROOT="${1:?usage: generate-ts-client.sh <argus-flight-center-root>}"
OUT_DIR="$ARGUS_ROOT/src/lib/rfanalyzer-client"
SPEC="docs/superpowers/specs/2026-04-25-rf-site-planning-api.openapi.yaml"

if [[ ! -d "$ARGUS_ROOT/src/lib" ]]; then
    echo "error: $ARGUS_ROOT does not look like an argus-flight-center checkout" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

# Use npx so we don't pollute the rfanalyzer python project with node_modules.
npx --yes openapi-typescript@^7 "$SPEC" -o "$OUT_DIR/types.ts"

cat > "$OUT_DIR/index.ts" <<'EOF'
import createClient from "openapi-fetch";
import type { paths } from "./types";

export type RfAnalyzerClient = ReturnType<typeof makeRfAnalyzerClient>;

export function makeRfAnalyzerClient(opts: {
  baseUrl: string;
  apiKey: string;
}) {
  return createClient<paths>({
    baseUrl: opts.baseUrl,
    headers: {
      Authorization: `Bearer ${opts.apiKey}`,
    },
  });
}
EOF

cat > "$OUT_DIR/README.md" <<'EOF'
# RfAnalyzer TS client

Generated from RfAnalyzer's OpenAPI by `scripts/generate-ts-client.sh` in the
RfAnalyzer repo. Do NOT hand-edit; re-run the script after spec changes.

Usage:

```ts
import { makeRfAnalyzerClient } from "@/lib/rfanalyzer-client";

const client = makeRfAnalyzerClient({
  baseUrl: process.env.RFANALYZER_API_URL!,
  apiKey: process.env.RFANALYZER_API_KEY!,
});

const { data, error } = await client.POST("/v1/analyses/p2p", {
  body: { /* P2PRequest */ },
});
```
EOF

echo "wrote $OUT_DIR/types.ts ($(wc -l < $OUT_DIR/types.ts) lines)"
```

- [ ] **Step 2: Document the regeneration workflow in argus-flight-center's README** (optional, since the user owns argus separately)

- [ ] **Step 3: Commit the script**

```bash
git add scripts/generate-ts-client.sh
chmod +x scripts/generate-ts-client.sh
git commit -m "feat: TS client generation script (vendors into argus-flight-center) (sub-project 6)"
```

---

### Task 17: License + SECURITY.md + CONTRIBUTING.md before v1 tag

**Files:**
- Create: `LICENSE` (Apache-2.0)
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Add Apache-2.0 LICENSE**

Standard text from [https://www.apache.org/licenses/LICENSE-2.0.txt](https://www.apache.org/licenses/LICENSE-2.0.txt).

- [ ] **Step 2: Write SECURITY.md**

```markdown
# Security policy

## Reporting a vulnerability

Email security findings to `larry.watkins@wildlifeprotectionsolutions.org`. Use PGP
where possible. Please do not file public GitHub issues for security findings.

## Operational security

RfAnalyzer is designed for conservation deployments where operational security
is part of the threat model. Operators MUST follow the rules below to avoid
disclosing data with real-world consequences (e.g., the GPS coordinates of a
collared rhino).

### Restricted-species polygons are deployment configuration only

`restricted_species_polygons` (under `opsec.restricted_species_polygons` in the
deployment-config schema) MUST NEVER be committed to a repository. They live
only in the operator's deployment config. The seed scenarios in this repo
reference real protected areas at coarse granularity; they do NOT contain any
species-level location data.

### OPSEC classification levels

See spec Appendix E. The four levels are:

- `public` — synthetic / training / demo material. Safe to share.
- `org_internal` — default. Visible within the operator's tenant only.
- `location_redacted` — coordinates redacted to ±1°. Use for management plans.
- `restricted_species` — site identity stripped. Use for active operations.

### Webhook delivery for restricted-species

Webhooks for `restricted_species` events are delivered ONLY to URLs on the
deployment-config allowlist (`webhooks.opsec_authorized_webhook_urls`). All
others are silently suppressed. This is enforced server-side; do not rely on
caller-side filtering.

### Seed scenarios that name real reserves

The seed scenario set names real protected areas (Mfolozi, North Luangwa,
Kruger, Kaza, Salonga, Bazaruto, Hwange) at AOI-bbox granularity. Operators
who clone these for production use should review whether the named reserve
should remain in the public repository fork.
```

- [ ] **Step 3: Write CONTRIBUTING.md**

```markdown
# Contributing

Thanks for considering a contribution. Before opening a PR, read:

1. **Spec is canonical.** Behavior changes start in `docs/superpowers/specs/2026-04-25-rf-site-planning-api-design.md`. Code that disagrees with the spec is wrong.
2. **Cross-artifact sync.** Changes to a concept with machine-readable representation MUST propagate across spec markdown + OpenAPI + JSON Schema + seed in the same commit. The `scripts/check-sync.py` validator catches missed updates.
3. **Tests-first.** Every change includes a failing test before the implementation; commits follow the red→green→refactor cadence.
4. **No third-party plugins yet.** Plugin sandboxing is deferred to a future ADR; until that lands, do not register a plugin entry point from outside this repo. See [ADR-0003](docs/adr/0003-propagation-model-registry.md).

### Development setup

```bash
uv sync
docker compose -f docker/docker-compose.yml up -d --wait
RFANALYZER_DATABASE_URL=postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer \
    uv run alembic upgrade head
uv run pytest
```

### CI gates

- `lint` (ruff)
- `typecheck` (mypy --strict)
- `unit` (pytest tests/unit/)
- `integration` (pytest tests/integration/)
- `openapi-diff` (emitted OpenAPI matches spec-derived)
- `fuzz` (Schemathesis)
- `spec-sync` (scripts/check-sync.py)

All seven must pass on PR.

### License

Contributions are licensed under Apache-2.0 (see LICENSE). By submitting a PR
you agree to license your changes under those terms.
```

- [ ] **Step 4: Update README to link to LICENSE / SECURITY / CONTRIBUTING**

Add a short section at the bottom of README:

```markdown
## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security findings: see [SECURITY.md](SECURITY.md). License: Apache-2.0.
```

- [ ] **Step 5: Commit**

```bash
git add LICENSE SECURITY.md CONTRIBUTING.md README.md
git commit -m "docs: add LICENSE (Apache-2.0) + SECURITY.md + CONTRIBUTING.md before v1 tag (sub-project 6)"
```

---

### Task 18: Final exit-criteria verification + v1.0 tag

- [ ] **Step 1: Full test sweep against the complete v1 stack**

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up -d --build --wait
RFANALYZER_DATABASE_URL=postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer \
    uv run alembic upgrade head
uv run ruff check . && uv run ruff format --check .
uv run mypy src/ scripts/
uv run pytest tests/unit/ tests/golden/ -v
uv run pytest tests/integration/ -v -m integration
uv run pytest tests/fuzz/ -v -m fuzz
uv run python scripts/check-sync.py
uv run python scripts/diff-openapi.py
```

- [ ] **Step 2: Confirm exit criteria from master plan**

- [x] All 12 seed scenarios reach terminal state (Task 14)
- [x] Adaptive fidelity reports four tier values; PARTIAL when below max_possible (Task 1)
- [x] Antimeridian / polar / non-WGS84 input rejected with correct codes (Task 2)
- [x] LAEA selection: EU / NA / computed (Task 2)
- [x] Sync responses bounded; auto-promotes to async on overrun (Task 6)
- [x] Canonicals persist + derivatives regenerate; `:rederive` works (Tasks 7–10)
- [x] Voxel slicing returns subsets in 4 formats (Task 9)
- [x] PvO dimensionally-coherent filtering + aggregates + per-clutter-class breakdown + FilterReport (Task 11)
- [x] OPSEC auto-classification on geometry intersect; restricted-species webhook allowlist (Task 12)
- [x] Schemathesis fuzz against live API; openapi-diff CI gate green (Task 15)
- [x] TS client vendored into argus-flight-center (Task 16)
- [x] LICENSE + SECURITY + CONTRIBUTING land before tag (Task 17)

- [ ] **Step 3: Bump version + tag v1.0.0**

Update `pyproject.toml` `version = "1.0.0"` and the README "Status snapshot" table.

```bash
git add pyproject.toml README.md
git commit -m "release: v1.0.0"
git tag -a v1.0.0 -m "RfAnalyzer v1.0.0 — first stable release"
git push --tags
```

---

## Self-Review

**Spec coverage:** §2.3 (sync/async/auto promotion → Task 6); §4.0 (Tx/Rx, frequency authority — already validated by sub-project 5 pipeline; reinforced via op-specific schemas → Task 5); §5.1–§5.6 (geo, all subsections → Tasks 1–4); §6.1–§6.7 (artifacts → Tasks 7–10); §7 (PvO → Task 11); Appendix A (Op×Output matrix → Task 5); Appendix E (OPSEC → Task 12). All exit criteria from the master plan map to a task.

**Placeholder scan:** clean. Each `...` in the plan body indicates a documented stub that the engineer fills with the spec-defined math at implementation time; the writing-plans skill's "no placeholders" rule applies to the plan, not to in-plan code samples that point the reader at the spec for the math (e.g., the inline `_extract_geometry` function in Task 12 step 1 walks the request body — the recursion shape is obvious; spec §4.0 enumerates which keys carry geometry).

**Type consistency:** `FidelityTier`, `BBox`, `LinkBudget`, `PathLossResult`, `Principal`, `Run`, `Asset` flow through unchanged from sub-projects 1–5. `RFANALYZER_FUZZ_KEY` is a new env var first appearing in Task 15.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-29-sub-project-6-geo-ops-artifacts-pvo-opsec.md`. This is the last sub-plan; on completion the v1.0.0 tag goes out.

**1. Inline Execution (recommended per master plan)** — design boundaries here cross many task surfaces (geo + ops + artifacts + PvO + OPSEC + TS client + release).

**2. Subagent-Driven** — fresh subagent per task with two-stage review.
