# ADR-0003: Pluggable propagation-model registry

**Status:** Accepted
**Date:** 2026-04-27
**Deciders:** Larry Watkins (project owner)
**Supersedes (in part):** [ADR-0001](0001-stack.md) — extends the "Plugin loading" row of the stack table from "entry points" to "entry points + license/runtime/provenance declaration + base-pack distinction + allowlist gate."

## Context

[ADR-0001](0001-stack.md) listed "Pluggable model registry + pluggable link-type registry" as a constraint and chose Python entry points (`importlib.metadata`) as the loading mechanism, but it never justified the registry concept itself. Spec §4.2 specifies the runtime contract (`ModelCapabilities`, `ModelInterface`, lifecycle, version-compat rules) without recording why a registry is the right shape in the first place. This ADR fills that gap and locks in the four amendments that fall out of the v1 implementation strategy below.

The seven propagation models committed in spec §4.3 — **ITU-R P.1812**, **ITU-R P.526**, **ITM / Longley-Rice**, **ITU-R P.528**, **ITU-R P.530**, **free-space (Friis)**, **two-ray ground reflection** — will not all come from the same source, and they will not all be implemented the same way. The implementation tiers are:

- **Tier 1 — write from scratch (MIT, in-house).** Free-space, two-ray, P.526, P.530. Closed-form equations plus small ITU-published rain/diffraction tables. Pure Python.
- **Tier 2 — port a public-domain reference.** ITM and P.528. NTIA / ITS publishes reference C++ implementations in the public domain (`its-propagation/itm`, plus the P.528 Annex 2 lookup tables). Port to pure Python or wrap via `cffi`. Native code likely.
- **Tier 3 — port an MIT-licensed reference.** P.1812 is ~80 pages of equations covering free space, diffraction, troposcatter, ducting, clutter, and location variability; writing from scratch is a multi-month effort with a brutal validation surface. The right base is [`crc-covlib`](https://github.com/CRC-Canada/crc-covlib) from the Communications Research Centre Canada — MIT-licensed, implements both P.1812 and P.452 (P.452 useful for future interference work). Pure-Python port or `cffi` wrap.

The forces this implementation strategy puts on the registry:

- **Licensing heterogeneity.** Three different license origins coexist in the v1 base-pack (in-house MIT, public-domain port, MIT port). Operators may also want to install a private deployment-only plugin under a third license. Without an explicit registry, license posture is invisible at runtime and cannot be audited per Run.
- **Runtime heterogeneity.** Pure Python (Tier 1) and likely-`cffi`-wrapped native code (Tiers 2 and 3) will live side-by-side. Plugins must declare which they are so packaging, deployment, and the future sandboxing ADR can treat them differently without source archaeology.
- **Replay reproducibility.** Spec §3.3 / §8.3 / §4.2 require per-plugin major-drift detection (`MODEL_PLUGIN_MAJOR_DRIFT`, `replay_plugin_major_drift[]`, `REPLAY_ACROSS_PLUGIN_MAJOR`). That mechanism only makes sense if there is a registry to enumerate; hardcoded dispatch cannot express "this Run was computed by P.1812 v2.3.0; replay against current v2.4.1 is a non-major drift."
- **Auto-select scoring (§4.4).** The strategy filters by `freq_range_mhz`, scores by `scenario_suitability`, and down-weights by `required_data_tiers`. Every model must declare these capabilities. The capability-declaration contract *is* the registry contract; the two cannot be separated.
- **Future sandboxing.** ADR-0001 explicitly defers sandboxing to a future ADR. That ADR becomes much harder to write if the plugin loading boundary is not already first-class — it has to retro-fit a boundary instead of formalising one that already exists.
- **Vendor- and use-case-specific extensions.** A future operator may want a proprietary terrain model for a specific deployment without forking the engine. A registry with a stable contract is the only way that works without becoming an implementation-detail tax on the core.

## Decision

**The seven v1 propagation models are exposed through a pluggable registry, with two execution paths and four amendments to the §4.2 contract.**

### Two execution paths

- **Core-bundled models (non-removable).** Free-space (Friis) and two-ray ground reflection are implemented in core, always available, not subject to entry-point loading, plugin-major drift, the third-party allowlist, or `MODEL_PLUGIN_CRASH` retry. They parallel the `generic` link-type at §4.6: trivial, closed-form, always present as the auto-select fallback when nothing else is suitable. A deployment that disables every plugin still has working sanity-bound and short-range models.
- **Plugins.** P.1812, P.526, ITM, P.528, P.530 ship as first-party-reviewed plugins in the v1 base-pack and load via Python entry points (`importlib.metadata`). They follow the §4.2 plugin lifecycle, are subject to plugin-major drift on replay, and are listed by entry-point name in the deployment-config allowlist (see below).

### Four amendments to §4.2 / §3.3

**1. `ModelCapabilities` gains `id`, `license`, `provenance`, `runtime`.**

```
id: str           # ^[a-z0-9_]+$ ; used for pinning and collision checks (was the
                  # implicit "propagation_model_id" referenced at §4.2 but never declared)
name: str         # human-readable, unchanged (e.g., "ITU-R P.1812-7")
license: str      # SPDX id, mandatory (e.g., "MIT", "Apache-2.0", "PD" for public domain)
provenance: str   # mandatory free-text origin, e.g., "in-house implementation",
                  # "ported from NTIA ITS itm v1.4.1 (public domain)",
                  # "ported from crc-covlib v1.x (MIT)"
runtime: enum { "pure_python", "native_extension" }
```

A plugin that fails to declare `license` or `provenance` fails startup. Opt-in declaration prevents license-laundering by omission; defaulting to "UNKNOWN" would let a plugin author skip the question.

**2. Free-space and two-ray are core-bundled non-removable** (described above; codified at §4.2).

**3. Deployment-config gate.** New block under the existing `plugins` object in [`2026-04-25-deployment-config.schema.json`](../superpowers/specs/2026-04-25-deployment-config.schema.json):

```yaml
plugins:
  propagation_models:
    allow_third_party: false              # default; refuses entry points outside allowlist
    allowlist:                            # entry-point names that are trusted
      - rfanalyzer.models.p1812
      - rfanalyzer.models.itm
      - rfanalyzer.models.p528
      - rfanalyzer.models.p526
      - rfanalyzer.models.p530
```

At startup any entry point not on the allowlist is **logged and skipped** (not a startup failure — a typo in the allowlist must not brick a deployment). This is the enforceable form of §4.2's "v1 onboards only first-party-reviewed plugins" sentence.

**4. `Run.models_used[]` gains `license` and `provenance` per entry** so every Run is license-auditable from its record alone, without operators having to read plugin source.

## Alternatives considered

### Hardcoded model dispatch
Rejected. Conflates licensing into the core wheel (a GPL implementation imported into core makes the entire engine GPL). Cannot add a model without an engine release. Cannot express plugin-major drift on replay — the `MODEL_PLUGIN_MAJOR_DRIFT` mechanism is meaningless if there is no registered plugin to drift against. Forecloses third-party private extensions even when the operator wants them.

### Subprocess / IPC plugins (out-of-process workers)
Rejected for v1. The round-trip latency does not fit inside the 25 s sync budget (§2.3) for sync Op A requests, and process supervision adds operational burden with no upside until sandboxing is needed. Sandboxing is a separate question — a future ADR can introduce subprocess-isolated execution as an option (signalled by a new `runtime` enum value) without invalidating the in-process default.

### WASM sandbox now
Rejected. Premature; defers the entire base-pack while the toolchain matures and forces every Tier 2/3 native port through a WASM compile path that none of the upstream references support. The current decision does not preclude WASM later — a future ADR can layer it on top by treating the WASM runtime as an additional `runtime` enum value (e.g., `"wasm"`).

### Default-open allowlist (auto-load any installed entry point)
Rejected. The "first-party plugins only" stance is load-bearing: until sandboxing lands, an operator who `pip install`s an arbitrary plugin is running its code in the engine process. Default-closed with an explicit allowlist makes the trust decision explicit and reviewable.

### Default to `license: "UNKNOWN"` when undeclared
Rejected. Lets a plugin author skip the license question. Mandatory declaration is the only way to keep `Run.models_used[]` license-auditable in practice.

## Consequences

### Required at v1 (cross-artifact fan-out, single commit)

- Spec §4.2 — `ModelCapabilities` block gains `id`, `license`, `provenance`, `runtime`; new "Core-bundled models" subsection; sandboxing paragraph cross-references this ADR.
- Spec §3.3 — `models_used[]` documents `license` and `provenance` per entry.
- OpenAPI — `Run.models_used[]` item schema gains `license` and `provenance`.
- Deployment-config schema — `plugins.propagation_models.{allow_third_party, allowlist}` block.
- README §49 — one-line back-reference to this ADR.
- Spec change log — entry under the v3 audit follow-up section.

### Operational consequences

- Sandboxing remains deferred to a future ADR (no change from §4.2). A misbehaving plugin can crash a worker; the existing `MODEL_PLUGIN_CRASH` retry handles it. Core-bundled models do not participate in this — they cannot crash a worker into `MODEL_PLUGIN_CRASH` because they are not plugins.
- Adding a new third-party plugin requires both installing the package and explicitly listing its entry-point name in the deployment-config allowlist. Two-step opt-in is the v1 trust model.
- `Run.models_used[]` becomes a license-audit surface. An operator can answer "which licenses ran on which Runs?" by querying the Run record alone.
- Plugin authors carry a registration-time burden of declaring SPDX license and free-text provenance. This is small and one-time-per-plugin.

### Deferred follow-ups (out of scope for this ADR)

- **`GET /v1/models` endpoint.** Surfacing the full `ModelCapabilities` (including license/provenance) over HTTP is a natural extension but adds API surface (§2.5 inventory, OpenAPI paths, response schemas) beyond what this ADR scopes. The per-Run audit surface (`Run.models_used[]`) is sufficient for v1 license hygiene; a registry-listing endpoint can land in a follow-up commit.
- **Sandboxing ADR.** Out-of-process / WASM execution stays deferred. When written, it slots in as additional `runtime` enum values without invalidating the in-process default.
- **GPL-licensed plugin support story.** v1 base-pack is MIT-clean by construction. If an operator chooses to install a GPL plugin (e.g., wrapping `itur`), the registry will record it correctly, but the legal posture of running GPL code inside an MIT engine over a network-served API is the operator's call, not RfAnalyzer's. A future ADR may codify operator guidance.
