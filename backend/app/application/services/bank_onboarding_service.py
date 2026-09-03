"""Bank Onboarding Service — Phase 36.1.

Manages automated registration, mTLS certificate issuance, database schema
provisioning, Vault transit key mapping, and connector configuration generation
for participating bank nodes.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from app.config import get_settings
from app.domain.entities import BankRegistration
from app.domain.enums import BankStatus
from app.infrastructure.database import init_tenant_tables
from app.infrastructure.models import TenantConfigModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class BankAlreadyExistsError(ValueError):
    """Raised when attempting to register a bank_id that already exists."""

    pass


class BankOnboardingService:
    """Automates bank node onboarding pipeline."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def register_bank(
        self,
        bank_id: str,
        legal_name: str,
        jurisdiction: str,
        contact_email: str,
        data_residency_region: str,
    ) -> BankRegistration:
        """Register a new bank node in PENDING_VERIFICATION state.

        Raises:
            BankAlreadyExistsError: If bank_id is already registered.
            ValueError: If bank_id format is invalid.
        """
        if not re.match(r"^[a-zA-Z0-9_-]{3,36}$", bank_id):
            raise ValueError(
                f"Invalid bank_id {bank_id!r}. Must be 3-36 alphanumeric characters, hyphens, or underscores."
            )

        # Check existing
        result = await self.session.execute(
            select(TenantConfigModel).where(TenantConfigModel.bank_id == bank_id)
        )
        if result.scalar_one_or_none() is not None:
            raise BankAlreadyExistsError(f"Bank with ID {bank_id!r} is already registered.")

        model = TenantConfigModel(
            bank_id=bank_id,
            legal_name=legal_name,
            jurisdiction=jurisdiction,
            contact_email=contact_email,
            data_residency_region=data_residency_region,
            status=BankStatus.PENDING_VERIFICATION,
            schema_provisioned=False,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)

        logger.info("Registered bank node bank_id=%s legal_name=%r", bank_id, legal_name)
        return self._to_entity(model)

    async def issue_mtls_certificate(self, bank_id: str) -> tuple[str, str]:
        """Issue real X.509 mTLS client certificate and private key PEM pair for a bank node.

        Returns:
            tuple[str, str]: (cert_pem, key_pem)
        """
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        from app.infrastructure.security.cert_generator import generate_self_signed_pem

        common_name = f"{bank_id.lower()}.client.cf-intelligence.io"
        cert_pem, key_pem = generate_self_signed_pem(common_name=common_name, days_valid=365)

        # Parse generated X.509 certificate to extract exact SHA-256 fingerprint and expiration
        x509_cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        fingerprint = f"SHA256:{x509_cert.fingerprint(hashes.SHA256()).hex()}"
        expires_at = x509_cert.not_valid_after_utc

        await self.session.execute(
            update(TenantConfigModel)
            .where(TenantConfigModel.bank_id == bank_id)
            .values(cert_fingerprint=fingerprint, cert_expires_at=expires_at)
        )
        await self.session.commit()

        # Authoritatively bind certificate fingerprint in servicer registry at onboarding time
        from app.infrastructure.grpc.servicer import register_bank_fingerprint

        register_bank_fingerprint(bank_id, fingerprint)

        logger.info(
            "Issued real X.509 mTLS cert for bank_id=%s fingerprint=%s",
            bank_id,
            fingerprint[:16],
        )
        return cert_pem, key_pem

    async def provision_tenant_schema(self, bank_id: str) -> None:
        """Provision schema tables for the bank tenant."""
        await init_tenant_tables(bank_id)
        await self.session.execute(
            update(TenantConfigModel)
            .where(TenantConfigModel.bank_id == bank_id)
            .values(schema_provisioned=True)
        )
        await self.session.commit()
        logger.info("Provisioned tenant schema for bank_id=%s", bank_id)

    async def provision_kms_key(self, bank_id: str) -> None:
        """Assign Vault transit KMS key path for tenant data encryption."""
        key_path = f"transit/keys/tenant_{bank_id}"
        await self.session.execute(
            update(TenantConfigModel)
            .where(TenantConfigModel.bank_id == bank_id)
            .values(vault_key_path=key_path)
        )
        await self.session.commit()
        logger.info("Assigned KMS key path for bank_id=%s: %s", bank_id, key_path)

    def generate_connector_config(self, bank_id: str) -> str:
        """Render connector configuration YAML for the bank client daemon."""
        coordinator_url = getattr(
            self.settings, "fl_coordinator_url", "https://coordinator.cf-intelligence.io"
        )
        yaml_config = f"""# CF-Intelligence Bank Client Connector Configuration
bank_id: "{bank_id}"
coordinator_url: "{coordinator_url}"
cert_path: "/etc/cfi/certs/{bank_id}.crt"
key_path: "/etc/cfi/certs/{bank_id}.key"
ca_cert_path: "/etc/cfi/certs/ca.crt"
connector_type: "PARQUET"
batch_size: 1000
dp_epsilon: 0.5
clip_norm: 1.0
health_port: 8080
"""
        return yaml_config

    async def activate_bank(self, bank_id: str) -> BankRegistration | None:
        """Activate bank node registration."""
        await self.session.execute(
            update(TenantConfigModel)
            .where(TenantConfigModel.bank_id == bank_id)
            .values(status=BankStatus.ACTIVE, activated_at=datetime.now(UTC))
        )
        await self.session.commit()
        logger.info("Activated bank node bank_id=%s", bank_id)
        return await self.get_bank(bank_id)

    async def get_bank(self, bank_id: str) -> BankRegistration | None:
        """Fetch bank registration by ID."""
        result = await self.session.execute(
            select(TenantConfigModel).where(TenantConfigModel.bank_id == bank_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_banks(self) -> list[BankRegistration]:
        """List all registered bank nodes."""
        result = await self.session.execute(
            select(TenantConfigModel).order_by(TenantConfigModel.created_at.desc())
        )
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    @staticmethod
    def _to_entity(model: TenantConfigModel) -> BankRegistration:
        return BankRegistration(
            bank_id=model.bank_id,
            legal_name=model.legal_name,
            jurisdiction=model.jurisdiction,
            contact_email=model.contact_email,
            data_residency_region=model.data_residency_region,
            status=model.status,
            cert_fingerprint=model.cert_fingerprint,
            vault_key_path=model.vault_key_path,
            schema_provisioned=model.schema_provisioned,
            created_at=model.created_at,
            activated_at=model.activated_at,
        )
