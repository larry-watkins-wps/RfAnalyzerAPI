# Scenario library

Each file in this directory is a runnable analysis-request fixture that binds one of the five operations (§4.0) to a specific real-world conservation use case using **only** seed catalog entries from [`../standard-profile-library.json`](../standard-profile-library.json). Scenarios serve three purposes:

1. **Demonstrate** how the abstract spec composes into concrete deployments.
2. **Regression-test** the engine — each scenario's expected response shape is documented so changes that break it are caught.
3. **Onboard** new operators with copy-paste-ready starting points.

## File shape

Each scenario JSON has the same top-level keys:

| Key | Purpose |
|---|---|
| `id` | Stable identifier matching the filename. |
| `title` | Human-readable one-liner. |
| `summary` | 1–2 paragraphs describing the deployment problem. |
| `operation` | One of `p2p`, `area`, `multi_link`, `multi_tx`, `voxel`. |
| `demonstrates` | List of spec sections / plugins / appendices the scenario exercises. |
| `tags` | Free-form filter tags. |
| `sites` | Inline `Site` definitions for the scenario (these are not part of the seed library — they are scenario-local). |
| `request` | The full request body. POST it to `/v1/analyses/{operation}` to run. |
| `expected` | Expected response shape: `status`, `fidelity_tier_dominant`, key warning codes, key artifact keys, plausible numerical ranges. Used for spec conformance testing. |
| `notes` | Free-form commentary. |

## Conventions

- All `ref` values resolve against the standard profile library — owner `system`, latest version, `share: shared`.
- Coordinates are illustrative locations within actual conservation areas (Kruger, Mfolozi, KAZA region, Okavango). They are **representative** — replace with operator-specific coordinates before running against a real deployment.
- AOI references use placeholder names like `kruger-north-2026q1`; operators will create their own AOI Packs (§5.3) before running. Where a scenario can run against the bundled global baseline, the AOI is omitted.
- Sensitivity classes are set per Appendix E; restricted-species scenarios are explicit so an operator running with `require_explicit_classification_in_polygon: true` does not get an `OPSEC_CLASSIFICATION_REQUIRED` rejection.

## Catalog

| File | Op | Sensitivity | Demonstrates |
|---|---|---|---|
| [`rhino-vhf-collar-tracking.json`](rhino-vhf-collar-tracking.json) | B (Area) | `restricted_species` | `vhf_telemetry` plugin (§4.6); Appendix E auto-classification; Yagi receiver semantics. |
| [`rhino-lora-collar-coverage.json`](rhino-lora-collar-coverage.json) | D (Multi-Tx) | `restricted_species` | Op D best-server with body-worn rx_template; `lora` SF planning over a wildlife home range; Appendix E auto-classification. |
| [`fence-line-lora-monitoring.json`](fence-line-lora-monitoring.json) | D (Multi-Tx) | `org_internal` | Op D best-server raster; `lora` plugin SF planning. |
| [`camera-trap-mesh-coverage.json`](camera-trap-mesh-coverage.json) | C (Multi-link) | `org_internal` | Op C multi-link site report; `lora` + `lte` + `vhf_telemetry` colocated. |
| [`anti-poaching-drone-dock.json`](anti-poaching-drone-dock.json) | E (Voxel) | `restricted_species` | Op E volumetric coverage; `drone_c2` plugin; Operating Volume; Appendix E. |
| [`boundary-rtk-survey.json`](boundary-rtk-survey.json) | A (P2P) | `org_internal` | Op A point-to-point; `rtk` plugin; path profile + cross-section. |
| [`ranger-vhf-handheld-comms.json`](ranger-vhf-handheld-comms.json) | B (Area) | `org_internal` | `generic` link-type for voice radio; PMR-446 patrol-area heatmap. |
| [`multi-jurisdictional-iot.json`](multi-jurisdictional-iot.json) | B (Area) | `org_internal` | `regulatory_profile_ref` (§3.7); transboundary deployment with `enforce_regulatory: true`. |
| [`acoustic-detector-mesh-salonga.json`](acoustic-detector-mesh-salonga.json) | D (Multi-Tx) | `org_internal` | `lora` plugin in worst-case forest clutter; `acoustic-sensor-lora-rx`; tree-cover attenuation. |
| [`marine-ais-patrol-bazaruto.json`](marine-ais-patrol-bazaruto.json) | B (Area) | `org_internal` | AIS-class-B 162 MHz over a marine AOI; two-ray-over-water; `ais-shore-receiver-162` + `ais-tracker-162-vessel`. |
| [`meshtastic-ranger-camp-relay.json`](meshtastic-ranger-camp-relay.json) | A (P2P) | `org_internal` | Op A on the `lora` plugin; Meshtastic LongFast 915 MHz; Fresnel + cross-section artifacts. |
| [`vehicle-tracker-patrol-road.json`](vehicle-tracker-patrol-road.json) | B (Area) | `org_internal` | Mobile `vehicle-tracker-lora-868` rx_template; `lora` SF planning along a road corridor. |

## Running a scenario

The scenarios are not executed automatically; they are fixtures. To run one against a deployed instance:

```bash
curl -X POST https://rf.local/v1/analyses/area \
  -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d "$(jq '.request' docs/superpowers/specs/seed/scenarios/rhino-vhf-collar-tracking.json)"
```

The `expected` block can be diffed against the response in CI to catch regressions.
