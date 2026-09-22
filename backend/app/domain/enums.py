"""Domain enumerations.

Defines the bounded vocabulary for simulation states, bank tiers,
and aggregation strategies used throughout the system.
"""

from enum import StrEnum


class SimulationStatus(StrEnum):
    """Lifecycle states of a simulation run."""

    PENDING = "pending"
    INITIALIZING_CLIENTS = "initializing_clients"
    GENERATING_DATA = "generating_data"
    TRAINING_LOCAL = "training_local"
    TRAINING_FEDERATED = "training_federated"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class BankTier(StrEnum):
    """Bank size classification affecting data volume and distribution."""

    LARGE = "large"
    MEDIUM = "medium"
    SMALL = "small"


class ModelType(StrEnum):
    """Whether a model was trained locally or via federation."""

    LOCAL = "local"
    FEDERATED = "federated"


class AggregationMethod(StrEnum):
    """Supported federated aggregation strategies."""

    FED_AVG = "fed_avg"
    FED_AVG_WEIGHTED = "fed_avg_weighted"
    FED_PROX = "fed_prox"
    KRUM = "krum"
    COORDINATE_WISE_MEDIAN = "coordinate_wise_median"
    FED_ADAM = "fed_adam"
    FED_ADAGRAD = "fed_adagrad"
    FED_YOGI = "fed_yogi"
    TRIMMED_MEAN = "trimmed_mean"
    BULYAN = "bulyan"
    SCAFFOLD = "scaffold"


class ClientStatus(StrEnum):
    """Status of a bank client during a training round."""

    ACTIVE = "active"
    DROPPED = "dropped"
    RECONNECTED = "reconnected"
    OFFLINE = "offline"


class PrivacyMechanism(StrEnum):
    """Available privacy-enhancing mechanisms."""

    NONE = "none"
    DIFFERENTIAL_PRIVACY = "differential_privacy"
    SECURE_AGGREGATION = "secure_aggregation"
    BOTH = "both"


class AdversarialAttackType(StrEnum):
    """Supported adversarial evasion attack algorithms for robust training."""

    NONE = "none"
    FGSM = "fgsm"
    PGD = "pgd"


class DPMode(StrEnum):
    """Differential privacy implementation mode.

    POST_HOC: Simplified approach — clip weight delta and add noise after training.
    OPACUS: Industry-standard — per-sample gradient clipping and noise during training
            using Meta AI's Opacus library.
    """

    POST_HOC = "post_hoc"
    OPACUS = "opacus"


class FLEngineType(StrEnum):
    """Federated learning engine implementation.

    CUSTOM: Built-in simulation engine with full control over failure
            injection, Byzantine robustness, and real-time observability.
    FLOWER: Industry-standard Flower (flwr.dev) framework adapter using
            Ray-based simulation for standards-compliant FL execution.
    """

    CUSTOM = "custom"
    FLOWER = "flower"


# ── Phase 2: AML Intelligence Platform ────────


