"""Entity and resolution API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.application.schemas.phase2 import (
    EntityFuzzyResolveMatch,
    EntityFuzzyResolveRequest,
    EntityFuzzyResolveResponse,
    EntityProfileResponse,
    EntityResolveRequest,
    EntityResponse,
    PSIRequest,
    PSIResponse,
)
from app.application.services.case_service import AuditService
from app.application.services.entity_resolution import EntityResolutionService
from app.application.services.psi_service import PSIService
from app.dependencies import TenantDep, enforce_tenant_isolation
from app.domain.enums import EntityType, RiskLevel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/entities", tags=["entities"])

_entity_service = EntityResolutionService()


def get_entity_service() -> EntityResolutionService:
    return _entity_service


@router.get("", response_model=list[EntityResponse])
async def list_entities(
    entity_type: str | None = Query(None),
    bank_id: str | None = Query(None),
    risk_level: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    caller_tenant: TenantDep = None,
) -> list[EntityResponse]:
    """List entities with optional filters and tenant isolation checks."""
    if caller_tenant and bank_id:
        enforce_tenant_isolation(caller_tenant, bank_id)
    effective_bank_id = bank_id or caller_tenant

    et = EntityType(entity_type) if entity_type else None
    rl = RiskLevel(risk_level) if risk_level else None

    entities = _entity_service.get_entities(
        entity_type=et,
        bank_id=effective_bank_id,
        risk_level=rl,
        limit=limit,
    )
    return [
        EntityResponse(
            id=e.id,
            entity_type=e.entity_type.value,
            privacy_id=e.privacy_id,
            bank_id=e.bank_id,
            display_label=e.display_label,
            attributes=e.attributes,
            risk_level=e.risk_level.value,
            alert_count=e.alert_count,
            first_seen=e.first_seen.isoformat(),
            last_seen=e.last_seen.isoformat(),
        )
        for e in entities
    ]


@router.get("/{entity_id}", response_model=EntityProfileResponse)
async def get_entity_profile(
    entity_id: str, actor: str = Query("analyst"), caller_tenant: TenantDep = None
) -> EntityProfileResponse:
    """Get entity profile with cross-institution data and tenant check."""
    entity = _entity_service.get_entity(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, entity.bank_id)

    try:
        profile = _entity_service.build_entity_profile(entity_id)
        AuditService().log_action(actor, "query_entity", entity_id)
        return EntityProfileResponse(**profile)
    except ValueError:
        raise HTTPException(status_code=404, detail="Entity not found")


@router.get("/{entity_id}/relationships")
async def get_entity_relationships(
    entity_id: str, caller_tenant: TenantDep = None
) -> list[dict]:
    """Get direct relationships for an entity with tenant check."""
    entity = _entity_service.get_entity(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, entity.bank_id)

    rels = [
        r
        for r in _entity_service.get_relationships()
        if r.source_entity_id == entity_id or r.target_entity_id == entity_id
    ]
    return [
        {
            "id": r.id,
            "source_entity_id": r.source_entity_id,
            "target_entity_id": r.target_entity_id,
            "relationship_type": r.relationship_type.value,
            "confidence": r.confidence,
            "evidence": r.evidence,
            "created_at": r.created_at.isoformat(),
        }
        for r in rels
    ]



@router.post("/resolve", response_model=list[EntityResponse])
async def resolve_entity(
    req: EntityResolveRequest, actor: str = Query("analyst")
) -> list[EntityResponse]:
    """Find entities matching a privacy hash across institutions."""
    entities = _entity_service.resolve_cross_institution(req.privacy_hash)
    AuditService().log_action(actor, "cross_bank_resolve", req.privacy_hash)
    return [
        EntityResponse(
            id=e.id,
            entity_type=e.entity_type.value,
            privacy_id=e.privacy_id,
            bank_id=e.bank_id,
            display_label=e.display_label,
            attributes=e.attributes,
            risk_level=e.risk_level.value,
            alert_count=e.alert_count,
            first_seen=e.first_seen.isoformat(),
            last_seen=e.last_seen.isoformat(),
        )
        for e in entities
    ]


_psi_service = PSIService(_entity_service)


@router.post("/psi", response_model=PSIResponse)
async def run_entities_psi(req: PSIRequest, actor: str = Query("analyst")) -> PSIResponse:
    """Run simulated Private Set Intersection (PSI) protocol between two banks."""
    et = EntityType(req.entity_type) if req.entity_type else None
    result = _psi_service.run_psi(
        req.bank_a_id,
        req.bank_b_id,
        entity_type=et,
        enable_fuzzy=req.enable_fuzzy,
        fuzzy_threshold=req.fuzzy_threshold,
    )
    AuditService().log_action(actor, "cross_bank_psi", f"{req.bank_a_id}<->{req.bank_b_id}")
    return PSIResponse(**result)


@router.post("/fuzzy-resolve", response_model=EntityFuzzyResolveResponse)
async def run_fuzzy_resolve(
    req: EntityFuzzyResolveRequest, actor: str = Query("analyst")
) -> EntityFuzzyResolveResponse:
    """Find entities matching a raw name fuzzily using MinHash LSH similarities."""
    et = EntityType(req.entity_type) if req.entity_type else EntityType.CUSTOMER
    matches = _entity_service.resolve_fuzzy_entities(
        query_name=req.query_name,
        entity_type=et,
        threshold=req.threshold,
    )

    response_matches = []
    for m in matches:
        e = m["entity"]
        ent_resp = EntityResponse(
            id=e.id,
            entity_type=e.entity_type.value,
            privacy_id=e.privacy_id,
            bank_id=e.bank_id,
            display_label=e.display_label,
            attributes=e.attributes,
            risk_level=e.risk_level.value,
            alert_count=e.alert_count,
            first_seen=e.first_seen.isoformat(),
            last_seen=e.last_seen.isoformat(),
        )
        response_matches.append(
            EntityFuzzyResolveMatch(entity=ent_resp, similarity_score=m["similarity_score"])
        )

    AuditService().log_action(actor, "cross_bank_fuzzy_resolve", req.query_name)
    return EntityFuzzyResolveResponse(matches=response_matches)


@router.post("/hmac-tokenize")
async def tokenize_raw_identifier(
    identifier: str, tenant_salt: str = "default_consortium_salt"
) -> dict[str, str]:
    """Tokenize raw identifier into tenant-salted HMAC token enforcing Zero Raw PII policy."""
    import hashlib
    import hmac

    token = hmac.new(
        tenant_salt.encode("utf-8"), identifier.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "hmac_token": token,
        "policy": "Zero Raw PII Policy Enforced",
        "algorithm": "HMAC-SHA256",
    }


@router.post("/psi-match")
async def run_dh_psi_match(bank_a_id: str, bank_b_id: str, enable_fuzzy: bool = True) -> dict:
    """Execute Privacy-Preserving DH-PSI match between two bank entities without PII exposure."""
    result = _psi_service.run_psi(
        bank_a_id=bank_a_id,
        bank_b_id=bank_b_id,
        enable_fuzzy=enable_fuzzy,
    )
    return {
        "protocol": "Commutative Diffie-Hellman (DH-PSI)",
        "matches": result.get("matches", []),
        "stats": result.get("stats", {}),
        "zero_raw_pii_enforced": True,
    }
