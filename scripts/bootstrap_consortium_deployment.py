#!/usr/bin/env python3
"""Consortium Deployment Bootstrap and Governance Provisioning Tool.

Automates end-to-end bootstrap of the reproducible multi-bank consortium:
1. Health Probes: Asserts readiness of Gateway, Frontend, Backend, Coordinator,
   Postgres, Redis, and all three institutional bank workers.
2. Synthetic Entity Ingestion: Ingests authoritative synthetic entities into
   Bank A, Bank B, and Bank C private storage vaults via authenticated worker APIs.
   - Bank A & Bank B share a matching target entity (authentic MATCH).
   - Bank C contains a distinct benign entity (authentic NO_MATCH).
3. User & Consortium Governance Provisioning: Registers sovereign institutional
   analysts and creates governed consortium and bilateral operational grants.
4. Worker Grant Authorization: Enrolls approved bilateral operational grants
   onto the worker daemons with immutable scope digests.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("consortium_bootstrap")


def load_env_file(env_path: Path) -> dict[str, str]:
    """Parse key-value pairs from .env file."""
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env[key.strip()] = val.strip()
    return env


def http_get(url: str, timeout: float = 5.0) -> tuple[int, dict[str, Any] | str]:
    """Perform HTTP GET returning (status_code, parsed_json_or_text)."""
    req = urllib.request.Request(url, headers={"User-Agent": "CFI-Bootstrap/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, raw
    except Exception as exc:
        raise ConnectionError(f"HTTP GET failed for {url}: {exc}") from exc


def http_post(
    url: str,
    payload: dict[str, Any],
    token: str | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any] | str]:
    """Perform HTTP POST returning (status_code, parsed_json_or_text)."""
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CFI-Bootstrap/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, raw
    except Exception as exc:
        raise ConnectionError(f"HTTP POST failed for {url}: {exc}") from exc


def wait_for_service(name: str, url: str, max_retries: int = 15, delay: float = 2.0) -> bool:
    """Poll URL until HTTP 200 or max_retries exhausted."""
    for attempt in range(1, max_retries + 1):
        try:
            code, _ = http_get(url, timeout=3.0)
            if code == 200:
                logger.info("Service '%s' is ready at %s", name, url)
                return True
        except Exception:
            pass
        logger.debug("Waiting for '%s' (%s), attempt %d/%d...", name, url, attempt, max_retries)
        time.sleep(delay)
    logger.error("Service '%s' failed to become ready at %s within timeout.", name, url)
    return False


def run_docker_exec(
    container_name: str,
    cmd: list[str],
    stdin_input: str | None = None,
) -> str:
    """Run command inside a Docker container via docker exec without leaking sensitive inputs."""
    exec_flags = ["docker", "exec", "-i"] if stdin_input is not None else ["docker", "exec"]
    full_cmd = exec_flags + [container_name] + cmd
    res = subprocess.run(
        full_cmd,
        input=stdin_input,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"docker exec failed on '{container_name}' (exit {res.returncode})"
        )
    return res.stdout.strip()


def _validate_analyst_credentials(env: dict[str, str]) -> dict[str, str]:
    """Validate that independent, non-predictable analyst and operator credentials are supplied."""
    required_keys = (
        "ANALYST_BANK_A_PASSWORD",
        "ANALYST_BANK_B_PASSWORD",
        "ANALYST_BANK_C_PASSWORD",
        "CONSORTIUM_OPERATOR_PASSWORD",
    )
    resolved: dict[str, str] = {}
    forbidden_prefixes = (
        "replace_with_",
        "analyst",
        "admin",
        "auditor",
        "operator",
        "password",
        "changeme",
        "default",
    )
    for key in required_keys:
        val = (env.get(key) or os.environ.get(key) or "").strip()
        if not val or len(val) < 16 or val.lower().startswith(forbidden_prefixes):
            raise ValueError(
                f"Invalid or missing '{key}': bootstrap requires a non-predictable generated credential (>= 16 chars)."
            )
        resolved[key] = val

    if len(set(resolved.values())) != len(required_keys):
        raise ValueError(
            "Credentials across Bank A, Bank B, Bank C, and Consortium Operator must be mutually independent."
        )
    return resolved


def compute_deterministic_privacy_id(raw_id: str, entity_type: str = "customer") -> str:
    """Derive SHA256 HMAC-style privacy ID for test fixtures."""
    import hashlib
    canonical = f"{entity_type.lower()}:{raw_id.strip()}".encode()
    return hashlib.sha256(canonical).hexdigest()[:32]


def bootstrap_consortium(
    env_file: Path,
    backend_url: str = "http://127.0.0.1:8000",
    coordinator_url: str = "http://127.0.0.1:8200",
    worker_a_url: str = "http://127.0.0.1:8101",
    worker_b_url: str = "http://127.0.0.1:8102",
    worker_c_url: str = "http://127.0.0.1:8103",
    gateway_url: str = "http://127.0.0.1:80",
    consortium_id: str = "consortium_consortium_deployment_01",
) -> dict[str, Any]:
    """Execute complete bootstrap sequence."""
    env = load_env_file(env_file)
    if not env:
        raise FileNotFoundError(f"Environment secrets file '{env_file}' not found.")

    analyst_creds = _validate_analyst_credentials(env)

    logger.info("--- Phase 1: Service Readiness Verifications ---")
    services = [
        ("Gateway", f"{gateway_url}/gateway-health"),
        ("Backend API", f"{backend_url}/health"),
        ("PSI Coordinator", f"{coordinator_url}/health"),
        ("Worker Bank A", f"{worker_a_url}/worker/health"),
        ("Worker Bank B", f"{worker_b_url}/worker/health"),
        ("Worker Bank C", f"{worker_c_url}/worker/health"),
    ]
    for name, probe_url in services:
        if not wait_for_service(name, probe_url):
            raise RuntimeError(f"Consortium readiness failed: '{name}' is unreachable at {probe_url}")

    logger.info("--- Phase 2: Ingesting Authoritative Synthetic Entities ---")
    pid_match = compute_deterministic_privacy_id("DE-TAX-88912234-MATCH", "customer")
    pid_clean = compute_deterministic_privacy_id("FR-TAX-11223344-CLEAN", "customer")

    entity_a = {
        "id": "target_nexus_investigation_01",
        "entity_type": "customer",
        "bank_id": "bank_a",
        "privacy_id": pid_match,
        "display_label": "CUST-A-NEXUS-01",
        "attributes": {
            "tax_id": "DE-TAX-88912234-MATCH",
            "jurisdiction": "DE",
            "risk_score": 0.88,
        },
        "risk_level": "CRITICAL",
        "alert_count": 3,
    }

    entity_b = {
        "id": "counterpart_b_nexus_match",
        "entity_type": "customer",
        "bank_id": "bank_b",
        "privacy_id": pid_match,
        "display_label": "CUST-B-NEXUS-MATCH",
        "attributes": {
            "tax_id": "DE-TAX-88912234-MATCH",
            "jurisdiction": "DE",
            "risk_score": 0.79,
        },
        "risk_level": "HIGH",
        "alert_count": 2,
    }

    entity_c = {
        "id": "counterpart_c_clean_benign",
        "entity_type": "customer",
        "bank_id": "bank_c",
        "privacy_id": pid_clean,
        "display_label": "CUST-C-CLEAN-01",
        "attributes": {
            "tax_id": "FR-TAX-11223344-CLEAN",
            "jurisdiction": "FR",
            "risk_score": 0.05,
        },
        "risk_level": "MINIMAL",
        "alert_count": 0,
    }

    code_a, res_a = http_post(
        f"{worker_a_url}/worker/store-entity",
        {"entity": entity_a},
        token=env["WORKER_BANK_A_INGESTION_TOKEN"],
    )
    code_b, res_b = http_post(
        f"{worker_b_url}/worker/store-entity",
        {"entity": entity_b},
        token=env["WORKER_BANK_B_INGESTION_TOKEN"],
    )
    code_c, res_c = http_post(
        f"{worker_c_url}/worker/store-entity",
        {"entity": entity_c},
        token=env["WORKER_BANK_C_INGESTION_TOKEN"],
    )

    if code_a != 200 or code_b != 200 or code_c != 200:
        raise RuntimeError(f"Entity ingestion failed: a={code_a}, b={code_b}, c={code_c}")
    logger.info("Synthetic entities stored into isolated vaults of Bank A, B, and C.")

    logger.info("--- Phase 3: Provisioning Users & Governed Consortium ---")
    py_bootstrap_script = f"""
