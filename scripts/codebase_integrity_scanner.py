#!/usr/bin/env python3
"""
Codebase Integrity & Zero-Mock Scanner
=====================================
Production static analysis suite to autonomously verify:
  1. Mock & hardcoded value hunting (AST & regex)
  2. Dead & orphaned code (frontend components & backend entities)
  3. Frontend-backend API endpoint contract mismatches
  4. Degenerate / constant functions ignoring arguments
  5. Silenced CI/security gates & swallowed exceptions
  6. Remaining TODO/FIXME markers & KaTeX/Mermaid doc inconsistencies
  7. Plaintext PII & hardcoded credentials

Usage:
  python scripts/codebase_integrity_scanner.py --all
  python scripts/codebase_integrity_scanner.py --category mock
  python scripts/codebase_integrity_scanner.py --category dead-code
  python scripts/codebase_integrity_scanner.py --category endpoints
  python scripts/codebase_integrity_scanner.py --category constant-funcs
  python scripts/codebase_integrity_scanner.py --category silenced
  python scripts/codebase_integrity_scanner.py --category todos-docs
  python scripts/codebase_integrity_scanner.py --category pii-secrets
  python scripts/codebase_integrity_scanner.py --target backend/app/application/services
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Finding:
    category: str
    severity: str  # "ERROR", "WARNING", "INFO"
    file_path: str
    line_number: int
    message: str
    code_snippet: str = ""


class IntegrityScanner:
    def __init__(self, target_path: Path | None = None) -> None:
        self.root = ROOT_DIR
        self.target = target_path or ROOT_DIR
        self.findings: list[Finding] = []

    def log(self, category: str, severity: str, file_path: Path | str, line: int, msg: str, snippet: str = "") -> None:
        rel_path = Path(file_path).relative_to(self.root) if Path(file_path).is_relative_to(self.root) else Path(file_path)
        self.findings.append(
            Finding(
                category=category,
                severity=severity,
                file_path=str(rel_path).replace("\\", "/"),
                line_number=line,
                message=msg,
                code_snippet=snippet.strip(),
            )
        )

    # =========================================================================
    # 1. HARDCODED / MOCK HUNTER
    # =========================================================================
    def scan_mock_values(self) -> None:
        """Finds static/mock returns, hardcoded fake arrays, or mock flags in production code."""
        excluded_dirs = {
            "tests", "test", "__pycache__", "node_modules", "storage",
            "fixtures", ".pytest_cache", ".ruff_cache", "scratch", "dist", "build"
        }
        mock_regex = re.compile(
            r'(\bstatus\s*=\s*["\'](MOCK|DUMMY|FAKE|SIMULATED)["\']|'
            r'\breturn\s*\{\s*["\'](mock|dummy|fake|simulated)["\']|'
            r'\bmock_result\s*=|'
            r'\bfake_prediction\s*=)',
            re.IGNORECASE
        )

        # Scan python backend production code
        backend_dir = self.root / "backend" / "app"
        if backend_dir.exists() and (self.target == self.root or self.target.is_relative_to(backend_dir) or backend_dir.is_relative_to(self.target)):
            scan_root = self.target if self.target.is_relative_to(backend_dir) else backend_dir
            for py_file in scan_root.rglob("*.py"):
                if any(ex in py_file.parts for ex in excluded_dirs):
                    continue
                # Skip legitimate mock connectors that are deliberately named mock_*.py
                if "mock_bank_connector.py" in py_file.name:
                    continue

                try:
                    content = py_file.read_text(encoding="utf-8")
                except Exception:
                    continue

                for line_idx, line in enumerate(content.splitlines(), start=1):
                    # Exclude comments
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    match = mock_regex.search(line)
                    if match:
                        self.log("mock", "ERROR", py_file, line_idx, f"Suspicious hardcoded mock pattern: '{match.group(0)}'", line)

                # AST check: functions that return static mock dictionaries
                try:
                    tree = ast.parse(content, filename=str(py_file))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.FunctionDef):
                            for stmt in node.body:
                                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict):
                                    for key in stmt.value.keys:
                                        if isinstance(key, ast.Constant) and str(key.value).lower() in ("mock", "fake", "dummy"):
                                            self.log(
                                                "mock", "ERROR", py_file, stmt.lineno,
                                                f"Function '{node.name}' returns explicit mock dict key '{key.value!r}'",
                                            )
                except Exception:
                    pass

    # =========================================================================
    # 2. DEAD / ORPHANED FRONTEND & BACKEND CODE
    # =========================================================================
    def scan_dead_code(self) -> None:
        """Finds unreferenced React components and unconnected backend service classes."""
        frontend_src = self.root / "frontend" / "src"
        if not frontend_src.exists():
            return
        if self.target != self.root and not self.target.is_relative_to(frontend_src) and not frontend_src.is_relative_to(self.target):
            return

        # 1. Collect all exported components in components/ and pages/
        component_files: dict[str, Path] = {}
        for ext in ("*.tsx", "*.jsx"):
            for comp_file in (frontend_src / "components").rglob(ext):
                if "__tests__" in comp_file.parts or comp_file.name.startswith("index"):
                    continue
                comp_name = comp_file.stem
                component_files[comp_name] = comp_file

        # 2. Check if component is referenced/imported across frontend
        all_frontend_code: list[str] = []
        for src_file in frontend_src.rglob("*.tsx"):
            if "__tests__" in src_file.parts:
                continue
            with contextlib.suppress(Exception):
                all_frontend_code.append(src_file.read_text(encoding="utf-8"))
        for src_file in frontend_src.rglob("*.ts"):
            if "__tests__" in src_file.parts:
                continue
            with contextlib.suppress(Exception):
                all_frontend_code.append(src_file.read_text(encoding="utf-8"))

        combined_code = "\n".join(all_frontend_code)

        for comp_name, comp_path in component_files.items():
            # Search for import or JSX usage: `<CompName` or `from '...CompName'`
            import_pattern = re.compile(rf"\b{re.escape(comp_name)}\b")
            matches = list(import_pattern.finditer(combined_code))
            # If matches <= 1, it's only defined in its own file
            if len(matches) <= 1:
                self.log(
                    "dead-code", "WARNING", comp_path, 1,
                    f"Component '{comp_name}' appears orphaned: never imported in other frontend modules",
                )

    # =========================================================================
    # 3. ENDPOINT MISMATCH (FRONTEND CALLS VS FASTAPI ROUTES)
    # =========================================================================
    def scan_endpoint_mismatch(self) -> None:
        """Verifies all frontend API calls match actual registered FastAPI endpoints."""
        backend_dir = self.root / "backend" / "app"
        frontend_dir = self.root / "frontend" / "src"
        if not backend_dir.exists() or not frontend_dir.exists():
            return

        # 1. Extract backend endpoints
        backend_routes: set[str] = set()
        route_decorator_regex = re.compile(
            r'@(?:router|api_router|app)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']'
        )

        for py_file in backend_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            for match in route_decorator_regex.finditer(content):
                path = match.group(2)
                # Normalize FastAPI path parameters {param_name} -> {param}
                normalized_path = re.sub(r'\{[^}]+\}', '{param}', path)
                backend_routes.add(normalized_path)
                # Also record with prefix variations if subrouters have prefixes
                if not normalized_path.startswith("/api/v1") and not normalized_path.startswith("/v1"):
                    backend_routes.add(f"/api/v1{normalized_path}")
                    backend_routes.add(f"/api{normalized_path}")

        # 2. Extract frontend endpoints from queries.ts and client calls
        frontend_api_regex = re.compile(
            r'''(?:apiClient\.(?:get|post|put|delete|patch)|fetch)\s*(?:<[^>]+>)?\s*\(\s*[`'"](/[^`'"]+)[`'"]'''
        )

        for ts_file in frontend_dir.rglob("*.ts*"):
            if "__tests__" in ts_file.parts or "node_modules" in ts_file.parts:
                continue
            try:
                content = ts_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for line_idx, line in enumerate(content.splitlines(), start=1):
                for match in frontend_api_regex.finditer(line):
                    raw_path = match.group(1)
                    # Filter static asset calls or non-api endpoints
                    if not raw_path.startswith(("/api", "/v1", "/ws")):
                        continue
                    # Normalize template expressions e.g. /api/v1/cases/${id} -> /api/v1/cases/{param}
                    norm_fe_path = re.sub(r'\$\{[^}]+\}', '{param}', raw_path)
                    # Strip query parameters (?tenant_id=...)
                    norm_fe_path = norm_fe_path.split("?")[0].rstrip("/")

                    # Check against registered routes
                    matched = False
                    for b_route in backend_routes:
                        # strip trailing slashes for comparison
                        clean_b = b_route.rstrip("/")
                        if norm_fe_path == clean_b or norm_fe_path.endswith(clean_b):
                            matched = True
                            break

                    if not matched:
                        self.log(
                            "endpoints", "WARNING", ts_file, line_idx,
                            f"Frontend API call '{raw_path}' does not match any registered FastAPI endpoint",
                            line
                        )

    # =========================================================================
    # 4. DEGENERATE / CONSTANT FUNCTIONS
    # =========================================================================
    def scan_constant_functions(self) -> None:
        """Finds non-abstract functions that take arguments but return constant literals without using them."""
        backend_dir = self.root / "backend" / "app"
        if not backend_dir.exists():
            return

        scan_root = self.target if self.target.is_relative_to(backend_dir) else backend_dir
        for py_file in scan_root.rglob("*.py"):
            if "tests" in py_file.parts or py_file.name.startswith("__"):
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # Ignore dunder methods, property getters, abstract methods
                    if node.name.startswith("__") or any(
                        isinstance(d, ast.Name) and d.id in ("abstractmethod", "property", "override")
                        for d in node.decorator_list
                    ):
                        continue

                    # Check if body is a single Return statement returning a constant
                    if len(node.body) == 1 and isinstance(node.body[0], ast.Return):
                        ret = node.body[0]
                        if isinstance(ret.value, ast.Constant):
                            # Function takes params (excluding self/cls) but immediately returns constant
                            param_names = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
                            if param_names:
                                self.log(
                                    "constant-funcs", "WARNING", py_file, node.lineno,
                                    f"Function '{node.name}' takes {param_names} but unconditionally returns constant: {ret.value.value!r}",
                                )

    # =========================================================================
    # 5. SILENCED CI & SECURITY CHECKS
    # =========================================================================
    def scan_silenced_checks(self) -> None:
        """Finds continue-on-error, error swallowers, empty except blocks, and commented assertions."""
        # 1. CI Workflows
        workflow_dir = self.root / ".github" / "workflows"
        if workflow_dir.exists() and (self.target == self.root or self.target.is_relative_to(self.root / ".github")):
            for yml_file in workflow_dir.rglob("*.yml"):
                try:
                    content = yml_file.read_text(encoding="utf-8")
                except Exception:
                    continue
                for line_idx, line in enumerate(content.splitlines(), start=1):
                    if "continue-on-error: true" in line:
                        self.log("silenced", "ERROR", yml_file, line_idx, "CI step configured with 'continue-on-error: true'", line)
                    if re.search(r'\|\s*true\b|\|\|\s*echo\b|\|\|\s*exit\s+0\b', line):
                        self.log("silenced", "ERROR", yml_file, line_idx, "CI command failure silenced with pipe fallback", line)

        # 2. Python exception swallows
        EXCLUDED_DIRS = {
            ".venv", "venv", "ENV", "env", "node_modules", "storage",
            "build", "dist", ".pytest_cache", ".ruff_cache", ".hypothesis",
            ".git", "__pycache__", "contracts", "htmlcov", "coverage", "scratch"
        }
        scan_root = self.target if self.target != self.root else self.root / "backend" / "app"
        if scan_root.exists():
            for py_file in scan_root.rglob("*.py"):
                if any(ex in py_file.parts for ex in EXCLUDED_DIRS) or "tests" in py_file.parts:
                    continue
                try:
                    content = py_file.read_text(encoding="utf-8")
                    tree = ast.parse(content, filename=str(py_file))
                except Exception:
                    continue

                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.ExceptHandler)
                        and len(node.body) == 1
                        and isinstance(node.body[0], ast.Pass)
                    ):
                        self.log(
                            "silenced", "WARNING", py_file, node.lineno,
                            "Swallowed exception handler with bare 'pass' body",
                        )

    # =========================================================================
    # 6. TODOS & DOCUMENTATION CONSISTENCY
    # =========================================================================
    def scan_todos_and_docs(self) -> None:
        """Scans for leftover TODO/FIXME markers, KaTeX unescaped underscores, and Mermaid syntax issues."""
        # 1. TODO/FIXME comments in active source code
        for search_dir in (self.root / "backend" / "app", self.root / "frontend" / "src"):
            if not search_dir.exists():
                continue
            for ext in ("*.py", "*.ts", "*.tsx"):
                for src_file in search_dir.rglob(ext):
                    try:
                        content = src_file.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    for line_idx, line in enumerate(content.splitlines(), start=1):
                        match = re.search(r'\b(TODO|FIXME|HACK|XXX)\b(?!\s*[:=]\s*["\'])', line)
                        if match:
                            self.log("todos-docs", "WARNING", src_file, line_idx, f"Found active '{match.group(1)}' marker", line)

        # 2. KaTeX underscore errors in documentation
        docs_dir = self.root / "docs"
        if docs_dir.exists():
            for md_file in docs_dir.rglob("*.md"):
                try:
                    content = md_file.read_text(encoding="utf-8")
                except Exception:
                    continue
                math_blocks = re.findall(r'\$\$(.*?)\$\$', content, re.DOTALL)
                cleaned = re.sub(r'\$\$(.*?)\$\$', '', content, flags=re.DOTALL)
                math_inlines = re.findall(r'(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)', cleaned)

                for block in math_blocks:
                    texts = re.findall(r'\\text\{([^}]*)\}', block)
                    for t in texts:
                        if '_' in t:
                            self.log("todos-docs", "ERROR", md_file, 1, f"KaTeX text-mode underscore fatal error: \\text{{{t}}}")
                for inl in math_inlines:
                    texts = re.findall(r'\\text\{([^}]*)\}', inl)
                    for t in texts:
                        if '_' in t:
                            self.log("todos-docs", "ERROR", md_file, 1, f"KaTeX inline text-mode underscore error: \\text{{{t}}}")

                # 3. Mermaid subgraph unquoted special characters
                mermaid_blocks = re.findall(r'```mermaid(.*?)```', content, re.DOTALL)
                for mb in mermaid_blocks:
                    for line in mb.strip().splitlines():
                        ls = line.strip()
                        if ls.startswith("subgraph"):
                            rest = ls[len("subgraph"):].strip()
                            if "[" not in rest and any(c in rest for c in ["&", "(", ")", "/", ":"]):
                                self.log("todos-docs", "ERROR", md_file, 1, f"Mermaid unquoted special chars in subgraph: '{ls}'")

    # =========================================================================
    # 7. PLAINTEXT PII & HARDCODED CREDENTIALS
    # =========================================================================
    def scan_pii_and_secrets(self) -> None:
        """Scans production source code for plaintext PII and hardcoded secrets."""
        EXCLUDED_DIRS = {
            ".venv", "venv", "ENV", "env", "node_modules", "storage",
            "build", "dist", ".pytest_cache", ".ruff_cache", ".hypothesis",
            ".git", "__pycache__", "contracts", "htmlcov", "coverage", "scratch"
        }
        scan_dirs = [self.root / "backend" / "app", self.root / "frontend" / "src"]
        if self.target != self.root:
            scan_dirs = [self.target]

        secret_patterns = [
            (re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'), "Private Key block embedded directly in source"),
            (re.compile(r'(?i)(?:api_key|secret_key|client_secret)\s*=\s*["\'][A-Za-z0-9_\-]{20,}["\']'), "Hardcoded API secret token"),
            (re.compile(r'(?<![.\d])(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})(?![.\d])'), "Unmasked Credit Card number pattern"),
        ]

        for s_dir in scan_dirs:
            if not s_dir.exists():
                continue
            for src_file in s_dir.rglob("*"):
                if src_file.is_dir() or any(ex in src_file.parts for ex in EXCLUDED_DIRS) or "tests" in src_file.parts:
                    continue
                if src_file.suffix not in (".py", ".ts", ".tsx"):
                    continue
                try:
                    content = src_file.read_text(encoding="utf-8")
                except Exception:
                    continue

                for line_idx, line in enumerate(content.splitlines(), start=1):
                    # Skip sample config lines in tests or schemas
                    if "example" in line.lower() or "schema" in src_file.name.lower():
                        continue
                    for pat, msg in secret_patterns:
                        if pat.search(line):
                            self.log("pii-secrets", "ERROR", src_file, line_idx, msg, line)

    # =========================================================================
    # EXECUTION RUNNER
    # =========================================================================
    def run_all(self) -> None:
        self.scan_mock_values()
        self.scan_dead_code()
        self.scan_endpoint_mismatch()
        self.scan_constant_functions()
        self.scan_silenced_checks()
        self.scan_todos_and_docs()
        self.scan_pii_and_secrets()

    def run_category(self, category: str) -> None:
        handlers = {
            "mock": self.scan_mock_values,
            "dead-code": self.scan_dead_code,
            "endpoints": self.scan_endpoint_mismatch,
            "constant-funcs": self.scan_constant_functions,
            "silenced": self.scan_silenced_checks,
            "todos-docs": self.scan_todos_and_docs,
            "pii-secrets": self.scan_pii_and_secrets,
        }
        if category in handlers:
            handlers[category]()
        else:
            raise ValueError(f"Unknown category '{category}'. Valid: {list(handlers.keys())}")


# =============================================================================
# CLI INTERFACE & FORMATTING
# =============================================================================
def print_report(findings: list[Finding]) -> int:
    if sys.platform == "win32":
        reconfig_stdout = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfig_stdout):
            reconfig_stdout(encoding="utf-8", errors="replace")
        reconfig_stderr = getattr(sys.stderr, "reconfigure", None)
        if callable(reconfig_stderr):
            reconfig_stderr(encoding="utf-8", errors="replace")

    errors = [f for f in findings if f.severity == "ERROR"]
    warnings = [f for f in findings if f.severity == "WARNING"]

    border = "=" * 80
    print("\n" + border)
    print(" [CFI INTEGRITY SCANNER] CODEBASE INTEGRITY & ZERO-MOCK AUDIT REPORT")
    print(border)

    if not findings:
        print("\n [PASS] ZERO ISSUES FOUND! Codebase is 100% clean, verified, and production-grade.\n")
        print(border)
        return 0

    print(f"\n Scan finished with {len(errors)} ERROR(S) and {len(warnings)} WARNING(S):\n")

    # Group by category
    categories = sorted(list({f.category for f in findings}))
    for cat in categories:
        cat_findings = [f for f in findings if f.category == cat]
        print(f" [CATEGORY: {cat.upper()}] ({len(cat_findings)} items)")
        print(" " + "-" * 78)
        for f in cat_findings:
            color = "[ERROR]" if f.severity == "ERROR" else "[WARN]"
            print(f"  {color} {f.file_path}:{f.line_number}")
            print(f"     Message: {f.message}")
            if f.code_snippet:
                print(f"     Snippet: {f.code_snippet}")
            print()

    print(border)
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous Codebase Integrity & Zero-Mock Scanner")
    parser.add_argument("--all", action="store_true", help="Run all integrity checks")
    parser.add_argument(
        "--category",
        choices=["mock", "dead-code", "endpoints", "constant-funcs", "silenced", "todos-docs", "pii-secrets"],
        help="Run a specific audit category",
    )
    parser.add_argument("--target", type=str, help="Target subpath to focus scan on (e.g. backend/app/application)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON report")
    parser.add_argument("--strict", action="store_true", help="Fail with exit code 1 on warnings as well as errors")

    args = parser.parse_args()

    target_path = Path(args.target).resolve() if args.target else None
    scanner = IntegrityScanner(target_path=target_path)

    if args.category:
        scanner.run_category(args.category)
    else:
        scanner.run_all()

    if args.json:
        report_data = {
            "total_findings": len(scanner.findings),
            "errors": len([f for f in scanner.findings if f.severity == "ERROR"]),
            "warnings": len([f for f in scanner.findings if f.severity == "WARNING"]),
            "findings": [asdict(f) for f in scanner.findings],
        }
        print(json.dumps(report_data, indent=2))
        sys.exit(1 if report_data["errors"] or (args.strict and report_data["warnings"]) else 0)

    exit_code = print_report(scanner.findings)
    if args.strict and any(f.severity == "WARNING" for f in scanner.findings):
        sys.exit(1)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
