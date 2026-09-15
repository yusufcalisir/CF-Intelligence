"""Dynamic Business Rules & Policy Evaluation Engine.

Provides an AST-based declarative condition evaluator and a database-backed rule
registry. Enables risk analysts to hot-reload and test fraud rules in real time.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.infrastructure.models import BusinessRuleModel

logger = logging.getLogger(__name__)


def evaluate_condition(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate a JSON condition AST recursively against a context dictionary.

    Supports:
        * Logical operations: "and" (list of dicts), "or" (list of dicts), "not" (dict)
        * Comparison operations: "field", "operator", "value" (or "min_value", "max_value")
        * Operators: ==, !=, >, >=, <, <=, in, not in, between, contains, not contains, matches, regex
    """
    if "and" in condition:
        sub_conds = condition["and"]
        if not isinstance(sub_conds, list):
            return False
        return all(evaluate_condition(c, context) for c in sub_conds)

    if "or" in condition:
        sub_conds = condition["or"]
        if not isinstance(sub_conds, list):
            return False
        return any(evaluate_condition(c, context) for c in sub_conds)

    if "not" in condition:
        sub_cond = condition["not"]
        if not isinstance(sub_cond, dict):
            return False
        return not evaluate_condition(sub_cond, context)

    # Basic comparison leaf
    field = condition.get("field")
    operator = condition.get("operator")
    target_value = condition.get("value")

    if field is None or operator is None:
        return False

    val = context.get(field)
    if val is None:
        return False

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
                return False
            return min_val <= float(val) <= max_val

        # All subsequent operators require target_value to be non-None
        if target_value is None:
            return False

        # 2. Boolean equality & inequality
        if isinstance(target_value, bool):
            val_bool = val if isinstance(val, bool) else str(val).strip().lower() in ("true", "1", "yes")
            if op == "==":
                return val_bool == target_value
            elif op == "!=":
                return val_bool != target_value
            return False

        # 3. Standard string / numeric equality
        if op == "==":
            return str(val).strip().lower() == str(target_value).strip().lower()
        elif op == "!=":
            return str(val).strip().lower() != str(target_value).strip().lower()

        # 4. Numeric comparisons
        elif op == ">":
            return float(val) > float(target_value)
        elif op == ">=":
            return float(val) >= float(target_value)
        elif op == "<":
            return float(val) < float(target_value)
        elif op == "<=":
            return float(val) <= float(target_value)

        # 5. Set / Substring membership
        elif op == "in":
            if isinstance(target_value, list):
                str_list = [str(x).strip().lower() for x in target_value]
                return str(val).strip().lower() in str_list
            return str(val).strip().lower() in str(target_value).strip().lower()
        elif op == "not in":
            if isinstance(target_value, list):
                str_list = [str(x).strip().lower() for x in target_value]
                return str(val).strip().lower() not in str_list
            return str(val).strip().lower() not in str(target_value).strip().lower()

        # 6. 'contains' and 'not contains'
        elif op == "contains":
            if isinstance(val, (list, tuple, set)):
                return str(target_value).strip().lower() in [str(x).strip().lower() for x in val]
            return str(target_value).strip().lower() in str(val).strip().lower()
        elif op == "not contains":
            if isinstance(val, (list, tuple, set)):
                return str(target_value).strip().lower() not in [str(x).strip().lower() for x in val]
            return str(target_value).strip().lower() not in str(val).strip().lower()

        # 7. Regex / Pattern matching
        elif op in ("matches", "regex"):
            pattern = re.compile(str(target_value), re.IGNORECASE)
            return bool(pattern.search(str(val)))

    except Exception as exc:
        logger.warning(
            "Condition evaluation failed on field=%s op=%s: %s",
            field,
            operator,
            exc,
        )
        return False

    return False


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

    async def create_rule(
        self,
        session: AsyncSession,
        rule_name: str,
        condition: dict[str, Any],
        action: str = "BLOCK_TRANSACTION",
        is_active: bool = True,
    ) -> BusinessRuleModel:
        """Create and persist a new dynamic business rule."""
        if not rule_name or not rule_name.strip():
            raise ValueError("Rule name must be a non-empty string.")
        if not isinstance(condition, dict) or not condition:
            raise ValueError("Condition must be a non-empty dictionary AST.")
        if not action or not action.strip():
            raise ValueError("Action must be a non-empty string.")

        invalidate_policy_cache()
        rule = BusinessRuleModel(
            id=str(uuid.uuid4()),
            rule_name=rule_name.strip(),
            condition=condition,
            action=action.strip(),
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
        if condition is not None and (not isinstance(condition, dict) or not condition):
            raise ValueError("Condition must be a non-empty dictionary AST.")
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
            rule.action = action.strip()
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
        return evaluate_condition(condition, transaction)