import json
import sys
from app.infrastructure.security.auth_service import AuthenticationService
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import MemberRole

creds = json.loads(sys.stdin.read())
auth_svc = AuthenticationService.get_instance()

# Register or update institutional analysts with least-privilege institutional roles
u_a = auth_svc.register_user(
    username="analyst_bank_a",
    plain_password=creds["ANALYST_BANK_A_PASSWORD"],
    bank_id="bank_a",
    roles=["compliance", "investigator"],
    clearance_level=2,
)
u_b = auth_svc.register_user(
    username="analyst_bank_b",
    plain_password=creds["ANALYST_BANK_B_PASSWORD"],
    bank_id="bank_b",
    roles=["compliance", "investigator"],
    clearance_level=2,
)
u_c = auth_svc.register_user(
    username="analyst_bank_c",
    plain_password=creds["ANALYST_BANK_C_PASSWORD"],
    bank_id="bank_c",
    roles=["compliance", "investigator"],
    clearance_level=2,
)
u_op = auth_svc.register_user(
    username="operator_consortium",
    plain_password=creds["CONSORTIUM_OPERATOR_PASSWORD"],
    bank_id="consortium",
    roles=["operator", "compliance"],
    clearance_level=3,
)
for uname in ("analyst_bank_a", "analyst_bank_b", "analyst_bank_c", "operator_consortium"):
    auth_svc.lockout_manager.reset(uname)

