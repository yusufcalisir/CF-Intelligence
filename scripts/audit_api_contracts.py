"""Cross-reference all FastAPI backend routes against frontend API calls."""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402


def audit():
    print("=" * 80)
    print("COMPREHENSIVE BACKEND - FRONTEND API CONTRACT & LINKAGE AUDIT")
    print("=" * 80)

    # 1. Backend Routes
    backend_routes = []
    for r in app.routes:
        if hasattr(r, "methods") and hasattr(r, "path"):
            methods = [m for m in getattr(r, "methods", []) if m not in ("HEAD", "OPTIONS")]
            endpoint_name = getattr(r, "name", str(getattr(r, "endpoint", "")))
            backend_routes.append({
                "path": getattr(r, "path", ""),
                "methods": methods,
                "endpoint": endpoint_name,
            })
        elif hasattr(r, "path"):  # WebSockets
            endpoint_name = getattr(r, "name", str(getattr(r, "endpoint", "")))
            backend_routes.append({
                "path": getattr(r, "path", ""),
                "methods": ["WS"],
                "endpoint": endpoint_name,
            })


    print(f"Discovered {len(backend_routes)} Registered Backend Endpoints / WebSockets.\n")

    # 2. Frontend API Calls
    frontend_files = list((FRONTEND_DIR / "src").glob("**/*.ts")) + list((FRONTEND_DIR / "src").glob("**/*.tsx"))
    frontend_endpoints = {}

    url_regex = re.compile(r"['\"`](/(?:api/v1|health|ws)[^'\"`\s?]*)['\"`]")

    for f in frontend_files:
        content = f.read_text(encoding="utf-8", errors="ignore")
        for match in url_regex.finditer(content):
            url = match.group(1)
            rel_f = str(f.relative_to(REPO_ROOT))
            if url not in frontend_endpoints:
                frontend_endpoints[url] = []
            frontend_endpoints[url].append(rel_f)

    print(f"Discovered {len(frontend_endpoints)} Distinct API Patterns Referenced in Frontend.\n")

    # Normalize routes for comparison (replace {param} and ${param} with uniform placeholder)
    def normalize(path_str):
        # normalize {var} and ${var} to {*}
        p = re.sub(r"\{[^}]+\}", "{*}", path_str)
        p = re.sub(r"\$\{[^}]+\}", "{*}", p)
        # remove query string if any
        p = p.split("?")[0]
        return p

    norm_backend = {normalize(r["path"]): r for r in backend_routes}
    norm_frontend = {normalize(url): (url, files) for url, files in frontend_endpoints.items()}

    # Check for frontend URLs with NO backend match
    print("--- 1. Frontend Endpoints Check (Are all Frontend API calls backed by FastAPI?) ---")
    broken_frontend = []
    for norm_url, (raw_url, files) in norm_frontend.items():
        if norm_url not in norm_backend:
            # Check if partial match or static
            broken_frontend.append((raw_url, files))

    if not broken_frontend:
        print("ALL Frontend API calls match valid Backend FastAPI endpoints!")
    else:
        print(f"Found {len(broken_frontend)} Frontend calls with NO direct matching backend route:")
        for url, files in broken_frontend:
            print(f"  - {url} (in {len(files)} files: {', '.join(files[:2])})")

    # Check for Backend endpoints NOT referenced in Frontend (e.g. backend-to-backend or CLI-only)
    print("\n--- 2. Backend Endpoints Coverage (Backend endpoints status) ---")
    unlinked_backend = []
    linked_backend = []
    for norm_p, r in norm_backend.items():
        if norm_p in norm_frontend:
            linked_backend.append(r)
        else:
            unlinked_backend.append(r)

    print(f"{len(linked_backend)} Backend Endpoints Directly Linked & Consumed by Frontend UI.")
    print(f"{len(unlinked_backend)} Backend Endpoints for Microservice Gateway / CLI / Admin / Background tasks:")
    for r in sorted(unlinked_backend, key=lambda x: x["path"]):
        print(f"  - {','.join(r['methods']):<12} {r['path']}")

    print("\n" + "=" * 80)

if __name__ == "__main__":
    audit()
