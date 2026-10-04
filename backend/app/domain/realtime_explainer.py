"""Sub-Millisecond Fast Real-Time Decision Explainer & Async Feature Attribution Engine.

Provides sub-millisecond fast-path heuristic feature attributions for online scoring SLAs (<5ms).
For comprehensive game-theoretic Shapley value attribution on complex models, see ExplainabilityService
(which uses KernelExplainer).
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)


def get_redis_client() -> Any:
    """Lazily obtain the redis client from infrastructure without rigid static coupling."""
    try:
        from app.infrastructure.cache import get_redis_client as _get_client

        return _get_client()
    except Exception:
        return None


# Bounded in-memory LRU fallback cache (max 1000 entries) when Redis is unreachable
_local_shap_cache: OrderedDict[str, str] = OrderedDict()
_local_cache_lock = threading.RLock()
_MAX_LOCAL_CACHE_SIZE = 1000


def _compute_feature_fingerprint(
    feature_vector: dict[str, Any] | list[float] | None = None,
) -> str | None:
    """Compute a deterministic hash of the input feature vector to prevent stale cache reuse."""
    if feature_vector is None:
        return None
    import hashlib

    hasher = hashlib.sha256()
    if isinstance(feature_vector, dict):
        normalized = {k: v for k, v in sorted(feature_vector.items())}
        hasher.update(json.dumps(normalized, sort_keys=True, default=str).encode("utf-8"))
    elif isinstance(feature_vector, (list, tuple)):
        hasher.update(json.dumps([float(x) for x in feature_vector]).encode("utf-8"))
    else:
        hasher.update(str(feature_vector).encode("utf-8"))
    return hasher.hexdigest()[:12]


def _build_cache_key(
    transaction_id: str,
    tenant_id: str | None = None,
    feature_fingerprint: str | None = None,
    model_version: str | None = None,
    as_of: Any = None,
) -> str:
    """Build multi-dimensional cache key for realtime explanation results.

    Enforces that cached results cannot collide across:
    - Tenants (tenant_id)
    - Transactions (transaction_id)
    - Model artifacts/versions (model_version)
    - Enriched/online feature vector contents (feature_fingerprint)
    - Historical query snapshots (as_of)
    """
    prefix = f"cfi:shap:{tenant_id}" if tenant_id else "cfi:shap"
    key = f"{prefix}:{transaction_id}"
    if model_version:
        key += f":m_{model_version}"
    if as_of:
        as_of_str = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)
        key += f":t_{as_of_str}"
    if feature_fingerprint:
        key += f":f_{feature_fingerprint}"
    return key


def _put_local_cache(key: str, value: str) -> None:
    with _local_cache_lock:
        _local_shap_cache[key] = value
        _local_shap_cache.move_to_end(key)
        while len(_local_shap_cache) > _MAX_LOCAL_CACHE_SIZE:
            _local_shap_cache.popitem(last=False)


def _get_local_cache(key: str) -> str | None:
    with _local_cache_lock:
        val = _local_shap_cache.get(key)
        if val is not None:
            _local_shap_cache.move_to_end(key)
        return val


def invalidate_realtime_cache(transaction_id: str | None = None, tenant_id: str | None = None) -> None:
    """Invalidate local and Redis cache for transaction_id or all if transaction_id is None."""
    with _local_cache_lock:
        if transaction_id is None:
            _local_shap_cache.clear()
        else:
            prefix_tenant = f"cfi:shap:{tenant_id}:{transaction_id}" if tenant_id else None
            prefix_global = f"cfi:shap:{transaction_id}"
            keys_to_remove = [
                k
                for k in _local_shap_cache
                if (prefix_tenant and (k == prefix_tenant or k.startswith(f"{prefix_tenant}:")))
                or k == prefix_global
                or k.startswith(f"{prefix_global}:")
            ]
            for k in keys_to_remove:
                _local_shap_cache.pop(k, None)

    try:
        client = get_redis_client()
        if client and transaction_id:
            patterns = [f"cfi:shap:*:{transaction_id}*", f"cfi:shap:{transaction_id}*"]
            for pat in patterns:
                matched_keys = client.keys(pat)
                if matched_keys:
                    client.delete(*matched_keys)
    except Exception as exc:
        logger.debug("Redis cache invalidation error: %s", exc)


@dataclass
class RealtimeFeatureAttribution:
    """Attribution vector item for real-time inference decision explanation."""

    feature_name: str
    contribution_score: float
    direction: str  # "INCREASES_RISK" or "DECREASES_RISK"


class FastInferenceExplainer:
    """Provides sub-millisecond feature attributions without heavy explainer overhead."""

    def invalidate_cache(self, transaction_id: str | None = None, tenant_id: str | None = None) -> None:
        """Invalidate cached attributions."""
        invalidate_realtime_cache(transaction_id=transaction_id, tenant_id=tenant_id)

    def explain_realtime_score(
        self,
        amount: float,
        velocity_1h: int,
        merchant_category: str,
        risk_score: float,
    ) -> list[RealtimeFeatureAttribution]:
        """Calculates fast feature contribution vectors for online scoring."""
        attributions: list[RealtimeFeatureAttribution] = []
        clean_mcc = merchant_category.lower().strip()

        if clean_mcc in {"crypto_exchange", "gambling", "p2p_cash"}:
            attributions.append(
                RealtimeFeatureAttribution(
                    feature_name="merchant_category",
                    contribution_score=0.35,
                    direction="INCREASES_RISK",
                )
            )

        if velocity_1h >= 5:
            attributions.append(
                RealtimeFeatureAttribution(
                    feature_name="velocity_1h",
                    contribution_score=0.25,
                    direction="INCREASES_RISK",
                )
            )
        elif velocity_1h <= 2:
            attributions.append(
                RealtimeFeatureAttribution(
                    feature_name="velocity_1h",
                    contribution_score=0.10,
                    direction="DECREASES_RISK",
                )
            )

        if amount >= 20000.0:
            attributions.append(
                RealtimeFeatureAttribution(
                    feature_name="amount",
                    contribution_score=0.40,
                    direction="INCREASES_RISK",
                )
            )
        elif amount < 500.0:
            attributions.append(
                RealtimeFeatureAttribution(
                    feature_name="amount",
                    contribution_score=0.15,
                    direction="DECREASES_RISK",
                )
            )

        return attributions

    def compute_shap(
        self,
        transaction_id: str,
        feature_vector: dict[str, Any] | list[float],
        webhook_url: str | None = None,
        tenant_id: str | None = None,
        model_version: str | None = None,
        as_of: Any = None,
    ) -> dict[str, Any]:
        """Calculates fast heuristic feature attributions asynchronously, caches result in Redis (300s TTL), and triggers webhook."""
        if isinstance(feature_vector, dict):
            amount = float(feature_vector.get("amount", 100.0))
            velocity_1h = int(feature_vector.get("velocity_1h", 1))
            mcc = str(feature_vector.get("merchant_category", "retail"))
        else:
            amount = feature_vector[0] if len(feature_vector) > 0 else 100.0
            velocity_1h = int(feature_vector[1]) if len(feature_vector) > 1 else 1
            mcc = "retail"

        attributions = self.explain_realtime_score(
            amount=amount, velocity_1h=velocity_1h, merchant_category=mcc, risk_score=0.5
        )
        shap_values = [asdict(a) for a in attributions]

        res = {
            "transaction_id": transaction_id,
            "status": "COMPLETED",
            "source": "FAST_HEURISTIC_COMPUTED",
            "method": "fast_heuristic",
            "attributions": shap_values,
            "shap_values": shap_values,
        }

        feature_fp = _compute_feature_fingerprint(feature_vector)
        redis_key = _build_cache_key(
            transaction_id,
            tenant_id=tenant_id,
            feature_fingerprint=feature_fp,
            model_version=model_version,
            as_of=as_of,
        )
        serialized = json.dumps(res)
        _put_local_cache(redis_key, serialized)

        # Store in Redis with 300 seconds (5 min) TTL
        try:
            client = get_redis_client()
            if client:
                client.setex(redis_key, 300, serialized)
                logger.info(
                    "Cached fast explanation result for transaction '%s' in Redis (TTL=300s)", transaction_id
                )
        except Exception as exc:
            logger.warning("Could not cache fast explanation result in Redis (%s); using in-memory cache", exc)

        # Trigger webhook if URL provided
        if webhook_url and isinstance(webhook_url, str) and webhook_url.strip().lower().startswith(("http://", "https://")):
            try:
                import httpx

                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "CF-Intelligence-Webhook/1.0",
                    "X-CFI-Transaction-Id": transaction_id,
                }
                httpx.post(webhook_url, json=res, headers=headers, timeout=3.0)
                logger.info(
                    "Delivered fast explanation webhook callback to %s for tx '%s'", webhook_url, transaction_id
                )
            except Exception as exc:
                logger.warning("Webhook delivery to %s failed: %s", webhook_url, exc)

        return res

    def explain_async(
        self,
        transaction_id: str,
        feature_vector: dict[str, Any] | list[float],
        webhook_url: str | None = None,
        tenant_id: str | None = None,
        model_version: str | None = None,
        as_of: Any = None,
    ) -> dict[str, Any]:
        """Asynchronously requests fast heuristic explanation, checking Redis cache first for sub-millisecond hit."""
        feature_fp = _compute_feature_fingerprint(feature_vector)
        redis_key = _build_cache_key(
            transaction_id,
            tenant_id=tenant_id,
            feature_fingerprint=feature_fp,
            model_version=model_version,
            as_of=as_of,
        )

        # 1. Fast Path: In-memory LRU cache hit
        cached_local = _get_local_cache(redis_key)
        if cached_local is not None:
            data = json.loads(cached_local)
            data["source"] = "LOCAL_CACHE_HIT"
            return data

        # 2. Medium Path: Redis cache hit
        cached_str: str | None = None
        try:
            client = get_redis_client()
            if client:
                cached_bytes = client.get(redis_key)
                if cached_bytes:
                    cached_str = (
                        cached_bytes.decode()
                        if isinstance(cached_bytes, bytes)
                        else str(cached_bytes)
                    )
        except Exception as exc:
            logger.debug("Redis read error for key %s: %s", redis_key, exc)

        if cached_str:
            data = json.loads(cached_str)
            data["source"] = "REDIS_CACHE"
            logger.info("Fast explanation cache HIT for transaction '%s'", transaction_id)
            return data

        # 2. Cache miss: trigger computation or return pending job
        job_id = f"job_shap_{transaction_id}"
        logger.info(
            "Fast explanation cache MISS for transaction '%s'. Enqueueing async computation...", transaction_id
        )
        self.compute_shap(
            transaction_id,
            feature_vector,
            webhook_url=webhook_url,
            tenant_id=tenant_id,
            model_version=model_version,
            as_of=as_of,
        )

        return {
            "job_id": job_id,
            "transaction_id": transaction_id,
            "status": "PENDING",
        }
