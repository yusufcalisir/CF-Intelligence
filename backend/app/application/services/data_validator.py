"""Data Validation & Contract Gating Service.

Integrates Pandera for streaming schema validation and
Great Expectations (v1.x) for data contract statistical stability checks.
"""

from __future__ import annotations

import logging
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
    """Orchestrates Pandera schema checks and Great Expectations statistical tests."""

    # Allowed categorical values for device_type validation
    ALLOWED_DEVICES = ["mobile_app", "web_browser", "pos_terminal", "atm", "phone_banking"]

    def __init__(self, alert_service: Any = None) -> None:
        self.alert_service = alert_service
        self._quarantine_store: dict[str, list[pd.DataFrame]] = {}

    def validate_streaming_batch(self, df: pd.DataFrame, bank_id: str) -> pd.DataFrame:
        """Validate an incoming streaming transaction batch using Pandera.

        If validation fails, the batch is quarantined, a system alert is triggered,
        and an exception is raised to abort ingestion.
        """
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
    ) -> None:
        """Run Great Expectations checks on bank data prior to model training.

        Uses GE 1.x ephemeral context and ValidationDefinition API.

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

        context = ge.get_context(mode="ephemeral")

        # Register datasource, asset, and batch definition
        ds = context.data_sources.add_pandas(f"ds_{bank_id}")
        asset = ds.add_dataframe_asset(f"asset_{bank_id}")
        bd = asset.add_batch_definition_whole_dataframe(f"bd_{bank_id}")

        # Build expectation suite
        suite = ExpectationSuite(name=f"contract_{bank_id}")
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
            gxe.ExpectColumnValuesToBeInSet(column="device_type", value_set=self.ALLOWED_DEVICES)
        )
        suite = context.suites.add(suite)

        # Register and run validation
        val_def = ValidationDefinition(name=f"val_{bank_id}", data=bd, suite=suite)
        val_def = context.validation_definitions.add(val_def)
        result = val_def.run(batch_parameters={"dataframe": df})

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
        """Quarantine a corrupted dataset batch and trigger a security/system alert."""
        if bank_id not in self._quarantine_store:
            self._quarantine_store[bank_id] = []
        self._quarantine_store[bank_id].append(df.copy())

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