token_a = auth_svc.create_token_bundle(u_a).access_token
token_b = auth_svc.create_token_bundle(u_b).access_token
token_c = auth_svc.create_token_bundle(u_c).access_token

# Register local entity in Bank A entity service
from app.application.services.entity_resolution import EntityResolutionService, _entity_to_dict
from app.application.services.alert_service import AlertIntelligenceService, _alert_to_dict
from app.application.services.case_service import CaseManagementService, _case_to_dict
from app.domain.enums import AlertSeverity, AlertStatus, CasePriority, CaseStatus
from app.domain.investigation_entities import Alert, Case, CaseEvent, Entity, EntityType, RiskLevel

ent_svc = EntityResolutionService()
ent = Entity(
    id="target_nexus_investigation_01",
    entity_type=EntityType.CUSTOMER,
    privacy_id="{pid_match}",
    bank_id="bank_a",
    display_label="CUST-A-NEXUS-01",
    attributes={{"tax_id": "DE-TAX-88912234-MATCH", "jurisdiction": "DE", "risk_score": 0.88}},
    risk_level=RiskLevel.CRITICAL,
    alert_count=3,
)
ent_svc._entities.set(ent.id, _entity_to_dict(ent))

# Seed authoritative Bank A alert referencing target_nexus_investigation_01
alert_svc = AlertIntelligenceService()
alert_a = Alert(
    id="alert_consortium_nexus_bank_a_01",
    bank_id="bank_a",
    transaction_id="tx_nexus_cross_bank_01",
    risk_score=880.0,
    severity=AlertSeverity.CRITICAL,
    status=AlertStatus.NEW,
    reason_codes=["CROSS_BANK_SYNDICATE_VELOCITY", "HIGH_RISK_CORRIDOR"],
    confidence=0.94,
    involved_entity_ids=["target_nexus_investigation_01"],
)
alert_svc._alert_store.set(alert_a.id, _alert_to_dict(alert_a))

# Seed authoritative Bank A case and isolated Bank B case for cross-tenant negative verification
case_svc = CaseManagementService()
if not case_svc.get_case("case_consortium_nexus_bank_a_01"):
    case_a = Case(
        id="case_consortium_nexus_bank_a_01",
        bank_id="bank_a",
        title="Bank A Cross-Border Syndicate Nexus Investigation",
        status=CaseStatus.OPEN,
        priority=CasePriority.P1_CRITICAL,
        assigned_to="analyst_bank_a",
        alert_ids=["alert_consortium_nexus_bank_a_01"],
        total_risk_score=880.0,
    )
    case_svc._cases.set(case_a.id, _case_to_dict(case_a))

if not case_svc.get_case("case_consortium_bank_b_isolated_01"):
    case_b = Case(
        id="case_consortium_bank_b_isolated_01",
        bank_id="bank_b",
        title="Bank B Confidential Counterparty Treasury Review",
        status=CaseStatus.OPEN,
        priority=CasePriority.P2_HIGH,
        assigned_to="analyst_bank_b",
        alert_ids=[],
        total_risk_score=640.0,
    )
    case_svc._cases.set(case_b.id, _case_to_dict(case_b))

