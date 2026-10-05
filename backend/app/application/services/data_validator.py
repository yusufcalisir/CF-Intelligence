"""Data Validation & Contract Gating Service.

Integrates Pandera for streaming schema validation and
Great Expectations (v1.x) for data contract statistical stability checks.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import great_expectations as ge
    import great_expectations.expectations as gxe
    import pandera as pa
    from great_expectations import ExpectationSuite, ValidationDefinition
    from pandera.errors import SchemaError
    from pandera.typing import Series  # noqa: TC002

    HAS_GREAT_EXPECTATIONS = True
    HAS_PANDERA = True
else:
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, module=r"pydantic.*")
            warnings.filterwarnings("ignore", message=r".*Valid config keys have changed in V2.*")
            import great_expectations as ge
            import great_expectations.expectations as gxe
            from great_expectations import ExpectationSuite, ValidationDefinition

        HAS_GREAT_EXPECTATIONS = True
    except ImportError:
        ge = None
        gxe = None
        ExpectationSuite = None
        ValidationDefinition = None
        HAS_GREAT_EXPECTATIONS = False

    try:
        import pandera.pandas as pa
        from pandera.errors import SchemaError
        from pandera.pandas import Series  # noqa: TC002

        HAS_PANDERA = True
    except ImportError:
        try:
            import pandera as pa
            from pandera.errors import SchemaError
            from pandera.typing import Series  # noqa: TC002

            HAS_PANDERA = True
        except ImportError:
            pa = None
            SchemaError = Exception
            Series = None
            HAS_PANDERA = False

import pandas as pd  # noqa: TC002

logger = logging.getLogger(__name__)


_ge_registration_lock = threading.Lock()
_gx_thread_local = threading.local()

if HAS_GREAT_EXPECTATIONS:
    from great_expectations.data_context.data_context.context_factory import project_manager

    if not getattr(project_manager, "_cf_thread_local_installed", False):
        orig_get_project = project_manager.get_project

        def _tl_get_project(*args: Any, **kwargs: Any) -> Any:
            ctx = orig_get_project(*args, **kwargs)
            _gx_thread_local.project = ctx
            return ctx

        project_manager.get_project = _tl_get_project  # type: ignore[method-assign]
        if hasattr(ge, "get_context"):
            ge.get_context = _tl_get_project

        orig_set_project = project_manager.set_project

        def _tl_set_project(project: Any) -> None:
            _gx_thread_local.project = project
            orig_set_project(project)

        project_manager.set_project = _tl_set_project  # type: ignore[method-assign]

        orig_get_datasources = getattr(project_manager.__class__, "get_datasources", None)
        if callable(orig_get_datasources):
            _orig_ds = orig_get_datasources

            def _tl_get_datasources(self: Any, *args: Any, **kwargs: Any) -> Any:
                if hasattr(_gx_thread_local, "project") and _gx_thread_local.project is not None:
                    return _gx_thread_local.project.data_sources.all()
                return _orig_ds(self, *args, **kwargs)

            project_manager.__class__.get_datasources = _tl_get_datasources  # type: ignore[method-assign]
            project_manager.get_datasources = lambda *args, **kwargs: _tl_get_datasources(project_manager, *args, **kwargs)  # type: ignore[method-assign]

        orig_project_prop = getattr(project_manager.__class__, "_project", None)
        orig_fget = getattr(orig_project_prop, "fget", None) if orig_project_prop is not None else None

        def _tl_get_project_prop(self: Any) -> Any:
            if hasattr(_gx_thread_local, "project") and _gx_thread_local.project is not None:
                return _gx_thread_local.project
            if callable(orig_fget):
                return orig_fget(self)
            return None

        project_manager.__class__._project = property(_tl_get_project_prop)
        project_manager._cf_thread_local_installed = True  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]


class DataContractValidationError(Exception):
    """Custom exception raised when a dataset fails the Great Expectations data contract gating."""

    pass


if HAS_PANDERA and pa is not None:

    class TransactionSchema(pa.DataFrameModel):
        """Pandera schema model for verifying transaction dataframe specifications."""

        transaction_amount: Series[float] = pa.Field(gt=0.0)
        velocity: Series[float] = pa.Field(ge=0.0)
        hour_of_day: Series[int] = pa.Field(ge=0, le=23)
        merchant_risk_score: Series[float] = pa.Field(ge=0.0, le=1.0)
        customer_history_score: Series[float] = pa.Field(ge=0.0, le=1.0)
        chargeback_count: Series[int] = pa.Field(ge=0)
        account_age_days: Series[int] = pa.Field(ge=0)
        country_code: Series[str] = pa.Field()
        merchant_category: Series[str] = pa.Field()
        device_type: Series[str] = pa.Field()

        @pa.check("country_code")
        def validate_country_code(self, series: Series[str]) -> Series[bool]:  # noqa: N805
            """Ensure country code conforms to ISO 2-letter standard."""
            result: Series[bool] = series.str.len() == 2  # type: ignore[assignment]
            return result

else:

    class TransactionSchema:  # type: ignore[no-redef]
        """Fallback empty schema model when Pandera is not installed."""

        @classmethod
        def validate(cls, df: pd.DataFrame) -> pd.DataFrame:
            """Fallback no-op validate method."""
            return df


class DataValidatorService:
    """Orchestrates Pandera schema checks, Zero-Raw-PII gating, and Great Expectations statistical tests."""

    # Allowed categorical values for device_type validation
    ALLOWED_DEVICES = ["mobile_app", "web_browser", "pos_terminal", "atm", "phone_banking"]

    # Forbidden cleartext PII identifiers (Zero Raw PII policy under GDPR Art. 25/32 & KVKK)
    FORBIDDEN_PII_TERMS = frozenset(
        {"iban", "ssn", "tckn", "pan", "cardnumber", "creditcard", "nationalid", "cvv"}
    )

    # Maximum quarantined batches retained per bank to prevent memory exhaustion (DoS defense)
    MAX_QUARANTINE_PER_BANK = 100

    def __init__(self, alert_service: Any = None) -> None:
        self.alert_service = alert_service
        self._quarantine_store: dict[str, list[pd.DataFrame]] = {}

    def validate_streaming_batch(self, df: pd.DataFrame, bank_id: str) -> pd.DataFrame:
        """Validate an incoming streaming transaction batch using Pandera and Zero-PII gating.

        If validation fails or forbidden cleartext PII is detected, the batch is quarantined,
        a system alert is triggered, and an exception is raised to abort ingestion.
        """
        # 1. Pre-flight Zero-Raw-PII Invariant Inspection
        detected_pii_cols = [
            c
            for c in df.columns
            if any(
                term in c.lower().replace("_", "").replace("-", "")
                for term in self.FORBIDDEN_PII_TERMS
            )
        ]
        if detected_pii_cols:
            error_msg = f"Zero Raw PII violation: forbidden cleartext PII column(s) detected: {', '.join(detected_pii_cols)}"
            self._quarantine_batch(df, bank_id, error_msg)
            raise DataContractValidationError(
                f"Streaming batch validation failed for bank {bank_id}: {error_msg}"
            )

        if not HAS_PANDERA or pa is None:
            logger.warning(
                "Pandera is not installed. Using Pandas fallback schema validation for bank %s.",
                bank_id,
            )
            # Lightweight Pandas fallback checks
            reasons = []
            if "transaction_amount" in df and (df["transaction_amount"] <= 0.0).any():
                reasons.append("transaction_amount must be positive (> 0)")
            if "country_code" in df and (df["country_code"].astype(str).str.len() != 2).any():
                reasons.append("country_code must be ISO 2-letter format")
            if reasons:
                error_msg = "; ".join(reasons)
                self._quarantine_batch(
                    df, bank_id, f"Pandera Schema Validation Failure: {error_msg}"
                )
                raise DataContractValidationError(
                    f"Pandera schema validation failed for bank {bank_id}: {error_msg}"
                )
            return df

        try:
            validated_df = TransactionSchema.validate(df)
            return validated_df
        except SchemaError as exc:
            logger.error(
                "Streaming batch validation failed for bank %s: %s. Quarantining batch.",
                bank_id,
                exc,
            )
            self._quarantine_batch(df, bank_id, "Pandera Schema Validation Failure")
            raise DataContractValidationError(
                f"Pandera schema validation failed for bank {bank_id}: {exc}"
            ) from exc

    def gate_data_contract(
        self,
        df: pd.DataFrame,
        bank_id: str,
        amount_mean_min: float = 10.0,
        amount_mean_max: float = 1000.0,
        simulation_id: str | None = None,
    ) -> None:
        """Run Great Expectations checks on bank data prior to model training.

        Uses GE 1.x ephemeral context and ValidationDefinition API.
        Deterministic resource keys derived from (purpose, simulation_id, bank_id)
        guarantee complete collision safety under concurrent multi-bank or multi-simulation execution.

        Verifies statistical properties:
        1. Null value ratios are 0 on critical numeric columns.
        2. Average transaction amount stays within historical confidence limits.
        3. Allowed category distributions are valid.

        If a check fails, triggers alerts and raises DataContractValidationError to halt training.
        """
        if (
            not HAS_GREAT_EXPECTATIONS
            or ge is None
            or gxe is None
            or ExpectationSuite is None
            or ValidationDefinition is None
        ):
            logger.warning(
                "Great Expectations is not installed. Using Pandas fallback statistical contract gating for bank %s.",
                bank_id,
            )
            # Lightweight Pandas fallback statistical contract checks
            reasons = []
            if "transaction_amount" in df and df["transaction_amount"].isnull().any():
                reasons.append(
                    "Expectation 'ExpectColumnValuesToNotBeNull' on column 'transaction_amount' failed."
                )
            if "velocity" in df and df["velocity"].isnull().any():
                reasons.append(
                    "Expectation 'ExpectColumnValuesToNotBeNull' on column 'velocity' failed."
                )
            if "transaction_amount" in df and not df["transaction_amount"].isnull().all():
                mean_val = float(df["transaction_amount"].mean())
                if mean_val < amount_mean_min or mean_val > amount_mean_max:
                    reasons.append(
                        f"Expectation 'ExpectColumnMeanToBeBetween' on column 'transaction_amount' failed (mean={mean_val:.2f})."
                    )
            if "device_type" in df:
                invalid_devices = set(df["device_type"].dropna()) - set(self.ALLOWED_DEVICES)
                if invalid_devices:
                    reasons.append(
                        f"Expectation 'ExpectColumnValuesToBeInSet' on column 'device_type' failed (invalid={invalid_devices})."
                    )

            if reasons:
                error_msg = "; ".join(reasons)
                self._quarantine_batch(
                    df, bank_id, f"Great Expectations Contract Failure: {error_msg}"
                )
                raise DataContractValidationError(
                    f"Great Expectations contract validation failed for bank {bank_id}: {error_msg}"
                )
            return

        sim_key = simulation_id or "standalone"
        purpose = "ingestion"
        ds_name = f"ds_{purpose}_{sim_key}_{bank_id}"
        asset_name = f"asset_{purpose}_{sim_key}_{bank_id}"
        bd_name = f"bd_{purpose}_{sim_key}_{bank_id}"
        suite_name = f"contract_{purpose}_{sim_key}_{bank_id}"
        val_name = f"val_{purpose}_{sim_key}_{bank_id}"

        # Atomic isolated registration scoped to this exact (purpose, simulation_id, bank_id)
        with _ge_registration_lock:
            context = ge.get_context(mode="ephemeral")
            _gx_thread_local.project = context

            ds = context.data_sources.add_pandas(ds_name)
            asset = ds.add_dataframe_asset(asset_name)
            bd = asset.add_batch_definition_whole_dataframe(bd_name)

            suite = ExpectationSuite(name=suite_name)
            suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column="transaction_amount"))
            suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column="velocity"))
            suite.add_expectation(
                gxe.ExpectColumnMeanToBeBetween(
                    column="transaction_amount",
                    min_value=amount_mean_min,
                    max_value=amount_mean_max,
                )
            )
            suite.add_expectation(
                gxe.ExpectColumnValuesToBeInSet(
                    column="device_type", value_set=self.ALLOWED_DEVICES
                )
            )
            suite = context.suites.add(suite)

            val_def = ValidationDefinition(name=val_name, data=bd, suite=suite)
            val_def = context.validation_definitions.add(val_def)

        # Run validation outside lock to allow true concurrent evaluation across simulations/banks
        try:
            result = val_def.run(batch_parameters={"dataframe": df})
        finally:
            with _ge_registration_lock:
                with contextlib.suppress(Exception):
                    context.validation_definitions.delete(val_name)
                with contextlib.suppress(Exception):
                    context.suites.delete(suite_name)
                with contextlib.suppress(Exception):
                    context.data_sources.delete(ds_name)
            if getattr(_gx_thread_local, "project", None) is context:
                _gx_thread_local.project = None

        if not result.success:
            failures = []
            for r in result.results:
                if not r.success:
                    exp_cfg = r.expectation_config
                    exp_type = exp_cfg.type if exp_cfg else "unknown"
                    exp_col = exp_cfg.kwargs.get("column", "?") if exp_cfg else "?"
                    failures.append(f"Expectation '{exp_type}' on column '{exp_col}' failed.")
            error_msg = "; ".join(failures)
            logger.critical(
                "Data Contract validation failed on bank %s node! Errors: %s. Quarantining dataset.",
                bank_id,
                error_msg,
            )
            self._quarantine_batch(df, bank_id, f"Great Expectations Contract Failure: {error_msg}")
            raise DataContractValidationError(
                f"Great Expectations contract validation failed for bank {bank_id}: {error_msg}"
            )

        logger.info(
            "Great Expectations data contract validation successfully passed for bank %s.",
            bank_id,
        )

    def _quarantine_batch(self, df: pd.DataFrame, bank_id: str, reason: str) -> None:
        """Quarantine a corrupted dataset batch with bounded memory and sanitized PII."""
        if bank_id not in self._quarantine_store:
            self._quarantine_store[bank_id] = []

        # Enforce bounded FIFO quarantine storage to prevent memory resource exhaustion (Vector 18)
        if len(self._quarantine_store[bank_id]) >= self.MAX_QUARANTINE_PER_BANK:
            self._quarantine_store[bank_id].pop(0)

        # Sanitize DataFrame copy before in-memory storage to prevent heap PII retention (Vector 15)
        safe_df = df.copy()
        for col in list(safe_df.columns):
            col_clean = col.lower().replace("_", "").replace("-", "")
            if any(term in col_clean for term in self.FORBIDDEN_PII_TERMS):
                safe_df[col] = "[REDACTED_QUARANTINE_PII]"

        self._quarantine_store[bank_id].append(safe_df)

        if self.alert_service:
            try:
                self.alert_service.create_system_alert(
                    title="Data Contract Compliance Alert",
                    description=f"Bank {bank_id} dataset quarantined. Reason: {reason}",
                    severity="CRITICAL",
                    metadata={"bank_id": bank_id, "row_count": len(df)},
                )
            except Exception as e:
                logger.warning("Failed to log alert for data contract quarantine: %s", e)
