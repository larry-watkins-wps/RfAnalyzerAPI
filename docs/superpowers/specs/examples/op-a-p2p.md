# Op A — Point-to-point

Companion to spec §4.0 (Op A), §6.1 (link_budget, path_profile, rendered_cross_section).

**Scenario.** Will the 868 MHz LoRa link from "Olifants Dock" to camera trap **CT-014** close, given known canopy along the path?

## Request

`POST /v1/analyses/p2p`

```json
{
  "operation": "p2p",
  "tx_site":      { "ref": "olifants-dock" },
  "tx_equipment": { "ref": "lora-gateway-868-omni-6dbi" },
  "rx_site":      { "ref": "ct-014" },
  "rx_equipment": { "ref": "camera-trap-lora-rx", "owner": "shared" },
  "aoi":          { "ref": "kruger-north-2026q1" },
  "outputs":      ["link_budget", "path_profile", "rendered_cross_section"],
  "color_map":    "signal_strength_default",
  "min_fidelity_tier": "T2_TERRAIN_PLUS_CLUTTER"
}
```

## Response (sync, 200)

```json
{
  "id": "run_2026_04_25_olifants_ct014_001",
  "status": "COMPLETED",
  "operation": "p2p",
  "submitted_by_key":   "key_field_team_alpha",
  "submitted_at":       "2026-04-25T14:18:33Z",
  "inputs_resolved_at": "2026-04-25T14:18:33Z",
  "completed_at":       "2026-04-25T14:18:38Z",
  "mode_requested":  "auto",
  "mode_executed":   "sync",
  "engine_version":  "0.2.0",
  "engine_major":    0,
  "models_used": [{ "name": "ITU-R P.1812", "version": "4-with-clutter" }],
  "fidelity_tier_dominant":     "T2_TERRAIN_PLUS_CLUTTER",
  "fidelity_tier_min":          "T2_TERRAIN_PLUS_CLUTTER",
  "fidelity_tier_max":          "T2_TERRAIN_PLUS_CLUTTER",
  "fidelity_tier_max_possible": "T2_TERRAIN_PLUS_CLUTTER",
  "output_artifact_refs": [
    {
      "key": "link_budget", "class": "canonical",
      "content_type": "application/json", "size_bytes": 1842,
      "sha256": "sha256:b1f4...", "materialized": true,
      "download_url": "https://rf.local/v1/runs/run_2026_04_25_olifants_ct014_001/artifacts/link_budget?sig=..."
    },
    {
      "key": "path_profile", "class": "canonical",
      "content_type": "application/geo+json", "size_bytes": 18342,
      "sha256": "sha256:c2a7...", "materialized": true,
      "download_url": "..."
    },
    {
      "key": "rendered_cross_section", "class": "derivative",
      "content_type": "image/png", "size_bytes": 224310,
      "sha256": "sha256:d3e9...", "expires_at": "2026-04-26T14:18:38Z",
      "materialized": true, "download_url": "..."
    }
  ],
  "warnings": [],
  "pinned": false
}
```

## `link_budget` artifact (downloaded)

```json
{
  "tx": { "site": "olifants-dock", "equipment": "lora-gateway-868-omni-6dbi", "freq_mhz": 868.1 },
  "rx": { "site": "ct-014",         "equipment": "camera-trap-lora-rx" },
  "geometry": { "distance_km": 4.27, "bearing_deg": 142.3, "elev_diff_m": -38.2 },
  "components_db": {
    "tx_power_dbm":             14.0,
    "tx_antenna_gain_dbi":       6.0,
    "tx_cable_loss_db":         -1.2,
    "polarization_mismatch_db":  0.0,
    "free_space_loss_db":     -103.4,
    "terrain_diffraction_db":   -2.1,
    "clutter_loss_db":          -8.7,
    "building_loss_db":          0.0,
    "rx_antenna_gain_dbi":       2.0,
    "rx_feeder_loss_db":        -0.5,
    "received_power_dbm":      -93.9
  },
  "polarization_detail": { "base_mismatch_db": 0.0, "depolarization_d": 0.34, "effective_db": 0.0 },
  "rx_sensitivity_dbm": -123.0,
  "fade_margin_db":      29.1,
  "fading": { "model": "rician", "k_factor_db": 8.5, "availability_pct": 99.6 },
  "result": "PASS"
}
```

## Notes

- `mode_executed: "sync"` because Op A geometry stays under all three auto-async thresholds (§2.3).
- `min_fidelity_tier: T2_TERRAIN_PLUS_CLUTTER` is satisfied because the AOI Pack ships DTM + clutter; the AOI's `fidelity_tier_max_possible` is also T2 (no DSM), so the run completes as `COMPLETED` not `PARTIAL` (§5.4, §8.1).
- `rendered_cross_section` is a derivative — note its `expires_at` is 24 h after completion. Re-styling it later goes through `POST /runs/{id}/artifacts:rederive` instead of re-running propagation.
- Polarization mismatch: both ends are vertical (base 0 dB), so no attenuation applies even though clutter recorded `d=0.34` (§4.5 floor exception when base ≤ 3 dB).