gov_svc = ConsortiumGovernanceService()
c_id = "{consortium_id}"
consortium = gov_svc.get_consortium(c_id)
if not consortium:
    gov_svc.create_consortium(
        consortium_id=c_id,
        name="Consortium European Pilot Deployment",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.add_member(c_id, "bank_b", role=MemberRole.FULL_MEMBER, auth_token=token_a)
    gov_svc.add_member(c_id, "bank_c", role=MemberRole.FULL_MEMBER, auth_token=token_a)

# Bilateral Grant A <-> B
grant_ab = gov_svc.get_pairwise_operational_grant(c_id, "grant_consortium_deployment_ab")
if not grant_ab:
    gov_svc.register_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_consortium_deployment_ab",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="BILATERAL_AML_TREATY",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU", "US"],
        institution_jurisdictions={{"bank_a": "EU", "bank_b": "EU"}},
        allow_cross_border=False,
        allow_institution_identity_sharing=True,
        revision=1,
        auth_token=token_a,
    )
    gov_svc.approve_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_consortium_deployment_ab",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

# Bilateral Grant A <-> C
grant_ac = gov_svc.get_pairwise_operational_grant(c_id, "grant_consortium_deployment_ac")
if not grant_ac:
    gov_svc.register_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_consortium_deployment_ac",
        institution_a="bank_a",
        institution_b="bank_c",
        authority="BILATERAL_AML_TREATY",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU", "US"],
        institution_jurisdictions={{"bank_a": "EU", "bank_c": "EU"}},
        allow_cross_border=False,
        allow_institution_identity_sharing=True,
        revision=1,
        auth_token=token_a,
    )
    gov_svc.approve_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_consortium_deployment_ac",
        approving_bank_id="bank_c",
        auth_token=token_c,
    )

g_ab = gov_svc.get_pairwise_operational_grant(c_id, "grant_consortium_deployment_ab")
g_ac = gov_svc.get_pairwise_operational_grant(c_id, "grant_consortium_deployment_ac")

