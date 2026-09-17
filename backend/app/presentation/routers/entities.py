"""Entity resolution, Diffie-Hellman Private Set Intersection (DH-PSI), and LSH API endpoints.

Implements cross-bank entity lookups, 2048-bit commutative Diffie-Hellman PSI,
MinHash LSH fuzzy identity linkage, type-salted HMAC tokenization, and GDPR right-to-erasure.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.application.schemas.entities import (
    EntityDeleteResponse,
    EntityFuzzyResolveMatch,
    EntityFuzzyResolveRequest,
    EntityFuzzyResolveResponse,
    EntityProfileResponse,
    EntityRelationshipItem,
    EntityResolveRequest,
    EntityResponse,
    HMACTokenizeRequest,
    HMACTokenizeResponse,
    PSIMatchDirectRequest,
    PSIMatchDirectResponse,
    PSIRequest,
    PSIResponse,
    PSIStatsResponse,
)
from app.application.services.case_service import AuditService
from app.application.services.entity_resolution import EntityResolutionService
from app.application.services.psi_service import PSIService
from app.dependencies import TenantDep, enforce_tenant_isolation
from app.domain.enums import EntityType, RiskLevel

logger = logging.getLogger(__name__)

# Dual routing for /api/v1/entities and /v1/entities
router = APIRouter(prefix="/api/v1/entities", tags=["entities"])
api_router = APIRouter(prefix="/v1/entities", tags=["entities"])

# Dual routing for /api/v1/psi and /v1/psi
psi_router = APIRouter(prefix="/api/v1/psi", tags=["psi"])
psi_api_router = APIRouter(prefix="/v1/psi", tags=["psi"])

_entity_service = EntityResolutionService()
_psi_service = PSIService(_entity_service)


def get_entity_service() -> EntityResolutionService:
    """Retrieve singleton EntityResolutionService instance."""
    return _entity_service


def get_psi_service() -> PSIService:
    """Retrieve singleton PSIService instance."""
    return _psi_service


# ── Entity Listing and Search ───────────────────────────────────────────────

@router.get("", response_model=list[EntityResponse], status_code=status.HTTP_200_OK)
@api_router.get("", response_model=list[EntityResponse], status_code=status.HTTP_200_OK)
async def list_entities(
    entity_type: str | None = Query(None, description="Filter by entity type"),
    bank_id: str | None = Query(None, description="Filter by bank ID"),
    risk_level: str | None = Query(None, description="Filter by risk level"),
    limit: int = Query(50, ge=1, le=200, description="Page limit"),
    caller_tenant: TenantDep = None,
) -> list[EntityResponse]:
    """List entities with optional filters and tenant isolation checks."""
    if caller_tenant and bank_id:
        enforce_tenant_isolation(caller_tenant, bank_id)
    effective_bank_id = bank_id or caller_tenant

    et = None
    if entity_type:
        try:
            et = EntityType(entity_type.lower())
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid entity_type: '{entity_type}'. Allowed: {[e.value for e in EntityType]}",
            ) from err

    rl = None
    if risk_level:
        try:
            rl = RiskLevel(risk_level.lower())
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid risk_level: '{risk_level}'. Allowed: {[r.value for r in RiskLevel]}",
            ) from err

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


# ── Entity Profile & GDPR Erasure ──────────────────────────────────────────

@router.get("/{entity_id}", response_model=EntityProfileResponse, status_code=status.HTTP_200_OK)
@api_router.get("/{entity_id}", response_model=EntityProfileResponse, status_code=status.HTTP_200_OK)
async def get_entity_profile(
    entity_id: str,
    actor: str = Query("analyst", description="Audited actor initiating profile query"),
    caller_tenant: TenantDep = None,
) -> EntityProfileResponse:
    """Get entity profile with cross-institution intelligence and tenant check."""
    if not entity_id or not entity_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="entity_id cannot be blank")

    entity = _entity_service.get_entity(entity_id.strip())
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Entity '{entity_id}' not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, entity.bank_id)

    try:
        profile = _entity_service.build_entity_profile(entity_id.strip())
        AuditService().log_action(actor, "query_entity", entity_id.strip())
        return EntityProfileResponse(**profile)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Entity '{entity_id}' not found") from err


@router.delete("/{entity_id}", response_model=EntityDeleteResponse, status_code=status.HTTP_200_OK)
@api_router.delete("/{entity_id}", response_model=EntityDeleteResponse, status_code=status.HTTP_200_OK)
async def delete_entity_endpoint(
    entity_id: str,
    actor: str = Query("analyst", description="Audited actor executing erasure"),
    caller_tenant: TenantDep = None,
) -> EntityDeleteResponse:
    """Purge an entity and remove it from LSH indices (GDPR Art. 17 right-to-erasure)."""
    if not entity_id or not entity_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="entity_id cannot be blank")

    entity = _entity_service.get_entity(entity_id.strip())
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Entity '{entity_id}' not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, entity.bank_id)

    deleted = _entity_service.delete_entity(entity_id.strip())
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Entity '{entity_id}' not found")

    AuditService().log_action(actor, "gdpr_delete_entity", entity_id.strip())
    return EntityDeleteResponse(
        deleted=True,
        entity_id=entity_id.strip(),
        policy="GDPR Art. 17 Right-to-Erasure Enforced",
    )


@router.get("/{entity_id}/relationships", response_model=list[EntityRelationshipItem], status_code=status.HTTP_200_OK)
@api_router.get("/{entity_id}/relationships", response_model=list[EntityRelationshipItem], status_code=status.HTTP_200_OK)
async def get_entity_relationships(
    entity_id: str,
    caller_tenant: TenantDep = None,
) -> list[EntityRelationshipItem]:
    """Get direct graph relationships for an entity with tenant check."""
    if not entity_id or not entity_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="entity_id cannot be blank")

    entity = _entity_service.get_entity(entity_id.strip())
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Entity '{entity_id}' not found")

    if caller_tenant:
        enforce_tenant_isolation(caller_tenant, entity.bank_id)

    rels = [
        r
        for r in _entity_service.get_relationships()
        if r.source_entity_id == entity_id.strip() or r.target_entity_id == entity_id.strip()
    ]
    return [
        EntityRelationshipItem(
            id=r.id,
            source_entity_id=r.source_entity_id,
            target_entity_id=r.target_entity_id,
            relationship_type=r.relationship_type.value,
            confidence=r.confidence,
            evidence=json.dumps(r.evidence) if isinstance(r.evidence, (list, dict)) else str(r.evidence or ""),
            created_at=r.created_at.isoformat(),
        )
        for r in rels
    ]


# ── Entity Resolution & HMAC Tokenization ───────────────────────────────────

@router.post("/resolve", response_model=list[EntityResponse], status_code=status.HTTP_200_OK)
@api_router.post("/resolve", response_model=list[EntityResponse], status_code=status.HTTP_200_OK)
async def resolve_entity(
    req: EntityResolveRequest,
    actor: str = Query("analyst", description="Audited actor performing resolution"),
) -> list[EntityResponse]:
    """Find entities matching a privacy hash across consortium bank institutions."""
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


@router.post("/fuzzy-resolve", response_model=EntityFuzzyResolveResponse, status_code=status.HTTP_200_OK)
@api_router.post("/fuzzy-resolve", response_model=EntityFuzzyResolveResponse, status_code=status.HTTP_200_OK)
async def run_fuzzy_resolve(
    req: EntityFuzzyResolveRequest,
    actor: str = Query("analyst", description="Audited actor performing fuzzy resolution"),
) -> EntityFuzzyResolveResponse:
    """Find entities matching a raw name fuzzily using MinHash LSH similarities."""
    et = EntityType.CUSTOMER
    if req.entity_type:
        try:
            et = EntityType(req.entity_type.lower())
        except ValueError:
            et = EntityType.CUSTOMER

    query_text = req.query_name or req.raw_identifier or ""
    if not query_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="query_name (or raw_identifier) must not be empty.",
        )

    thresh = req.threshold if req.threshold is not None else (req.similarity_threshold if req.similarity_threshold is not None else 0.70)

    matches = _entity_service.resolve_fuzzy_entities(
        query_name=query_text,
        entity_type=et,
        threshold=thresh,
        bank_id=req.bank_id,
        limit=req.limit,
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


@router.post("/hmac-tokenize", response_model=HMACTokenizeResponse, status_code=status.HTTP_200_OK)
@api_router.post("/hmac-tokenize", response_model=HMACTokenizeResponse, status_code=status.HTTP_200_OK)
async def tokenize_raw_identifier(
    payload: HMACTokenizeRequest | None = None,
    identifier: str | None = Query(None, description="Raw identifier string to tokenize"),
    tenant_salt: str = Query("default_consortium_salt", description="Salt string for type-salting"),
) -> HMACTokenizeResponse:
    """Tokenize raw identifier into tenant-salted HMAC token enforcing Zero Raw PII policy."""
    import hashlib
    import hmac

    raw_id = (payload.identifier if payload else None) or identifier
    salt = (payload.tenant_salt if payload else None) or tenant_salt

    if not raw_id or not raw_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="identifier must be provided as a non-empty string in request body or query parameter",
        )

    token = hmac.new(
        salt.encode("utf-8"), raw_id.strip().encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return HMACTokenizeResponse(
        hmac_token=token,
        policy="Zero Raw PII Policy Enforced",
        algorithm="HMAC-SHA256",
    )


# ── Private Set Intersection (DH-PSI) Endpoints under /entities ─────────────

@router.post("/psi", response_model=PSIResponse, status_code=status.HTTP_200_OK)
@api_router.post("/psi", response_model=PSIResponse, status_code=status.HTTP_200_OK)
async def run_entities_psi(
    req: PSIRequest,
    actor: str = Query("analyst", description="Audited actor initiating PSI protocol"),
) -> PSIResponse:
    """Run simulated Diffie-Hellman Private Set Intersection (PSI) protocol between two banks."""
    if not req.bank_a_id or not req.bank_b_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bank_a_id and bank_b_id must be provided",
        )

    et = None
    if req.entity_type:
        try:
            et = EntityType(req.entity_type.lower())
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid entity_type: '{req.entity_type}'",
            ) from err

    result = _psi_service.run_psi(
        req.bank_a_id,
        req.bank_b_id,
        entity_type=et,
        enable_tee=req.enable_tee,
        enable_fuzzy=req.enable_fuzzy,
        fuzzy_threshold=req.fuzzy_threshold,
    )
    AuditService().log_action(actor, "cross_bank_psi", f"{req.bank_a_id}<->{req.bank_b_id}")
    return PSIResponse(**result)


@router.post("/psi-match", response_model=PSIMatchDirectResponse, status_code=status.HTTP_200_OK)
@api_router.post("/psi-match", response_model=PSIMatchDirectResponse, status_code=status.HTTP_200_OK)
async def run_dh_psi_match(
    bank_a_id: str = Query(..., min_length=3, max_length=64, description="First bank participant ID"),
    bank_b_id: str = Query(..., min_length=3, max_length=64, description="Second bank participant ID"),
    enable_fuzzy: bool = Query(True, description="Enable fuzzy attribute intersection"),
) -> PSIMatchDirectResponse:
    """Execute Privacy-Preserving DH-PSI match between two bank entities without PII exposure."""
    if not bank_a_id or not bank_b_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bank_a_id and bank_b_id must be provided",
        )

    result = _psi_service.run_psi(
        bank_a_id=bank_a_id,
        bank_b_id=bank_b_id,
        enable_fuzzy=enable_fuzzy,
    )
    return PSIMatchDirectResponse(
        protocol="Commutative Diffie-Hellman (DH-PSI)",
        matched_cardinality=len(result.get("matches", [])),
        matches=result.get("matches", []),
        stats=result.get("stats", {}),
        zero_raw_pii_enforced=True,
    )


# ── Standalone Dedicated PSI Router (/api/v1/psi and /v1/psi) ───────────────

@psi_router.post("/match", response_model=PSIMatchDirectResponse, status_code=status.HTTP_200_OK)
@psi_api_router.post("/match", response_model=PSIMatchDirectResponse, status_code=status.HTTP_200_OK)
@psi_router.post("/dh", response_model=PSIMatchDirectResponse, status_code=status.HTTP_200_OK)
@psi_api_router.post("/dh", response_model=PSIMatchDirectResponse, status_code=status.HTTP_200_OK)
async def run_dh_psi_match_direct(
    payload: PSIMatchDirectRequest | None = None,
    bank_a_id: str | None = Query(None, description="Bank A participant ID (fallback)"),
    bank_b_id: str | None = Query(None, description="Bank B participant ID (fallback)"),
    enable_fuzzy: bool = Query(True, description="Fuzzy resolution flag"),
) -> PSIMatchDirectResponse:
    """Execute Privacy-Preserving DH-PSI match directly targeting /api/v1/psi/match and /psi/dh."""
    src = (payload.source_bank_id if payload else None) or bank_a_id or "bank_alpha"
    tgt = (payload.target_bank_id if payload else None) or bank_b_id or "bank_beta"
    fuzzy = payload.enable_fuzzy if payload else enable_fuzzy

    if not src or not tgt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_bank_id and target_bank_id must be provided",
        )

    client_hashes = payload.client_ecdh_blinded_hashes if payload else []
    if client_hashes:
        result = _psi_service.run_psi_direct(
            source_bank_id=src,
            target_bank_id=tgt,
            client_ecdh_blinded_hashes=client_hashes,
            enable_fuzzy=fuzzy,
        )
    else:
        result = _psi_service.run_psi(
            bank_a_id=src,
            bank_b_id=tgt,
            enable_fuzzy=fuzzy,
        )

    return PSIMatchDirectResponse(
        protocol="Commutative Diffie-Hellman (DH-PSI)",
        matched_cardinality=len(result.get("matches", [])),
        matches=result.get("matches", []),
        stats=result.get("stats", {}),
        zero_raw_pii_enforced=True,
    )


@psi_router.get("/stats", response_model=PSIStatsResponse, status_code=status.HTTP_200_OK)
@psi_api_router.get("/stats", response_model=PSIStatsResponse, status_code=status.HTTP_200_OK)
async def get_psi_protocol_stats() -> PSIStatsResponse:
    """Retrieve public cryptographic telemetry and configuration of the DH-PSI engine."""
    return PSIStatsResponse()


@psi_router.post("/handshake", response_model=dict[str, Any], status_code=status.HTTP_200_OK)
@psi_api_router.post("/handshake", response_model=dict[str, Any], status_code=status.HTTP_200_OK)
async def initiate_dh_psi_handshake(
    bank_id: str = Query("bank_alpha", description="Initiating bank participant ID"),
) -> dict[str, Any]:
    """Initiate cryptographic parameter handshake for Commutative Diffie-Hellman protocol."""
    from app.domain.psi_service import PRIME_BIT_LENGTH, PSI_PRIME

    if not bank_id or not bank_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bank_id cannot be blank")

    return {
        "bank_id": bank_id.strip(),
        "prime_bit_length": PRIME_BIT_LENGTH,
        "generator": 2,
        "prime_modulus_hex": hex(PSI_PRIME),
        "protocol": "Commutative Diffie-Hellman (DH-PSI 2048-bit)",
        "status": "ready_for_blinded_exchange",
    }
