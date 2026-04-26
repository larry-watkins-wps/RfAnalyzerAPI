#!/usr/bin/env python3
"""Cross-artifact sync validator (spec §working agreements, README §cross-artifact sync).

Runs every structural check the README enumerates as a single command:

  - YAML-load the OpenAPI 3.1 spec.
  - JSON-load the analysis-requests JSON Schema.
  - JSON-load every scenario, every test-vector JSON, the standard profile library.
  - JSON-Schema-validate every scenario's `request` against the analysis-requests schema.
  - Verify the antenna-pattern asset MANIFEST.txt SHA-256 entries match the bytes on disk.
  - Arithmetic-check the golden test vectors (delegated to seed/test-vectors/README.md
    convention; this script just ensures the file is well-formed).

Run from the repo root or anywhere — paths are computed from this file's location.

Exits 0 on success, 1 on any failure. Output is line-oriented so CI can grep results.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS = REPO_ROOT / "docs" / "superpowers" / "specs"
SEED = SPECS / "seed"

OPENAPI_PATH = SPECS / "2026-04-25-rf-site-planning-api.openapi.yaml"
SCHEMA_PATH = SPECS / "2026-04-25-analysis-requests.schema.json"
SCENARIOS_DIR = SEED / "scenarios"
VECTORS_DIR = SEED / "test-vectors"
LIBRARY_PATH = SEED / "standard-profile-library.json"
ANTENNA_MANIFEST = SEED / "antenna_patterns" / "MANIFEST.txt"


failures: list[str] = []


def report(ok: bool, message: str) -> None:
    if ok:
        print(f"OK   {message}")
    else:
        print(f"FAIL {message}")
        failures.append(message)


def check_openapi() -> None:
    try:
        import yaml  # type: ignore
    except ImportError:
        report(False, "OpenAPI YAML parse — PyYAML not installed (`pip install pyyaml`)")
        return
    try:
        with OPENAPI_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or data.get("openapi", "").startswith(("3.0", "3.1")) is False:
            report(False, f"OpenAPI parsed but `openapi` field is not 3.0/3.1: {OPENAPI_PATH}")
            return
        report(True, f"OpenAPI parses (openapi={data.get('openapi')}, version={data.get('info', {}).get('version')})")
    except Exception as exc:  # noqa: BLE001
        report(False, f"OpenAPI parse failed: {exc}")


def load_json(path: Path) -> object | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        report(False, f"JSON parse failed for {path.relative_to(REPO_ROOT)}: {exc}")
        return None


def check_schema() -> object | None:
    schema = load_json(SCHEMA_PATH)
    if schema is None:
        return None
    if not isinstance(schema, dict) or "$schema" not in schema:
        report(False, f"JSON Schema missing $schema: {SCHEMA_PATH}")
        return None
    report(True, f"JSON Schema parses ($schema={schema['$schema']!r})")
    return schema


def check_library() -> None:
    data = load_json(LIBRARY_PATH)
    if data is None or not isinstance(data, dict):
        return
    counts = {
        k: len(data.get(k, [])) if isinstance(data.get(k), list) else 0
        for k in ("antennas", "radio_profiles", "equipment_profiles", "clutter_tables")
    }
    report(
        True,
        "Standard profile library parses ("
        + ", ".join(f"{c} {k}" for k, c in counts.items())
        + ")",
    )


def check_scenarios(schema: object | None) -> None:
    if schema is None:
        return
    try:
        from jsonschema import Draft202012Validator  # type: ignore
    except ImportError:
        report(False, "Scenario validation — jsonschema not installed (`pip install jsonschema`)")
        return
    validator = Draft202012Validator(schema)  # type: ignore[arg-type]
    files = sorted(SCENARIOS_DIR.glob("*.json"))
    if not files:
        report(False, "No scenario JSONs found")
        return
    for path in files:
        scenario = load_json(path)
        if scenario is None or not isinstance(scenario, dict):
            continue
        request = scenario.get("request")
        if request is None:
            report(False, f"Scenario {path.name} has no `request` block")
            continue
        errors = sorted(validator.iter_errors(request), key=lambda e: e.path)
        if errors:
            for err in errors:
                report(False, f"Scenario {path.name} fails JSON Schema: {list(err.path)} → {err.message}")
        else:
            report(True, f"Scenario validates: {path.name}")


def check_test_vectors() -> None:
    files = sorted(VECTORS_DIR.glob("*.json"))
    if not files:
        report(False, "No test-vector JSONs found")
        return
    for path in files:
        if load_json(path) is not None:
            report(True, f"Test vector parses: {path.name}")


def check_antenna_manifest() -> None:
    """Manifest format: `<filename>\\t<sha256>\\t<size_bytes>` per line, with `#` comments."""
    if not ANTENNA_MANIFEST.exists():
        report(False, f"Antenna manifest not found: {ANTENNA_MANIFEST}")
        return
    with ANTENNA_MANIFEST.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                report(False, f"MANIFEST.txt line {line_no} malformed: {line!r}")
                continue
            # Identify the hex-looking 64-char token as the hash; the other text is the filename.
            hash_idx = next((i for i, t in enumerate(parts) if len(t) == 64 and all(c in "0123456789abcdefABCDEF" for c in t)), -1)
            if hash_idx < 0:
                report(False, f"MANIFEST.txt line {line_no} has no SHA-256 hex token: {line!r}")
                continue
            expected_hash = parts[hash_idx]
            filename = parts[0] if hash_idx != 0 else parts[1]
            asset_path = ANTENNA_MANIFEST.parent / filename
            if not asset_path.exists():
                report(False, f"MANIFEST.txt references missing file: {filename}")
                continue
            with asset_path.open("rb") as af:
                actual = hashlib.sha256(af.read()).hexdigest()
            if actual.lower() != expected_hash.lower():
                report(False, f"MANIFEST.txt hash mismatch for {filename}: expected {expected_hash}, got {actual}")
            else:
                report(True, f"Antenna pattern matches manifest hash: {filename}")


def main() -> int:
    print(f"# RfAnalyzer cross-artifact sync check (root: {REPO_ROOT})")
    check_openapi()
    schema = check_schema()
    check_library()
    check_scenarios(schema)
    check_test_vectors()
    check_antenna_manifest()
    print()
    if failures:
        print(f"=== {len(failures)} failure(s) ===")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("=== all checks passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