class AlertSeverity(StrEnum):
    """Severity classification for fraud alerts."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertStatus(StrEnum):
    """Lifecycle states of a fraud alert."""

    NEW = "new"
    INVESTIGATING = "investigating"
    CONFIRMED_FRAUD = "confirmed_fraud"
    FALSE_POSITIVE = "false_positive"
    ESCALATED = "escalated"
    CLOSED = "closed"


class CaseStatus(StrEnum):
    """Lifecycle states of an investigation case."""

    OPEN = "open"
    ASSIGNED = "assigned"
    INVESTIGATING = "investigating"
    PENDING_REVIEW = "pending_review"
    ESCALATED = "escalated"
    CLOSED_CONFIRMED = "closed_confirmed"
    CLOSED_FALSE_POSITIVE = "closed_false_positive"
    SAR_FILED = "sar_filed"


class CasePriority(StrEnum):
    """Priority classification for investigation cases."""

    P1_CRITICAL = "p1_critical"
    P2_HIGH = "p2_high"
    P3_MEDIUM = "p3_medium"
    P4_LOW = "p4_low"


class TriagePriority(StrEnum):
    """Priority classification for automated alert triage."""

    P1_CRITICAL = "p1_critical"
    P2_HIGH = "p2_high"
    P3_MEDIUM = "p3_medium"
    P4_LOW = "p4_low"


class TriageAction(StrEnum):
    """Recommended operational workflow action for triaged alerts."""

    ESCALATE_IMMEDIATE = "escalate_immediate"
    INVESTIGATE_CASE = "investigate_case"
    QUEUE_STANDARD = "queue_standard"
    AUTO_MONITOR = "auto_monitor"


class EntityType(StrEnum):
    """Types of entities in the financial crime graph."""

    CUSTOMER = "customer"
    MERCHANT = "merchant"
    DEVICE = "device"
    CARD = "card"
    EMAIL = "email"
    PHONE = "phone"
    IP_ADDRESS = "ip_address"


class RelationshipType(StrEnum):
    """Types of edges in the entity relationship graph."""

    OWNS = "owns"
    USES = "uses"
    TRANSACTS_WITH = "transacts_with"
    SHARES_DEVICE = "shares_device"
    SHARES_IP = "shares_ip"
    LINKED_ALERT = "linked_alert"
    SAME_ENTITY = "same_entity"


class RiskLevel(StrEnum):
    """Risk classification for entities and transactions."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    MINIMAL = "minimal"


class IntelligenceType(StrEnum):
    """Types of shared intelligence between institutions."""

    FRAUD_ALERT = "fraud_alert"
    PATTERN_MATCH = "pattern_match"
    VELOCITY_ANOMALY = "velocity_anomaly"
    ENTITY_LINK = "entity_link"
    RISK_SCORE_CHANGE = "risk_score_change"


class ScenarioType(StrEnum):
    """Pre-built fraud scenario types for simulation."""

    FRAUD_RING = "fraud_ring"
    ACCOUNT_TAKEOVER = "account_takeover"
    MONEY_LAUNDERING = "money_laundering"
    CARD_TESTING = "card_testing"


# ── Phase 5: Federated Graph Embedding ────────


class GNNArchitecture(StrEnum):
    """Graph Neural Network architecture for federated graph embedding."""

    GRAPHSAGE = "graphsage"


# ── Phase 36: Bank Onboarding ─────────────────


class BankStatus(StrEnum):
    """Enterprise status of a participating bank node."""

    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDED = "offboarded"


# ── Phase 106: Inter-Bank FININT Case Messaging ────────────────────────────────


class FinintTicketType(StrEnum):
    """Structured cross-institution FININT request type.

    Each type maps to a distinct compliance workflow and escalation path
    per European Collaborative FININT interchange standards.
    """

    URGENT_FREEZE_REQUEST = "URGENT_FREEZE_REQUEST"
    MULE_ACCOUNT_ALERT = "MULE_ACCOUNT_ALERT"
    INFORMATION_REQUEST = "INFORMATION_REQUEST"
    TRANSACTION_DISPUTE_TRACE = "TRANSACTION_DISPUTE_TRACE"


class FinintTicketStatus(StrEnum):
    """Immutable lifecycle states of an inter-bank FININT ticket.

    Transitions are strictly ordered and cannot regress:
    OPEN → ACKNOWLEDGED → FUNDS_FROZEN / INFORMATION_ATTACHED / DECLINED → CLOSED
    """

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FUNDS_FROZEN = "FUNDS_FROZEN"
    INFORMATION_ATTACHED = "INFORMATION_ATTACHED"
    DECLINED = "DECLINED"
    CLOSED = "CLOSED"


# ── Phase 107: SEPA Instant Payment Recall (ISO 20022) ────────────────────────


