# Golden test vectors

Numerical input → expected-output triples for verifying engine implementations against the spec. Each vector is fully self-contained and computable by hand or a short Python snippet — there is no opaque ground truth here.

These vectors are **not** a replacement for empirical model validation against measured data; they verify that the engine correctly composes the documented formulas in the spec. Empirical validation belongs to the predicted-vs-observed pipeline (§7.3).

## Layout

| File | Purpose |
|---|---|
| [`golden-test-vectors.json`](golden-test-vectors.json) | All vectors. One JSON document with a top-level `vectors[]` array; each entry is one test case. |

## Vector entry shape

Each entry in `vectors[]`:

```json
{
  "id": "fspl-1km-868mhz",
  "category": "free_space" | "two_ray" | "polarization" | "cable_loss" |
              "frequency_authority" | "full_link_budget",
  "spec_refs": ["§4.5", "§4.1 stage 9"],
  "description": "...",
  "inputs":  { ... },
  "expected": { ... },
  "tolerance": { "abs_db": 0.05 },
  "notes": "..."
}
```

`tolerance` carries the absolute or relative tolerance against which an implementation should match `expected`. Free-space and pure formula vectors are tight (≤ 0.05 dB); composite vectors widen to 0.5 dB to allow internal rounding.

## Source of truth

For every vector, the formula and a worked example are in this README's appendix below — so a reader can see why the expected number is what it is. If a vector's expected value disagrees with the spec, **fix the vector, not the spec** (per the working agreements in the project root CLAUDE.md).

## Formula appendix

### Free-space path loss (Friis, dB)

```
FSPL_dB = 32.45 + 20·log10(d_km) + 20·log10(f_MHz)
```

Reference: standard Friis formulation using d in kilometers and f in megahertz; the 32.45 constant absorbs `20·log10(4π/c)` with unit conversions.

Worked: d = 1 km, f = 868 MHz → FSPL = 32.45 + 0 + 58.776 = 91.226 dB.

### Polarization mismatch (spec §4.5)

Base mismatch from the V/H/RHCP/LHCP/slant/dual table. After per-path depolarization factor `d ∈ [0, 1]`:

```
mismatch_loss_db = base_mismatch_db                                  if base ≤ 3
                  = max(3, base_mismatch_db × (1 − d))               otherwise
```

The 3 dB floor avoids implausibly clean cross-pol in dense canopy.

### Cable loss curve interpolation (spec §3.2 Equipment Profile)

`cable_loss_curve: [{freq_mhz, loss_db}]`. Loss at frequency `f`: piecewise-linear interpolation between the two enclosing knots. Outside the curve's domain, the engine emits a warning and clamps to the nearest knot — vectors here stay inside the domain.

### Frequency authority (spec §4.0)

`|rx.radio.freq_mhz − tx.radio.freq_mhz| ≤ tx.radio.bandwidth_khz × 1.5 / 1000` MHz must hold; otherwise validation rejects with `RX_TX_FREQ_MISMATCH`.

### Full link budget (spec §4.1 stage 9)

```
Pr_dBm = tx_power_dbm
       + tx_antenna_gain_dbi
       − tx_cable_loss_db
       − path_loss_db
       − clutter_loss_db
       − polarization_mismatch_db
       + rx_antenna_gain_dbi
       − rx_feeder_loss_db
fade_margin_db = Pr_dBm − rx_sensitivity_dbm
```
