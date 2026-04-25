# Asset upload — direct and multipart

Companion to spec §3.5 (asset model). Worked HTTP exchanges for both flows. SHA-256 is the asset's identity, so duplicate uploads short-circuit.

## Direct upload (≤ 50 MB)

A 220 KB antenna pattern file in MSI format.

### 1. Initiate

`POST /v1/assets:initiate`

```json
{
  "filename":     "kp_yagi_868_pattern.msi",
  "content_type": "application/x-msi-pattern",
  "size_bytes":   224301,
  "sha256":       "1a3f9b7e6d2c4a8e5b1f3d9c7a6b2e8f4d1c5b9a7e3f8d6c2b4a1e9f7d3b5c8a",
  "purpose":      "antenna_pattern"
}
```

Response:

```json
{
  "asset_id": "sha256:1a3f9b7e6d2c4a8e5b1f3d9c7a6b2e8f4d1c5b9a7e3f8d6c2b4a1e9f7d3b5c8a",
  "mode": "direct",
  "upload": {
    "method":  "PUT",
    "url":     "https://artifact-store.rf.local/uploads/sha256-1a3f9b.../signed?sig=...",
    "headers": { "Content-Type": "application/x-msi-pattern" },
    "expires_at": "2026-04-25T14:35:00Z"
  }
}
```

### 2. PUT bytes

```
PUT https://artifact-store.rf.local/uploads/sha256-1a3f9b.../signed?sig=...
Content-Type: application/x-msi-pattern
Content-Length: 224301

<bytes>
```

→ `200 OK`

### 3. Complete

`POST /v1/assets/sha256:1a3f9b...c8a:complete`

```json
{}
```

Response:

```json
{
  "asset_id":     "sha256:1a3f9b7e6d2c4a8e5b1f3d9c7a6b2e8f4d1c5b9a7e3f8d6c2b4a1e9f7d3b5c8a",
  "content_type": "application/x-msi-pattern",
  "size_bytes":   224301,
  "sha256":       "1a3f9b7e6d2c4a8e5b1f3d9c7a6b2e8f4d1c5b9a7e3f8d6c2b4a1e9f7d3b5c8a",
  "ready":        true
}
```

The `asset_id` is now usable as `Antenna.pattern_asset_ref`.

---

## Multipart upload (≥ 50 MB)

A 2.1 GiB DTM raster covering a new BYO AOI Pack.

### 1. Initiate

`POST /v1/assets:initiate`

```json
{
  "filename":     "kruger-north-dtm-2026.tif",
  "content_type": "image/tiff",
  "size_bytes":   2254857600,
  "sha256":       "a8f3e1d9c7b2a6e4f1d8c3b7e9a2f5c1b8d4e7f3a9c5b1e8d2f6a3c9b4e7d1f5",
  "purpose":      "raster_dtm"
}
```

Response:

```json
{
  "asset_id": "sha256:a8f3e1d9c7b2a6e4f1d8c3b7e9a2f5c1b8d4e7f3a9c5b1e8d2f6a3c9b4e7d1f5",
  "mode": "multipart",
  "part_size_bytes": 16777216,
  "parts": [
    { "part_number":   1, "upload_url": "https://artifact-store.rf.local/.../part-1?sig=...",   "expires_at": "2026-04-25T15:00:00Z" },
    { "part_number":   2, "upload_url": "https://artifact-store.rf.local/.../part-2?sig=...",   "expires_at": "2026-04-25T15:00:00Z" }
    /* … 134 parts total at 16 MiB each; last part is shorter … */
  ],
  "complete_url": "https://rf.local/v1/assets/sha256:a8f3e1...:complete",
  "abort_url":    "https://rf.local/v1/assets/sha256:a8f3e1...:abort"
}
```

### 2. PUT each part (in parallel)

```
PUT https://artifact-store.rf.local/.../part-1?sig=...
Content-Length: 16777216

<bytes>

→ 200 OK
ETag: "abc123def456..."
```

Repeat for parts 2..134 (parallelism limited by the caller's available bandwidth; field deployments on satellite/4G typically run 2–4 parallel PUTs).

### 3. Complete

`POST /v1/assets/sha256:a8f3e1...:complete`

```json
{
  "parts": [
    { "part_number":   1, "etag": "abc123..." },
    { "part_number":   2, "etag": "def456..." }
    /* … */
  ]
}
```

Response: same shape as the direct flow's complete response.

---

## Idempotent re-upload

If a caller initiates with a SHA-256 already in the store:

`POST /v1/assets:initiate` with the same body as before →

```json
{
  "asset_id":       "sha256:1a3f9b...",
  "already_exists": true,
  "ready":          true
}
```

No upload required; the `asset_id` can be referenced immediately.

## Cleanup

If `:complete` is not called within 24 h of `:initiate`, the upload is auto-aborted and any uploaded parts are reclaimed. To explicitly cancel before then: `POST <abort_url>`.