result = {{
    "consortium_id": c_id,
    "scope_digest_ab": g_ab["scope_digest"] if g_ab else "",
    "scope_digest_ac": g_ac["scope_digest"] if g_ac else "",
}}
print(json.dumps(result))
"""

    stdout = run_docker_exec(
        "cfi-api-server",
        ["python", "-c", py_bootstrap_script],
        stdin_input=json.dumps(analyst_creds),
    )
    # Extract the last line which contains JSON output
    last_line = [ln for ln in stdout.splitlines() if ln.strip().startswith("{")][-1]
    gov_meta = json.loads(last_line)
    digest_ab = gov_meta["scope_digest_ab"]
    digest_ac = gov_meta["scope_digest_ac"]
    logger.info("Consortium governance established. Scope digests: AB=%s, AC=%s", digest_ab[:12], digest_ac[:12])

    logger.info("Synchronizing authoritative identity and consortium stores to coordinator daemon...")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        t_acc = os.path.join(td, "auth_identity_store.json")
        t_gov = os.path.join(td, "consortium_governance_store.json")
        subprocess.run(["docker", "cp", "cfi-api-server:/app/storage/auth_identity_store.json", t_acc], check=True)
        subprocess.run(["docker", "cp", "cfi-api-server:/app/storage/consortium_governance_store.json", t_gov], check=True)
        subprocess.run(["docker", "cp", t_acc, "cfi-coordinator:/vault/auth_identity_store.json"], check=True)
        subprocess.run(["docker", "cp", t_gov, "cfi-coordinator:/vault/consortium_governance_store.json"], check=True)
        subprocess.run(["docker", "exec", "-u", "0", "cfi-coordinator", "chown", "-R", "worker:worker", "/vault"], check=True)
        subprocess.run(["docker", "exec", "-u", "0", "cfi-coordinator", "chmod", "600", "/vault/auth_identity_store.json", "/vault/consortium_governance_store.json"], check=True)
    logger.info("Stores synchronized to coordinator vault.")

    logger.info("--- Phase 4: Authorizing Grants on Institutional Workers ---")
    # Worker Bank A authorizes AB and AC
    code_a_ab, _ = http_post(
        f"{worker_a_url}/worker/authorize-grant",
        {
            "consortium_id": consortium_id,
            "grant_id": "grant_consortium_deployment_ab",
            "revision": 1,
            "scope_digest": digest_ab,
            "permitted_counterparts": ["bank_b"],
            "permitted_purposes": ["AML_COLLABORATIVE_INVESTIGATION"],
            "allow_institution_identity_sharing": True,
        },
        token=env["WORKER_BANK_A_ADMIN_TOKEN"],
    )
    code_a_ac, _ = http_post(
        f"{worker_a_url}/worker/authorize-grant",
        {
            "consortium_id": consortium_id,
            "grant_id": "grant_consortium_deployment_ac",
            "revision": 1,
            "scope_digest": digest_ac,
            "permitted_counterparts": ["bank_c"],
            "permitted_purposes": ["AML_COLLABORATIVE_INVESTIGATION"],
            "allow_institution_identity_sharing": True,
        },
        token=env["WORKER_BANK_A_ADMIN_TOKEN"],
    )

    # Worker Bank B authorizes AB
    code_b_ab, _ = http_post(
        f"{worker_b_url}/worker/authorize-grant",
        {
            "consortium_id": consortium_id,
            "grant_id": "grant_consortium_deployment_ab",
            "revision": 1,
            "scope_digest": digest_ab,
            "permitted_counterparts": ["bank_a"],
            "permitted_purposes": ["AML_COLLABORATIVE_INVESTIGATION"],
            "allow_institution_identity_sharing": True,
        },
        token=env["WORKER_BANK_B_ADMIN_TOKEN"],
    )

    # Worker Bank C authorizes AC
    code_c_ac, _ = http_post(
        f"{worker_c_url}/worker/authorize-grant",
        {
            "consortium_id": consortium_id,
            "grant_id": "grant_consortium_deployment_ac",
            "revision": 1,
            "scope_digest": digest_ac,
            "permitted_counterparts": ["bank_a"],
            "permitted_purposes": ["AML_COLLABORATIVE_INVESTIGATION"],
            "allow_institution_identity_sharing": True,
        },
        token=env["WORKER_BANK_C_ADMIN_TOKEN"],
    )

    if not all(c == 200 for c in [code_a_ab, code_a_ac, code_b_ab, code_c_ac]):
        raise RuntimeError(
            f"Worker grant authorization failed: a_ab={code_a_ab}, a_ac={code_a_ac}, b_ab={code_b_ab}, c_ac={code_c_ac}"
        )

    logger.info("Institutional worker nodes authorized bilateral grants successfully.")

    summary = {
        "status": "BOOTSTRAP_SUCCESSFUL",
        "consortium_id": consortium_id,
        "institutions": ["bank_a", "bank_b", "bank_c"],
        "grants": ["grant_consortium_deployment_ab", "grant_consortium_deployment_ac"],
        "entities": {
            "target_nexus_investigation_01": {"bank": "bank_a", "expected_match": "bank_b"},
            "counterpart_b_nexus_match": {"bank": "bank_b", "matches": "target_nexus_investigation_01"},
            "counterpart_c_clean_benign": {"bank": "bank_c", "matches": "NONE"},
        },
        "endpoints": {
            "gateway": gateway_url,
            "backend": backend_url,
            "coordinator": coordinator_url,
            "worker_a": worker_a_url,
            "worker_b": worker_b_url,
            "worker_c": worker_c_url,
        },
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap consortium deployment environment.")
    parser.add_argument("--env-file", type=Path, default=Path(".env.consortium"))
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--coordinator-url", default="http://127.0.0.1:8200")
    parser.add_argument("--worker-a-url", default="http://127.0.0.1:8101")
    parser.add_argument("--worker-b-url", default="http://127.0.0.1:8102")
    parser.add_argument("--worker-c-url", default="http://127.0.0.1:8103")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:80")
    parser.add_argument("--consortium-id", default="consortium_consortium_deployment_01")
    args = parser.parse_args()

    summary = bootstrap_consortium(
        env_file=args.env_file,
        backend_url=args.backend_url,
        coordinator_url=args.coordinator_url,
        worker_a_url=args.worker_a_url,
        worker_b_url=args.worker_b_url,
        worker_c_url=args.worker_c_url,
        gateway_url=args.gateway_url,
        consortium_id=args.consortium_id,
    )
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
