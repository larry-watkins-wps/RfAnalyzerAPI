# Op B — Area heatmap

Companion to spec §4.0 (Op B), §6.1 (geotiff/stats/contours/kmz), §8.9 (artifact refs).

**Scenario.** From a candidate LoRa-868 gateway position, produce a coverage heatmap for camera-trap reception across a 10 km × 10 km AOI at 1.5 m AGL.

## Request

`POST /v1/analyses/area`

```json
{
  "operation": "area",
  "tx_site":      { "lat": -24.012, "lon": 31.624 },
  "tx_equipment": {
    "radio":   { "ref": "lora-868-eu",       "owner": "shared" },
    "antenna": { "ref": "omni-6dbi-870mhz",  "owner": "shared" },
    "mount_height_m_agl": 12.0,
    "cable_loss_db":       1.2,
    "azimuth_deg":         0
  },
  "rx_template":               { "ref": "camera-trap-lora-rx", "owner": "shared" },
  "rx_altitude_override_m_agl": 1.5,
  "aoi":                       { "ref": "kruger-north-2026q1" },
  "resolution_m":              30,
  "outputs":          ["geotiff", "geojson_contours", "kmz", "stats", "fidelity_tier_raster", "lora_best_sf"],
  "contour_levels_db":[-110, -120, -130, -140],
  "color_map":        "signal_strength_default",
  "mode":             "auto",
  "webhook_url":      "https://field-tools.local/rf-runs/notify"
}
```

The `tx_equipment` is fully inlined and itself uses Reference shapes for `radio` and `antenna`. Mixing inline + ref is permitted (§2.3).

## Response (async, 202)

```json
{
  "run_id": "run_2026_04_25_area_001",
  "status_url": "https://rf.local/v1/runs/run_2026_04_25_area_001/status",
  "mode_executed": "async",
  "reason": "requested",
  "webhook_url": "https://field-tools.local/rf-runs/notify"
}
```

## Webhook delivery (terminal state)

```
POST https://field-tools.local/rf-runs/notify
X-Signature: HMAC-SHA256(secret, "2026-04-25T14:23:01Z" + "." + body)
Content-Type: application/json

{
  "run_id":        "run_2026_04_25_area_001",
  "status":        "COMPLETED",
  "signed_at":     "2026-04-25T14:23:01Z",
  "artifacts_url": "https://rf.local/v1/runs/run_2026_04_25_area_001",
  "warnings":      [],
  "error":         null
}
```

## Run record on completion (`GET /v1/runs/run_2026_04_25_area_001`)

```json
{
  "id": "run_2026_04_25_area_001",
  "status": "COMPLETED",
  "operation": "area",
  "mode_requested": "auto",
  "mode_executed":  "async",
  "engine_version": "0.2.0",
  "fidelity_tier_dominant":     "T2_TERRAIN_PLUS_CLUTTER",
  "fidelity_tier_min":          "T2_TERRAIN_PLUS_CLUTTER",
  "fidelity_tier_max":          "T2_TERRAIN_PLUS_CLUTTER",
  "fidelity_tier_max_possible": "T2_TERRAIN_PLUS_CLUTTER",
  "models_used": [{ "name": "ITU-R P.1812", "version": "4-with-clutter" }],
  "output_artifact_refs": [
    { "key": "geotiff",              "class": "canonical",  "size_bytes":  6230000, "expires_at": "2026-05-25T14:23:01Z", "materialized": true, "download_url": "..." },
    { "key": "stats",                "class": "canonical",  "size_bytes":     8420,                                       "materialized": true, "download_url": "..." },
    { "key": "fidelity_tier_raster", "class": "canonical",  "size_bytes":   210000, "expires_at": "2026-05-25T14:23:01Z", "materialized": true, "download_url": "..." },
    { "key": "lora_best_sf",         "class": "canonical",  "size_bytes":   210000, "expires_at": "2026-05-25T14:23:01Z", "materialized": true, "download_url": "..." },
    { "key": "geojson_contours",     "class": "derivative", "size_bytes":   142000, "expires_at": "2026-04-26T14:23:01Z", "materialized": true, "download_url": "..." },
    { "key": "kmz",                  "class": "derivative", "size_bytes":  4180000, "expires_at": "2026-04-26T14:23:01Z", "materialized": true, "download_url": "..." }
  ],
  "warnings": [],
  "pinned": false
}
```

## `stats` artifact

```json
{
  "aoi_km2": 100.04,
  "rx_sensitivity_dbm": -123.0,
  "pct_above_sensitivity": 78.4,
  "received_power_dbm": { "mean": -118.2, "median": -116.4, "p5": -141.3, "p95": -98.8 },
  "fade_margin_db":     { "mean":    4.8, "median":    6.6, "p5":  -18.3, "p95":  24.2 },
  "histogram_dbm": [
    { "bin_low": -180, "bin_high": -150, "count":  4823 },
    { "bin_low": -150, "bin_high": -140, "count": 22441 },
    { "bin_low": -140, "bin_high": -130, "count": 49210 },
    { "bin_low": -130, "bin_high": -120, "count": 71834 },
    { "bin_low": -120, "bin_high": -110, "count": 65122 },
    { "bin_low": -110, "bin_high": -100, "count": 35640 },
    { "bin_low": -100, "bin_high":  -90, "count":  6210 }
  ]
}
```

## Notes

- `mode: "auto"` resolved to async because Op B is in the auto-async op set (§2.3).
- The six requested outputs were eagerly materialized at submission. Canonicals (`geotiff`, `stats`, `fidelity_tier_raster`, `lora_best_sf`) persist to their per-class TTL; derivatives (`kmz`, `geojson_contours`) cap at 24 h regardless.
- Re-styling the KMZ with a different colormap or contour set is `POST /v1/runs/run_2026_04_25_area_001/artifacts:rederive {from: "geotiff", to: "kmz", parameters: {color_map: "lora_sf"}}` — no propagation re-run (§6.7).
- `link_budget` was not requested — for area ops, `stats` is the natural aggregate; per-pixel link budgets would balloon storage with little additional value.
