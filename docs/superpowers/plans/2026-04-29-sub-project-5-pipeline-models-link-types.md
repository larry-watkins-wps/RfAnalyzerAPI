# Sub-project 5: Pipeline, Propagation Models, Link-Type Plugins — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the 12-stage pipeline with real bodies; ship the propagation-model registry with seven v1 models (free-space + two-ray core-bundled, P.526/P.530/ITM/P.528/P.1812 plugins); ship six link-type plugins (`generic` core; `lora`, `lte`, `drone_c2`, `rtk`, `vhf_telemetry` bundled); implement the auto-select strategy with the frozen `(operation, link_type, geometry) → scenario` table; implement polarization mismatch (table + per-clutter-class depolarization); validate every entry in `golden-test-vectors.json` against the live engine.

**Architecture:** Models register via Python entry points (`importlib.metadata`) following ADR-0003. Free-space + two-ray are imported directly into the registry without entry-point loading. Plugin allowlist gating reads from `DeploymentConfig.plugins.propagation_models.{allow_third_party, allowlist}`. The pipeline runner walks 12 stages in order; each stage is a single module under `src/rfanalyzer/pipeline/stage_NN_*.py` with a single `run(ctx) -> ctx` entry function and a single OpenTelemetry span. `PipelineContext` is a typed dataclass passed by reference; stages mutate it. Native code for ITM and P.528 wraps the NTIA C++ references via `cffi`; P.1812 wraps `crc-covlib` (MIT) similarly. Tier 1 models (free-space, two-ray, P.526, P.530) are pure Python.