class RecallReasonCode(StrEnum):
    """ISO 20022 payment cancellation reason codes used in camt.056 messages.

    Codes defined by the EPC SEPA Instant Credit Transfer (SCT Inst) rulebook
    and the ISO 20022 External Code Sets (ExternalCancellationReason1Code).
    """

    FRAD = "FRAD"   # Fraudulent Origination — highest priority, 4h SLA
    TECH = "TECH"   # Technical Problem (duplicate, encoding error)
    DUPL = "DUPL"   # Duplicate Transfer — accidental re-submission
    CUST = "CUST"   # Requested by Originating Customer
    UPAY = "UPAY"   # Undue Payment — erroneous beneficiary / amount
    COVR = "COVR"   # Cover Payment recall


class RecallMessageType(StrEnum):
    """ISO 20022 message types involved in the SEPA recall workflow."""

    CAMT_056 = "camt.056.001.08"   # FIToFIPaymentCancellationRequest
    PACS_004 = "pacs.004.001.09"   # PaymentReturn (positive recall)
    CAMT_029 = "camt.029.001.09"   # ResolutionOfInvestigation (negative/partial)
    PACS_008 = "pacs.008.001.08"   # Original credit transfer (source)


class RecallStatus(StrEnum):
    """Lifecycle states of a SEPA payment recall case.

    State machine:
    INITIATED → SENT → ACKNOWLEDGED_BY_CREDITOR_AGENT →
        FUNDS_RETURNED (pacs.004) | UNABLE_TO_RECALL (camt.029 - NOAS/NOOR)
    Also: PROVISIONAL_HOLD_ACTIVE during pending resolution.
    """

    INITIATED = "INITIATED"
    SENT = "SENT"
    ACKNOWLEDGED_BY_CREDITOR_AGENT = "ACKNOWLEDGED_BY_CREDITOR_AGENT"
    PROVISIONAL_HOLD_ACTIVE = "PROVISIONAL_HOLD_ACTIVE"
    FUNDS_RETURNED = "FUNDS_RETURNED"
    UNABLE_TO_RECALL = "UNABLE_TO_RECALL"
    PARTIALLY_RETURNED = "PARTIALLY_RETURNED"
    CANCELLED = "CANCELLED"


class ResolutionCode(StrEnum):
    """camt.029 ResolutionOfInvestigation reason codes (negative recall outcomes).

    Source: ISO 20022 ExternalInvestigationExecutionConfirmation1Code.
    """

    NOAS = "NOAS"   # No Answer from beneficiary / account frozen
    NOOR = "NOOR"   # No Original Transaction Received (unrecognised)
    LEGL = "LEGL"   # Legal proceedings initiated — funds frozen by court
    CUST = "CUST"   # Beneficiary customer disputed the recall
    AGNT = "AGNT"   # Agent-level technical reason (routing error)


# ── Phase 108: Real-Time Sanctions & PEP Screening ────────────────────────────


class WatchlistSource(StrEnum):
    """Multi-jurisdiction sanctions and PEP watchlist sources.

    Sources are loaded at screening time. Real production deployments connect
    to live OFAC SDN, EU Consolidated, and UN Security Council feeds.
    """

    EU_CONSOLIDATED = "EU_CONSOLIDATED"   # EU Council Consolidated Sanctions List
    UN_SECURITY_COUNCIL = "UN_SECURITY_COUNCIL"  # UN SC Consolidated List (1267/1989)
    OFAC_SDN = "OFAC_SDN"                 # US OFAC Specially Designated Nationals
    HM_TREASURY = "HM_TREASURY"           # UK HMT Financial Sanctions
    PEP_GLOBAL = "PEP_GLOBAL"             # Politically Exposed Persons — global aggregated
    INTERNAL_GOODLIST = "INTERNAL_GOODLIST"  # Institution-level false-positive suppression list


