#!/usr/bin/env python3
"""CLI tool for compliance officers to generate EU AI Act compliance certificates.

Generates cryptographically signed JSON compliance certificates and structured
Markdown compliance binders for EU AI Act Regulation (EU) 2024/1689 Articles 10-15.

Usage:
    python scripts/export_compliance_report.py \\
      --model-version v2.1.0 \\
      --fl-rounds 25 \\
      --dp-epsilon 2.3 \\
      --dp-delta 1e-5 \\
      --iban-pass-rate 0.9995 \\
      --anomaly-count 1 \\
      --dual-signoff \\
      --model-auc 0.93 \\
      --model-f1 0.88 \\
      --audit-log-sha256 abc123... \\
      --hyperparams-sha256 def456... \\
      --explainability-method SHAP \\
      --consortium-size 5 \\
      --output-dir certs/ \\
      --format json,markdown \\
      --signing-key-env CFI_COMPLIANCE_SIGNING_KEY
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _resolve_signing_key(env_var: str, hex_key: str | None) -> bytes:
    """Resolves the HMAC signing key from environment variable or hex CLI argument."""
    if hex_key:
        try:
            return bytes.fromhex(hex_key)
        except ValueError as exc:
            sys.exit(f"Error: --signing-key-hex is not valid hexadecimal: {exc}")

    raw = os.environ.get(env_var, "")
    if raw:
        # Accept both hex-encoded and raw string signing keys from env
        try:
            return bytes.fromhex(raw)
        except ValueError:
            return raw.encode("utf-8")

    # Default insecure dev key — warns loudly
    print(
        "WARNING: No signing key provided via --signing-key-hex or "
        f"${env_var}. Using insecure development key. DO NOT use in production.",
        file=sys.stderr,
    )
    return b"cfi-platform-dev-signing-key-insecure"


def build_parser() -> argparse.ArgumentParser:
    """Builds the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="export_compliance_report",
        description=(
            "Generate EU AI Act (EU) 2024/1689 compliance certificates for the "
            "CFI Platform Federated Learning global model."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Model identity
    parser.add_argument(
        "--model-version",
        default="v1.0.0",
        help="Semantic version tag of the assessed FL global model (default: v1.0.0)",
    )

    # FL metrics
    parser.add_argument(
        "--fl-rounds",
        type=int,
        default=10,
        metavar="N",
        help="Number of FL training rounds completed (default: 10)",
    )
    parser.add_argument(
        "--dp-epsilon",
        type=float,
        default=1.0,
        metavar="ε",
        help="Cumulative DP epsilon budget consumed (default: 1.0)",
    )
    parser.add_argument(
        "--dp-delta",
        type=float,
        default=1e-5,
        metavar="δ",
        help="Differential privacy delta parameter (default: 1e-5)",
    )
    parser.add_argument(
        "--iban-pass-rate",
        type=float,
        default=1.0,
        metavar="RATE",
        help="Fraction of transactions passing ISO 13616 IBAN validation (default: 1.0)",
    )
    parser.add_argument(
        "--anomaly-count",
        type=int,
        default=0,
        metavar="N",
        help="Number of Byzantine/spectral anomalies detected (default: 0)",
    )

    # Governance
    parser.add_argument(
        "--dual-signoff",
        action="store_true",
        default=False,
        help="Flag indicating dual-role sign-off (ML Engineer + Compliance Officer) was granted",
    )
    parser.add_argument(
        "--model-auc",
        type=float,
        default=0.0,
        metavar="AUC",
        help="Model Area Under ROC Curve (default: 0.0)",
    )
    parser.add_argument(
        "--model-f1",
        type=float,
        default=0.0,
        metavar="F1",
        help="Model F1 score (default: 0.0)",
    )
    parser.add_argument(
        "--audit-log-sha256",
        default="",
        metavar="HASH",
        help="SHA-256 hex digest of the FL round audit log",
    )
    parser.add_argument(
        "--hyperparams-sha256",
        default="",
        metavar="HASH",
        help="SHA-256 hex digest of training hyperparameters JSON",
    )
    parser.add_argument(
        "--explainability-method",
        default="SHAP",
        help="Explainability method used for model transparency (default: SHAP)",
    )
    parser.add_argument(
        "--consortium-size",
        type=int,
        default=1,
        metavar="N",
        help="Number of participating bank nodes in the consortium (default: 1)",
    )

    # Signing key
    parser.add_argument(
        "--signing-key-env",
        default="CFI_COMPLIANCE_SIGNING_KEY",
        metavar="ENV_VAR",
        help="Environment variable name holding the HMAC signing key (default: CFI_COMPLIANCE_SIGNING_KEY)",
    )
    parser.add_argument(
        "--signing-key-hex",
        default=None,
        metavar="HEX",
        help="HMAC signing key as hex string (overrides --signing-key-env; not recommended for production)",
    )

    # Output
    parser.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Directory to write certificate files to (default: current directory)",
    )
    parser.add_argument(
        "--format",
        default="json,markdown",
        metavar="FMT",
        help="Comma-separated output formats: json, markdown (default: json,markdown)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress stdout output (only write files)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the compliance certificate export CLI.

    Returns:
        0 on success, 1 on compliance failure, 2 on argument/IO error.
    """
    # Lazy import to allow importability checks in tests
    from app.domain.ai_act_compliance import EUAIActComplianceEngine  # noqa: PLC0415

    parser = build_parser()
    args = parser.parse_args(argv)

    signing_key = _resolve_signing_key(args.signing_key_env, args.signing_key_hex)

    engine = EUAIActComplianceEngine(
        model_version=args.model_version,
        fl_rounds_completed=args.fl_rounds,
        dp_epsilon=args.dp_epsilon,
        dp_delta=args.dp_delta,
        iban_pass_rate=args.iban_pass_rate,
        spectral_anomaly_count=args.anomaly_count,
        dual_signoff_approved=args.dual_signoff,
        model_auc=args.model_auc,
        model_f1=args.model_f1,
        audit_log_sha256=args.audit_log_sha256,
        hyperparams_sha256=args.hyperparams_sha256,
        explainability_method=args.explainability_method,
        consortium_size=args.consortium_size,
    )

    cert = engine.generate_certificate(signing_key)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"cfi_compliance_{timestamp}"

    formats = {f.strip().lower() for f in args.format.split(",")}
    written: list[str] = []

    if "json" in formats:
        json_path = output_dir / f"{base_name}.json"
        json_path.write_text(EUAIActComplianceEngine.export_json(cert), encoding="utf-8")
        written.append(str(json_path))

    if "markdown" in formats:
        md_path = output_dir / f"{base_name}.md"
        md_path.write_text(EUAIActComplianceEngine.export_markdown(cert), encoding="utf-8")
        written.append(str(md_path))

    if not args.quiet:
        overall = "COMPLIANT [PASS]" if cert.overall_compliant else "NON-COMPLIANT [FAIL]"
        print(f"Certificate ID  : {cert.cert_id}")
        print(f"Model Version   : {cert.model_version}")
        print(f"Issued At       : {cert.issued_at}")
        print(f"Overall Status  : {overall}")
        print(f"Fingerprint     : {cert.cert_hash}")
        print(f"Files written   : {', '.join(written)}")

    return 0 if cert.overall_compliant else 1


if __name__ == "__main__":
    sys.exit(main())