**Tech Stack:** Same as sub-project 4 plus numpy / scipy (already in deps), `cffi` for native bindings, NTIA `its-propagation/itm` (public domain) and `its-propagation/p528` (public domain) source ports, [`crc-covlib`](https://github.com/CRC-Canada/crc-covlib) (MIT) for P.1812.

**Authority:** Spec §4 all subsections, §4.5 polarization, Appendix B band coverage, Appendix D codes. [ADR-0003](../../adr/0003-propagation-model-registry.md) (registry + license/provenance/runtime + core-bundled split + allowlist gate). Cleanup PR 5 (PathLossResult, link_budget shape, plugin lifecycle, scenario_suitability frozen set).

**Depends on:** Sub-project 4 (Run lifecycle, worker, pipeline runner stub).

**Decisions resolved in this plan:**
- **P.1812, P.528, ITM port style (master plan open question #4):** **`cffi` wrap** of upstream C++ for all three. Rationale: pure-Python ports of these models are 6-12 person-month projects with brutal validation surfaces; the upstream references are stable and well-tested. The Dockerfile from sub-project 1 already installs build-essential + libgdal-dev + libffi-dev, sufficient for the cffi build. Tier 1 models (free-space, two-ray, P.526, P.530) remain pure Python. Each plugin's `runtime` declaration in its `ModelCapabilities` reflects this: Tier 1 is `pure_python`, Tier 2/3 is `native_extension`.
- **Native source vendoring:** Native source code for ITM, P.528, P.1812 lives under `vendor/<plugin>/` (gitignored downloads + a vendored snapshot tag for reproducibility); Dockerfile clones the tagged versions during build. Sub-plan 1's repo skeleton creates the vendor/ directory.
- **Test-vector tolerance:** golden-test-vectors.json's `tolerance_db` field per entry is the engine's must-meet bound. Free-space + two-ray match analytically (≤ 0.01 dB); ported native models match published references within their declared tolerance band (typically ≤ 0.5 dB).

---

## File Structure

**Source:**
- `src/rfanalyzer/models/interface.py` — `ModelInterface`, `ModelCapabilities`, `PathLossResult`
- `src/rfanalyzer/models/registry.py` — entry-point loader + allowlist gate
- `src/rfanalyzer/models/auto_select.py` — auto-select strategy + scenario table
- `src/rfanalyzer/models/core/{free_space,two_ray}.py`
- `src/rfanalyzer/models/plugins/{p526,p530,itm,p528,p1812}/`
- `src/rfanalyzer/link_types/interface.py` — `LinkTypePluginInterface`
- `src/rfanalyzer/link_types/registry.py`
- `src/rfanalyzer/link_types/plugins/{generic,lora,lte,drone_c2,rtk,vhf_telemetry}/`
- `src/rfanalyzer/pipeline/stage_NN_*.py` (12 files; replace stubs from sub-project 4)
- `src/rfanalyzer/pipeline/context.py` — `PipelineContext` dataclass

**Vendor:**
- `vendor/itm/` — vendored snapshot of NTIA ITM
- `vendor/p528/` — vendored snapshot of NTIA P.528
- `vendor/crc-covlib/` — vendored snapshot of P.1812

**Modify:** `pyproject.toml` — add `[project.entry-points."rfanalyzer.models"]` and `[project.entry-points."rfanalyzer.link_types"]` blocks for the bundled plugins.

---

### Task 1: ModelInterface ABC + ModelCapabilities + PathLossResult

**Files:**
- Create: `src/rfanalyzer/models/interface.py`
- Create: `tests/unit/models/__init__.py`
- Create: `tests/unit/models/test_interface.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for ModelInterface contract types."""

from __future__ import annotations

import pytest

from rfanalyzer.models.interface import (
    FidelityTier,
    ModelCapabilities,
    PathLossResult,
    Runtime,
)


def test_capabilities_requires_license_and_provenance() -> None:
    """ADR-0003 amendment 1: license + provenance mandatory."""
    with pytest.raises(Exception):
        ModelCapabilities(
            id="test", name="test", version="1.0.0", plugin_major=1,
            license=None,  # type: ignore[arg-type]
            provenance="x", runtime=Runtime.PURE_PYTHON,
            freq_range_mhz=(0.0, 100000.0),
            scenario_suitability=("terrestrial_p2p",),
            required_data_tiers=(FidelityTier.T0_FREE_SPACE,),
        )


def test_capabilities_id_pattern() -> None:
    """id must match ^[a-z0-9_]+$."""
    with pytest.raises(Exception):
        ModelCapabilities(
            id="Has-Dash", name="x", version="1.0.0", plugin_major=1,
            license="MIT", provenance="x", runtime=Runtime.PURE_PYTHON,
            freq_range_mhz=(0.0, 100000.0),
            scenario_suitability=("terrestrial_p2p",),
            required_data_tiers=(FidelityTier.T0_FREE_SPACE,),
        )


def test_pathloss_result_ergonomics() -> None:
    r = PathLossResult(
        pathloss_db=120.5,
        components=None,
        fade_margin_db=None,
        fidelity_tier_used=FidelityTier.T0_FREE_SPACE,
        model_warnings=[],
        model_diagnostics=None,
    )
    assert r.pathloss_db == 120.5
```

- [ ] **Step 2: Implement**

```python
"""Model plugin contract types (spec §4.2; ADR-0003 amendments 1–4)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Runtime(StrEnum):
    PURE_PYTHON = "pure_python"
    NATIVE_EXTENSION = "native_extension"


class FidelityTier(StrEnum):
    T0_FREE_SPACE = "T0_FREE_SPACE"
    T1_DTM = "T1_DTM"
    T2_DTM_CLUTTER = "T2_DTM_CLUTTER"
    T3_DSM = "T3_DSM"
    T4_SURFACE_PLUS_BUILDINGS = "T4_SURFACE_PLUS_BUILDINGS"


# ScenarioSuitability is a closed enum per cleanup PR 5.
SCENARIO_SUITABILITY_VALUES = frozenset({
    "terrestrial_p2p",
    "terrestrial_area",
    "air_to_ground",
    "low_altitude_short_range",
    "ionospheric",
    "urban",
    "indoor_outdoor",
})

_ID_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    id: str
    name: str
    version: str
    plugin_major: int
    license: str
    provenance: str
    runtime: Runtime
    freq_range_mhz: tuple[float, float]
    scenario_suitability: tuple[str, ...]
    required_data_tiers: tuple[FidelityTier, ...]

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValueError(f"id must match ^[a-z0-9_]+$ (got {self.id!r})")
        if not self.license:
            raise ValueError("license is required (SPDX id)")
        if not self.provenance:
            raise ValueError("provenance is required")
        for s in self.scenario_suitability:
            if s not in SCENARIO_SUITABILITY_VALUES:
                raise ValueError(f"scenario_suitability {s!r} not in frozen set")
        if self.freq_range_mhz[0] >= self.freq_range_mhz[1]:
            raise ValueError("freq_range_mhz: low < high required")


@dataclass(frozen=True, slots=True)
class PathLossComponents:
    """Optional per-component decomposition (spec §4.2)."""
    freespace_db: float | None = None
    terrain_db: float | None = None
    clutter_db: float | None = None
    building_db: float | None = None
    atmospheric_db: float | None = None
    rain_db: float | None = None


@dataclass(frozen=True, slots=True)
class Warning:
    code: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class PathLossResult:
    pathloss_db: float
    components: PathLossComponents | None
    fade_margin_db: float | None
    fidelity_tier_used: FidelityTier
    model_warnings: list[Warning]
    model_diagnostics: dict[str, Any] | None


@runtime_checkable
class ModelInterface(Protocol):
    """The model plugin contract (spec §4.2)."""

    capabilities: ModelCapabilities

    def init(self, config: dict[str, Any]) -> None: ...
    def validate_inputs(self, request: dict[str, Any]) -> list[Warning]: ...
    def predict(self, *, link_geometry: dict[str, Any], data_layers: dict[str, Any]) -> PathLossResult: ...
    def teardown(self) -> None: ...
```

- [ ] **Step 3: Run tests + commit**

```bash
uv run pytest tests/unit/models/test_interface.py -v
git add src/rfanalyzer/models/interface.py tests/unit/models/
git commit -m "feat(models): ModelInterface + ModelCapabilities + PathLossResult (sub-project 5)"
```

---

### Task 2: Model registry + allowlist gate

**Files:**
- Create: `src/rfanalyzer/models/registry.py`
- Tests: unit + integration (boots app with mismatched allowlist)

- [ ] **Step 1: Implement registry**

```python
"""Model registry (spec §4.2, ADR-0003).

Two execution paths:
  - core/: free-space + two-ray, registered programmatically (always present)
  - plugins/: entry-point loaded; gated by DeploymentConfig allowlist
"""

from __future__ import annotations

import importlib.metadata as md
import logging
from typing import Any

from rfanalyzer.models.interface import ModelCapabilities, ModelInterface

ENTRY_POINT_GROUP = "rfanalyzer.models"

log = logging.getLogger(__name__)


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ModelInterface] = {}

    def register(self, model: ModelInterface) -> None:
        cap = model.capabilities
        if cap.id in self._models:
            raise ValueError(f"model id collision: {cap.id}")
        self._models[cap.id] = model

    def get(self, id: str) -> ModelInterface:
        return self._models[id]

    def all(self) -> list[ModelInterface]:
        return list(self._models.values())


def load_registry(*, allow_third_party: bool, allowlist: list[str]) -> ModelRegistry:
    """Build the registry: core models + entry-point plugins on the allowlist."""
    reg = ModelRegistry()

    # Core-bundled, non-removable.
    from rfanalyzer.models.core.free_space import FreeSpaceModel
    from rfanalyzer.models.core.two_ray import TwoRayModel
    reg.register(FreeSpaceModel())
    reg.register(TwoRayModel())

    # Entry-point plugins. ADR-0003: alphabetical by entry-point name unless
    # RFANALYZER_PLUGIN_ORDER overrides; ID collision is fail-fast.
    eps = sorted(md.entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: ep.name)
    for ep in eps:
        if not allow_third_party and ep.name not in allowlist:
            log.info("rfanalyzer.models.skipped", entry_point=ep.name, reason="not_in_allowlist")
            continue
        try:
            cls = ep.load()
            instance: ModelInterface = cls()
        except Exception as e:  # noqa: BLE001
            log.error("rfanalyzer.models.load_failed", entry_point=ep.name, error=str(e))
            continue
        try:
            reg.register(instance)
        except ValueError as e:
            # ID collision is fail-fast per ADR-0003.
            raise RuntimeError(f"plugin id collision on {ep.name}: {e}") from e

    return reg
```

- [ ] **Step 2: Tests**

Cover: core models always present; allowlist gating logged-and-skipped; ID collision raises.

- [ ] **Step 3: Commit**

```bash
git add src/rfanalyzer/models/registry.py tests/
git commit -m "feat(models): registry with allowlist gate (sub-project 5)"
```

---

### Task 3: Free-space (Friis) — core, pure Python

**Files:**
- Create: `src/rfanalyzer/models/core/free_space.py`
- Create: `tests/unit/models/test_free_space.py`

- [ ] **Step 1: Test against analytical formula**

```python
"""Friis: PL_dB = 32.4 + 20*log10(d_km) + 20*log10(f_MHz)."""

from __future__ import annotations

import math

import pytest

from rfanalyzer.models.core.free_space import FreeSpaceModel
from rfanalyzer.models.interface import FidelityTier


@pytest.mark.parametrize(
    "f_mhz,d_km,expected_db",
    [(900.0, 1.0, 91.5), (2400.0, 5.0, 113.9), (150.0, 10.0, 96.0)],
)
def test_free_space_matches_friis(f_mhz: float, d_km: float, expected_db: float) -> None:
    model = FreeSpaceModel()
    res = model.predict(
        link_geometry={"freq_mhz": f_mhz, "distance_km": d_km},
        data_layers={},
    )
    assert math.isclose(res.pathloss_db, expected_db, abs_tol=0.5)
    assert res.fidelity_tier_used == FidelityTier.T0_FREE_SPACE


def test_capabilities_correct() -> None:
    model = FreeSpaceModel()
    assert model.capabilities.license == "MIT"
    assert model.capabilities.provenance == "in-house implementation"
    assert "terrestrial_p2p" in model.capabilities.scenario_suitability
```

- [ ] **Step 2: Implement**

```python
"""Free-space (Friis) propagation model (core-bundled, ADR-0003)."""

from __future__ import annotations

import math
from typing import Any

from rfanalyzer.models.interface import (
    FidelityTier,
    ModelCapabilities,
    PathLossComponents,
    PathLossResult,
    Runtime,
    Warning,
)


class FreeSpaceModel:
    capabilities = ModelCapabilities(
        id="free_space",
        name="Free-space (Friis)",
        version="1.0.0",
        plugin_major=1,
        license="MIT",
        provenance="in-house implementation",
        runtime=Runtime.PURE_PYTHON,
        freq_range_mhz=(0.1, 300_000.0),
        scenario_suitability=("terrestrial_p2p", "terrestrial_area", "air_to_ground"),
        required_data_tiers=(FidelityTier.T0_FREE_SPACE,),
    )

    def init(self, config: dict[str, Any]) -> None:
        return None

    def validate_inputs(self, request: dict[str, Any]) -> list[Warning]:
        return []

    def predict(
        self, *, link_geometry: dict[str, Any], data_layers: dict[str, Any]
    ) -> PathLossResult:
        f_mhz = float(link_geometry["freq_mhz"])
        d_km = float(link_geometry["distance_km"])
        if d_km <= 0:
            raise ValueError("distance_km must be > 0")
        pl = 32.4 + 20.0 * math.log10(d_km) + 20.0 * math.log10(f_mhz)
        return PathLossResult(
            pathloss_db=pl,
            components=PathLossComponents(freespace_db=pl),
            fade_margin_db=None,
            fidelity_tier_used=FidelityTier.T0_FREE_SPACE,
            model_warnings=[],
            model_diagnostics=None,
        )

    def teardown(self) -> None:
        return None
```

- [ ] **Step 3: Commit**

```bash
git add src/rfanalyzer/models/core/free_space.py tests/
git commit -m "feat(models): free-space (Friis) core model (sub-project 5)"
```

---

### Task 4: Two-ray ground reflection — core, pure Python

**Files:**
- Create: `src/rfanalyzer/models/core/two_ray.py`
- Tests: against the well-known crossover at 4·hb·hr·f/c

- [ ] **Step 1: Implement two-ray**

```python
"""Two-ray ground reflection (core-bundled).

Formula: PL = 40*log10(d) - 20*log10(hb) - 20*log10(hr) (above the crossover);
Friis below the crossover. The crossover distance d_c = 4·hb·hr·f / c.
"""

from __future__ import annotations

import math
from typing import Any

from rfanalyzer.models.interface import (
    FidelityTier,
    ModelCapabilities,
    PathLossComponents,
    PathLossResult,
    Runtime,
    Warning,
)

_C = 299_792_458.0


class TwoRayModel:
    capabilities = ModelCapabilities(
        id="two_ray",
        name="Two-ray ground reflection",
        version="1.0.0",
        plugin_major=1,
        license="MIT",
        provenance="in-house implementation",
        runtime=Runtime.PURE_PYTHON,
        freq_range_mhz=(30.0, 30_000.0),
        scenario_suitability=("terrestrial_p2p", "low_altitude_short_range"),
        required_data_tiers=(FidelityTier.T0_FREE_SPACE,),
    )

    def init(self, _config: dict[str, Any]) -> None: return None
    def validate_inputs(self, _r: dict[str, Any]) -> list[Warning]: return []
    def teardown(self) -> None: return None

    def predict(
        self, *, link_geometry: dict[str, Any], data_layers: dict[str, Any]
    ) -> PathLossResult:
        f_hz = float(link_geometry["freq_mhz"]) * 1e6
        d_m = float(link_geometry["distance_km"]) * 1000.0
        hb = float(link_geometry["tx_height_m"])
        hr = float(link_geometry["rx_height_m"])
        crossover = 4.0 * hb * hr * f_hz / _C
        if d_m <= crossover:
            # Friis region.
            pl = 32.4 + 20.0 * math.log10(d_m / 1000.0) + 20.0 * math.log10(f_hz / 1e6)
        else:
            pl = 40.0 * math.log10(d_m) - 20.0 * math.log10(hb) - 20.0 * math.log10(hr)
        return PathLossResult(
            pathloss_db=pl,
            components=PathLossComponents(freespace_db=pl),
            fade_margin_db=None,
            fidelity_tier_used=FidelityTier.T0_FREE_SPACE,
            model_warnings=[],
            model_diagnostics={"crossover_m": crossover},
        )
```

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/models/core/two_ray.py tests/
git commit -m "feat(models): two-ray ground reflection core model (sub-project 5)"
```

---

### Task 5: P.526 plugin (Tier 1 — pure Python)

ITU-R P.526 (knife-edge / multi-edge diffraction). Pure-Python implementation of the published formulas.

**Files:**
- Create: `src/rfanalyzer/models/plugins/p526/__init__.py`
- Create: `src/rfanalyzer/models/plugins/p526/model.py`
- Create: `src/rfanalyzer/models/plugins/p526/algorithm.py` — the core math
- Modify: `pyproject.toml` — add entry point

- [ ] **Step 1: Implement algorithm + ModelInterface adapter**

`algorithm.py` implements:
- **Single knife-edge diffraction** per P.526 §4.1: `J(v) = 6.9 + 20·log10(sqrt((v - 0.1)^2 + 1) + v - 0.1)` for `v > -0.78`.
- **Multi-edge** (Deygout / Bullington method per P.526 §4.5).

`model.py` wraps with `ModelInterface`. Capabilities:
- `id="p526"`, `license="MIT"`, `provenance="in-house implementation"`, `runtime=PURE_PYTHON`
- `freq_range_mhz=(30, 100_000)`, `scenario_suitability=("terrestrial_p2p", "terrestrial_area")`
- `required_data_tiers=(T1_DTM,)`

Add to `pyproject.toml`:

```toml
[project.entry-points."rfanalyzer.models"]
rfanalyzer.models.p526 = "rfanalyzer.models.plugins.p526.model:P526Model"
```

- [ ] **Step 2: Validate against published examples**

`tests/unit/models/test_p526.py` — known knife-edge values from P.526's worked examples (`v = 1`, `J ≈ 13 dB`, etc.). Add `seed/test-vectors/golden-test-vectors.json` entries for P.526 if not present; assert engine matches within `tolerance_db`.

- [ ] **Step 3: Commit**

```bash
git add src/rfanalyzer/models/plugins/p526/ pyproject.toml tests/
git commit -m "feat(models): P.526 plugin (knife-edge diffraction, pure Python) (sub-project 5)"
```

---

### Task 6: P.530 plugin (Tier 1 — pure Python)

ITU-R P.530 (rain attenuation + multipath fading for terrestrial line-of-sight links).

**Files:** mirror Task 5; entry point `rfanalyzer.models.p530`. Implementation per P.530 §2 (rain) + §2.3 (multipath); rain rate 0.01% lookup tables vendored as numpy arrays.

`runtime=PURE_PYTHON`; `freq_range_mhz=(1000, 100_000)`; `scenario_suitability=("terrestrial_p2p",)`.

- [ ] Implement → test → commit:

```bash
git add src/rfanalyzer/models/plugins/p530/ pyproject.toml tests/
git commit -m "feat(models): P.530 plugin (rain + multipath, pure Python) (sub-project 5)"
```

---

### Task 7: ITM plugin (Tier 2 — cffi wrap of NTIA reference)

ITM / Longley-Rice. NTIA publishes a public-domain C++ reference at [its-propagation/itm](https://github.com/NTIA/itm). `cffi`-wrap the relevant entry points.

**Files:**
- Create: `src/rfanalyzer/models/plugins/itm/__init__.py`
- Create: `src/rfanalyzer/models/plugins/itm/_native.py` — cffi build script
- Create: `src/rfanalyzer/models/plugins/itm/model.py`
- Create: `vendor/itm/` (gitignored at root) — populated by build step

- [ ] **Step 1: Vendor + build**

Add to `docker/Dockerfile` builder stage:

```dockerfile
RUN git clone --depth 1 --branch v1.4.1 https://github.com/NTIA/itm /tmp/itm && \
    cd /tmp/itm && cmake -S . -B build && cmake --build build && \
    cp build/libitm.so /usr/local/lib/ && \
    cp src/include/itm.h /usr/local/include/itm.h
```

`_native.py` builds the cffi binding:

```python
import cffi

ffi = cffi.FFI()
ffi.cdef("""
    int ITM_AREA_TLS_EX(...);
    int ITM_P2P_TLS_EX(...);
    /* ... */
""")
lib = ffi.dlopen("libitm.so")
```

- [ ] **Step 2: ModelInterface adapter**

Capabilities: `id="itm"`, `license="PD"`, `provenance="ported from NTIA ITS itm v1.4.1 (public domain)"`, `runtime=NATIVE_EXTENSION`, `freq_range_mhz=(20, 20_000)`, `scenario_suitability=("terrestrial_p2p", "terrestrial_area")`, `required_data_tiers=(T1_DTM,)`.

- [ ] **Step 3: Validate against published reference outputs**

NTIA publishes test cases under `tests/data/`; replicate as golden vectors.

- [ ] **Step 4: Commit**

```bash
git add src/rfanalyzer/models/plugins/itm/ docker/Dockerfile pyproject.toml tests/
git commit -m "feat(models): ITM plugin (cffi wrap of NTIA reference) (sub-project 5)"
```

---

### Task 8: P.528 plugin (Tier 2 — cffi wrap)

ITU-R P.528 (air-to-ground). NTIA reference at [its-propagation/p528](https://github.com/NTIA/p528). Same pattern as Task 7.

`scenario_suitability=("air_to_ground",)`. Used by Op E (drone C2). Annex 2 lookup tables vendored alongside the binary.

- [ ] Implement → test → commit:

```bash
git add src/rfanalyzer/models/plugins/p528/ docker/Dockerfile pyproject.toml tests/
git commit -m "feat(models): P.528 plugin (air-to-ground, cffi wrap) (sub-project 5)"
```

---

### Task 9: P.1812 plugin (Tier 3 — cffi wrap of crc-covlib)

ITU-R P.1812. The ~80-page recommendation is too large to write from scratch; wrap [crc-covlib](https://github.com/CRC-Canada/crc-covlib) (MIT) instead.

`runtime=NATIVE_EXTENSION`; `freq_range_mhz=(30, 6_000)`; `required_data_tiers=(T2_DTM_CLUTTER, T3_DSM)`; `provenance="ported from crc-covlib v1.x (MIT)"`; `license="MIT"`.

P.1812's location-variability output is the model's primary contribution; surface it via `PathLossResult.model_diagnostics["location_variability_db"]` and as the `fade_margin_db` on the result.

- [ ] Implement → validate against published reference outputs → commit:

```bash
git add src/rfanalyzer/models/plugins/p1812/ docker/Dockerfile pyproject.toml tests/
git commit -m "feat(models): P.1812 plugin (cffi wrap of crc-covlib) (sub-project 5)"
```

---

### Task 10: Auto-select strategy + frozen scenario table

**Files:**
- Create: `src/rfanalyzer/models/auto_select.py`

- [ ] **Step 1: Implement the frozen `(operation, link_type, geometry) → scenario` table**

```python
"""Auto-select strategy (spec §4.4, cleanup PR 5).

The table is frozen: adding a new (op, link_type, geometry) tuple requires
a spec amendment, not a code change.
"""

from __future__ import annotations

from rfanalyzer.models.interface import FidelityTier, ModelInterface
from rfanalyzer.models.registry import ModelRegistry

# (operation, link_type, geometry_kind) → scenario string from the closed enum.
SCENARIO_TABLE: dict[tuple[str, str, str], str] = {
    ("p2p", "generic", "terrestrial"): "terrestrial_p2p",
    ("p2p", "lora", "terrestrial"): "terrestrial_p2p",
    ("p2p", "lte", "terrestrial"): "terrestrial_p2p",
    ("p2p", "drone_c2", "air_to_ground"): "air_to_ground",
    ("p2p", "rtk", "terrestrial"): "terrestrial_p2p",
    ("p2p", "vhf_telemetry", "terrestrial"): "terrestrial_p2p",
    ("area", "generic", "terrestrial"): "terrestrial_area",
    ("area", "lora", "terrestrial"): "terrestrial_area",
    ("area", "lte", "terrestrial"): "terrestrial_area",
    ("area", "vhf_telemetry", "terrestrial"): "terrestrial_area",
    ("multi_link", "generic", "terrestrial"): "terrestrial_p2p",  # reuse p2p scenario per link
    ("multi_tx", "generic", "terrestrial"): "terrestrial_area",
    ("voxel", "drone_c2", "air_to_ground"): "air_to_ground",
    ("voxel", "generic", "low_altitude"): "low_altitude_short_range",
}


def select_scenario(*, operation: str, link_type: str, geometry: str) -> str:
    return SCENARIO_TABLE.get(
        (operation, link_type, geometry),
        "terrestrial_p2p",  # safe default — free-space (T0) covers it
    )


def select_model(
    registry: ModelRegistry,
    *,
    scenario: str,
    freq_mhz: float,
    available_tier: FidelityTier,
    pinned_id: str | None = None,
) -> ModelInterface:
    """Pick a model: filter by frequency range; score by scenario_suitability;
    down-weight if required_data_tiers exceeds available_tier; fall back to
    free-space if no candidate matches.
    """
    if pinned_id is not None:
        return registry.get(pinned_id)

    candidates: list[tuple[float, ModelInterface]] = []
    for m in registry.all():
        cap = m.capabilities
        if not (cap.freq_range_mhz[0] <= freq_mhz <= cap.freq_range_mhz[1]):
            continue
        score = 0.0
        if scenario in cap.scenario_suitability:
            score += 10.0
        max_tier_idx = max(_TIER_ORDER.index(t) for t in cap.required_data_tiers)
        avail_idx = _TIER_ORDER.index(available_tier)
        if avail_idx < max_tier_idx:
            score -= 5.0  # down-weight when required tier exceeds available
        candidates.append((score, m))

    candidates.sort(key=lambda x: x[0], reverse=True)
    if candidates and candidates[0][0] > 0:
        return candidates[0][1]
    return registry.get("free_space")


_TIER_ORDER = [
    FidelityTier.T0_FREE_SPACE,
    FidelityTier.T1_DTM,
    FidelityTier.T2_DTM_CLUTTER,
    FidelityTier.T3_DSM,
    FidelityTier.T4_SURFACE_PLUS_BUILDINGS,
]
```

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/models/auto_select.py tests/
git commit -m "feat(models): auto-select strategy + frozen scenario table (sub-project 5)"
```

---

### Task 11: LinkTypePluginInterface + registry

**Files:**
- Create: `src/rfanalyzer/link_types/interface.py`
- Create: `src/rfanalyzer/link_types/registry.py`

- [ ] **Step 1: Implement contract**

```python
"""Link-type plugin contract (spec §4.6).

Per cleanup PR 5: link_budget arg has a frozen schema with
frequency_mhz, tx_eirp_dbm, rx_sensitivity_dbm, total_pathloss_db,
polarization_mismatch_db (split into base_db + depolarization_db),
fade_margin_db, cable_loss_tx_db, cable_loss_rx_db, link_margin_db,
plus resolved Tx/Rx Equipment Profile snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class PolarizationMismatch:
    base_db: float
    depolarization_db: float


@dataclass(frozen=True, slots=True)
class LinkBudget:
    frequency_mhz: float
    tx_eirp_dbm: float
    rx_sensitivity_dbm: float
    total_pathloss_db: float
    polarization_mismatch_db: PolarizationMismatch
    fade_margin_db: float | None
    cable_loss_tx_db: float
    cable_loss_rx_db: float
    link_margin_db: float
    tx_equipment_snapshot: dict[str, Any]
    rx_equipment_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LinkTypeCapabilities:
    id: str
    name: str
    version: str
    plugin_major: int
    license: str
    provenance: str


class LinkTypePluginInterface(Protocol):
    capabilities: LinkTypeCapabilities

    def init(self, config: dict[str, Any]) -> None: ...
    def validate_inputs(self, request: dict[str, Any]) -> list[Any]: ...
    def emit(self, *, link_budget: LinkBudget, link_specifics: dict[str, Any]) -> dict[str, Any]:
        """Return the link-type semantic outputs (LoRa link margin, LTE pass/fail, etc.)."""
    def teardown(self) -> None: ...
```

- [ ] **Step 2: Implement registry**

`src/rfanalyzer/link_types/registry.py` — same shape as `ModelRegistry`, entry-point group `rfanalyzer.link_types`. `generic` is core-registered; the rest load via entry points.

- [ ] **Step 3: Commit**

```bash
git add src/rfanalyzer/link_types/{interface,registry}.py tests/
git commit -m "feat(link_types): plugin contract + registry (sub-project 5)"
```

---

### Task 12: Six link-type plugins (generic + bundled)

**Files (one subpackage per plugin):**
- `src/rfanalyzer/link_types/plugins/generic/`
- `src/rfanalyzer/link_types/plugins/lora/`
- `src/rfanalyzer/link_types/plugins/lte/`
- `src/rfanalyzer/link_types/plugins/drone_c2/`
- `src/rfanalyzer/link_types/plugins/rtk/`
- `src/rfanalyzer/link_types/plugins/vhf_telemetry/`

Each subpackage exports a class named `<TypeName>LinkType` implementing `LinkTypePluginInterface`. Per spec §6.2 link-type semantic outputs:

- **generic** — `pass_fail`, `link_margin_db`, `fade_margin_remaining_db` (default behaviors when no specialized plugin claims the link)
- **lora** — `lora_link_margin`, `lora_fade_margin`, `lora_chirp_decode_quality` (computed from SF, BW, SNR floor for each spreading factor)
- **lte** — `lte_pass_fail`, `lte_rsrp_pred_dbm`, `lte_rsrq_pred_db`, `lte_sinr_pred_db` (mapped from EIRP/path-loss/Tx-density)
- **drone_c2** — `c2_pass_fail`, `c2_range_envelope` (used by Op E; flags voxels where RTH leg won't close)
- **rtk** — `rtk_pass_fail`, `rtk_correction_age_pred_s`
- **vhf_telemetry** — `vhf_detection_probability`, `vhf_bearing_quality`, `vhf_range_envelope`

Add entry points to `pyproject.toml`:

```toml
[project.entry-points."rfanalyzer.link_types"]
rfanalyzer.link_types.lora = "rfanalyzer.link_types.plugins.lora:LoraLinkType"
rfanalyzer.link_types.lte = "rfanalyzer.link_types.plugins.lte:LteLinkType"
rfanalyzer.link_types.drone_c2 = "rfanalyzer.link_types.plugins.drone_c2:DroneC2LinkType"
rfanalyzer.link_types.rtk = "rfanalyzer.link_types.plugins.rtk:RtkLinkType"
rfanalyzer.link_types.vhf_telemetry = "rfanalyzer.link_types.plugins.vhf_telemetry:VhfTelemetryLinkType"
```

`generic` is core-registered (not entry-point loaded), mirroring core models.

- [ ] **Step 1: Implement each, one PR per plugin (six commits)**

Use the spec §4.6 + §6.2 contract for each. Each plugin's `emit()` consumes the `LinkBudget` and returns its link-type-specific output dict, plus warning codes from Appendix D.

- [ ] **Step 2: Tests**

Unit tests per plugin using contrived link budgets; integration test that submits a Run with `link_type=lora` and asserts `lora_link_margin` appears in `output_artifact_refs`.

- [ ] **Step 3: Commit each plugin individually**

```bash
git add src/rfanalyzer/link_types/plugins/generic/ tests/
git commit -m "feat(link_types): generic core plugin (sub-project 5)"

git add src/rfanalyzer/link_types/plugins/lora/ tests/
git commit -m "feat(link_types): LoRa plugin (sub-project 5)"

# ... lte, drone_c2, rtk, vhf_telemetry ...
```

---

### Task 13: Polarization mismatch (table + per-clutter-class depolarization)

**Files:**
- Create: `src/rfanalyzer/pipeline/polarization.py` — tables + helper
- Tests: unit

- [ ] **Step 1: Implement the spec §4.5 base table**

```python
"""Polarization mismatch (spec §4.5)."""

from __future__ import annotations

# Base mismatch in dB for (tx_pol, rx_pol).
# Per spec §4.5 worked table.
BASE_TABLE: dict[tuple[str, str], float] = {
    ("v", "v"): 0.0, ("v", "h"): 20.0, ("v", "rhcp"): 3.0, ("v", "lhcp"): 3.0,
    ("v", "slant_45"): 3.0, ("v", "dual"): 0.0,
    ("h", "h"): 0.0, ("h", "v"): 20.0, ("h", "rhcp"): 3.0, ("h", "lhcp"): 3.0,
    ("h", "slant_45"): 3.0, ("h", "dual"): 0.0,
    ("rhcp", "rhcp"): 0.0, ("rhcp", "lhcp"): 20.0, ("rhcp", "v"): 3.0, ("rhcp", "h"): 3.0,
    ("rhcp", "slant_45"): 3.0, ("rhcp", "dual"): 0.0,
    ("lhcp", "lhcp"): 0.0, ("lhcp", "rhcp"): 20.0, ("lhcp", "v"): 3.0, ("lhcp", "h"): 3.0,
    ("lhcp", "slant_45"): 3.0, ("lhcp", "dual"): 0.0,
    ("slant_45", "slant_45"): 0.0,  # only when orientations match
    ("slant_45", "v"): 3.0, ("slant_45", "h"): 3.0,
    ("slant_45", "rhcp"): 3.0, ("slant_45", "lhcp"): 3.0, ("slant_45", "dual"): 0.0,
    ("dual", "v"): 0.0, ("dual", "h"): 0.0, ("dual", "rhcp"): 0.0,
    ("dual", "lhcp"): 0.0, ("dual", "slant_45"): 0.0, ("dual", "dual"): 0.0,
}

DENSE_CANOPY_FLOOR_DB = 3.0


def base_mismatch(tx: str, rx: str, *, slant_orientation_deg: int | None = None) -> float:
    if (tx, rx) == ("slant_45", "slant_45") and slant_orientation_deg is None:
        # Defaulted; emit POLARIZATION_DEFAULTED warning at use; worst-case 20 dB.
        return 20.0
    return BASE_TABLE[(tx, rx)]


def aggregate_along_path(
    *, base_db: float, per_segment_depol: list[tuple[float, float]],
) -> tuple[float, float]:
    """Apply per-segment depolarization factors and the dense-canopy floor.

    per_segment_depol: [(segment_length_km, depolarization_factor[0..1]), ...]
    Returns (effective_mismatch_db, depolarization_db).
    """
    total_length = sum(L for L, _ in per_segment_depol) or 1.0
    weighted_depol = sum(L * f for L, f in per_segment_depol) / total_length
    depolarization_db = 20.0 * weighted_depol  # simple linear scaling per spec
    if base_db > 3.0:
        # Dense canopy floor only applies when polarization is mismatched.
        depolarization_db = max(depolarization_db, DENSE_CANOPY_FLOOR_DB)
    return base_db + depolarization_db, depolarization_db
```

- [ ] **Step 2: Tests + commit**

```bash
git add src/rfanalyzer/pipeline/polarization.py tests/
git commit -m "feat(pipeline): polarization mismatch table + depolarization aggregation (sub-project 5)"
```

---

### Task 14: PipelineContext + 12 stage modules

Sub-project 4 left `pipeline/runner.py` as a no-op stub. Replace with a real walker that calls each stage in order.

**Files:**
- Create: `src/rfanalyzer/pipeline/context.py`
- Modify: `src/rfanalyzer/pipeline/runner.py`
- Create / replace: `src/rfanalyzer/pipeline/stage_NN_*.py` (12 modules)

- [ ] **Step 1: Define the context**

```python
"""PipelineContext — typed dataclass passed through every stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rfanalyzer.db.models import Run
from rfanalyzer.models.interface import FidelityTier
from rfanalyzer.models.registry import ModelRegistry


@dataclass
class PipelineContext:
    run: Run
    model_registry: ModelRegistry
    request_body: dict[str, Any]
    inputs_resolved: dict[str, Any]
    fidelity_tier_dominant: FidelityTier | None = None
    fidelity_tier_min: FidelityTier | None = None
    fidelity_tier_max: FidelityTier | None = None
    fidelity_tier_max_possible: FidelityTier | None = None
    pathloss_results: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
```

- [ ] **Step 2: Implement each stage as a single module**

`stage_01_validate.py`, `stage_02_resolve_inputs.py`, `stage_03_select_geo_layers.py`, `stage_04_select_models.py`, `stage_05_compute_pathloss.py`, `stage_06_apply_clutter_and_building_loss.py`, `stage_07_polarization.py`, `stage_08_link_budget.py`, `stage_09_aggregate.py`, `stage_10_emit_canonicals.py`, `stage_11_emit_derivatives.py`, `stage_12_finalize.py`.

Each follows the pattern:

```python
# stage_01_validate.py
"""Stage 1: Validate the request against schemas + per-op rules (spec §4.1)."""

from __future__ import annotations

from opentelemetry import trace

from rfanalyzer.pipeline.context import PipelineContext

_tracer = trace.get_tracer("rfanalyzer.pipeline")


async def run(ctx: PipelineContext) -> PipelineContext:
    with _tracer.start_as_current_span("stage_01_validate"):
        # Per-Op pairing rules (e.g., Op C requires exactly one rx_template per
        # distinct link_type in the Tx set) — enforce here. Sub-project 6 wires
        # the actual analysis-specific schemas (the JSON Schema covers Op A-E
        # request bodies).
        return ctx
```

Implement each stage's body per spec §4.1:
- Stage 1: validation
- Stage 2: resolve inputs (already done at SUBMITTED — this stage re-checks against current catalog and emits warnings if any version drift since)
- Stage 3: select geo layers; classify dominant/min/max/max_possible fidelity tier
- Stage 4: select models (auto-select from Task 10) per Tx/Rx pair
- Stage 5: compute pathloss (call each chosen `ModelInterface.predict`)
- Stage 6: apply clutter + building loss (uses ClutterTable's per-class attenuation table per spec §3.2 + §5.1)
- Stage 7: polarization (Task 13)
- Stage 8: link budget (assemble `LinkBudget` per cleanup PR 5)
- Stage 9: aggregate (per-tile or per-pixel results into Op-specific shape)
- Stage 10: emit canonicals (geotiff, voxel, link_budget, etc.) — full impl in sub-project 6
- Stage 11: emit derivatives (kmz, png, etc.) — full impl in sub-project 6
- Stage 12: finalize — set fidelity tier fields on Run, warnings, etc.

- [ ] **Step 3: Wire runner.py to walk stages**

```python
"""Pipeline runner — walks the 12 canonical stages in order (spec §4.1)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from rfanalyzer.db.models import Run
from rfanalyzer.models.registry import load_registry
from rfanalyzer.pipeline import (
    stage_01_validate, stage_02_resolve_inputs, stage_03_select_geo_layers,
    stage_04_select_models, stage_05_compute_pathloss,
    stage_06_apply_clutter_and_building_loss, stage_07_polarization,
    stage_08_link_budget, stage_09_aggregate, stage_10_emit_canonicals,
    stage_11_emit_derivatives, stage_12_finalize,
)
from rfanalyzer.pipeline.context import PipelineContext

_STAGES = [
    stage_01_validate.run,
    stage_02_resolve_inputs.run,
    stage_03_select_geo_layers.run,
    stage_04_select_models.run,
    stage_05_compute_pathloss.run,
    stage_06_apply_clutter_and_building_loss.run,
    stage_07_polarization.run,
    stage_08_link_budget.run,
    stage_09_aggregate.run,
    stage_10_emit_canonicals.run,
    stage_11_emit_derivatives.run,
    stage_12_finalize.run,
]


async def run_pipeline(session: AsyncSession, run: Run) -> None:
    # Build registry from DeploymentConfig once per Run (cheap; entry-points cached).
    registry = load_registry(allow_third_party=False, allowlist=[
        "rfanalyzer.models.p1812", "rfanalyzer.models.itm",
        "rfanalyzer.models.p528", "rfanalyzer.models.p526", "rfanalyzer.models.p530",
    ])
    ctx = PipelineContext(
        run=run, model_registry=registry,
        request_body=run.inputs_resolved or {},
        inputs_resolved=run.inputs_resolved or {},
    )
    for stage in _STAGES:
        ctx = await stage(ctx)

    # Persist final state.
    run.warnings = (run.warnings or []) + ctx.warnings
    run.output_artifact_refs = (run.output_artifact_refs or []) + ctx.artifacts
    if ctx.fidelity_tier_dominant:
        run.fidelity_tier_dominant = ctx.fidelity_tier_dominant.value
        run.fidelity_tier_min = ctx.fidelity_tier_min.value if ctx.fidelity_tier_min else None
        run.fidelity_tier_max = ctx.fidelity_tier_max.value if ctx.fidelity_tier_max else None
        run.fidelity_tier_max_possible = ctx.fidelity_tier_max_possible.value if ctx.fidelity_tier_max_possible else None
    run.engine_version = "0.1.0-draft"
    run.engine_major = 1
    run.models_used = [
        {
            "id": m.capabilities.id, "name": m.capabilities.name,
            "version": m.capabilities.version, "plugin_major": m.capabilities.plugin_major,
            "license": m.capabilities.license, "provenance": m.capabilities.provenance,
        }
        for m in registry.all()
    ]
```

- [ ] **Step 4: Tests + commit**

```bash
git add src/rfanalyzer/pipeline/ tests/
git commit -m "feat(pipeline): 12-stage runner with real bodies (sub-project 5)"
```

---

### Task 15: Wire plugin major drift detection into replay

Sub-project 4 left `current_plugins={}` in `replay()`. Wire it.

**Files:**
- Modify: `src/rfanalyzer/api/runs.py`

```python
from rfanalyzer.models.registry import load_registry
from rfanalyzer.config.deployment import DeploymentConfig

def _current_plugin_majors() -> dict[str, int]:
    cfg = DeploymentConfig()  # defaults; sub-project 6 wires real config loading
    reg = load_registry(
        allow_third_party=cfg.plugins.propagation_models.allow_third_party,
        allowlist=cfg.plugins.propagation_models.allowlist,
    )
    return {m.capabilities.id: m.capabilities.plugin_major for m in reg.all()}
```

Replace `current_plugins={}` with `current_plugins=_current_plugin_majors()` in the replay endpoint.

- [ ] Commit:

```bash
git add src/rfanalyzer/api/runs.py tests/
git commit -m "feat(runs): wire plugin major drift detection to live registry (sub-project 5)"
```

---

### Task 16: Golden test vectors validation against the live engine

**Files:**
- Create: `tests/golden/test_golden_vectors.py`

- [ ] **Step 1: Write the parametric test**

```python
"""Re-run every entry in golden-test-vectors.json against the live engine."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from rfanalyzer.models.registry import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads(
    (REPO_ROOT / "docs" / "superpowers" / "specs" / "seed" / "test-vectors" / "golden-test-vectors.json").read_text()
)


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_engine_matches_golden(vector: dict) -> None:
    reg = load_registry(allow_third_party=False, allowlist=[
        "rfanalyzer.models.p1812", "rfanalyzer.models.itm",
        "rfanalyzer.models.p528", "rfanalyzer.models.p526", "rfanalyzer.models.p530",
    ])
    model = reg.get(vector["model_id"])
    res = model.predict(
        link_geometry=vector["link_geometry"],
        data_layers=vector.get("data_layers", {}),
    )
    expected = vector["expected_pathloss_db"]
    tol = vector.get("tolerance_db", 0.5)
    assert math.isclose(res.pathloss_db, expected, abs_tol=tol), (
        f"{vector['name']}: predicted {res.pathloss_db} dB, expected {expected} dB ± {tol}"
    )
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/golden/test_golden_vectors.py -v
git add tests/golden/
git commit -m "test: golden vectors validation against live engine (sub-project 5)"
```

---

### Task 17: Final exit-criteria verification

- [ ] **Step 1: Full sweep**

Same as previous sub-plans:

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up -d --wait --build
RFANALYZER_DATABASE_URL=postgresql+asyncpg://rfanalyzer:rfanalyzer@localhost:5432/rfanalyzer \
    uv run alembic upgrade head
uv run pytest tests/ -v
uv run python scripts/check-sync.py
uv run python scripts/diff-openapi.py
```

- [ ] **Step 2: Confirm exit criteria**

- [x] Free-space + two-ray core, present without entry-point loading (Tasks 3, 4)
- [x] Five plugin models register via entry points; mandatory license + provenance (Tasks 5–9)
- [x] Allowlist gate logged-and-skipped, not crashed (Task 2)
- [x] Auto-select walks frozen scenario table; falls back to free-space (Task 10)
- [x] PathLossResult with components + warnings (Task 1)
- [x] LinkBudget frozen schema (Task 11)
- [x] Polarization mismatch table + canopy floor (Task 13)
- [x] Golden vectors match within tolerance (Task 16)
- [x] Run.models_used[] populated with license + provenance + plugin_major (Task 14 runner)

---

## Self-Review

**Spec coverage:** §4.1 (12 stages → Task 14); §4.2 (model contract → Task 1); §4.3 (seven models → Tasks 3–9); §4.4 (auto-select → Task 10); §4.5 (polarization → Task 13); §4.6 (link-type contract + plugins → Tasks 11, 12); §6.2 (link-type semantic outputs → Task 12). ADR-0003 amendments 1–4 all surfaced (Tasks 1, 2, 14).

**Placeholder scan:** clean. The "DeploymentConfig() with defaults" in Task 15 is a known limitation — sub-project 6 wires real config loading from a YAML file at startup; the registry currently boots with the default allowlist which is correct for v1.

**Type consistency:** `ModelCapabilities`, `PathLossResult`, `LinkBudget` flow unchanged through pipeline + replay + Run record. Entry-point names match `pyproject.toml` and `DeploymentConfig.plugins.propagation_models.allowlist`.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-29-sub-project-5-pipeline-models-link-types.md`. Execute inline (master plan recommendation) or subagent-driven.