class MatchAlgorithm(StrEnum):
    """Algorithms used for name matching during sanctions/PEP screening."""

    EXACT = "EXACT"                    # Exact string equality (after normalisation)
    LEVENSHTEIN = "LEVENSHTEIN"        # Edit distance — catches typos, transpositions
    JARO_WINKLER = "JARO_WINKLER"      # Similarity weighted for common prefixes
    DOUBLE_METAPHONE = "DOUBLE_METAPHONE"  # Phonetic — cross-language sound equivalence
    TRANSLITERATION = "TRANSLITERATION"   # Cyrillic / Arabic / Chinese → Latin script


class ScreeningEntityType(StrEnum):
    """Type of entity being screened."""

    INDIVIDUAL = "INDIVIDUAL"
    LEGAL_ENTITY = "LEGAL_ENTITY"
    VESSEL = "VESSEL"
    AIRCRAFT = "AIRCRAFT"


class ScreeningStatus(StrEnum):
    """Lifecycle status of a screening request."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MatchDisposition(StrEnum):
    """Compliance officer disposition for a screening hit.

    True positive → block / escalate.
    False positive → goodlist the entity.
    Pending → awaiting analyst review.
    """

    PENDING_REVIEW = "PENDING_REVIEW"
    CONFIRMED_MATCH = "CONFIRMED_MATCH"    # True positive — transaction blocked
    FALSE_POSITIVE = "FALSE_POSITIVE"      # Goodlisted — suppressed in future screens
    ESCALATED = "ESCALATED"               # Referred to senior compliance / FIU


# ── European FIU & UNODC goAML / AMLA Regulatory Reporting Enums ──────────────

class RegulatoryReportType(StrEnum):
    """European FIU & UNODC goAML regulatory report types."""

    STR = "STR"  # Suspicious Transaction Report
    SAR = "SAR"  # Suspicious Activity Report
    TTR = "TTR"  # Threshold Transaction Report (e.g. Cash >= €10,000)
    AIF = "AIF"  # Additional Information File / Follow-up report


class RegulatoryReportStatus(StrEnum):
    """Lifecycle status of a regulatory report undergoing supervisory approval and filing."""

    DRAFT = "DRAFT"
    PENDING_SUPERVISORY_APPROVAL = "PENDING_SUPERVISORY_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TRANSMITTED = "TRANSMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class LegalBasisType(StrEnum):
    """European AML & GDPR legal basis justifying cross-border financial intelligence filing."""

    AMLD6_ART_33 = "AMLD6_ART_33"                # Directive (EU) 2018/1673 & 2015/849 Art 33 Mandatory FIU Filing
    AMLA_RULEBOOK_ART_51 = "AMLA_RULEBOOK_ART_51"  # EU AMLA Single Rulebook Harmonised Reporting Obligation
    GDPR_ART_6_1_F = "GDPR_ART_6_1_F"            # GDPR Art. 6(1)(f) Legitimate Interest for Crime Detection
    GDPR_ART_9_2_G = "GDPR_ART_9_2_G"            # GDPR Art. 9(2)(g) Substantial Public Interest
    FATF_REC_20 = "FATF_REC_20"                  # FATF Recommendation 20 Suspicious Transaction Reporting


class ReportingEntityRole(StrEnum):
    """Financial institution role submitting regulatory filings to European FIUs."""

    CREDIT_INSTITUTION = "CREDIT_INSTITUTION"
    PAYMENT_INSTITUTION = "PAYMENT_INSTITUTION"
    ELECTRONIC_MONEY_INSTITUTION = "ELECTRONIC_MONEY_INSTITUTION"
    VIRTUAL_ASSET_SERVICE_PROVIDER = "VIRTUAL_ASSET_SERVICE_PROVIDER"


class RegulatorySubmissionFormat(StrEnum):
    """Standardized transmission format for regulatory submission."""

    GOAML_XML_4_0 = "GOAML_XML_4_0"              # UNODC goAML v4.0 XML Standard
    AMLA_JSON_SCHEMA = "AMLA_JSON_SCHEMA"        # EU AMLA Single Rulebook Interchange JSON
    FIU_ENCRYPTED_ARCHIVE = "FIU_ENCRYPTED_ARCHIVE"  # Encrypted Digital Envelope Archive

