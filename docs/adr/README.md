# Architecture Decision Records

Decisions about implementation stack, deployment model, and cross-cutting design choices live here. The design spec under [`../superpowers/specs/`](../superpowers/specs/) governs **behavior**; ADRs govern **how we build it**.

Numbered sequentially. Once accepted, an ADR is immutable — supersede it with a new ADR rather than rewriting history.

| # | Title | Status |
|---|---|---|
| [0001](0001-stack.md) | Implementation stack (Python 3.12 + FastAPI + pydantic v2) | Accepted |
| [0002](0002-argus-alignment-and-auth.md) | Argus alignment, auth model (`Authorization: Bearer` + argon2id), logging redaction | Accepted (supersedes parts of 0001) |
| [0003](0003-propagation-model-registry.md) | Pluggable propagation-model registry (license / runtime / provenance, core-bundled models, third-party allowlist) | Accepted (supersedes parts of 0001) |
