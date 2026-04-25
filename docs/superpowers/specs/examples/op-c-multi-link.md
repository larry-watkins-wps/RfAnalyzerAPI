# Op C — Multi-link site report

Companion to spec §4.0 (Op C pairing rule), §6.5 (combined site score).

**Scenario.** A candidate dock site has stock equipment: LoRa-868 gateway, LTE backhaul modem, and a 2.4 GHz drone C2 radio. Evaluate whether the site has acceptable coverage on all three links across a 5 km × 5 km AOI.

## Request

`POST /v1/analyses/multi_link`

```json
{
  "operation": "multi_link",
  "tx_site":   { "ref": "candidate-dock-A" },
  "rx_templates": [
    { "link_type": "lora",     "equipment": { "ref": "camera-trap-lora-rx",  "owner": "shared" } },
    { "link_type": "lte",      "equipment": { "ref": "lte-handset-omni",     "owner": "shared" } },
    { "link_type": "drone_c2", "equipment": { "ref": "drone-rc-2_4ghz-omni", "owner": "shared" } }
  ],
  "aoi":          { "ref": "candidate-area-A" },
  "resolution_m": 25,
  "outputs":      ["link_budget", "geotiff", "stats", "fidelity_tier_raster"],
  "combined_site_score": {
    "weights_per_link_type": { "lora": 1.0, "lte": 0.7, "drone_c2": 1.5 }
  },
  "min_fidelity_tier": "T2_TERRAIN_PLUS_CLUTTER",
  "mode": "async"
}
```

`tx_equipment_refs` is omitted, so the engine uses `candidate-dock-A.default_equipment_refs[]`. Each Tx is paired with the rx_template whose `link_type` matches its radio's `link_type` (§4.0). If any Tx has a link_type with no matching template, the request fails with `OP_C_RX_TEMPLATE_MISSING`.

## Response (202)

```json
{
  "run_id": "run_2026_04_25_multilink_001",
  "status_url": "https://rf.local/v1/runs/run_2026_04_25_multilink_001/status",
  "mode_executed": "async",
  "reason": "requested"
}
```

## Run record on completion (`PARTIAL`)

```json
{
  "id": "run_2026_04_25_multilink_001",
  "status": "PARTIAL",
  "operation": "multi_link",
  "mode_executed": "async",
  "fidelity_tier_dominant":     "T2_TERRAIN_PLUS_CLUTTER",
  "fidelity_tier_max":          "T2_TERRAIN_PLUS_CLUTTER",
  "fidelity_tier_max_possible": "T3_SURFACE",
  "models_used": [
    { "name": "ITU-R P.1812", "version": "4-with-clutter" },
    { "name": "ITU-R P.1812", "version": "4-with-clutter" },
    { "name": "ITU-R P.528",  "version": "5" }
  ],
  "warnings": [
    { "code": "FIDELITY_DEGRADED", "detail": "AOI provides DSM but min_fidelity_tier=T2 was satisfied without it; T3 not attempted." }
  ],
  "output_artifact_refs": [
    { "key": "link_budget.lora",     "class": "canonical", "size_bytes":  16800, "materialized": true, "download_url": "..." },
    { "key": "link_budget.lte",      "class": "canonical", "size_bytes":  16800, "materialized": true, "download_url": "..." },
    { "key": "link_budget.drone_c2", "class": "canonical", "size_bytes":  16800, "materialized": true, "download_url": "..." },
    { "key": "geotiff.lora",         "class": "canonical", "size_bytes":1100000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." },
    { "key": "geotiff.lte",          "class": "canonical", "size_bytes":1100000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." },
    { "key": "geotiff.drone_c2",     "class": "canonical", "size_bytes":1100000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." },
    { "key": "stats.lora",           "class": "canonical", "size_bytes":   8200, "materialized": true, "download_url": "..." },
    { "key": "stats.lte",            "class": "canonical", "size_bytes":   8200, "materialized": true, "download_url": "..." },
    { "key": "stats.drone_c2",       "class": "canonical", "size_bytes":   8200, "materialized": true, "download_url": "..." },
    { "key": "stats.combined",       "class": "canonical", "size_bytes":   3400, "materialized": true, "download_url": "..." },
    { "key": "fidelity_tier_raster", "class": "canonical", "size_bytes":  44000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." }
  ]
}
```

## `stats.combined` artifact

```json
{
  "weights_per_link_type": { "lora": 1.0, "lte": 0.7, "drone_c2": 1.5 },
  "per_link": {
    "lora":     { "pct_above_sensitivity": 92.1, "median_margin_db": 14.2, "weakest_5pct_db":  -2.8 },
    "lte":      { "pct_above_sensitivity": 71.4, "median_margin_db":  6.8, "weakest_5pct_db": -12.6 },
    "drone_c2": { "pct_above_sensitivity": 88.9, "median_margin_db":  9.4, "weakest_5pct_db":  -3.1 }
  },
  "weakest_link":  "lte",
  "weakest_5pct_score_db_weighted": -8.3,
  "combined_score": 78.6
}
```

## Notes

- Per-link artifacts are namespaced by link_type: `geotiff.lora`, `geotiff.lte`, `geotiff.drone_c2`. The same convention applies to `link_budget.*` and `stats.*`.
- `stats.combined` aggregates across links using the caller-supplied weights; `weakest_link` identifies the gating link_type.
- `drone_c2` is auto-routed to ITU-R P.528 (air-to-ground capable); LoRa and LTE both use P.1812.
- The `PARTIAL` status here illustrates the §5.4 rule: the run completed at `T2`, but the AOI could have produced `T3` if `min_fidelity_tier` had been higher. Engineers downstream see the `FIDELITY_DEGRADED` warning and can re-run if needed.
