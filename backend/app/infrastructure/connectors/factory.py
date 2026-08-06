"""Bank Connector Factory resolving connector types dynamically."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.infrastructure.connectors.batch_connector import BatchEODFileConnector
from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector
from app.infrastructure.connectors.kafka_connector import KafkaBankConnector
from app.infrastructure.connectors.open_banking_connector import OpenBankingConnector
from app.infrastructure.connectors.parquet_connector import ParquetConnector
from app.infrastructure.connectors.rabbitmq_connector import RabbitMQBankConnector
from app.infrastructure.connectors.redis_connector import RedisBankConnector
from app.infrastructure.connectors.rest_connector import RESTBankConnector
from app.infrastructure.connectors.streaming_connector import StreamingPaymentConnector

if TYPE_CHECKING:
    from app.application.interfaces.bank_connector import BankConnectorInterface
    from app.config import Settings

logger = logging.getLogger(__name__)

APPROVED_PRODUCTION_CONNECTORS = {
    "iso20022",
    "open_banking",
    "psd2",
    "kafka",
    "rabbitmq",
    "parquet",
    "rest",
    "redis",
    "streaming",
    "batch",
    "benchmark",
}

CONNECTOR_REGISTRY: dict[str, type] = {
    "iso20022": ISO20022MessagingConnector,
    "batch": BatchEODFileConnector,
    "redis": RedisBankConnector,
    "streaming": StreamingPaymentConnector,
    "open_banking": OpenBankingConnector,
    "psd2": OpenBankingConnector,
    "rabbitmq": RabbitMQBankConnector,
    "kafka": KafkaBankConnector,
    "parquet": ParquetConnector,
    "rest": RESTBankConnector,
}


def register_connector(connector_type: str) -> Callable:
    """Decorator for registering custom connector implementations dynamically."""

    def decorator(cls: type) -> type:
        CONNECTOR_REGISTRY[connector_type.lower()] = cls
        APPROVED_PRODUCTION_CONNECTORS.add(connector_type.lower())
        return cls

    return decorator


class BankConnectorFactory:
    """Factory creating concrete BankConnector instances based on configuration settings."""

    @staticmethod
    def get_connector(
        bank_id: str,
        settings: Settings,
        model_service: Any = None,
        data_generator: Any = None,
    ) -> BankConnectorInterface:
        """Resolve and instantiate a concrete connector implementation for the specified bank."""
        # Convert bank-a to bank_a to lookup attributes
        key = bank_id.replace("-", "_")

        connector_type = getattr(settings, f"{key}_connector_type", "parquet")
        auth_type = getattr(settings, f"{key}_auth_type", "none")
        api_key = getattr(settings, f"{key}_api_key", "")

        logger.info(
            "Resolving connector for %s: type=%s, auth=%s",
            bank_id,
            connector_type,
            auth_type,
        )

        app_env = os.getenv("APP_ENV", "development").lower()
        if app_env == "production" and connector_type not in APPROVED_PRODUCTION_CONNECTORS:
            raise ValueError(
                f"Unknown connector type: {connector_type}. Production connectors: ISO20022, OPEN_BANKING, KAFKA, RABBITMQ, PARQUET, REST"
            )

        if connector_type in ("mock", "mq_skeleton"):
            raise ValueError(
                f"Connector type '{connector_type}' is deprecated and removed under Enterprise Zero-Mock Policy. "
                "Supported connectors: ISO20022, OPEN_BANKING, KAFKA, RABBITMQ, PARQUET, REST"
            )

        if connector_type == "rest":
            base_url = settings.bank_urls.get(bank_id) or "http://localhost:8000"
            return RESTBankConnector(
                base_url=base_url,
                auth_type=auth_type,
                api_key=api_key,
                oauth_token_url=getattr(settings, "oauth_token_url", ""),
                client_cert_path=getattr(settings, "client_cert_path", ""),
                client_key_path=getattr(settings, "client_key_path", ""),
            )
        elif connector_type == "redis":
            redis_url = settings.redis_url or "redis://localhost:6379/0"
            return RedisBankConnector(redis_url=redis_url)
        elif connector_type == "streaming":
            return StreamingPaymentConnector(topic=f"payments.{bank_id}")
        elif connector_type == "iso20022":
            return ISO20022MessagingConnector()
        elif connector_type == "batch":
            return BatchEODFileConnector()
        elif connector_type in ("parquet", "benchmark"):
            dataset_path = f"storage/benchmark_datasets/{bank_id.replace('-', '_')}.parquet"
            return ParquetConnector(filepath=dataset_path)
        elif connector_type in ("open_banking", "psd2"):
            base_url = getattr(settings, "psd2_base_url", "https://sandbox.berlingroup.org/psd2/v1")
            return OpenBankingConnector(base_url=base_url, auth_type=auth_type, api_key=api_key)
        elif connector_type == "rabbitmq":
            return RabbitMQBankConnector(
                host=getattr(settings, "rabbitmq_host", "localhost"),
                port=getattr(settings, "rabbitmq_port", 5672),
                username=getattr(settings, "rabbitmq_user", "guest"),
                password=getattr(settings, "rabbitmq_password", "guest"),
            )
        elif connector_type == "kafka":
            return KafkaBankConnector(
                bootstrap_servers=getattr(settings, "kafka_bootstrap_servers", "localhost:9092"),
                topic_prefix=getattr(settings, "kafka_topic_prefix", "cfi.payments"),
                security_protocol=getattr(settings, "kafka_security_protocol", "SASL_SSL"),
                sasl_mechanism=getattr(settings, "kafka_sasl_mechanism", "SCRAM-SHA-256"),
            )
        else:
            raise ValueError(
                f"Unknown connector type: {connector_type}. Production connectors: ISO20022, OPEN_BANKING, KAFKA, RABBITMQ, PARQUET, REST"
            )
