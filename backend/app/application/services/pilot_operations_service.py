"""Pilot Operations Service — Governed Two-Bank Pilot Lifecycle Management.

Adheres strictly to .agents/AGENTS.md:
- Full lifecycle: DRAFT -> PREFLIGHT -> AWAITING_APPROVAL -> AUTHORIZED -> ACTIVE -> PAUSED -> COMPLETED
- Terminal outcomes: ABORTED, FAILED
- Multi-party independent authorization: Bank A, Bank B, and Operator approvals required.
- Stale-approval rejection: any scope/config revision invalidates previous approvals.
- Kill switch: immediate PAUSE and ABORT failing closed on all new queries.
- Operational resilience & auditability: all events recorded in ImmutableAuditChain.
- Durable persistence via DurablePilotStore.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC
from typing import Any

from app.application.schemas.pilot_operations import (
    PilotPreflightRequest,
    PilotProvisionRequest,
    PilotTelemetrySummary,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ConsortiumStatus
from app.domain.institutional_identity import (
    InstitutionalVerificationRegistry,
    canonical_coordinator_readiness_bytes,
    canonical_worker_readiness_bytes,
)
from app.domain.pilot_operations import (
    InvalidPilotTransitionError,
    PilotApprovalRecord,
    PilotAuthorizationError,
    PilotEntity,
    PilotExecutionReceipt,
    PilotExpiredError,
    PilotOperationalConfig,
    PilotQuotaExceededError,
    PilotStatus,
    PreflightCheckResult,
    canonical_pilot_approval_bytes,
)
from app.domain.pilot_operations import (
    PilotGovernanceError as PilotGovernanceError,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore

logger = logging.getLogger(__name__)


class PilotOperationsService:
    """Orchestrates controlled two-bank pilot operations lifecycle and execution gating."""

    _instance: PilotOperationsService | None = None

    @classmethod
    def get_instance(cls) -> PilotOperationsService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(
        self,
        store: DurablePilotStore | None = None,
        consortium_service: ConsortiumGovernanceService | None = None,
        institutional_registry: InstitutionalVerificationRegistry | None = None,
        pilot_store: DurablePilotStore | None = None,
    ) -> None:
        self.store = store or pilot_store or DurablePilotStore.get_instance()
        self.consortium_service = consortium_service or ConsortiumGovernanceService()
        self.institutional_registry = institutional_registry or InstitutionalVerificationRegistry()
        self._audit_chain = ImmutableAuditChain.get_instance()
        self._lock = threading.RLock()

    def _resolve_topology_endpoints(self, entity: PilotEntity) -> tuple[str, str, str, list[str]]:
        """Resolves approved topology endpoints strictly from the immutable pilot configuration.

        Returns (bank_a_url, bank_b_url, coord_url, missing_nodes).
        NEVER falls back to environment variables or ambient system state.
        """
        topo = entity.config.topology or {}
        worker_eps = getattr(entity.config, "worker_endpoints", {}) or {}
        coord_ep = getattr(entity.config, "coordinator_endpoint", "") or ""

        insts = entity.config.participating_institutions
        bank_a = insts[0] if len(insts) > 0 else "bank_a"
        bank_b = insts[1] if len(insts) > 1 else "bank_b"

        bank_a_url = (
            worker_eps.get(bank_a)
            or worker_eps.get("bank_a")
            or topo.get(f"{bank_a}_worker_url")
            or topo.get(f"{bank_a}_url")
            or topo.get("bank_a_worker_url")
            or topo.get("bank_a_url")
            or topo.get(bank_a)
            or ""
        )
        bank_b_url = (
            worker_eps.get(bank_b)
            or worker_eps.get("bank_b")
            or topo.get(f"{bank_b}_worker_url")
            or topo.get(f"{bank_b}_url")
            or topo.get("bank_b_worker_url")
            or topo.get("bank_b_url")
            or topo.get(bank_b)
            or ""
        )
        coord_url = (
            coord_ep
            or topo.get("coordinator_url")
            or topo.get("coord_url")
            or topo.get("coordinator")
            or ""
        )

        missing = []
        if not bank_a_url:
            missing.append(f"{bank_a} worker")
        if not bank_b_url:
            missing.append(f"{bank_b} worker")
        if not coord_url:
            missing.append("coordinator")

        return str(bank_a_url).strip(), str(bank_b_url).strip(), str(coord_url).strip(), missing

    def _probe_node_health(
        self,
        url: str | None,
        expected_type: str = "worker",
        expected_bank_id: str | None = None,
    ) -> tuple[bool, str]:
        """Probe actual HTTP readiness and authenticate cryptographic institutional identity.

        Enforces:
        - Outbound destination validation (SSRF / cloud metadata / DNS rebinding prevention).
        - Loopback probe restricted strictly to synthetic test environment.
        - Nonce challenge-response to prevent probe replay.
        - Cryptographic Ed25519 signature verification against InstitutionalVerificationRegistry.
        - Independent coordinator HMAC authentication.
        """
        if not url or not url.strip():
            return False, "NOT_CONFIGURED: Topology endpoint URL is missing."

        clean_url = url.strip().rstrip("/")
        parsed = urllib.parse.urlparse(clean_url)
        if parsed.scheme not in ("http", "https"):
            return False, f"FORBIDDEN_SCHEME: Scheme '{parsed.scheme}' rejected. Only http and https permitted."

        hostname = (parsed.hostname or "").lower().strip()
        if not hostname:
            return False, "INVALID_URL: Missing hostname in topology endpoint URL."

        forbidden_hosts = {
            "169.254.169.254",
            "metadata.google.internal",
            "metadata.google",
            "100.100.100.200",
            "fd00:ec2::254",
        }
        if hostname in forbidden_hosts:
            return False, f"SSRF_PROHIBITED: Endpoint '{hostname}' points to forbidden cloud metadata."

        is_test_env = bool(
            os.getenv("PYTEST_CURRENT_TEST")
            or os.getenv("CFI_TEST_ENVIRONMENT")
            or os.getenv("CI")
            or os.getenv("APP_ENV") == "test"
        )

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addr_info = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            resolved_ips: list[str] = [str(ai[4][0]) for ai in addr_info]
        except Exception as dns_exc:
            return False, f"DNS_RESOLUTION_FAILED: Cannot resolve host '{hostname}': {dns_exc}"

        for ip_str in resolved_ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if ip_str in forbidden_hosts or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return False, f"SSRF_PROHIBITED: Endpoint resolved to forbidden address '{ip_str}'."
            if ip.is_loopback and not is_test_env:
                return False, f"SSRF_PROHIBITED: Loopback address '{ip_str}' rejected outside verified test environment."

        probe_nonce = secrets.token_hex(16)
        target_ip: str = resolved_ips[0]
        ip_formatted = f"[{target_ip}]" if ":" in target_ip else target_ip
        sub_path = "/worker/health" if expected_type == "worker" else "/health"
        probe_path = f"{parsed.scheme}://{ip_formatted}:{port}{sub_path}?nonce={probe_nonce}"

        class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def http_error_302(self, req, fp, code, msg, headers):
                raise urllib.error.HTTPError(req.full_url, code, "Redirect forbidden for security probe", headers, fp)
            http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())
            req = urllib.request.Request(probe_path, method="GET")
            req.add_header("Host", parsed.netloc)
            req.add_header("User-Agent", "CFI-Pilot-Preflight/1.0")
            with opener.open(req, timeout=2.0) as resp:
                if resp.status != 200:
                    return False, f"UNHEALTHY (status={resp.status})"
                body_bytes = resp.read()
                try:
                    data = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    return False, "INVALID_RESPONSE: Non-JSON body returned by node health probe."

                status_val = str(data.get("status", "")).upper()
                if status_val not in ("ONLINE", "HEALTHY"):
                    return False, f"UNHEALTHY_STATUS: Node reported status '{data.get('status')}'."

                # Verify challenge nonce strictly (must exist and match probe_nonce)
                resp_nonce = str(data.get("nonce", "")).strip()
                if not resp_nonce or resp_nonce != probe_nonce:
                    return False, (
                        f"AUTHENTICATION_FAILED: Challenge nonce mismatch or missing "
                        f"(received '{resp_nonce}', expected '{probe_nonce}')."
                    )

                # Verify timestamp freshness strictly (< 300 seconds)
                resp_ts_str = str(data.get("timestamp", "")).strip()
                if not resp_ts_str:
                    return False, "AUTHENTICATION_FAILED: Missing readiness timestamp in node health response."
                try:
                    resp_ts = datetime.datetime.fromisoformat(resp_ts_str)
                    if resp_ts.tzinfo is None:
                        resp_ts = resp_ts.replace(tzinfo=UTC)
                    now_utc = datetime.datetime.now(UTC)
                    if abs((now_utc - resp_ts).total_seconds()) > 300:
                        return False, f"AUTHENTICATION_FAILED: Stale readiness timestamp '{resp_ts_str}'."
                except Exception:
                    return False, f"AUTHENTICATION_FAILED: Invalid readiness timestamp format '{resp_ts_str}'."

                if expected_type == "worker":
                    clean_expected = (expected_bank_id or "").lower().strip()
                    resp_bank = str(data.get("bank_id", "")).lower().strip()
                    if clean_expected and resp_bank != clean_expected:
                        return False, (
                            f"IDENTITY_MISMATCH: Worker endpoint at '{clean_url}' returned bank_id '{resp_bank}', "
                            f"expected '{clean_expected}'."
                        )

                    resp_role = str(data.get("role", "")).lower().strip()
                    if resp_role and resp_role != "worker":
                        return False, f"IDENTITY_MISMATCH: Worker endpoint returned invalid role '{resp_role}'."

                    protocol = str(data.get("protocol", "")).strip()
                    if protocol and protocol != "OPENMINED-PSI-2.0.6-ECDH":
                        return False, f"PROTOCOL_UNSUPPORTED: Worker reported protocol '{protocol}', expected 'OPENMINED-PSI-2.0.6-ECDH'."

                    # Validate sovereign Ed25519 signature against InstitutionalVerificationRegistry
                    sig = str(data.get("signature", "")).strip()
                    if not sig:
                        return False, (
                            f"AUTHENTICATION_FAILED: Worker endpoint at '{clean_url}' returned unsigned health response "
                            "without cryptographic institutional workload identity."
                        )

                    canonical_stmt = canonical_worker_readiness_bytes(
                        bank_id=clean_expected,
                        nonce=resp_nonce,
                        timestamp=resp_ts_str,
                        protocol=protocol or "OPENMINED-PSI-2.0.6-ECDH",
                    )
                    verified = self.institutional_registry.verify_signature(clean_expected, sig, canonical_stmt)
                    if not verified:
                        return False, (
                            f"AUTHENTICATION_FAILED: Cryptographic readiness signature verification failed for institution '{clean_expected}'. "
                            "Signature is invalid or does not match enrolled public key."
                        )

                elif expected_type == "coordinator":
                    resp_role = str(data.get("role", "")).lower().strip()
                    if resp_role != "coordinator":
                        return False, f"IDENTITY_MISMATCH: Coordinator endpoint at '{clean_url}' returned role '{resp_role}', expected 'coordinator'."

                    coord_secret = (
                        os.environ.get("CFI_COORDINATOR_SECRET", "")
                        or os.environ.get("COORDINATOR_SECRET", "")
                        or os.environ.get("COORDINATOR_AUTH_SECRET", "")
                    )
                    if not coord_secret or not coord_secret.strip():
                        return False, (
                            "AUTHENTICATION_FAILED: Coordinator workload authentication secret is missing or unconfigured. "
                            "Every operational pilot must configure an independently trusted coordinator credential."
                        )

                    coord_id = str(data.get("coordinator_id", "coordinator")).strip()
                    if not coord_id:
                        return False, "IDENTITY_MISMATCH: Coordinator endpoint returned empty coordinator_id."

                    coord_proto = str(data.get("protocol", "OPENMINED-PSI-2.0.6-ECDH")).strip()
                    if coord_proto != "OPENMINED-PSI-2.0.6-ECDH":
                        return False, f"PROTOCOL_UNSUPPORTED: Coordinator reported protocol '{coord_proto}', expected 'OPENMINED-PSI-2.0.6-ECDH'."

                    actual_sig = str(data.get("coordinator_signature", "")).strip()
                    if not actual_sig:
                        return False, (
                            f"AUTHENTICATION_FAILED: Coordinator endpoint at '{clean_url}' returned unsigned health response "
                            "without cryptographic coordinator workload signature."
                        )

                    canonical_coord = canonical_coordinator_readiness_bytes(
                        nonce=resp_nonce,
                        timestamp=resp_ts_str,
                        role="coordinator",
                        coordinator_id=coord_id,
                        protocol=coord_proto,
                    )
                    expected_sig = hmac.new(coord_secret.encode("utf-8"), canonical_coord, hashlib.sha256).hexdigest()
                    if not hmac.compare_digest(actual_sig, expected_sig):
                        return False, "AUTHENTICATION_FAILED: Coordinator HMAC readiness signature verification failed."

                return True, f"ONLINE (type={expected_type}, bank={expected_bank_id or 'coord'}, protocol=OPENMINED-PSI-2.0.6-ECDH, authenticated=True)"
        except Exception as exc:
            return False, f"OFFLINE ({exc})"

    def provision_pilot(
        self,
        req: PilotProvisionRequest | None = None,
        principal: AuthenticatedPrincipal | None = None,
        pilot_id: str | None = None,
        consortium_id: str | None = None,
        participating_institutions: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
        allowed_jurisdictions: list[str] | None = None,
        approved_synthetic_datasets: list[str] | None = None,
        max_queries_total: int = 50,
        max_queries_per_institution: int = 25,
        duration_days: int = 14,
        creator_actor: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Provision a new two-bank pilot in DRAFT state."""
        if req is None:
            if not pilot_id or not consortium_id:
                raise ValueError("Both pilot_id and consortium_id are mandatory to provision a pilot.")

            topo = dict(kwargs.get("topology") or {})
            worker_eps = dict(kwargs.get("worker_endpoints") or {})
            for k, v in worker_eps.items():
                topo[f"{k.lower()}_worker_url"] = v
            coord_ep = kwargs.get("coordinator_endpoint") or kwargs.get("coordinator_url")
            if coord_ep:
                topo["coordinator_url"] = coord_ep

            total_q = kwargs.get("query_quota_total") or kwargs.get("max_queries_total") or max_queries_total
            per_inst_q = kwargs.get("query_quota_per_institution") or kwargs.get("max_queries_per_institution") or max_queries_per_institution
            d_sets = kwargs.get("approved_datasets") or kwargs.get("approved_synthetic_datasets") or approved_synthetic_datasets or {}

            req = PilotProvisionRequest(
                pilot_id=pilot_id,
                consortium_id=consortium_id,
                participating_institutions=participating_institutions or ["bank_a", "bank_b"],
                allowed_purposes=allowed_purposes or ["AML_INVESTIGATION"],
                allowed_jurisdictions=allowed_jurisdictions or ["EU"],
                approved_datasets=d_sets,
                query_quota_total=total_q,
                query_quota_per_institution=per_inst_q,
                duration_days=duration_days,
                starts_at=str(kwargs.get("starts_at") or ""),
                expires_at=str(kwargs.get("expires_at") or ""),
                operational_grant_id=str(kwargs.get("operational_grant_id") or ""),
                topology=topo,
            )
        clean_pilot_id = req.pilot_id.strip()
        clean_consortium_id = req.consortium_id.lower().strip()

        # Check existing
        existing = self.store.get_pilot(clean_pilot_id)
        if existing is not None:
            raise ValueError(f"Pilot with ID '{clean_pilot_id}' already exists.")

        # Validate consortium exists and is active
        consortium = self.consortium_service.get_consortium(clean_consortium_id)
        if not consortium:
            raise ValueError(f"Consortium '{clean_consortium_id}' does not exist.")
        if consortium.status != ConsortiumStatus.ACTIVE:
            raise ValueError(
                f"Consortium '{clean_consortium_id}' is in non-active status '{consortium.status.value}'."
            )

        # Validate institutions belong to consortium
        clean_insts = [b.lower().strip() for b in req.participating_institutions]
        if len(clean_insts) < 2:
            raise ValueError("A two-bank pilot requires at least 2 participating institutions.")
        for inst in clean_insts:
            if inst not in consortium.members or inst in consortium.removed_members:
                raise ValueError(
                    f"Institution '{inst}' is not an active member of consortium '{clean_consortium_id}'."
                )

        # Bind operational grant from consortium metadata if not specified
        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        grant_id = req.operational_grant_id or op_grant.get("grant_id", f"grant_{clean_consortium_id}")
        grant_revision = int(op_grant.get("revision", 1))
        grant_scope_digest = str(op_grant.get("scope_digest", ""))

        now = datetime.datetime.now(UTC)
        starts_at = req.starts_at or now.isoformat()
        expires_at = req.expires_at or (now + datetime.timedelta(days=14)).isoformat()

        merged_topo = dict(req.topology or {})
        worker_endpoints_dict = dict(kwargs.get("worker_endpoints") or {})
        for w_k, w_v in worker_endpoints_dict.items():
            clean_k = w_k.lower().strip()
            if clean_k not in clean_insts:
                raise ValueError(f"Worker endpoint for unapproved institution '{w_k}' rejected.")
            if clean_k not in merged_topo and f"{clean_k}_worker_url" not in merged_topo:
                merged_topo[f"{clean_k}_worker_url"] = str(w_v)
        c_url = kwargs.get("coordinator_endpoint") or kwargs.get("coordinator_url")
        if c_url and "coordinator_url" not in merged_topo and "coord_url" not in merged_topo:
            merged_topo["coordinator_url"] = str(c_url)

        for ep_key in list(merged_topo.keys()):
            if "_worker_url" in ep_key:
                inst_prefix = ep_key.replace("_worker_url", "").lower().strip()
                if inst_prefix not in clean_insts:
                    raise ValueError(f"Topology worker endpoint for unapproved institution '{inst_prefix}' rejected.")

        config = PilotOperationalConfig(
            pilot_id=clean_pilot_id,
            revision=1,
            consortium_id=clean_consortium_id,
            participating_institutions=clean_insts,
            allowed_purposes=req.allowed_purposes,
            allowed_jurisdictions=req.allowed_jurisdictions,
            approved_datasets=req.approved_datasets,
            authorized_operators=req.authorized_operators or ([principal.principal_id] if principal else ["operator_admin"]),
            operational_grant_id=grant_id,
            grant_revision=grant_revision,
            grant_scope_digest=grant_scope_digest,
            topology=merged_topo,
            worker_endpoints=worker_endpoints_dict,
            coordinator_endpoint=str(c_url or merged_topo.get("coordinator_url") or merged_topo.get("coord_url") or ""),
            starts_at=starts_at,
            expires_at=expires_at,
            query_quota_total=req.query_quota_total,
            query_quota_per_institution=req.query_quota_per_institution,
        )

        entity = PilotEntity(
            config=config,
            status=PilotStatus.DRAFT,
            status_reason="Provisioned in DRAFT state. Preflight checks required before approval.",
        )

        actor_id = principal.principal_id if principal else "system"
        evt = self._audit_chain.append_event(
            event_type="PILOT_PROVISIONED",
            actor_id=actor_id,
            action=f"provision_pilot:{clean_pilot_id}",
            resource_id=f"pilot:{clean_pilot_id}",
            details={
                "pilot_id": clean_pilot_id,
                "consortium_id": clean_consortium_id,
                "participating_institutions": clean_insts,
                "scope_digest": entity.scope_digest,
                "query_quota_total": config.query_quota_total,
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info("Provisioned pilot %s in consortium %s", clean_pilot_id, clean_consortium_id)
        return entity

    def run_preflight(
        self,
        pilot_id: str,
        principal: AuthenticatedPrincipal | None = None,
        preflight_req: PilotPreflightRequest | None = None,
        operator_actor: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Execute preflight readiness verification for a provisioned pilot."""
        entity = self._get_required_pilot(pilot_id)
        if entity.status not in (PilotStatus.DRAFT, PilotStatus.PREFLIGHT, PilotStatus.AWAITING_APPROVAL):
            raise InvalidPilotTransitionError(
                f"Cannot run preflight for pilot '{pilot_id}' in status '{entity.status.value}'."
            )

        if entity.status == PilotStatus.DRAFT:
            entity.transition_to(
                PilotStatus.PREFLIGHT,
                "Executing preflight readiness probes.",
            )

        checks: list[PreflightCheckResult] = []

        # 1. Consortium existence & status
        consortium = self.consortium_service.get_consortium(entity.config.consortium_id)
        c_active = consortium is not None and consortium.status == ConsortiumStatus.ACTIVE
        checks.append(PreflightCheckResult(
            check_name="CONSORTIUM_ACTIVE",
            passed=c_active,
            details=f"Consortium '{entity.config.consortium_id}' status: {consortium.status.value if consortium else 'NOT_FOUND'}",
        ))

        # 2. Member eligibility
        insts = entity.config.participating_institutions
        members_ok = True
        members_detail = []
        for inst in insts:
            if not consortium or inst not in consortium.members or inst in consortium.removed_members:
                members_ok = False
                members_detail.append(f"{inst}: NOT_ACTIVE")
            else:
                members_detail.append(f"{inst}: ACTIVE")
        checks.append(PreflightCheckResult(
            check_name="INSTITUTION_MEMBERSHIP",
            passed=members_ok,
            details="; ".join(members_detail),
        ))

        # 3. Operational Data-Sharing Grant
        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        grant_ok = bool(
            op_grant
            and op_grant.get("is_active", True)
            and int(op_grant.get("revision", 0)) >= 1
            and not set(op_grant.get("revoked_by_institutions", [])) & set(insts)
        )
        checks.append(PreflightCheckResult(
            check_name="OPERATIONAL_GRANT_VALID",
            passed=grant_ok,
            details=f"Grant ID: {op_grant.get('grant_id', 'NONE')}, active: {op_grant.get('is_active')}, rev: {op_grant.get('revision')}",
        ))

        # 4. Institutional Verification Keys
        keys_ok = True
        keys_detail = []
        for inst in insts:
            has_k = self.institutional_registry.has_key(inst)
            keys_detail.append(f"{inst}: {'ENROLLED' if has_k else 'MISSING'}")
            if not has_k:
                keys_ok = False
        checks.append(PreflightCheckResult(
            check_name="INSTITUTIONAL_KEYS_ENROLLED",
            passed=keys_ok,
            details="; ".join(keys_detail),
        ))

        # 5. Purpose & Jurisdiction Allowlist
        purposes_ok = len(entity.config.allowed_purposes) > 0 and len(entity.config.allowed_jurisdictions) > 0
        checks.append(PreflightCheckResult(
            check_name="PURPOSE_AND_JURISDICTION_CONFIGURED",
            passed=purposes_ok,
            details=f"Purposes: {entity.config.allowed_purposes}, Jurisdictions: {entity.config.allowed_jurisdictions}",
        ))

        # 6. Quotas
        quota_ok = entity.config.query_quota_total > 0 and entity.config.query_quota_per_institution > 0
        checks.append(PreflightCheckResult(
            check_name="QUERY_QUOTA_BOUNDED",
            passed=quota_ok,
            details=f"Total: {entity.config.query_quota_total}, Per-Inst: {entity.config.query_quota_per_institution}",
        ))

        # 7. Mandatory Infrastructure Readiness & Topology Reachability Probes
        bank_a = entity.config.participating_institutions[0] if len(entity.config.participating_institutions) > 0 else "bank_a"
        bank_b = entity.config.participating_institutions[1] if len(entity.config.participating_institutions) > 1 else "bank_b"

        bank_a_url, bank_b_url, coord_url, missing_nodes = self._resolve_topology_endpoints(entity)

        checks.append(PreflightCheckResult(
            check_name="MANDATORY_TOPOLOGY_CONFIGURED",
            passed=len(missing_nodes) == 0,
            details=f"Missing mandatory topology endpoints: {', '.join(missing_nodes)}" if missing_nodes else "All mandatory topology nodes configured.",
        ))

        if bank_a_url:
            a_ok, a_det = self._probe_node_health(bank_a_url, "worker", expected_bank_id=bank_a)
            checks.append(PreflightCheckResult(
                check_name="BANK_A_WORKER_REACHABLE",
                passed=a_ok,
                details=f"Worker A ({bank_a_url}): {a_det}",
            ))
        else:
            checks.append(PreflightCheckResult(
                check_name="BANK_A_WORKER_REACHABLE",
                passed=False,
                details=f"Bank A worker endpoint not configured for '{bank_a}'.",
            ))

        if bank_b_url:
            b_ok, b_det = self._probe_node_health(bank_b_url, "worker", expected_bank_id=bank_b)
            checks.append(PreflightCheckResult(
                check_name="BANK_B_WORKER_REACHABLE",
                passed=b_ok,
                details=f"Worker B ({bank_b_url}): {b_det}",
            ))
        else:
            checks.append(PreflightCheckResult(
                check_name="BANK_B_WORKER_REACHABLE",
                passed=False,
                details=f"Bank B worker endpoint not configured for '{bank_b}'.",
            ))

        if coord_url:
            c_ok, c_det = self._probe_node_health(coord_url, "coordinator")
            checks.append(PreflightCheckResult(
                check_name="COORDINATOR_REACHABLE",
                passed=c_ok,
                details=f"Coordinator ({coord_url}): {c_det}",
            ))
        else:
            checks.append(PreflightCheckResult(
                check_name="COORDINATOR_REACHABLE",
                passed=False,
                details="Coordinator endpoint not configured.",
            ))

        entity.preflight_checks = checks
        all_passed = all(c.passed for c in checks)

        if all_passed:
            entity.transition_to(
                PilotStatus.AWAITING_APPROVAL,
                "Preflight readiness verified. Awaiting bilateral institutional and operator approvals.",
            )
        else:
            failed_names = [c.check_name for c in checks if not c.passed]
            entity.transition_to(
                PilotStatus.FAILED,
                f"Preflight verification failed for: {', '.join(failed_names)}.",
            )

        actor_id = principal.principal_id if principal else "system"
        evt = self._audit_chain.append_event(
            event_type="PILOT_PREFLIGHT_EXECUTED",
            actor_id=actor_id,
            action=f"preflight_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={
                "pilot_id": pilot_id,
                "all_passed": all_passed,
                "checks": [{"name": c.check_name, "passed": c.passed} for c in checks],
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)

        if not all_passed:
            failed_names = [c.check_name for c in checks if not c.passed]
            raise PilotGovernanceError(
                f"Preflight verification failed for pilot '{pilot_id}': {', '.join(failed_names)}."
            )

        return entity

    def approve_pilot(
        self,
        pilot_id: str,
        approving_institution: str,
        principal: AuthenticatedPrincipal | str | None = None,
        notes: str = "",
        signature: str = "",
        approver_actor: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Record independent institutional or operator approval for a pilot."""
        clean_inst = approving_institution.lower().strip()
        is_operator = clean_inst in ("operator", "pilot_operator", "admin", "consortium")

        if not isinstance(principal, AuthenticatedPrincipal) or not principal.is_authenticated:
            raise PermissionError(
                "Authentication required: Independent institutional approval requires an authenticated principal."
            )

        caller_tenant = (principal.tenant_id or "").lower().strip()
        if not is_operator:
            # Institution approval: strictly require caller_tenant == clean_inst
            # An operator or admin cannot manufacture a bank's institutional approval!
            if caller_tenant != clean_inst:
                raise PermissionError(
                    f"Cross-bank approval rejected: Principal from institution '{caller_tenant}' "
                    f"cannot approve pilot on behalf of '{approving_institution}'. "
                    "Consortium operators and platform administrators cannot manufacture institutional approvals."
                )
            inst_roles = {"admin", "super_admin", "compliance", "compliance_officer", "security_officer", "institutional_approver"}
            roles = getattr(principal, "roles", []) or []
            if not principal.is_privileged and not any(r in inst_roles for r in roles):
                raise PermissionError(
                    f"Principal '{principal.principal_id}' lacks required institutional approval role for '{approving_institution}'."
                )
        else:
            gov_roles = {"admin", "super_admin", "ml_lead", "operator", "pilot_operator", "security_officer", "compliance", "system_admin"}
            roles = getattr(principal, "roles", []) or []
            if not principal.is_privileged and not any(r in gov_roles for r in roles):
                raise PermissionError(
                    f"Principal '{principal.principal_id}' lacks operator authority to grant OPERATOR pilot approval."
                )

        entity = self._get_required_pilot(pilot_id)
        if entity.status not in (PilotStatus.AWAITING_APPROVAL, PilotStatus.PREFLIGHT, PilotStatus.DRAFT):
            raise InvalidPilotTransitionError(
                f"Cannot record approval for pilot '{pilot_id}' in status '{entity.status.value}'. Must be in AWAITING_APPROVAL."
            )

        target_inst_key = "OPERATOR" if is_operator else clean_inst
        if not is_operator and clean_inst not in [b.lower().strip() for b in entity.config.participating_institutions]:
            raise PermissionError(
                f"Institution '{clean_inst}' is not a participating bank of pilot '{pilot_id}'."
            )

        challenge_bytes = canonical_pilot_approval_bytes(
            pilot_id=entity.config.pilot_id,
            revision=entity.config.revision,
            scope_digest=entity.config.compute_scope_digest(),
            approver_institution=target_inst_key,
            approver_id=principal.principal_id,
        )

        has_key = self.institutional_registry.has_key(clean_inst)
        if signature:
            verified = self.institutional_registry.verify_signature(clean_inst, signature, challenge_bytes)
            if not verified:
                raise PilotAuthorizationError(
                    f"Cryptographic signature verification failed for institution '{clean_inst}'. "
                    "Approval signature is invalid or does not match enrolled public key."
                )
        elif has_key and not is_operator:
            raise PilotAuthorizationError(
                f"Missing cryptographic signature: Institution '{clean_inst}' has an enrolled Ed25519 verification key. "
                "Approval must be cryptographically signed."
            )

        approver_id = principal.principal_id
        approver_role = "operator" if is_operator else (principal.roles[0] if principal.roles else "institutional_approver")

        approval_rec = PilotApprovalRecord(
            approver_id=approver_id,
            approver_institution=target_inst_key,
            approver_role=approver_role,
            scope_digest=entity.config.compute_scope_digest(),
            signature=signature,
            notes=notes,
        )

        entity.record_approval(approval_rec)

        evt = self._audit_chain.append_event(
            event_type="PILOT_APPROVAL_RECORDED",
            actor_id=approver_id,
            action=f"approve_pilot:{pilot_id}:{target_inst_key}",
            resource_id=f"pilot:{pilot_id}",
            details={
                "pilot_id": pilot_id,
                "approver_institution": target_inst_key,
                "approver_id": approver_id,
                "scope_digest": approval_rec.scope_digest,
                "is_fully_approved": entity.is_fully_approved(),
                "new_status": entity.status.value,
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info(
            "Recorded approval for pilot %s from %s. Fully approved: %s, Status: %s",
            pilot_id,
            target_inst_key,
            entity.is_fully_approved(),
            entity.status.value,
        )
        return entity

    def activate_pilot(
        self,
        pilot_id: str,
        principal: AuthenticatedPrincipal | str | None = None,
        operator_actor: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Activate an AUTHORIZED pilot for operational matching queries."""
        entity = self._get_required_pilot(pilot_id)

        auth_principal: AuthenticatedPrincipal | None = None
        if isinstance(principal, AuthenticatedPrincipal):
            auth_principal = principal
        elif isinstance(principal, str):
            operator_actor = principal

        if auth_principal:
            gov_roles = {"admin", "super_admin", "ml_lead", "operator", "pilot_operator", "security_officer", "compliance", "system_admin"}
            roles = getattr(auth_principal, "roles", []) or []
            if not auth_principal.is_privileged and not any(r in gov_roles for r in roles):
                raise PermissionError(
                    f"Principal '{auth_principal.principal_id}' lacks operator authority to activate pilot."
                )

        if entity.status == PilotStatus.ACTIVE:
            logger.info("Pilot '%s' is already ACTIVE. Idempotent activation succeeded.", pilot_id)
            return entity

        # Check approvals first before status
        if not entity.is_fully_approved():
            is_ok, missing = entity.has_all_required_approvals()
            raise PilotAuthorizationError(
                f"Pilot '{pilot_id}' cannot be activated: missing approval from {missing}. "
                "All participating institutions and operator must approve before activation."
            )

        # Revalidate that mandatory topology is configured
        _, _, _, missing_topo = self._resolve_topology_endpoints(entity)
        if missing_topo:
            raise PilotGovernanceError(
                f"Pilot '{pilot_id}' cannot be activated: Mandatory topology missing: {', '.join(missing_topo)}. "
                "All participating worker and coordinator endpoints must be configured."
            )

        # Revalidate that preflight was executed and MANDATORY_TOPOLOGY_CONFIGURED passed
        topo_check = next((c for c in entity.preflight_checks if c.check_name == "MANDATORY_TOPOLOGY_CONFIGURED"), None)
        if not topo_check or not topo_check.passed:
            raise PilotGovernanceError(
                f"Pilot '{pilot_id}' cannot be activated: Mandatory topology has not been verified. "
                "Preflight check MANDATORY_TOPOLOGY_CONFIGURED must pass before activation."
            )

        if entity.status != PilotStatus.AUTHORIZED:
            raise InvalidPilotTransitionError(
                f"Cannot activate pilot '{pilot_id}' in status '{entity.status.value}'. "
                "Pilot must be fully approved and in AUTHORIZED status."
            )

        entity.transition_to(
            PilotStatus.ACTIVE,
            "Pilot successfully activated by authorized operator. Operational matching permitted.",
        )

        actor_id = auth_principal.principal_id if auth_principal else (operator_actor or "operator")
        evt = self._audit_chain.append_event(
            event_type="PILOT_ACTIVATED",
            actor_id=actor_id,
            action=f"activate_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={
                "pilot_id": pilot_id,
                "activated_at": entity.activated_at,
                "scope_digest": entity.scope_digest,
                "participating_institutions": entity.config.participating_institutions,
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info("Activated pilot %s for operational matching", pilot_id)
        return entity

    def pause_pilot(
        self,
        pilot_id: str,
        reason: str | None = None,
        principal: AuthenticatedPrincipal | str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Effective kill switch: pause an active pilot immediately."""
        entity = self._get_required_pilot(pilot_id)
        if entity.status != PilotStatus.ACTIVE:
            raise InvalidPilotTransitionError(
                f"Cannot pause pilot '{pilot_id}' in non-active status '{entity.status.value}'."
            )

        if isinstance(reason, str) and isinstance(principal, str):
            if any(k in reason for k in ("operator", "admin", "user", "bank_")):
                actual_actor = reason
                actual_reason = principal
            else:
                actual_reason = reason
                actual_actor = principal
        else:
            actual_reason = reason or "Paused by operator"
            actual_actor = (
                principal.principal_id
                if isinstance(principal, AuthenticatedPrincipal)
                else (principal if principal else "operator")
            )

        entity.transition_to(PilotStatus.PAUSED, f"Paused by operator: {actual_reason}")

        actor_id = actual_actor
        evt = self._audit_chain.append_event(
            event_type="PILOT_PAUSED",
            actor_id=actor_id,
            action=f"pause_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={"pilot_id": pilot_id, "reason": actual_reason},
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.warning("PAUSED pilot %s: %s", pilot_id, actual_reason)
        return entity

    def resume_pilot(
        self,
        pilot_id: str,
        principal: AuthenticatedPrincipal | str | None = None,
        reason: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Resume a paused pilot after revalidating infrastructure health and authority."""
        entity = self._get_required_pilot(pilot_id)
        if entity.status != PilotStatus.PAUSED:
            raise InvalidPilotTransitionError(
                f"Cannot resume pilot '{pilot_id}' in status '{entity.status.value}'. Must be PAUSED."
            )

        # Re-check authority: Consortium and grant still valid
        consortium = self.consortium_service.get_consortium(entity.config.consortium_id)
        if not consortium or consortium.status != ConsortiumStatus.ACTIVE:
            raise PilotAuthorizationError("Underlying consortium is no longer active.")

        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        if not op_grant or not op_grant.get("is_active", True) or op_grant.get("status") == "REVOKED":
            raise PilotAuthorizationError("Underlying operational grant is no longer active.")

        # Re-check exact grant revision and scope digest binding
        current_rev = int(op_grant.get("revision", 0))
        if current_rev != entity.config.grant_revision:
            raise PilotAuthorizationError(
                f"Cannot resume pilot '{pilot_id}': Operational grant revision mismatch "
                f"(approved revision {entity.config.grant_revision}, current active revision {current_rev}). "
                "Fresh pilot approvals are required after grant modification."
            )
        current_digest = str(op_grant.get("scope_digest", ""))
        if current_digest and entity.config.grant_scope_digest and current_digest != entity.config.grant_scope_digest:
            raise PilotAuthorizationError(
                f"Cannot resume pilot '{pilot_id}': Operational grant scope digest has mutated. "
                "Fresh bilateral institutional approvals are required before resumption."
            )
        grant_banks = {b.lower().strip() for b in op_grant.get("participating_banks", [])}
        if grant_banks and not set([b.lower().strip() for b in entity.config.participating_institutions]).issubset(grant_banks):
            raise PilotAuthorizationError(
                f"Cannot resume pilot '{pilot_id}': Participating institutions removed from operational grant."
            )

        revoked = set(op_grant.get("revoked_by_institutions", []))
        if revoked & set(entity.config.participating_institutions):
            raise PilotAuthorizationError(
                f"One or more participating institutions revoked consent: {sorted(revoked)}. Resumption denied."
            )

        # Re-check not expired
        now = datetime.datetime.now(UTC)
        if entity.config.expires_at:
            exp_dt = datetime.datetime.fromisoformat(entity.config.expires_at)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=UTC)
            if now >= exp_dt:
                raise PilotExpiredError(f"Pilot '{pilot_id}' has expired. Cannot resume.")

        # Re-check quota remaining
        if entity.queries_executed >= entity.config.query_quota_total:
            raise PilotQuotaExceededError("Pilot quota already exhausted. Cannot resume.")

        # Live infrastructure readiness verification before resumption
        bank_a = entity.config.participating_institutions[0] if len(entity.config.participating_institutions) > 0 else "bank_a"
        bank_b = entity.config.participating_institutions[1] if len(entity.config.participating_institutions) > 1 else "bank_b"

        bank_a_url, bank_b_url, coord_url, missing = self._resolve_topology_endpoints(entity)
        if missing:
            raise PilotGovernanceError(
                f"Cannot resume pilot '{pilot_id}': Preflight verification failed. Missing mandatory topology endpoints: {', '.join(missing)}."
            )

        ok, det = self._probe_node_health(bank_a_url, "worker", expected_bank_id=bank_a)
        if not ok:
            raise PilotGovernanceError(f"Cannot resume pilot '{pilot_id}': Preflight probe failed for Bank A worker at '{bank_a_url}': {det}")

        ok, det = self._probe_node_health(bank_b_url, "worker", expected_bank_id=bank_b)
        if not ok:
            raise PilotGovernanceError(f"Cannot resume pilot '{pilot_id}': Preflight probe failed for Bank B worker at '{bank_b_url}': {det}")

        ok, det = self._probe_node_health(coord_url, "coordinator")
        if not ok:
            raise PilotGovernanceError(f"Cannot resume pilot '{pilot_id}': Preflight probe failed for Coordinator at '{coord_url}': {det}")

        entity.transition_to(PilotStatus.ACTIVE, "Resumed after health and authorization revalidation.")

        actor_id = principal.principal_id if isinstance(principal, AuthenticatedPrincipal) else (principal if principal else "operator")
        evt = self._audit_chain.append_event(
            event_type="PILOT_RESUMED",
            actor_id=actor_id,
            action=f"resume_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={"pilot_id": pilot_id},
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info("Resumed pilot %s", pilot_id)
        return entity

    def abort_pilot(
        self,
        pilot_id: str,
        reason: str,
        principal: AuthenticatedPrincipal | str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Emergency kill switch: terminate pilot permanently in ABORTED state."""
        entity = self._get_required_pilot(pilot_id)
        entity.transition_to(PilotStatus.ABORTED, f"Aborted by operator: {reason}")

        actor_id = principal.principal_id if isinstance(principal, AuthenticatedPrincipal) else (principal if principal else "operator")
        evt = self._audit_chain.append_event(
            event_type="PILOT_ABORTED",
            actor_id=actor_id,
            action=f"abort_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={"pilot_id": pilot_id, "reason": reason},
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.warning("ABORTED pilot %s: %s", pilot_id, reason)
        return entity

    def complete_pilot(
        self,
        pilot_id: str,
        principal: AuthenticatedPrincipal | str | None = None,
        reason: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Mark a pilot as successfully COMPLETED."""
        entity = self._get_required_pilot(pilot_id)
        completion_msg = reason or f"Pilot successfully completed with {entity.queries_executed} queries executed."
        entity.transition_to(
            PilotStatus.COMPLETED,
            completion_msg,
        )

        actor_id = principal.principal_id if isinstance(principal, AuthenticatedPrincipal) else (principal if principal else "operator")
        evt = self._audit_chain.append_event(
            event_type="PILOT_COMPLETED",
            actor_id=actor_id,
            action=f"complete_pilot:{pilot_id}",
            resource_id=f"pilot:{pilot_id}",
            details={
                "pilot_id": pilot_id,
                "queries_executed": entity.queries_executed,
                "queries_denied": entity.queries_denied,
            },
        )
        entity.audit_event_ids.append(evt.event_id)

        self.store.save_pilot(entity)
        logger.info("Completed pilot %s", pilot_id)
        return entity

    def validate_pilot_query(
        self,
        pilot_id: str | None = None,
        requesting_bank_id: str | None = None,
        target_bank_id: str | None = None,
        purpose: str = "",
        requesting_bank: str | None = None,
        target_bank: str | None = None,
        consortium_id: str | None = None,
        **kwargs: Any,
    ) -> PilotEntity:
        """Authoritatively validates execution gating for a matching query under this pilot."""
        clean_req = (requesting_bank_id or requesting_bank or "").lower().strip()
        clean_target = (target_bank_id or target_bank or "").lower().strip()
        if not pilot_id:
            scoped = self.find_active_or_scoped_pilot(
                requesting_bank=clean_req,
                target_bank=clean_target,
                purpose=purpose,
                consortium_id=consortium_id,
            )
            if scoped:
                pilot_id = scoped.config.pilot_id
            else:
                raise ValueError("pilot_id is required or must match an existing governed pilot scope.")
        entity = self._get_required_pilot(pilot_id)
        entity.validate_executable(
            requesting_bank_id=clean_req,
            target_bank_id=clean_target,
            purpose=purpose,
        )

        # Verify underlying consortium operational grant is still active and unchanged
        consortium = self.consortium_service.get_consortium(entity.config.consortium_id)
        if not consortium or consortium.status != ConsortiumStatus.ACTIVE:
            raise PilotAuthorizationError(
                f"Underlying consortium '{entity.config.consortium_id}' is not ACTIVE. Query rejected."
            )
        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        if not op_grant or not op_grant.get("is_active", True) or op_grant.get("status") == "REVOKED":
            raise PilotAuthorizationError("Underlying operational data sharing grant is inactive or revoked.")

        # Exact Approved Grant Revision & Scope Binding
        grant_id = op_grant.get("grant_id")
        if grant_id and entity.config.operational_grant_id and grant_id != entity.config.operational_grant_id:
            raise PilotAuthorizationError(
                f"Operational grant ID mismatch: pilot was approved under grant '{entity.config.operational_grant_id}', "
                f"current active grant is '{grant_id}'."
            )
        current_rev = int(op_grant.get("revision", 0))
        if current_rev != entity.config.grant_revision:
            raise PilotAuthorizationError(
                f"Operational grant revision mismatch: pilot was approved under revision {entity.config.grant_revision}, "
                f"current active grant revision is {current_rev}. Fresh pilot approval required."
            )
        current_digest = str(op_grant.get("scope_digest", ""))
        if current_digest and entity.config.grant_scope_digest and current_digest != entity.config.grant_scope_digest:
            raise PilotAuthorizationError(
                "Operational grant scope digest mismatch: grant terms, scope, or disclosure policy have mutated. "
                "Prior pilot approval is invalid without fresh bilateral authorization."
            )

        grant_banks = {b.lower().strip() for b in op_grant.get("participating_banks", [])}
        if grant_banks:
            if clean_req not in grant_banks:
                raise PilotAuthorizationError(f"Requesting bank '{clean_req}' is no longer in operational grant participating institutions.")
            if clean_target not in grant_banks:
                raise PilotAuthorizationError(f"Target bank '{clean_target}' is no longer in operational grant participating institutions.")

        grant_purposes = set(op_grant.get("allowed_purposes", []))
        if grant_purposes and not set(entity.config.allowed_purposes).issubset(grant_purposes):
            raise PilotAuthorizationError(
                f"Pilot purposes {entity.config.allowed_purposes} are no longer permitted under active grant."
            )

        grant_jurisdictions = set(op_grant.get("allowed_jurisdictions", []))
        if grant_jurisdictions and not set(entity.config.allowed_jurisdictions).issubset(grant_jurisdictions):
            raise PilotAuthorizationError(
                f"Pilot jurisdictions {entity.config.allowed_jurisdictions} are no longer permitted under active grant."
            )

        revoked = set(op_grant.get("revoked_by_institutions", []))
        if clean_req in revoked:
            raise PilotAuthorizationError(f"Requesting bank '{clean_req}' revoked operational consent.")
        if clean_target in revoked:
            raise PilotAuthorizationError(f"Counterpart bank '{clean_target}' revoked operational consent.")

        return entity

    def find_active_or_scoped_pilot(
        self,
        requesting_bank: str,
        target_bank: str,
        purpose: str,
        pilot_id: str | None = None,
        consortium_id: str | None = None,
    ) -> PilotEntity | None:
        """Find governing pilot for the institutional pair and purpose.

        If pilot_id is supplied, retrieves that exact pilot and verifies scope binding.
        Otherwise, discovers any governing pilot (active or non-active) for this scope.
        Non-active pilots (PAUSED, ABORTED, COMPLETED, EXPIRED, FAILED) remain authoritatively
        bound so that client omission of pilot_id cannot bypass pilot containment.
        If no pilot was provisioned for this scope, returns None to allow separately authorized M4 usage.
        """
        clean_req = requesting_bank.lower().strip()
        clean_target = target_bank.lower().strip()
        clean_cid = consortium_id.lower().strip() if consortium_id else None

        if pilot_id:
            entity = self.store.get_pilot(pilot_id.strip())
            if not entity:
                raise PilotGovernanceError(f"Governing pilot '{pilot_id}' not found.")
            p_insts = {b.lower().strip() for b in entity.config.participating_institutions}
            if clean_req not in p_insts or clean_target not in p_insts:
                raise PilotAuthorizationError(
                    f"Supplied pilot '{pilot_id}' participating institutions {entity.config.participating_institutions} "
                    f"do not match query institutions ('{clean_req}', '{clean_target}')."
                )
            if clean_cid and entity.config.consortium_id.lower().strip() != clean_cid:
                raise PilotAuthorizationError(
                    f"Supplied pilot '{pilot_id}' consortium '{entity.config.consortium_id}' "
                    f"does not match query consortium '{clean_cid}'."
                )
            return entity

        all_pilots = self.store.list_pilots()
        matching_pilots: list[PilotEntity] = []
        for p in all_pilots:
            if clean_cid and p.config.consortium_id.lower().strip() != clean_cid:
                continue
            p_insts = {b.lower().strip() for b in p.config.participating_institutions}
            if clean_req in p_insts and clean_target in p_insts:
                matching_pilots.append(p)

        if not matching_pilots:
            return None

        # Prefer an active pilot matching purpose
        for p in matching_pilots:
            if p.status == PilotStatus.ACTIVE and (not purpose or purpose in p.config.allowed_purposes):
                return p

        # If any active pilot exists for this pair
        for p in matching_pilots:
            if p.status == PilotStatus.ACTIVE:
                return p

        # If no active pilot, return the governing non-active pilot (most recently updated)
        # so downstream validation authoritatively enforces kill switch / failure containment
        # and rejects the query rather than silently escaping to uncontained M4 execution!
        return matching_pilots[0]

    def revalidate_pilot_active(
        self,
        pilot_id: str,
        requesting_bank: str,
        target_bank: str,
        purpose: str,
    ) -> PilotEntity:
        """Revalidate effective pilot authorization immediately before authoritative signal admission."""
        clean_req = requesting_bank.lower().strip()
        clean_target = target_bank.lower().strip()
        entity = self._get_required_pilot(pilot_id)
        if entity.status != PilotStatus.ACTIVE:
            raise PilotGovernanceError(
                f"Kill switch activated: Pilot '{pilot_id}' is in non-active status '{entity.status.value}'. "
                f"Reason: {entity.status_reason}. Authoritative signal admission rejected."
            )
        if entity.has_expired():
            raise PilotExpiredError(
                f"Kill switch activated: Pilot '{pilot_id}' expired. Authoritative signal admission rejected."
            )
        entity.validate_executable(clean_req, clean_target, purpose)

        consortium = self.consortium_service.get_consortium(entity.config.consortium_id)
        if not consortium or consortium.status != ConsortiumStatus.ACTIVE:
            raise PilotAuthorizationError(
                f"Underlying consortium '{entity.config.consortium_id}' is not ACTIVE. Admission rejected."
            )
        c_meta = getattr(consortium, "metadata", {}) or {}
        op_grant = c_meta.get("operational_data_sharing_grant") or {}
        if not op_grant or not op_grant.get("is_active", True) or op_grant.get("status") == "REVOKED":
            raise PilotAuthorizationError("Underlying operational data sharing grant is inactive or revoked.")

        # Re-check exact grant revision & scope digest binding
        current_rev = int(op_grant.get("revision", 0))
        if current_rev != entity.config.grant_revision:
            raise PilotAuthorizationError(
                f"Operational grant revision mismatch: pilot was approved under revision {entity.config.grant_revision}, "
                f"current active grant revision is {current_rev}. Admission rejected."
            )
        current_digest = str(op_grant.get("scope_digest", ""))
        if current_digest and entity.config.grant_scope_digest and current_digest != entity.config.grant_scope_digest:
            raise PilotAuthorizationError(
                "Operational grant scope digest mismatch: grant terms have mutated. Admission rejected."
            )

        grant_banks = {b.lower().strip() for b in op_grant.get("participating_banks", [])}
        if grant_banks:
            if clean_req not in grant_banks:
                raise PilotAuthorizationError(f"Requesting bank '{clean_req}' is no longer in operational grant.")
            if clean_target not in grant_banks:
                raise PilotAuthorizationError(f"Target bank '{clean_target}' is no longer in operational grant.")

        revoked = set(op_grant.get("revoked_by_institutions", []))
        if clean_req in revoked:
            raise PilotAuthorizationError(f"Requesting bank '{clean_req}' revoked operational consent.")
        if clean_target in revoked:
            raise PilotAuthorizationError(f"Counterpart bank '{clean_target}' revoked operational consent.")

        return entity

    def reserve_query_slot(
        self,
        pilot_id: str,
        requesting_bank: str,
        target_bank: str,
        purpose: str,
        reservation_id: str | None = None,
    ) -> tuple[PilotEntity, str]:
        """Atomically reserve execution slot in durable store."""
        import uuid
        res_id = reservation_id or f"res_{uuid.uuid4().hex[:12]}"
        return self.store.reserve_query_slot(
            pilot_id=pilot_id,
            requesting_bank_id=requesting_bank,
            target_bank_id=target_bank,
            purpose=purpose,
            reservation_id=res_id,
        )

    def commit_query_reservation(
        self,
        reservation_id: str,
        pilot_id: str,
        receipt_id: str,
        latency_ms: float,
        match_confirmed: bool | None,
        signal_id: str | None = None,
        case_id: str | None = None,
    ) -> tuple[PilotEntity, PilotExecutionReceipt]:
        """Commit an in-flight reservation upon successful PSI execution and result admission."""
        return self.store.commit_query_reservation(
            reservation_id=reservation_id,
            pilot_id=pilot_id,
            receipt_id=receipt_id,
            latency_ms=latency_ms,
            match_confirmed=match_confirmed,
            signal_id=signal_id,
            case_id=case_id,
        )

    def release_query_reservation(
        self,
        reservation_id: str,
        pilot_id: str,
        reason: str = "FAILED",
        is_denied: bool = True,
    ) -> None:
        """Release reservation upon error or mid-flight abort."""
        self.store.release_query_reservation(
            reservation_id=reservation_id,
            pilot_id=pilot_id,
            reason=reason,
            is_denied=is_denied,
        )

    def record_query_outcome(
        self,
        pilot_id: str,
        requesting_bank_id: str = "",
        target_bank_id: str = "",
        purpose: str = "",
        status: str = "COMPLETED",
        latency_ms: float = 0.0,
        match_confirmed: bool | None = None,
        receipt_id: str | None = None,
        reason: str | None = None,
        success: bool | None = None,
        is_match: bool | None = None,
        requesting_bank: str | None = None,
        signal_id: str | None = None,
        case_id: str | None = None,
        **kwargs: Any,
    ) -> PilotExecutionReceipt:
        """Record query latency, counts, and receipt in persistent pilot store."""
        req_bank = requesting_bank_id or requesting_bank or "bank_a"
        target_bank = target_bank_id or kwargs.get("target_bank") or "bank_b"
        query_purpose = purpose or kwargs.get("query_purpose") or "AML_INVESTIGATION"
        outcome_status = status
        if success is not None:
            outcome_status = "COMPLETED" if success else "FAILED"
        if is_match is not None and match_confirmed is None:
            match_confirmed = is_match

        with self._lock:
            entity = self._get_required_pilot(pilot_id)
            receipt = entity.record_query_outcome(
                requesting_bank_id=req_bank,
                target_bank_id=target_bank,
                purpose=query_purpose,
                status=outcome_status,
                latency_ms=latency_ms,
                match_confirmed=match_confirmed,
                receipt_id=receipt_id,
                reason=reason,
                signal_id=signal_id,
                case_id=case_id,
            )
            self.store.save_pilot(entity)
            return receipt

    def get_pilot(self, pilot_id: str) -> PilotEntity | None:
        """Fetch pilot by ID."""
        return self.store.get_pilot(pilot_id)

    def list_pilots(self) -> list[PilotEntity]:
        """List all pilots in store."""
        return self.store.list_pilots()

    def get_active_pilots(self) -> list[PilotEntity]:
        """List currently active pilots."""
        return self.store.get_active_pilots()

    def export_audit_dossier(self, pilot_id: str) -> dict[str, Any]:
        """Export durable audit dossier with enriched immutable audit events."""
        entity = self._get_required_pilot(pilot_id)
        dossier = entity.export_audit_dossier()
        audit_events = []
        for eid in entity.audit_event_ids:
            evt = self._audit_chain.get_event(eid) if hasattr(self._audit_chain, "get_event") else None
            if evt:
                audit_events.append({
                    "event_id": getattr(evt, "event_id", eid),
                    "event_type": getattr(evt, "event_type", ""),
                    "actor_id": getattr(evt, "actor_id", getattr(evt, "actor", "")),
                    "action": getattr(evt, "action", ""),
                    "resource_id": getattr(evt, "resource_id", getattr(evt, "target_id", "")),
                    "timestamp": getattr(evt, "timestamp", ""),
                    "digest": getattr(evt, "digest", getattr(evt, "curr_hash", "")),
                    "event_digest": getattr(evt, "digest", getattr(evt, "curr_hash", "")),
                })
        dossier["audit_trail"] = audit_events
        dossier["audit_events"] = audit_events
        dossier["execution_receipts"] = entity.execution_receipts
        return dossier

    def get_telemetry_summary(self, pilot_id: str) -> PilotTelemetrySummary:
        """Compile live operational telemetry summary for pilot visibility."""
        entity = self._get_required_pilot(pilot_id)
        avg_lat = (
            sum(entity.latencies_ms) / len(entity.latencies_ms)
            if entity.latencies_ms
            else 0.0
        )
        sorted_lats = sorted(entity.latencies_ms)
        p95_lat = (
            sorted_lats[int(len(sorted_lats) * 0.95)]
            if sorted_lats
            else 0.0
        )

        return PilotTelemetrySummary(
            pilot_id=entity.config.pilot_id,
            status=entity.status.value,
            queries_executed=entity.queries_executed,
            queries_denied=entity.queries_denied,
            queries_total=entity.queries_executed + entity.queries_denied,
            queries_successful=entity.queries_executed,
            queries_failed=entity.queries_denied,
            matches_found=entity.matches_count,
            queries_by_institution=entity.institution_query_counts,
            query_quota_total=entity.config.query_quota_total,
            quota_remaining=max(0, entity.config.query_quota_total - entity.queries_executed),
            mean_latency_ms=round(avg_lat, 2),
            p95_latency_ms=round(p95_lat, 2),
            institution_query_counts=entity.institution_query_counts,
            last_operation_at=entity.updated_at if entity.queries_executed > 0 else None,
            bilateral_authorization_valid=entity.is_fully_approved() and entity.status == PilotStatus.ACTIVE,
        )

    def _get_required_pilot(self, pilot_id: str) -> PilotEntity:
        entity = self.store.get_pilot(pilot_id)
        if not entity:
            raise ValueError(f"Pilot '{pilot_id}' not found.")
        return entity
