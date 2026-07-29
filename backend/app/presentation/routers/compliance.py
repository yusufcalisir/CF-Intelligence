"""Compliance & Security Audit API Router."""

from __future__ import annotations

from fastapi import APIRouter
from app.application.services.security_compliance import SecurityComplianceEngine

router = APIRouter(prefix="/v1/compliance", tags=["Security & Compliance"])
compliance_engine = SecurityComplianceEngine()


@router.get("/soc2-evidence")
@router.post("/soc2-evidence")
def get_soc2_evidence_report():
    """Generates an automated SOC 2 Type II evidence collection report."""
    return compliance_engine.generate_soc2_evidence_report()
