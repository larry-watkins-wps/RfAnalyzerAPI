# ADR-0002: Argus alignment, auth model, and logging redaction

**Status:** Accepted
**Date:** 2026-04-26
**Deciders:** Larry Watkins (project owner)
**Supersedes (in part):** [ADR-0001](0001-stack.md) — Postgres image choice and the `authorization|cookie|password|secret|api_key|*token` regex sketch under "Logging".

## Context

[ADR-0001](0001-stack.md) locked Python 3.12 / FastAPI / pydantic v2 / Postgres 16 and committed to mirroring [argus-flight-center](https://github.com/wildlifeprotection/argus-flight-center)'s storage and logging shapes for cross-service log aggregation. It deferred three concrete decisions that the v3 implementation cannot start without:

- **Which Postgres image.** The spec assumes Postgres 16 but does not pin a tagged image. argus runs `postgis/postgis:16-3.4` because some routes touch geometry, and RfAnalyzer will eventually need PostGIS once §5.5's reprojection logic moves out of in-process Python (and even before that, sharing the same image avoids stack drift between the two services).
- **The auth credential model.** Spec §8.4 declares the *adapter contract* (a `Principal` carrying `scopes`, `rate_limit_class`, `storage_class`) but says nothing about how the v1 API key is presented, stored, looked up, or revoked. The OpenAPI hand-waved "X-Api-Key header"; argus uses bearer tokens. Diverging here makes the generated TypeScript client awkward for argus and forces every cross-service request to special-case headers.
- **The logging redaction list.** ADR-0001 sketched a regex (`authorization|cookie|password|secret|api_key|*token`). That style of redaction has two known failure modes: case sensitivity (`Authorization` vs `authorization`) and partial-match false positives (any field with `token` in the name, including unrelated business fields). argus's `src/lib/logger.ts` uses an explicit, case-insensitive key set with bounded recursion; the divergence breaks the "same field shape across both services" property ADR-0001 was trying to preserve.

These decisions are independent in practice but share the same alignment concern (match argus where matching is cheap and meaningful) and ship together.

## Decision

### 1. Postgres image: `postgis/postgis:16-3.4` with PostGIS mandatory

`docker-compose.yml` and the production Helm/Kubernetes manifests pin `postgis/postgis:16-3.4`. The PostGIS extension is enabled at first-boot via a migration. The v1 spec does not currently expose a PostGIS-backed query, but:

- The image is a strict superset of vanilla `postgres:16` — there is no operational cost to choosing it.
- It matches argus, so a single `pg_dump`-compatible image is enough to spin up either service.
- It unblocks any future spec evolution that wants to push spatial logic from Python into SQL (e.g., `restricted_species_polygons` intersection at SUBMITTED is currently in-process; Postgres-side ST_Intersects would be a one-line change once we adopt PostGIS).

### 2. Auth: `Authorization: Bearer <api-key>`, hashed-at-rest with argon2id

The wire format is `Authorization: Bearer <api-key>` — not `X-Api-Key`. This matches argus and the broader ecosystem; the spec's existing scopes and adapter contract (§8.4) carry over unchanged.

**At-rest storage.** Bearer keys are stored as argon2id hashes; the cleartext key never touches disk. The first 8 characters of the cleartext (the `prefix`) are stored alongside the hash and indexed for fast lookup. On request the API extracts the prefix, finds candidate rows by prefix, then verifies argon2id against the candidate's hash. The 8-character prefix gives roughly 36⁸ ≈ 2.8 × 10¹² collision space, which is comfortable at any plausible tenant scale and keeps lookup at one indexed row read in the common case.

**Tenant API key table:**

```
tenant_api_keys (
    id            uuid primary key,
    prefix        char(8) not null,
    hash          text     not null,                      -- argon2id, includes salt + parameters
    tenant_id     uuid     not null,
    scopes        text[]   not null default '{}',         -- per spec §8.4 / Appendix E.5
    rate_limit_class text,                                -- per Principal.rate_limit_class
    storage_class    text,                                -- per Principal.storage_class
    created_at    timestamptz not null default now(),
    expires_at    timestamptz,
    revoked_at    timestamptz,
    label         text,                                   -- operator-facing name
    last_used_at  timestamptz
);
create index tenant_api_keys_prefix_idx on tenant_api_keys (prefix) where revoked_at is null;
create index tenant_api_keys_tenant_idx on tenant_api_keys (tenant_id);
```

argon2id parameters (`memory_kib=65536`, `iterations=3`, `parallelism=4` by default) are tunable via the deployment-config schema (`auth.bearer_key_store.argon2id.*`).

**Bearer key in the redaction list.** The literal string the bearer key takes — both the `authorization` header and the `bearer` field — appears in the redaction list (§3 below). Logs never show a bearer prefix or the full token.

### 3. Logging redaction: explicit key set, case-insensitive, recurse-5, replace with `[REDACTED]`

This **supersedes** the regex sketch in ADR-0001's "Logging" row. The redaction algorithm is:

- **Match by exact key name, case-insensitive.** No partial substring matching.
- **Recurse into nested objects up to 5 levels deep** (matches argus). Beyond that, leave the value as-is to bound CPU.
- **Replace with the literal string `"[REDACTED]"`** (not null, not `***`) so that downstream systems can distinguish "field was present but redacted" from "field was missing".
- **Apply at log-emit time only.** The in-memory representation is unchanged so application logic continues to work.

**Default key set (snake_case + camelCase mirrors):**

```
authorization, cookie, password, secret,
api_key, apikey, api_secret, apisecret,
token, access_token, refresh_token, id_token, session_token,
bearer, password_hash,
accessToken, refreshToken, idToken, sessionToken,
apiKey, apiSecret, passwordHash
```

This is argus's `REDACT_KEYS` set verbatim, extended with the snake_case mirrors RfAnalyzer needs because Python and Pydantic emit snake_case by default. Operators may extend the list via `logging.redaction_keys` in the deployment-config schema; reducing the list below this baseline is not supported.

### Optional (decision-recorded, no implementation yet)

- **Redis 7-alpine optional dev sidecar** for rate limiting and idempotency keys, mirroring argus's optional Redis usage for ephemeral state. **NOT a queue** — the SKIP-LOCKED queue lives in Postgres per ADR-0001. When Redis is absent the deployment falls back to Postgres for both rate-limiting buckets and idempotency-key storage; this is the default for single-tenant local-mode (spec §8.5). The implementation work to wire Redis is out of scope for v1.0 but is recorded here so the optionality is explicit.
- **Leader election via `pg_advisory_lock`.** The §8.1 lease sweeper, the asset-orphan GC sweep, and the Comparison-pin reconciliation all need exactly one runner across the API replicas. argus uses a `node-cron` + Postgres-leader pattern; mirroring it lets us avoid a Redis or Zookeeper dependency. The implementation again ships post-v1.0; the decision is recorded here so when it lands it does not look like a surprise.

## Alternatives considered

### Bearer + plaintext storage / Bearer + symmetric encryption

Rejected. argon2id is the password-hashing-competition winner and is what every modern bearer-token store uses. Symmetric encryption stores the cleartext under a deployment key, which is no better than plaintext when the key is co-located with the database (the standard local-mode deployment shape per §8.5).

### `X-Api-Key` (the original spec stance)

Rejected. The `X-` prefix has been deprecated for new headers since [RFC 6648](https://www.rfc-editor.org/rfc/rfc6648); `Authorization: Bearer` is the standard idiom; argus uses the standard idiom; the generated TypeScript client argus consumes is cleaner with a single auth field. Keeping `X-Api-Key` would force every argus call to special-case header naming.

### JWT bearer

Rejected for v1. JWTs add signing/verification machinery and a key-rotation story we don't need at single-tenant scale. The auth adapter contract in §8.4 leaves the door open for a JWT adapter later; this ADR commits only to the wire format and the v1 storage shape.

### Regex redaction (the ADR-0001 sketch)

Rejected. Case sensitivity ate one of argus's earliest log-leak incidents (an `Authorization` header with a capital A passed straight through a `lowercase-only` regex). Substring matching produces false positives in any field whose name contains `token` (a `match_token` for matching algorithms, a `token_count` for tokenizers, etc.). An explicit case-insensitive key set is both safer and faster.

## Trade-offs

- **PostGIS image is bigger** than vanilla `postgres:16` (~250 MB vs ~80 MB). Acceptable: image pull happens once per deployment, and the pulled bytes are cached.
- **argon2id is CPU-expensive on every authenticated request.** Mitigated by the 8-character prefix index — the typical request reads one row by indexed prefix, then runs argon2id once. argon2id parameters sized to ~50 ms on the target hardware; per-request P50 auth latency budget is 75 ms (50 ms argon2id + 25 ms slop).
- **Explicit key set requires updates as new sensitive fields appear.** Acceptable: the schema lives in the deployment-config schema, so adding a key is a one-line config diff; the alternative (regex) was already wrong.

## Consequences

**Easier:**
- argus and RfAnalyzer share a Postgres image; one operator runs both with the same backup tooling.
- The generated TypeScript client uses a single `Authorization` field; argus's existing fetch wrapper composes cleanly.
- Logs from both services scrub the same set of keys; aggregate dashboards and alerts apply across the fleet.
- The bearer-key model is forward-compatible with JWT (drop in a different adapter; the table goes away or moves under it).

**Harder:**
- Operators rotating an API key must coordinate with their callers; the table has no built-in "old key valid for N hours" grace window. Mitigated by encouraging callers to provision multiple active keys and rotate one at a time.
- Sweepers (lease, asset GC, pin reconciliation) need leader election; until the `pg_advisory_lock`-based leader pattern lands (post-v1.0), a single API replica must be designated as the sweeper-runner.

## Action items

1. [ ] Create the `tenant_api_keys` migration; wire the auth adapter to verify argon2id against `prefix → hash`.
2. [ ] Update `docker-compose.yml` to pin `postgis/postgis:16-3.4` and run `CREATE EXTENSION IF NOT EXISTS postgis;` in the boot migration.
3. [ ] Implement structlog redaction processor against the explicit key set with 5-level recursion.
4. [ ] Generate the OpenAPI client with `openapi-typescript` and replace any `X-Api-Key` references in argus with `Authorization: Bearer`.
5. [ ] Land the deployment-config schema (`docs/superpowers/specs/2026-04-25-deployment-config.schema.json`) in code as a typed pydantic model.
6. [ ] (Post-v1.0) Implement the `pg_advisory_lock` leader-election helper for sweepers; begin using it for the lease-sweeper from spec §8.1.
7. [ ] (Post-v1.0) Add the optional Redis 7-alpine sidecar for rate limiting and idempotency-key storage; wire fallback to Postgres when absent.
