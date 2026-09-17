"""Dynamic Business Rules & Policy Evaluation Engine.

Provides an AST-based declarative condition evaluator and a database-backed rule
registry. Enables risk analysts to hot-reload and test fraud rules in real time.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.infrastructure.models import BusinessRuleModel

logger = logging.getLogger(__name__)

_ALLOWED_OPERATORS = frozenset({
    "==",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "in",
    "not in",
    "between",
    "contains",
    "not contains",
    "matches",
    "regex",
})


def validate_condition_ast(condition: Any) -> list[str]:
    """Recursively validate a JSON condition AST.

    Checks structural correctness, operator validity, ReDoS bounds, and
    collects all referenced field names.
    Raises ValueError on any invalid condition structure.
    """
    if not isinstance(condition, dict) or not condition:
        raise ValueError("Condition AST must be a non-empty dictionary.")

    fields: list[str] = []

    if "and" in condition:
        sub_conds = condition["and"]
        if not isinstance(sub_conds, list) or not sub_conds:
            raise ValueError("'and' block must contain a non-empty list of condition objects.")
        for idx, sub in enumerate(sub_conds):
            if not isinstance(sub, dict):
                raise ValueError(f"'and' child at index {idx} must be a dictionary condition.")
            fields.extend(validate_condition_ast(sub))
        return list(dict.fromkeys(fields))

    if "or" in condition:
        sub_conds = condition["or"]
        if not isinstance(sub_conds, list) or not sub_conds:
            raise ValueError("'or' block must contain a non-empty list of condition objects.")
        for idx, sub in enumerate(sub_conds):
            if not isinstance(sub, dict):
                raise ValueError(f"'or' child at index {idx} must be a dictionary condition.")
            fields.extend(validate_condition_ast(sub))
        return list(dict.fromkeys(fields))

    if "not" in condition:
        sub_cond = condition["not"]
        if not isinstance(sub_cond, dict) or not sub_cond:
            raise ValueError("'not' block must contain a non-empty dictionary condition.")
        fields.extend(validate_condition_ast(sub_cond))
        return list(dict.fromkeys(fields))

    field = condition.get("field")
    operator = condition.get("operator")

    if not field or not isinstance(field, str) or not field.strip():
        raise ValueError("Condition leaf must specify a valid, non-empty 'field' string.")

    if not operator or not isinstance(operator, str) or not operator.strip():
        raise ValueError("Condition leaf must specify a valid, non-empty 'operator' string.")

    op = operator.strip().lower()
    if op not in _ALLOWED_OPERATORS:
        raise ValueError(f"Unsupported operator '{operator}'. Allowed operators: {sorted(_ALLOWED_OPERATORS)}")

    if op == "between":
        if "min_value" in condition and "max_value" in condition:
            try:
                float(condition["min_value"])
                float(condition["max_value"])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid min_value/max_value for 'between': {exc}") from exc
        elif "value" in condition and isinstance(condition["value"], (list, tuple)) and len(condition["value"]) == 2:
            try:
                float(condition["value"][0])
                float(condition["value"][1])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid array bounds for 'between': {exc}") from exc
        elif (
            "value" in condition
            and isinstance(condition["value"], dict)
            and "min" in condition["value"]
            and "max" in condition["value"]
        ):
            try:
                float(condition["value"]["min"])
                float(condition["value"]["max"])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid dict bounds for 'between': {exc}") from exc
        else:
            raise ValueError("'between' operator requires 'min_value' and 'max_value', or a 2-element 'value' range.")
    elif op in ("matches", "regex"):
        target_val = condition.get("value")
        if target_val is None or not isinstance(target_val, str):
            raise ValueError(f"Operator '{op}' requires a string 'value' regex pattern.")
        if len(target_val) > 256:
            raise ValueError("Regex pattern length exceeds maximum limit of 256 characters (ReDoS guard).")
        try:
            re.compile(target_val)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression pattern: {exc}") from exc
    else:
        if "value" not in condition or condition["value"] is None:
            raise ValueError(f"Operator '{op}' requires a non-null 'value'.")

    fields.append(field.strip())
    return list(dict.fromkeys(fields))


def evaluate_condition(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate a JSON condition AST recursively against a context dictionary.

    Supports:
        * Logical operations: "and" (list of dicts), "or" (list of dicts), "not" (dict)
        * Comparison operations: "field", "operator", "value" (or "min_value", "max_value")
        * Operators: ==, !=, >, >=, <, <=, in, not in, between, contains, not contains, matches, regex
    """
    matches, _ = evaluate_condition_with_trace(condition, context)
    return matches


