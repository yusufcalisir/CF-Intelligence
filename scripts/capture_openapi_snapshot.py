#!/usr/bin/env python
"""Capture the current OpenAPI 3.1.0 schema from a running API instance.

Usage (with server running on port 7860):
    python scripts/capture_openapi_snapshot.py
    python scripts/capture_openapi_snapshot.py --host localhost --port 8000

The snapshot is saved to backend/storage/openapi/openapi_snapshot.json and
should be committed to version control to enable contract regression detection
across code revisions (per Section 14, Recommendation 9 of the API Scientific Audit).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: pip install httpx")
    sys.exit(1)

SNAPSHOT_PATH = Path(__file__).parent.parent / "backend" / "storage" / "openapi" / "openapi_snapshot.json"


def capture(host: str = "localhost", port: int = 7860) -> None:
    url = f"http://{host}:{port}/openapi.json"
    print(f"Fetching OpenAPI schema from {url} ...")
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
    except Exception as exc:
        print(f"ERROR: Could not fetch schema — {exc}")
        sys.exit(1)

    schema = r.json()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Deterministic serialisation for clean git diffs
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")

    info = schema.get("info", {})
    endpoints = len(schema.get("paths", {}))
    print(f"Snapshot saved to {SNAPSHOT_PATH}")
    print(f"  API:       {info.get('title', '?')} v{info.get('version', '?')}")
    print(f"  Endpoints: {endpoints} paths")
    print(f"  OpenAPI:   {schema.get('openapi', '?')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    capture(args.host, args.port)
