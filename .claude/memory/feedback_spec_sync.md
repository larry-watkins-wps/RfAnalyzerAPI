---
name: Cross-artifact sync rule for RfAnalyzer spec changes
description: Every spec concept change must propagate across spec markdown, OpenAPI, JSON Schema, and seed in the same commit; walk the per-change-kind fan-out before claiming complete
type: feedback
originSessionId: de03615d-447d-4e3c-85e3-ed963200ddc7
---
When editing the RfAnalyzer spec, every change to a concept that has a machine-readable representation must propagate across all four artifact surfaces in the **same commit**:

1. Design spec markdown — `docs/superpowers/specs/2026-04-25-rf-site-planning-api-design.md`
2. OpenAPI 3.1 — `docs/superpowers/specs/2026-04-25-rf-site-planning-api.openapi.yaml`
3. JSON Schema 2020-12 — `docs/superpowers/specs/2026-04-25-analysis-requests.schema.json`
4. Seed library / scenarios / test vectors — `docs/superpowers/specs/seed/**` (when applicable)

Fan-out by change kind:

- **New catalog entity** → §3.2 entity table (bump count) · §3.x detail subsection · §3.6 reference graph · §2.5 endpoint inventory · spec change log · OpenAPI path family · OpenAPI component schema · OpenAPI page wrapper · JSON Schema `InlineX` def · `RefOrInlineX` def · any `AnalysisCommon` hook.
- **New error / warning / filter code** → spec Appendix D · OpenAPI `ProblemDetail.code` (or `warnings.items.code`) enum · spec change log.
- **New enum value** (LinkType, SensitivityClass, LicenseClass, FidelityTier, propagation/fading model, polarization, operation, run-status, asset purpose, output key) → spec narrative · OpenAPI enum · JSON Schema enum · `examples` lists.
- **New pipeline stage or stage behavior** → §4.1 prose · §4.1 mermaid · Appendix A row if applicable.

Before claiming the change is complete, re-run structural validators: PyYAML on the OpenAPI; `json.load` on the JSON Schema and every seed JSON; arithmetic check on golden test vectors when relevant.

**Why:** The spec is canonical, but four files must agree before the contract is implementable. In the regulatory_profile + Appendix E session (commit d212bac), one pair of concepts fanned out to ~17 edit sites; a single missed surface produces an incoherent contract that breaks code-gen and confuses implementers. The user explicitly flagged that maintaining sync needs to be a procedural step, not a hope.

**How to apply:** Walk the fan-out list before claiming a spec change complete. The same rule is recorded in `CLAUDE.md` (Working agreements — auto-loaded in every session in this repo) and the project root `README.md` (Cross-artifact sync section — for human readers and AI assistants reading the repo cold). Treat all three as redundant copies of the same rule by design — drift between them defeats the purpose.
