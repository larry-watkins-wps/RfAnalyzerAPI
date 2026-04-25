# Examples

Worked request and response payloads for each analysis operation, plus the asset upload flow. Each file pairs realistic JSON with brief commentary tying it back to the spec.

| File | Op | Highlights |
|---|---|---|
| [op-a-p2p.md](op-a-p2p.md) | A — point-to-point | Sync response; full link-budget; polarization mismatch detail. |
| [op-b-area.md](op-b-area.md) | B — area heatmap | Async with webhook delivery; canonical-vs-derivative materialization; full `stats` shape. |
| [op-c-multi-link.md](op-c-multi-link.md) | C — multi-link site | Per-link artifact namespacing; `combined_site_score`; `PARTIAL` completion with warnings. |
| [op-d-multi-tx.md](op-d-multi-tx.md) | D — multi-Tx best-server | `best_server_raster` with NoData and JSON sidecar; per-Tx wins/weakest stats. |
| [op-e-voxel.md](op-e-voxel.md) | E — 3D / voxel | Voxel quantization; voxel slice endpoint at a single altitude. |
| [asset-upload.md](asset-upload.md) | n/a | Direct (small) and multipart (large) upload flows; idempotent re-upload. |

These examples are intentionally close to copy-paste-runnable. They serve three purposes:

1. **Spec validation.** Turning abstract schemas into concrete payloads exercises edge cases (NoData encoding, derivative TTLs, Op C pairing rules, fidelity reporting on PARTIAL completions).
2. **Integration-test fixtures** for the eventual implementation.
3. **Quick reference** for new clients integrating with the API.

Where examples diverge from the spec, **the spec is canonical** — fix the example.