def evaluate_condition_with_trace(
    condition: dict[str, Any],
    context: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Evaluate condition AST recursively while collecting matched fields."""
    if not isinstance(condition, dict):
        return False, []

    if "and" in condition:
        sub_conds = condition["and"]
        if not isinstance(sub_conds, list):
            return False, []
        matched_fields: list[str] = []
        for c in sub_conds:
            sub_matches, sub_fields = evaluate_condition_with_trace(c, context)
            if not sub_matches:
                return False, []
            matched_fields.extend(sub_fields)
        return True, list(dict.fromkeys(matched_fields))

    if "or" in condition:
        sub_conds = condition["or"]
        if not isinstance(sub_conds, list):
            return False, []
        for c in sub_conds:
            sub_matches, sub_fields = evaluate_condition_with_trace(c, context)
            if sub_matches:
                return True, sub_fields
        return False, []

    if "not" in condition:
        sub_cond = condition["not"]
        if not isinstance(sub_cond, dict):
            return False, []
        sub_matches, sub_fields = evaluate_condition_with_trace(sub_cond, context)
        return not sub_matches, sub_fields

    # Basic comparison leaf
    field = condition.get("field")
    operator = condition.get("operator")
    target_value = condition.get("value")

    if field is None or operator is None:
        return False, []

    val = context.get(field)
    if val is None:
        return False, []

    try:
        op = str(operator).strip().lower()

        # 1. 'between' range check
        if op == "between":
            if "min_value" in condition and "max_value" in condition:
                min_val = float(condition["min_value"])
                max_val = float(condition["max_value"])
            elif isinstance(target_value, (list, tuple)) and len(target_value) == 2:
                min_val = float(target_value[0])
                max_val = float(target_value[1])
            elif isinstance(target_value, dict) and "min" in target_value and "max" in target_value:
                min_val = float(target_value["min"])
                max_val = float(target_value["max"])
            else:
                return False, []
            matches = min_val <= float(val) <= max_val
            return matches, [field] if matches else []

        # All subsequent operators require target_value to be non-None
        if target_value is None:
            return False, []

        # 2. Boolean equality & inequality
        if isinstance(target_value, bool):
            val_bool = val if isinstance(val, bool) else str(val).strip().lower() in ("true", "1", "yes")
            if op == "==":
                matches = val_bool == target_value
                return matches, [field] if matches else []
            elif op == "!=":
                matches = val_bool != target_value
                return matches, [field] if matches else []
            return False, []

        # 3. Standard string / numeric equality
        if op == "==":
            matches = str(val).strip().lower() == str(target_value).strip().lower()
            return matches, [field] if matches else []
        elif op == "!=":
            matches = str(val).strip().lower() != str(target_value).strip().lower()
            return matches, [field] if matches else []

        # 4. Numeric comparisons
        elif op == ">":
            matches = float(val) > float(target_value)
            return matches, [field] if matches else []
        elif op == ">=":
            matches = float(val) >= float(target_value)
            return matches, [field] if matches else []
        elif op == "<":
            matches = float(val) < float(target_value)
            return matches, [field] if matches else []
        elif op == "<=":
            matches = float(val) <= float(target_value)
            return matches, [field] if matches else []

        # 5. Set / Substring membership
        elif op == "in":
            if isinstance(target_value, list):
                str_list = [str(x).strip().lower() for x in target_value]
                matches = str(val).strip().lower() in str_list
            else:
                matches = str(val).strip().lower() in str(target_value).strip().lower()
            return matches, [field] if matches else []
        elif op == "not in":
            if isinstance(target_value, list):
                str_list = [str(x).strip().lower() for x in target_value]
                matches = str(val).strip().lower() not in str_list
            else:
                matches = str(val).strip().lower() not in str(target_value).strip().lower()
            return matches, [field] if matches else []

        # 6. 'contains' and 'not contains'
        elif op == "contains":
            if isinstance(val, (list, tuple, set)):
                matches = str(target_value).strip().lower() in [str(x).strip().lower() for x in val]
            else:
                matches = str(target_value).strip().lower() in str(val).strip().lower()
            return matches, [field] if matches else []
        elif op == "not contains":
            if isinstance(val, (list, tuple, set)):
                matches = str(target_value).strip().lower() not in [str(x).strip().lower() for x in val]
            else:
                matches = str(target_value).strip().lower() not in str(val).strip().lower()
            return matches, [field] if matches else []

        # 7. Regex / Pattern matching
        elif op in ("matches", "regex"):
            pattern = re.compile(str(target_value), re.IGNORECASE)
            matches = bool(pattern.search(str(val)))
            return matches, [field] if matches else []

    except Exception as exc:
        logger.warning(
            "Condition evaluation failed on field=%s op=%s: %s",
            field,
            operator,
            exc,
        )
        return False, []

    return False, []


_ACTIVE_RULES_CACHE: list[BusinessRuleModel] | None = None
_ACTIVE_RULES_CACHE_TIME: float = 0.0
_CACHE_TTL: float = 5.0
_CACHE_LOCK = threading.Lock()


def invalidate_policy_cache() -> None:
    """Invalidate the in-memory policy rules cache."""
    global _ACTIVE_RULES_CACHE, _ACTIVE_RULES_CACHE_TIME
    with _CACHE_LOCK:
        _ACTIVE_RULES_CACHE = None
        _ACTIVE_RULES_CACHE_TIME = 0.0


class PolicyEngineService:
    """Manages business policies and executes active transaction screening rules."""

    def __init__(self) -> None:
        pass

    async def list_rules(self, session: AsyncSession) -> list[BusinessRuleModel]:
        """Fetch all policy rules in the active tenant repository."""
        stmt = select(BusinessRuleModel).order_by(BusinessRuleModel.rule_name)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def get_rule(self, session: AsyncSession, rule_id: str) -> BusinessRuleModel | None:
        """Fetch a single business rule by primary key ID."""
        stmt = select(BusinessRuleModel).where(BusinessRuleModel.id == rule_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_active_rules(self, session: AsyncSession) -> list[BusinessRuleModel]:
        """Fetch active policy rules for real-time transaction screening (cached)."""
        global _ACTIVE_RULES_CACHE, _ACTIVE_RULES_CACHE_TIME
        now = datetime.now(UTC).timestamp()
        with _CACHE_LOCK:
            if _ACTIVE_RULES_CACHE is not None and (now - _ACTIVE_RULES_CACHE_TIME) < _CACHE_TTL:
                return _ACTIVE_RULES_CACHE

        stmt = (
            select(BusinessRuleModel)
            .where(BusinessRuleModel.is_active.is_(True))
            .order_by(BusinessRuleModel.rule_name)
        )
        res = await session.execute(stmt)
        rules = list(res.scalars().all())
        with _CACHE_LOCK:
            _ACTIVE_RULES_CACHE = rules
            _ACTIVE_RULES_CACHE_TIME = now
        return rules

    async def ensure_default_rules(
        self,
        session: AsyncSession,
        default_rules: list[Any],
    ) -> list[BusinessRuleModel]:
        """Ensure initial default rules exist in the persistent database.

        If the database repository is empty or missing default seed rules,
        they are safely persisted so that subsequent GET, PUT, and DELETE
        operations target real database records.
        """
        stmt = select(BusinessRuleModel).order_by(BusinessRuleModel.rule_name)
        res = await session.execute(stmt)
        existing = {r.id: r for r in res.scalars().all()}

        new_count = 0
        for dr in default_rules:
            if isinstance(dr, dict):
                raw_id = dr.get("id")
                name = dr.get("rule_name")
                cond = dr.get("condition")
                act = dr.get("action")
                active = dr.get("is_active", True)
            else:
                raw_id = getattr(dr, "id", None)
                name = getattr(dr, "rule_name", None)
                cond = getattr(dr, "condition", None)
                act = getattr(dr, "action", None)
                active = getattr(dr, "is_active", True)

            dr_id = str(raw_id).strip() if raw_id else None
            if dr_id and dr_id not in existing:
                # Check if a rule with same rule_name already exists to avoid unique constraint violations
                name_exists = any(r.rule_name == name for r in existing.values())
                if not name_exists:
                    rule = BusinessRuleModel(
                        id=dr_id,
                        rule_name=str(name) if name else dr_id,
                        condition=cond if isinstance(cond, dict) else {},
                        action=str(act).strip().upper() if act else "ALLOW",
                        is_active=bool(active),
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                    session.add(rule)
                    existing[dr_id] = rule
                    new_count += 1

        if new_count > 0:
            await session.commit()
            invalidate_policy_cache()
            logger.info("Persisted %d default business rules to active database", new_count)

        return list(existing.values())

    async def create_rule(
        self,
        session: AsyncSession,
        rule_name: str,
        condition: dict[str, Any],
        action: str = "BLOCK_TRANSACTION",
        is_active: bool = True,
        rule_id: str | None = None,
    ) -> BusinessRuleModel:
        """Create and persist a new dynamic business rule."""
        if not rule_name or not rule_name.strip():
            raise ValueError("Rule name must be a non-empty string.")
        if not isinstance(condition, dict) or not condition:
            raise ValueError("Condition must be a non-empty dictionary AST.")
        if not action or not action.strip():
            raise ValueError("Action must be a non-empty string.")

        # Validate condition AST syntax before persisting
        validate_condition_ast(condition)

        invalidate_policy_cache()
        rule = BusinessRuleModel(
            id=rule_id.strip() if rule_id and rule_id.strip() else str(uuid.uuid4()),
            rule_name=rule_name.strip(),
            condition=condition,
            action=action.strip().upper(),
            is_active=is_active,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(rule)
        await session.commit()
        if hasattr(session, "refresh"):
            res = session.refresh(rule)
            if hasattr(res, "__await__"):
                await res
        logger.info("Created business rule: %s (%s)", rule_name, action)
        return rule

    async def update_rule(
        self,
        session: AsyncSession,
        rule_id: str,
        rule_name: str | None = None,
        condition: dict[str, Any] | None = None,
        action: str | None = None,
        is_active: bool | None = None,
    ) -> BusinessRuleModel | None:
        """Update and hot-reload an existing rule configuration."""
        if rule_name is not None and not rule_name.strip():
            raise ValueError("Rule name cannot be empty.")
        if condition is not None:
            if not isinstance(condition, dict) or not condition:
                raise ValueError("Condition must be a non-empty dictionary AST.")
            validate_condition_ast(condition)
        if action is not None and not action.strip():
            raise ValueError("Action cannot be empty.")

        stmt = select(BusinessRuleModel).where(BusinessRuleModel.id == rule_id)
        res = await session.execute(stmt)
        rule = res.scalar_one_or_none()
        if not rule:
            return None

        if rule_name is not None:
            rule.rule_name = rule_name.strip()
        if condition is not None:
            rule.condition = condition
        if action is not None:
            rule.action = action.strip().upper()
        if is_active is not None:
            rule.is_active = is_active

        rule.updated_at = datetime.now(UTC)
        await session.commit()
        invalidate_policy_cache()
        logger.info("Updated business rule: %s", rule.rule_name)
        return rule

    async def delete_rule(self, session: AsyncSession, rule_id: str) -> bool:
        """Remove a rule from the repository."""
        stmt = select(BusinessRuleModel).where(BusinessRuleModel.id == rule_id)
        res = await session.execute(stmt)
        rule = res.scalar_one_or_none()
        if not rule:
            return False

        await session.delete(rule)
        await session.commit()
        invalidate_policy_cache()
        logger.info("Deleted business rule ID: %s", rule_id)
        return True

    def test_rule(self, condition: dict[str, Any], transaction: dict[str, Any]) -> bool:
        """Evaluate a condition AST locally without writing to the database."""
        validate_condition_ast(condition)
        return evaluate_condition(condition, transaction)

    def test_rule_detailed(
        self,
        condition: dict[str, Any],
        transaction: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        """Validate condition AST and evaluate against transaction context with matched field tracing."""
        validate_condition_ast(condition)
        return evaluate_condition_with_trace(condition, transaction)

    async def evaluate_rules(
        self,
        session: AsyncSession | None,
        transaction: dict[str, Any],
        stop_on_first_match: bool = False,
        tenant_id: str | None = None,
        default_rules: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Screen a transaction context against active business policy rules."""
        start_time = time.perf_counter()
        rules: list[Any] = []
        if session is not None:
            try:
                rules = await self.get_active_rules(session)
            except Exception as exc:
                logger.warning("Failed to load active rules from DB (%s), using fallbacks", exc)
                rules = []

        if not rules and default_rules:
            rules = default_rules

        triggered: list[dict[str, Any]] = []

        # Severity hierarchy: (rank, aggregate_decision, risk_score_delta)
        severity_map: dict[str, tuple[int, str, float]] = {
            "BLOCK_TRANSACTION": (7, "BLOCK", 0.60),
            "ESCALATE_TO_SAR": (6, "BLOCK", 0.50),
            "FLAG_CRITICAL": (5, "BLOCK", 0.40),
            "FLAG_HIGH_RISK": (4, "REVIEW", 0.25),
            "HOLD_FOR_REVIEW": (3, "REVIEW", 0.20),
            "REQUIRE_MFA": (2, "REVIEW", 0.15),
            "ALLOW": (1, "ALLOW", 0.0),
        }

        max_sev_rank = 1
        highest_action = "ALLOW"
        decision = "ALLOW"
        risk_score_delta = 0.0

        for r in rules:
            if isinstance(r, dict):
                cond = r.get("condition", {})
                action = str(r.get("action", "ALLOW")).strip().upper()
                rule_id = str(r.get("id", "rule_unknown"))
                rule_name = str(r.get("rule_name", "Unknown Rule"))
                priority = r.get("priority", 100)
            else:
                cond = getattr(r, "condition", {})
                action = str(getattr(r, "action", "ALLOW")).strip().upper()
                rule_id = str(getattr(r, "id", "rule_unknown"))
                rule_name = str(getattr(r, "rule_name", "Unknown Rule"))
                priority = getattr(r, "priority", 100)

            matches, _ = evaluate_condition_with_trace(cond, transaction)
            if matches:
                triggered.append({
                    "rule_id": rule_id,
                    "rule_name": rule_name,
                    "action": action,
                    "priority": priority,
                    "matched_condition": cond,
                })
                rank, rule_decision, delta = severity_map.get(action, (3, "REVIEW", 0.15))
                if rank > max_sev_rank:
                    max_sev_rank = rank
                    highest_action = action
                    decision = rule_decision
                risk_score_delta = min(1.0, risk_score_delta + delta)
                if stop_on_first_match:
                    break

        latency_ms = round((time.perf_counter() - start_time) * 1000.0, 3)
        return {
            "evaluated_rules_count": len(rules),
            "triggered_rules_count": len(triggered),
            "highest_severity_action": highest_action,
            "decision": decision,
            "risk_score_delta": round(risk_score_delta, 4),
            "triggered_rules": triggered,
            "latency_ms": latency_ms,
        }
