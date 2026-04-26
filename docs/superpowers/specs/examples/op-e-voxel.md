# Op E — 3D / volumetric coverage

Companion to spec §4.0 (Op E), §6.1 (voxel canonical), §6.6 (slicing).

**Scenario.** An autonomous drone dock (DJI Dock 2 in this deployment) will fly drones over a 6 km × 6 km flight envelope at altitudes 60–120 m AGL. Will the 2.4 GHz C2 link close everywhere across that volume?

## Request

`POST /v1/analyses/voxel`

```json
{
  "operation": "voxel",
  "tx_sites": [
    { "site": { "ref": "olifants-dock" }, "equipment": { "ref": "dji-dock-2-c2-2_4ghz", "owner": "shared" } }
  ],
  "rx_template":      { "ref": "drone-rc-2_4ghz-omni", "owner": "shared" },
  "operating_volume": { "ref": "olifants-flight-envelope-2026q1" },
  "resolution_m":     30,
  "altitude_step_m":       15,
  "outputs":          ["voxel", "c2_pass_fail", "c2_range_envelope", "stats"],
  "voxel_lossless":   false,
  "min_fidelity_tier":"T3_SURFACE",
  "mode":             "async"
}
```

`operating_volume` is a Reference; alternatively the caller could inline `{ bbox, polygon, altitude_min_m_agl, altitude_max_m_agl, altitude_step_m, home_site_ref? }`. The Operating Volume's altitude range × `altitude_step_m` define the voxel's altitude axis. (`home_site_ref` is optional; here `olifants-flight-envelope-2026q1` already pins the dock as its home Site.)

`min_fidelity_tier: T3_SURFACE` requires the AOI Pack to include DSM; if it doesn't, the run fails fast with `FIDELITY_FLOOR_NOT_MET` (§5.4).

## Response (202)

```json
{
  "run_id": "run_2026_04_25_voxel_001",
  "status_url": "https://rf.local/v1/runs/run_2026_04_25_voxel_001/status",
  "mode_executed": "async",
  "reason": "requested"
}
```

## Run record on completion

```json
{
  "id": "run_2026_04_25_voxel_001",
  "status": "COMPLETED",
  "operation": "voxel",
  "mode_executed": "async",
  "fidelity_tier_dominant":     "T3_SURFACE",
  "fidelity_tier_max_possible": "T3_SURFACE",
  "models_used": [{ "name": "ITU-R P.528", "version": "5" }],
  "output_artifact_refs": [
    { "key": "voxel",             "class": "canonical", "content_type": "application/x-netcdf", "size_bytes": 64200000, "expires_at": "2026-05-02T14:40:00Z", "materialized": true, "download_url": "..." },
    { "key": "c2_pass_fail",      "class": "canonical", "content_type": "image/tiff",           "size_bytes":  1840000, "expires_at": "2026-05-25T14:40:00Z", "materialized": true, "download_url": "..." },
    { "key": "c2_range_envelope", "class": "canonical", "content_type": "application/geo+json", "size_bytes":    22000,                                       "materialized": true, "download_url": "..." },
    { "key": "stats",             "class": "canonical", "content_type": "application/json",     "size_bytes":    14000,                                       "materialized": true, "download_url": "..." }
  ]
}
```

## Slicing the voxel for the 90 m AGL plane only

`GET /v1/runs/run_2026_04_25_voxel_001/artifacts/voxel/slice?altitudes=90&format=geotiff&color_map=pass_fail`

```json
{
  "key": "voxel.slice",
  "class": "derivative",
  "content_type": "image/tiff",
  "size_bytes":  240000,
  "sha256": "sha256:f1e9...",
  "expires_at": "2026-04-26T14:55:00Z",
  "download_url": "https://rf.local/v1/runs/run_2026_04_25_voxel_001/artifacts/voxel/slice/voxel-slice-90m.tif?sig=...",
  "materialized": true
}
```

## Notes

- `voxel` was stored at default 0.5 dB quantization (`voxel_lossless: false`); ~64 MB on disk after zlib + chunk + quantize. Lossless would have been ~4× larger (§6.1, §8.2).
- `voxel` TTL is 7 days; the per-altitude GeoTIFF stack would have been a separate ~1.4 GB derivative — instead, callers slice on demand (§6.6).
- `c2_pass_fail` is a single per-altitude raster stack stored as a tiled multi-band GeoTIFF. The full 3D pass/fail decision lives in the voxel; this is the engineer-friendly surface.
- `c2_range_envelope` is a small GeoJSON polygon per altitude — useful for overlaying on flight-planning maps without loading rasters.
- A subsequent `POST /v1/runs/{id}/pin` would prevent the voxel's 7-day expiry; useful if this Run becomes part of a Comparison/Plan.
