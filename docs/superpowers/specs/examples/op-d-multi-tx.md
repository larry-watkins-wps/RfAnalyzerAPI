# Op D — Multi-Tx best-server

Companion to spec §4.0 (Op D), §6.1 (best_server_raster NoData + tiebreak).

**Scenario.** Five candidate dock sites are being considered. Compute per-pixel best-server coverage across a 20 km × 20 km AOI to pick the site with the strongest weakest-pixel reception.

## Request

`POST /v1/analyses/multi_tx`

```json
{
  "operation": "multi_tx",
  "tx_sites": [
    { "site": { "ref": "candidate-dock-A" }, "equipment": { "ref": "lora-868-gateway-stock", "owner": "shared" } },
    { "site": { "ref": "candidate-dock-B" }, "equipment": { "ref": "lora-868-gateway-stock", "owner": "shared" } },
    { "site": { "ref": "candidate-dock-C" }, "equipment": { "ref": "lora-868-gateway-stock", "owner": "shared" } },
    { "site": { "ref": "candidate-dock-D" }, "equipment": { "ref": "lora-868-gateway-stock", "owner": "shared" } },
    { "site": { "ref": "candidate-dock-E" }, "equipment": { "ref": "lora-868-gateway-stock", "owner": "shared" } }
  ],
  "rx_template":  { "ref": "camera-trap-lora-rx", "owner": "shared" },
  "aoi":          { "ref": "candidate-area-large" },
  "resolution_m": 50,
  "outputs":      ["best_server_raster", "geotiff", "stats", "fidelity_tier_raster"],
  "color_map":    "signal_strength_default"
}
```

## Response (202)

```json
{
  "run_id": "run_2026_04_25_multitx_001",
  "status_url": "https://rf.local/v1/runs/run_2026_04_25_multitx_001/status",
  "mode_executed": "async",
  "reason": "requested"
}
```

## Run record on completion

```json
{
  "id": "run_2026_04_25_multitx_001",
  "status": "COMPLETED",
  "operation": "multi_tx",
  "mode_executed": "async",
  "fidelity_tier_dominant":     "T2_TERRAIN_PLUS_CLUTTER",
  "fidelity_tier_max_possible": "T2_TERRAIN_PLUS_CLUTTER",
  "models_used": [{ "name": "ITU-R P.1812", "version": "4-with-clutter" }],
  "output_artifact_refs": [
    { "key": "best_server_raster",   "class": "canonical", "size_bytes":   880000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." },
    { "key": "geotiff.A",            "class": "canonical", "size_bytes":  3520000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." },
    { "key": "geotiff.B",            "class": "canonical", "size_bytes":  3520000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." },
    { "key": "geotiff.C",            "class": "canonical", "size_bytes":  3520000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." },
    { "key": "geotiff.D",            "class": "canonical", "size_bytes":  3520000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." },
    { "key": "geotiff.E",            "class": "canonical", "size_bytes":  3520000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." },
    { "key": "stats",                "class": "canonical", "size_bytes":    18400,                                  "materialized": true, "download_url": "..." },
    { "key": "fidelity_tier_raster", "class": "canonical", "size_bytes":   180000, "expires_at": "2026-05-25T...", "materialized": true, "download_url": "..." }
  ]
}
```

## `best_server_raster` JSON sidecar

```json
{
  "tx_assignments": {
    "0": { "tx_site": null,                "tx_equipment": null,                       "label": "NoData (no Tx closes the link)" },
    "1": { "tx_site": "candidate-dock-A",  "tx_equipment": "lora-868-gateway-stock",   "label": "A" },
    "2": { "tx_site": "candidate-dock-B",  "tx_equipment": "lora-868-gateway-stock",   "label": "B" },
    "3": { "tx_site": "candidate-dock-C",  "tx_equipment": "lora-868-gateway-stock",   "label": "C" },
    "4": { "tx_site": "candidate-dock-D",  "tx_equipment": "lora-868-gateway-stock",   "label": "D" },
    "5": { "tx_site": "candidate-dock-E",  "tx_equipment": "lora-868-gateway-stock",   "label": "E" }
  },
  "tiebreak": "highest fade margin; on equal margin, lowest tx index"
}
```

## `stats` snippet

```json
{
  "per_tx": {
    "candidate-dock-A": { "wins_pct": 18.4, "weakest_5pct_db":  -8.2, "median_margin_db_when_winning":  6.4 },
    "candidate-dock-B": { "wins_pct": 12.1, "weakest_5pct_db": -14.6, "median_margin_db_when_winning":  3.1 },
    "candidate-dock-C": { "wins_pct": 28.7, "weakest_5pct_db":  -1.9, "median_margin_db_when_winning": 11.8 },
    "candidate-dock-D": { "wins_pct": 22.0, "weakest_5pct_db":  -5.3, "median_margin_db_when_winning":  8.7 },
    "candidate-dock-E": { "wins_pct": 14.2, "weakest_5pct_db": -11.8, "median_margin_db_when_winning":  4.0 }
  },
  "no_winner_pct": 4.6
}
```

## Notes

- `best_server_raster` is a UInt8 GeoTIFF; pixel value `0` = NoData (no Tx closes the link), `1..5` map to candidate-dock-A..E in submission order. The tx_assignments JSON sidecar makes the mapping self-describing for downstream tools.
- The wildlife team would likely combine `wins_pct` with `weakest_5pct_db` to pick a site — candidate **C** wins both metrics here.
- Per-Tx `geotiff.{A..E}` artifacts let the team inspect any single candidate's full coverage without re-running.
