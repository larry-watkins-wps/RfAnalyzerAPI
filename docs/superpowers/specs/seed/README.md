# Seed data

Bundled, system-owned, read-only catalog content. Loaded into the catalog DB and artifact store on first boot of a deployment (§3.4 of the design spec). Operators clone-and-customize but cannot mutate these entries.

## Contents

| File | Purpose |
|---|---|
| [`standard-profile-library.json`](standard-profile-library.json) | All bundled `Antenna` / `RadioProfile` / `EquipmentProfile` / `ClutterTable` records — 18 antennas, 17 radio profiles, 21 equipment profiles, 2 clutter tables. |
| [`antenna_patterns/`](antenna_patterns/) | Bundled antenna-pattern asset bytes (MSI Planet text format). Referenced by `Antenna.pattern_asset_ref` in the library above by `sha256:<hex>`. |
| [`antenna_patterns/MANIFEST.txt`](antenna_patterns/MANIFEST.txt) | `filename TAB sha256 TAB size_bytes` for every bundled pattern. Used by the bootstrap step to register the bytes as Assets (§3.5) before catalog records are loaded. |
| [`generate_patterns.py`](generate_patterns.py) | Reproduces the pattern files. Patterns are committed; the script is the build recipe, not a runtime dependency. |
| [`scenarios/`](scenarios/) | Runnable analysis-request fixtures binding each operation (Op A–E) to a concrete conservation use case using only library entries. **Documentation/test fixtures, not catalog content** — not loaded at boot. |
| [`test-vectors/`](test-vectors/) | Numerical input → expected-output triples for verifying engine implementations against documented formulas (free-space, polarization, cable-loss interpolation, frequency authority, full link budget). **Documentation/test fixtures, not catalog content.** |

## Boot sequence

On first boot, the API service:

1. Computes `sha256` of every file under `antenna_patterns/`, registers them via the asset path (§3.5), short-circuiting the upload (assets with the matching content hash already in store skip transfer).
2. Loads `standard-profile-library.json`, creating every `Antenna` / `RadioProfile` / `EquipmentProfile` / `ClutterTable` record under owner `system`, `share: shared`, `version: 1`. Pattern files are linked by their `sha256:` asset id.
3. Marks the seed run complete; subsequent boots are idempotent (records keyed by `(owner, name)` are reused).

## Coverage

The library is the v1 baseline that every deployment can rely on. It exercises every link-type plugin bundled with the engine:

- `lora` — gateway 868/915, sensors (camera trap, fence, gate, collar, acoustic, mesh, vehicle tracker).
- `lte` — common bands (B1/B3/B20/B28), Cat-M1, NB-IoT, outdoor CPE, handset, vehicle tracker.
- `drone_c2` — 2.4 / 5.8 GHz dock seed.
- `rtk` — 2.4 GHz base seed.
- `vhf_telemetry` — narrowband VHF wildlife collars (large + small mammal), Yagi-equipped hand-held receiver, AIS-class-B-like 162 MHz vessel tracker + coastal shore station.

Plus a deliberate scaffold (`iridium-sbd-modem-tx`) for satellite endpoints whose space-segment link is out of v1 scope — the profile shape round-trips so terrestrial models can be built around it; the link-budget half remains undefined until a satellite link-type plugin is registered.

## Editing

If you change `generate_patterns.py`, re-run it and commit the regenerated `.msi` files **and** the updated `pattern_asset_ref` SHAs in `standard-profile-library.json` together. The hashes are content-addressed identifiers; they must match the file bytes exactly.

The library is treated as part of the contract: removing or renaming a `system`-owned entry is a breaking change for any operator who has cloned-and-customized from it.
