"""Adaptive Dynamic Differential Privacy Budget Auto-Scaler.

Implements a Rényi Differential Privacy (RDP) and Privacy Loss Random Variable (PRV)
accountant with dynamic noise multiplier auto-scaling (sigma_t) based on instantaneous
loss velocity, batch sampling ratio, and remaining budget trajectory.
Supports multi-tenant bank node tracking, hard budget aborts, and cryptographically
chained audit records.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from datetime import UTC, datetime
from typing import Any

from app.application.services.privacy_service import PrivacyBudgetExceededError
from app.domain.value_objects_rdp import (
    DEFAULT_RDP_ORDERS,
    AutoScalerTelemetry,
    DynamicNoiseCalibration,
    PrivacyAuditEvent,
    RDPAccountantState,
)

logger = logging.getLogger(__name__)


CONSORTIUM_BANK_METADATA: dict[str, dict[str, str]] = {
    "bank_alpha": {"bank_name": "Garanti BBVA Node", "tier": "Tier-1 Enterprise"},
    "bank_beta": {"bank_name": "İş Bankası Node", "tier": "Tier-1 Enterprise"},
    "bank_gamma": {"bank_name": "Akbank Node", "tier": "Tier-1 Enterprise"},
    "bank_delta": {"bank_name": "Yapı Kredi Node", "tier": "Tier-2 Regional"},
}


class AdaptiveDPAutoScaler:
    """Rényi Differential Privacy accountant and dynamic noise auto-scaler."""

    _instance: AdaptiveDPAutoScaler | None = None

    @classmethod
    def get_instance(cls) -> AdaptiveDPAutoScaler:
        """Returns the process-wide singleton instance of AdaptiveDPAutoScaler."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(
        self,
        target_epsilon: float = 4.0,
        target_delta: float = 1e-5,
        nominal_sigma: float = 1.2,
        nominal_clip: float = 1.0,
        orders: list[float] | None = None,
        fail_on_exhaustion: bool = False,
    ) -> None:
        if target_epsilon <= 0.0:
            raise ValueError(f"target_epsilon must be positive, got {target_epsilon}")
        if target_delta <= 0.0 or target_delta >= 1.0:
            raise ValueError(f"target_delta must be in (0, 1), got {target_delta}")
        if nominal_sigma <= 0.0:
            raise ValueError(f"nominal_sigma must be positive, got {nominal_sigma}")
        if nominal_clip <= 0.0:
            raise ValueError(f"nominal_clip must be positive, got {nominal_clip}")

        self.target_epsilon = float(target_epsilon)
        self.target_delta = float(target_delta)
        self.nominal_sigma = float(nominal_sigma)
        self.nominal_clip = float(nominal_clip)
        self.orders = orders or DEFAULT_RDP_ORDERS
        self.fail_on_exhaustion = fail_on_exhaustion

        self._lock = threading.RLock()
        # Circuit breaker safety freeze state
        self._training_circuit_breaker_active: bool = False
        self._frozen_by_node: str | None = None
        self._frozen_at: str | None = None
        self._freeze_reason: str | None = None

        # Per-node RDP accounting maps: node_id -> {alpha: cumulative_rdp}
        self._node_cumulative_rdp: dict[str, dict[float, float]] = {
            "global": {alpha: 0.0 for alpha in self.orders}
        }
        # Per-node calibration histories: node_id -> list[DynamicNoiseCalibration]
        self._node_calibration_history: dict[str, list[DynamicNoiseCalibration]] = {
            "global": []
        }
        # Cryptographically chained audit event log
        self._audit_events: list[PrivacyAuditEvent] = []
        self._genesis_hash = hashlib.sha256(b"RDP_AUTOSCALER_GENESIS_BLOCK_V1").hexdigest()
        self._init_genesis_event()

    def _init_genesis_event(self) -> None:
        """Initializes the genesis block for the SHA-256 privacy audit chain."""
        ts = datetime.now(UTC).isoformat()
        canonical = (
            f"0:{ts}:0:system:{self.nominal_sigma:.6f}:{self.nominal_clip:.6f}:"
            f"0.000000:{self.target_epsilon:.6f}:CALIBRATING:{self._genesis_hash}"
        )
        block_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        genesis_event = PrivacyAuditEvent(
            step=0,
            timestamp_utc=ts,
            round_id=0,
            node_id="system",
            calibrated_sigma=self.nominal_sigma,
            gradient_clip_c=self.nominal_clip,
            cumulative_epsilon=0.0,
            target_epsilon=self.target_epsilon,
            risk_tier="CALIBRATING",
            previous_hash=self._genesis_hash,
            block_hash=block_hash,
        )
        self._audit_events.append(genesis_event)

    @property
    def cumulative_rdp(self) -> dict[float, float]:
        """Provides backwards-compatible access to global cumulative RDP bounds."""
        with self._lock:
            return self._node_cumulative_rdp.setdefault(
                "global", {alpha: 0.0 for alpha in self.orders}
            )

    @cumulative_rdp.setter
    def cumulative_rdp(self, val: dict[float, float]) -> None:
        with self._lock:
            self._node_cumulative_rdp["global"] = val

    @property
    def calibration_history(self) -> list[DynamicNoiseCalibration]:
        """Provides backwards-compatible access to global calibration history."""
        with self._lock:
            return self._node_calibration_history.setdefault("global", [])

    def compute_rdp_gaussian(self, sigma: float, q: float, alpha: float) -> float:
        """Computes analytical Rényi Differential Privacy bound for subsampled Gaussian mechanism.

        For subsampled Gaussian mechanism with sampling ratio q and noise sigma:
            eps_RDP(alpha) <= (alpha * q^2) / (2 * sigma^2) + O(q^3)
        """
        if sigma <= 0.0:
            return float("inf")
        if q <= 0.0:
            return 0.0
        if alpha <= 1.0:
            raise ValueError(f"RDP order alpha must be > 1.0, got {alpha}")

        return (alpha * (q**2)) / (2.0 * (sigma**2))

    def convert_rdp_to_approx_dp(
        self, rdp_map: dict[float, float], delta: float
    ) -> tuple[float, float]:
        """Converts cumulative RDP bounds to standard (epsilon, delta)-DP via convex dual minimization.

        Returns:
            (optimal_epsilon, optimal_alpha)
        """
        if delta <= 0.0 or delta >= 1.0:
            raise ValueError(f"Delta must be in (0, 1), got {delta}")

        if all(v <= 0.0 for v in rdp_map.values()):
            return 0.0, self.orders[0]

        best_eps = float("inf")
        best_alpha = self.orders[0]

        for alpha, rdp_eps in rdp_map.items():
            if alpha <= 1.0:
                continue
            converted_eps = rdp_eps + (math.log(1.0 / delta) / (alpha - 1.0))
            if converted_eps < best_eps:
                best_eps = converted_eps
                best_alpha = alpha

        return best_eps, best_alpha

    def _record_audit_event(
        self,
        round_id: int,
        node_id: str,
        calibrated_sigma: float,
        gradient_clip_c: float,
        cumulative_eps: float,
        risk_tier: str,
    ) -> PrivacyAuditEvent:
        """Appends a new cryptographically chained audit record to the SHA-256 hash trail."""
        prev_hash = self._audit_events[-1].block_hash if self._audit_events else self._genesis_hash
        step = len(self._audit_events)
        ts = datetime.now(UTC).isoformat()
        canonical = (
            f"{step}:{ts}:{round_id}:{node_id}:{calibrated_sigma:.6f}:"
            f"{gradient_clip_c:.6f}:{cumulative_eps:.6f}:{self.target_epsilon:.6f}:"
            f"{risk_tier}:{prev_hash}"
        )
        block_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        event = PrivacyAuditEvent(
            step=step,
            timestamp_utc=ts,
            round_id=round_id,
            node_id=node_id,
            calibrated_sigma=calibrated_sigma,
            gradient_clip_c=gradient_clip_c,
            cumulative_epsilon=cumulative_eps,
            target_epsilon=self.target_epsilon,
            risk_tier=risk_tier,
            previous_hash=prev_hash,
            block_hash=block_hash,
        )
        self._audit_events.append(event)
        return event

    def verify_audit_chain(self) -> bool:
        """Verifies the cryptographic integrity of the SHA-256 audit hash chain."""
        with self._lock:
            if not self._audit_events:
                return True
            # Check genesis block
            if self._audit_events[0].previous_hash != self._genesis_hash:
                return False

            for i in range(1, len(self._audit_events)):
                curr = self._audit_events[i]
                prev = self._audit_events[i - 1]
                if curr.previous_hash != prev.block_hash:
                    return False
                canonical = (
                    f"{curr.step}:{curr.timestamp_utc}:{curr.round_id}:{curr.node_id}:"
                    f"{curr.calibrated_sigma:.6f}:{curr.gradient_clip_c:.6f}:"
                    f"{curr.cumulative_epsilon:.6f}:{curr.target_epsilon:.6f}:"
                    f"{curr.risk_tier}:{curr.previous_hash}"
                )
                expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                if curr.block_hash != expected:
                    return False
            return True

    def auto_scale_noise_multiplier(
        self,
        round_id: int,
        current_loss: float,
        prev_loss: float | None = None,
        batch_size: int = 256,
        total_samples: int = 10_000,
        total_rounds: int = 50,
        node_id: str = "global",
        enforce_budget_limit: bool | None = None,
        target_epsilon: float | None = None,
    ) -> DynamicNoiseCalibration:
        """Dynamically computes the optimal noise multiplier sigma_t and gradient clip C_t for round t.

        Args:
            round_id: Current training round index (must be >= 0).
            current_loss: Current training batch loss.
            prev_loss: Previous round training batch loss (for loss velocity calculation).
            batch_size: Local batch size.
            total_samples: Total training samples in local node dataset.
            total_rounds: Planned total training rounds.
            node_id: Bank node or participant identifier (defaults to "global").
            enforce_budget_limit: If True (or default True when fail_on_exhaustion is set),
                raises PrivacyBudgetExceededError if cumulative epsilon exceeds target_epsilon.
            target_epsilon: Optional per-call budget ceiling override (defaults to self.target_epsilon).

        Returns:
            DynamicNoiseCalibration record for this round.
        """
        if round_id < 0:
            raise ValueError(f"round_id must be non-negative, got {round_id}")
        if current_loss < 0.0:
            raise ValueError(f"current_loss must be non-negative, got {current_loss}")
        if prev_loss is not None and prev_loss < 0.0:
            raise ValueError(f"prev_loss must be non-negative, got {prev_loss}")
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        if total_samples <= 0:
            raise ValueError(f"total_samples must be positive, got {total_samples}")
        if total_rounds <= 0:
            raise ValueError(f"total_rounds must be positive, got {total_rounds}")

        effective_target_eps = (
            self.target_epsilon if target_epsilon is None else float(target_epsilon)
        )
        if effective_target_eps <= 0.0:
            raise ValueError(f"target_epsilon must be positive, got {effective_target_eps}")

        with self._lock:
            # Initialize node accountant if needed
            if node_id not in self._node_cumulative_rdp:
                self._node_cumulative_rdp[node_id] = {alpha: 0.0 for alpha in self.orders}
                self._node_calibration_history[node_id] = []

            sample_ratio_q = max(0.001, min(1.0, batch_size / total_samples))

            # 1. Compute loss velocity
            if prev_loss is None or prev_loss <= 0.0:
                loss_velocity = 0.05
            else:
                loss_velocity = abs(current_loss - prev_loss) / max(0.1, prev_loss)

            # 2. Dynamic noise scaling factor
            progress_ratio = min(1.0, max(0.0, round_id / total_rounds))
            velocity_boost = 1.0 + (0.5 * min(1.0, loss_velocity))
            budget_decay = max(0.65, 1.0 - (0.35 * progress_ratio))
            sampling_scale = math.sqrt(sample_ratio_q / 0.0256)

            calibrated_sigma = self.nominal_sigma * velocity_boost * budget_decay * sampling_scale
            calibrated_sigma = max(0.4, min(3.5, calibrated_sigma))

            # Dynamic clip norm
            calibrated_clip = self.nominal_clip * max(
                0.5, min(2.0, 1.0 + (0.2 * (1.0 - progress_ratio)))
            )

            # 3. Update cumulative RDP accountant for node and global
            for alpha in self.orders:
                step_rdp = self.compute_rdp_gaussian(calibrated_sigma, sample_ratio_q, alpha)
                self._node_cumulative_rdp[node_id][alpha] += step_rdp
                if node_id != "global":
                    self._node_cumulative_rdp["global"][alpha] += step_rdp

            # 4. Convert to (epsilon, delta)-DP for node
            current_eps, opt_alpha = self.convert_rdp_to_approx_dp(
                self._node_cumulative_rdp[node_id], self.target_delta
            )
            instant_eps = (
                sample_ratio_q * math.sqrt(2.0 * math.log(1.25 / self.target_delta))
            ) / calibrated_sigma

            calibration = DynamicNoiseCalibration(
                round_id=round_id,
                current_loss=current_loss,
                loss_velocity=loss_velocity,
                batch_size=batch_size,
                sample_ratio_q=sample_ratio_q,
                calibrated_sigma=calibrated_sigma,
                gradient_clip_c=calibrated_clip,
                instantaneous_epsilon=instant_eps,
                optimal_alpha=opt_alpha,
                node_id=node_id,
            )
            self._node_calibration_history[node_id].append(calibration)
            if node_id != "global":
                self._node_calibration_history["global"].append(calibration)

            # Compute risk tier and circuit-breaker trigger
            pct = (current_eps / effective_target_eps) * 100.0
            if pct >= 100.0:
                risk_tier = "EXHAUSTED"
                if not self._training_circuit_breaker_active:
                    self._training_circuit_breaker_active = True
                    self._frozen_by_node = node_id
                    self._frozen_at = datetime.now(UTC).isoformat()
                    self._freeze_reason = (
                        f"Automated RDP circuit breaker triggered by node '{node_id}': "
                        f"Cumulative epsilon ({current_eps:.3f}) exhausted budget ({effective_target_eps:.3f})"
                    )
                    logger.warning("SAFETY CIRCUIT BREAKER TRIPPED: %s", self._freeze_reason)
            elif pct >= 80.0:
                risk_tier = "BUDGET_WARNING"
            elif len(self._node_calibration_history[node_id]) < 3:
                risk_tier = "CALIBRATING"
            else:
                risk_tier = "OPTIMAL"

            # Record in cryptographically chained audit trail
            self._record_audit_event(
                round_id=round_id,
                node_id=node_id,
                calibrated_sigma=calibrated_sigma,
                gradient_clip_c=calibrated_clip,
                cumulative_eps=current_eps,
                risk_tier=risk_tier,
            )

            logger.info(
                "[%s] Round %d Auto-Scaled DP: sigma=%.3f, clip=%.2f, eps_cum=%.3f (alpha*=%.1f, delta=%.1e)",
                node_id,
                round_id,
                calibrated_sigma,
                calibrated_clip,
                current_eps,
                opt_alpha,
                self.target_delta,
            )

            # Hard budget abort enforcement
            should_enforce = (
                self.fail_on_exhaustion
                if enforce_budget_limit is None
                else enforce_budget_limit
            )
            if should_enforce and current_eps > effective_target_eps:
                raise PrivacyBudgetExceededError(
                    f"Cumulative privacy budget exceeded for node '{node_id}'! "
                    f"Total: {current_eps:.4f} > Limit: {effective_target_eps:.4f}"
                )

            return calibration

    def get_accountant_state(
        self, node_id: str = "global", target_epsilon: float | None = None
    ) -> RDPAccountantState:
        """Returns the current state of the RDP privacy accountant for a specific bank node."""
        with self._lock:
            rdp_map = self._node_cumulative_rdp.get(
                node_id, {alpha: 0.0 for alpha in self.orders}
            )
            history = self._node_calibration_history.get(node_id, [])
            current_eps, opt_alpha = self.convert_rdp_to_approx_dp(
                rdp_map, self.target_delta
            )
            eff_target = (
                self.target_epsilon if target_epsilon is None else float(target_epsilon)
            )
            exhaustion_pct = min(100.0, (current_eps / eff_target) * 100.0)

            return RDPAccountantState(
                total_rounds=len(history),
                cumulative_rdp=dict(rdp_map),
                target_epsilon=eff_target,
                target_delta=self.target_delta,
                current_epsilon_at_delta=current_eps,
                optimal_alpha_order=opt_alpha,
                budget_exhaustion_pct=exhaustion_pct,
                is_budget_exceeded=current_eps > eff_target,
                node_id=node_id,
            )

    def get_telemetry(self, node_id: str = "global") -> AutoScalerTelemetry:
        """Returns real-time health telemetry and budget trajectory for a specific bank node."""
        with self._lock:
            state = self.get_accountant_state(node_id=node_id)
            history = self._node_calibration_history.get(node_id, [])
            active_sigma = (
                history[-1].calibrated_sigma if history else self.nominal_sigma
            )
            active_clip = (
                history[-1].gradient_clip_c if history else self.nominal_clip
            )

            # Risk tier evaluation
            if state.budget_exhaustion_pct >= 100.0:
                risk_tier = "EXHAUSTED"
            elif state.budget_exhaustion_pct >= 80.0:
                risk_tier = "BUDGET_WARNING"
            elif state.total_rounds < 3:
                risk_tier = "CALIBRATING"
            else:
                risk_tier = "OPTIMAL"

            # Signal-to-noise ratio
            snr = 1.0 / active_sigma if active_sigma > 0 else 0.0
            remaining_pct = max(0.0, 100.0 - state.budget_exhaustion_pct)

            # Real structured audit events from the SHA-256 hash trail
            node_events = [
                e.to_dict()
                for e in self._audit_events
                if e.node_id in (node_id, "system", "global")
            ][-10:]

            return AutoScalerTelemetry(
                active_sigma=active_sigma,
                active_clip_norm=active_clip,
                cumulative_epsilon=state.current_epsilon_at_delta,
                target_epsilon=self.target_epsilon,
                remaining_budget_pct=remaining_pct,
                projected_final_epsilon=min(
                    self.target_epsilon, state.current_epsilon_at_delta * 1.15
                ),
                snr_signal_to_noise=snr,
                risk_tier=risk_tier,
                history=list(history[-10:]),
                audit_events=node_events,
                audit_chain_valid=self.verify_audit_chain(),
                node_id=node_id,
            )

    def get_all_nodes_summary(self) -> list[dict[str, Any]]:
        """Returns privacy budget consumption summary across all participating bank nodes."""
        with self._lock:
            summaries: list[dict[str, Any]] = []
            for n_id in sorted(self._node_cumulative_rdp.keys()):
                if n_id == "global":
                    continue
                st = self.get_accountant_state(node_id=n_id)
                summaries.append(
                    {
                        "node_id": n_id,
                        "rounds_completed": st.total_rounds,
                        "cumulative_epsilon": round(st.current_epsilon_at_delta, 4),
                        "target_epsilon": st.target_epsilon,
                        "budget_exhaustion_pct": round(st.budget_exhaustion_pct, 2),
                        "is_budget_exceeded": st.is_budget_exceeded,
                    }
                )
            return summaries

    def freeze_training(
        self,
        node_id: str | None = None,
        reason: str = "Manual operator intervention",
    ) -> dict[str, Any]:
        """Freezes all consortium model training updates by engaging the safety circuit breaker."""
        with self._lock:
            self._training_circuit_breaker_active = True
            self._frozen_by_node = node_id or "secops_admin"
            self._frozen_at = datetime.now(UTC).isoformat()
            self._freeze_reason = reason
            logger.warning("Consortium training FROZEN by %s: %s", self._frozen_by_node, reason)
            return {
                "success": True,
                "action": "freeze",
                "training_circuit_breaker_active": True,
                "frozen_by_node": self._frozen_by_node,
                "frozen_at": self._frozen_at,
                "message": f"Consortium training frozen successfully: {reason}",
                "timestamp": self._frozen_at,
            }

    def unfreeze_training(
        self,
        actor: str = "secops_admin",
        reason: str = "Operator manual unfreeze",
    ) -> dict[str, Any]:
        """Disengages the safety circuit breaker and resumes consortium training."""
        with self._lock:
            self._training_circuit_breaker_active = False
            prev_node = self._frozen_by_node
            self._frozen_by_node = None
            self._frozen_at = None
            self._freeze_reason = None
            now_ts = datetime.now(UTC).isoformat()
            logger.info(
                "Consortium training RESUMED by %s (previously frozen by %s): %s",
                actor,
                prev_node,
                reason,
            )
            return {
                "success": True,
                "action": "unfreeze",
                "training_circuit_breaker_active": False,
                "frozen_by_node": None,
                "frozen_at": None,
                "message": f"Consortium training resumed successfully by {actor}",
                "timestamp": now_ts,
            }

    def reset_node_budget(self, node_id: str | None = None) -> dict[str, Any]:
        """Resets the cumulative RDP accountant budget for a specific node or all nodes."""
        with self._lock:
            if node_id and node_id not in ("all", "global"):
                if node_id in self._node_cumulative_rdp:
                    self._node_cumulative_rdp[node_id] = {alpha: 0.0 for alpha in self.orders}
                    self._node_calibration_history[node_id] = []
                msg = f"Privacy budget for node '{node_id}' reset successfully."
            else:
                self.reset()
                msg = "Consortium privacy budgets reset across all participating bank nodes."
            now_ts = datetime.now(UTC).isoformat()
            return {
                "success": True,
                "action": "reset_budget",
                "training_circuit_breaker_active": self._training_circuit_breaker_active,
                "frozen_by_node": self._frozen_by_node,
                "frozen_at": self._frozen_at,
                "message": msg,
                "timestamp": now_ts,
            }

    def get_consortium_budgets(self) -> dict[str, Any]:
        """Returns consortium-wide per-bank RDP accountant budget telemetry and safety lock status."""
        with self._lock:
            active_nodes = [k for k in self._node_cumulative_rdp if k != "global"]
            # If no non-global nodes registered yet, report canonical consortium banks
            nodes_to_query = active_nodes if active_nodes else ["bank_alpha", "bank_beta", "bank_gamma"]

            node_budgets: list[dict[str, Any]] = []
            any_exceeded = False

            for nid in nodes_to_query:
                st = self.get_accountant_state(node_id=nid)
                history = self._node_calibration_history.get(nid, [])
                calibrated_sigma = history[-1].calibrated_sigma if history else self.nominal_sigma
                meta = CONSORTIUM_BANK_METADATA.get(
                    nid,
                    {"bank_name": f"{nid.replace('_', ' ').title()} Node", "tier": "Consortium Node"},
                )

                if st.budget_exhaustion_pct >= 100.0 or st.is_budget_exceeded:
                    risk_tier = "EXHAUSTED"
                    any_exceeded = True
                elif st.budget_exhaustion_pct >= 80.0:
                    risk_tier = "BUDGET_WARNING"
                elif st.total_rounds < 3:
                    risk_tier = "CALIBRATING"
                else:
                    risk_tier = "OPTIMAL"

                node_budgets.append(
                    {
                        "node_id": nid,
                        "bank_name": meta["bank_name"],
                        "tier": meta["tier"],
                        "rounds_completed": st.total_rounds,
                        "cumulative_epsilon": round(st.current_epsilon_at_delta, 4),
                        "target_epsilon": st.target_epsilon,
                        "target_delta": st.target_delta,
                        "budget_exhaustion_pct": round(st.budget_exhaustion_pct, 2),
                        "is_budget_exceeded": st.is_budget_exceeded,
                        "optimal_alpha_order": st.optimal_alpha_order,
                        "calibrated_sigma": round(calibrated_sigma, 3),
                        "risk_tier": risk_tier,
                    }
                )

            global_rdp = {str(k): round(v, 6) for k, v in self.cumulative_rdp.items()}

            return {
                "consortium_target_epsilon": self.target_epsilon,
                "consortium_target_delta": self.target_delta,
                "total_nodes_active": len(node_budgets),
                "any_budget_exceeded": any_exceeded or self._training_circuit_breaker_active,
                "training_circuit_breaker_active": self._training_circuit_breaker_active,
                "frozen_by_node": self._frozen_by_node,
                "frozen_at": self._frozen_at,
                "freeze_reason": self._freeze_reason,
                "node_budgets": node_budgets,
                "global_cumulative_rdp": global_rdp,
                "updated_at": datetime.now(UTC).isoformat(),
            }

    def reset(self) -> None:
        """Resets accountant state across all nodes while reinitializing the audit chain."""
        with self._lock:
            self._training_circuit_breaker_active = False
            self._frozen_by_node = None
            self._frozen_at = None
            self._freeze_reason = None
            self._node_cumulative_rdp = {
                "global": {alpha: 0.0 for alpha in self.orders}
            }
            self._node_calibration_history = {"global": []}
            self._audit_events.clear()
            self._init_genesis_event()

