# 🏛️ Federated Consortium Governance & Membership Protocol Specification

The Federated Consortium Governance engine ([`ConsortiumGovernanceService`](../backend/app/application/services/consortium_service.py)) and Policy Enforcement Engine ([`ConsortiumPolicyEngine`](../backend/app/domain/consortium_policy.py)) enable multi-bank alliances (e.g. European AML Network, Nordic Fraud Defense Consortium) to establish democratic, quorum-based governance rules and enforce technical invariants for collaborative fraud detection.

> [!NOTE]
> For security threat models, unlearning quarantined nodes, and multi-tenant access controls, refer to [`docs/security_controls_matrix.md`](security_controls_matrix.md), [`docs/threat_model.md`](threat_model.md), and [`backend/app/application/services/federated_unlearning_engine.py`](../backend/app/application/services/federated_unlearning_engine.py).

---

## 📌 1. Architectural Governance Flow & Quorum Mechanics

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        CONSORTIUM QUORUM GOVERNANCE LIFECYCLE                          │
│                                                                                        │
│   [ Founder Bank: bank_a ] ──► create_consortium(quorum=51%, max_epsilon=4.0)          │
│                                           │                                            │
│                                           ▼                                            │
│                                Status: `ACTIVE`                                        │
│                                Member: `bank_a` (FOUNDER)                              │
│                                           │                                            │
│                                           ▼ propose_membership_change()                │
│                                1. Proposal: `PENDING`                                  │
│                                   (Target: bank_b, Action: ADD_MEMBER)                 │
│                                           │                                            │
│                                           ├───► cast_vote(proposal_id, bank_b, True)   │
│                                           │                                            │
│                                           ▼ Evaluate Quorum: Votes >= K/N              │
│                               ┌───────────────────────┐                                │
│                               │ 2. Quorum Reached?    │                                │
│                               └───────────────────────┘                                │
│                                      │          │                                      │
│               ratio_for >= quorum    │          │ ratio_against > 1.0 - quorum         │
│                                      ▼          ▼                                      │
│                         ┌────────────────┐   ┌────────────────┐                        │
│                         │   `APPROVED`   │   │   `REJECTED`   │                        │
│                         │   (Automated   │   │   (Proposal    │                        │
│                         │    State Sync) │   │    Terminated) │                        │
│                         └────────────────┘   └────────────────┘                        │
│                                      │                                                 │
│                                      ▼                                                 │
│                      [ Member Activated: bank_b (FULL_MEMBER) ]                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Quorum Threshold Formulations

A governance proposal evaluates member votes dynamically across the consortium's active voting membership:

$$\mathrm{ratio}_{\mathrm{for}} = \frac{|\mathcal{V}_{\mathrm{for}}|}{N_{\mathrm{members}}}, \quad \mathrm{ratio}_{\mathrm{against}} = \frac{|\mathcal{V}_{\mathrm{against}}|}{N_{\mathrm{members}}}$$

where:
- $|\mathcal{V}_{\mathrm{for}}|$ is the affirmative vote tally.
- $|\mathcal{V}_{\mathrm{against}}|$ is the dissenting vote tally.
- $N_{\mathrm{members}}$ is the active voting member count.

Quorum threshold resolution follows two deterministic state transitions:

$$
\mathrm{Status} = \begin{cases}
\mathrm{APPROVED} & \text{if } \mathrm{ratio}_{\mathrm{for}} \ge \theta_{\mathrm{quorum}} \\
\mathrm{REJECTED} & \text{if } \mathrm{ratio}_{\mathrm{against}} > 1.0 - \theta_{\mathrm{quorum}} \\
\mathrm{PENDING} & \text{otherwise}
\end{cases}
$$

- **Approval Condition**: If $\mathrm{ratio}_{\mathrm{for}}$ meets or exceeds the required quorum ratio $\theta_{\mathrm{quorum}}$ (e.g. $0.51$ or $0.66$), the proposal transitions immediately to `APPROVED` and its action is executed.
- **Rejection Condition**: If $\mathrm{ratio}_{\mathrm{against}}$ exceeds the margin ($> 1.0 - \theta_{\mathrm{quorum}}$), reaching quorum is mathematically impossible; the proposal transitions immediately to `REJECTED`.

---

## ⚖️ 2. Member Roles & Privileges

Member institutions are assigned explicit privileges via [`MemberRole`](../backend/app/domain/consortium_governance.py):

| Role Name | Assignment Method | Governance & Training Privileges |
| :--- | :--- | :--- |
| **`FOUNDER`** | Assigned to the alliance creator upon consortium initialization. | Full voting rights ($1.0$), proposal creation, initial parameter calibration. |
| **`FULL_MEMBER`** | Admitted via democratic $K/N$ proposal vote. | Full voting rights ($1.0$), proposal creation, active participation in FL rounds. |
| **`OBSERVER`** | Admitted for read-only regulatory or compliance audit monitoring. | Telemetry inspection only; zero voting power on membership or policy proposals. |

---

## 📑 3. Proposal Actions & Lifecycle State Machine

The proposal lifecycle is governed by [`ProposalAction`](../backend/app/domain/consortium_governance.py) and [`ProposalStatus`](../backend/app/domain/consortium_governance.py):

```python
class ProposalAction(str, Enum):
    ADD_MEMBER = "ADD_MEMBER"
    REMOVE_MEMBER = "REMOVE_MEMBER"
    UPDATE_POLICY = "UPDATE_POLICY"

class ProposalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
```

### Proposal Actions
1. **Member Onboarding (`ADD_MEMBER`)**:
   - Requires weighted voting quorum approval ($\ge \theta_{\mathrm{quorum}}$).
   - Upon approval, the target institution is added as `FULL_MEMBER` (or specified role) with designated voting weight and joins active federation rounds.
2. **Malicious Node Eviction (`REMOVE_MEMBER`)**:
   - If an institution exhibits repeated Byzantine poisoning, collusion, or SLA breach, members vote to evict.
   - Upon approval, the node is pruned from `consortium.members`, and confidential federated unlearning ([`federated_unlearning_engine.py`](../backend/app/application/services/federated_unlearning_engine.py)) erases historical parameter footprints via Lineage Subtraction.
3. **Policy Calibration (`UPDATE_POLICY`)**:
   - Dynamically calibrates consortium invariants without hard fork:
     - `new_quorum_ratio`: adjusts required majority threshold $\theta_{\mathrm{quorum}} \in (0, 1]$.
     - `new_max_epsilon`: alters global Differential Privacy budget expenditure cap $\epsilon_{\max}$.
     - `new_min_members_n`: raises or lowers participant floor $N_{\min}$.
     - `new_status`: transitions consortium status (`ACTIVE`, `SUSPENDED`, `ARCHIVED`).

### 3.1 Weighted Quorum Evaluation Engine

Votes are evaluated based on institutions' allocated stake weights (`voting_power` $\ge 0.0$):

$$\mathrm{ratio}_{\mathrm{for}} = \frac{\sum_{b \in \mathcal{V}_{\mathrm{for}}} \mathrm{power}(b)}{\sum_{m \in \mathcal{M}_{\mathrm{eligible}}} \mathrm{power}(m)}, \quad \mathrm{ratio}_{\mathrm{against}} = \frac{\sum_{b \in \mathcal{V}_{\mathrm{against}}} \mathrm{power}(b)}{\sum_{m \in \mathcal{M}_{\mathrm{eligible}}} \mathrm{power}(m)}$$

Weighted quorum evaluation follows deterministic execution rules:

$$
\mathrm{Status} = \begin{cases}
\mathrm{APPROVED} & \text{if } \mathrm{ratio}_{\mathrm{for}} \ge \theta_{\mathrm{quorum}} \\
\mathrm{REJECTED} & \text{if } \mathrm{ratio}_{\mathrm{against}} > 1.0 - \theta_{\mathrm{quorum}} \\
\mathrm{EXPIRED} & \text{if elapsed time} \ge \mathrm{TTL} \\
\mathrm{CANCELLED} & \text{if withdrawn by sponsor} \\
\mathrm{PENDING} & \text{otherwise}
\end{cases}
$$

- **Approval Rule**: If $\mathrm{ratio}_{\mathrm{for}}$ meets or exceeds quorum threshold $\theta_{\mathrm{quorum}}$, status transitions immediately to `APPROVED` and the proposed action is executed.
- **Early Rejection Rule**: If $\mathrm{ratio}_{\mathrm{against}}$ exceeds the threshold ($> 1.0 - \theta_{\mathrm{quorum}}$), reaching quorum is mathematically impossible; status immediately transitions to `REJECTED`.
- **TTL Expiration**: If `elapsed_time` $\ge$ `ttl_seconds` (default 24 hours), status transitions to `EXPIRED` and the voting window closes.
- **Sponsor Cancellation**: The proposing bank can voluntarily withdraw a pending proposal before resolution, transitioning state to `CANCELLED`.
- **Role Hierarchy**: `OBSERVER` institutions have `voting_power` $= 0.0$ and cannot sponsor proposals or cast votes.

---

## 🛡️ 4. Pre-Round Policy Enforcement Engine & Dynamic Sharing Rules

Prior to launching any federated training round, [`ConsortiumPolicyEngine`](../backend/app/domain/consortium_policy.py) evaluates 7 pre-flight invariants:

1. **Quorum Member Count & Sybil Duplicate Prevention**:
   - Verifies that unique active participating banks meet or exceed `min_active_members` (default: 2 banks).
   - Enforces Sybil protection by rejecting participation manifests containing duplicate bank identities (`unique_banks != participating_banks`).
2. **Differential Privacy Expenditure Cap**:
   - Asserts that proposed round noise budget satisfies $\epsilon_{\mathrm{round}} \le \min(\epsilon_{\max}^{\mathrm{consortium}}, \epsilon_{\max}^{\mathrm{policy}})$.
   - Validates that $\epsilon_{\mathrm{round}}$ is positive and mathematically finite ($\epsilon > 0.0, \epsilon \neq \infty, \epsilon \neq \mathrm{NaN}$).
3. **Active Membership Authentication**:
   - Validates that all candidate bank nodes are active members of the consortium (`bank_id in consortium.members` with active status).
4. **Model Architecture Whitelist**:
   - Ensures the neural network architecture is whitelisted (`PyTorch_MLP`, `GraphSAGE`, `GAT`, `LogisticRegression`).
5. **Cross-Border Data Sovereignty & Regional Governance Rings**:
   - Validates participant jurisdictions against `allowed_regions` (e.g., `["EU", "EEA"]`).
   - If participant banks span multiple sovereign regions (e.g., `EU` and `US`), cross-border model exchange is prohibited unless `allow_cross_border_sharing=True` is explicitly approved via consortium governance voting (Schrems II and GDPR Article 22 compliance).
6. **Restricted PII Feature Governance**:
   - Scans proposed shared feature sets against `restricted_features` (e.g., `["ssn", "national_id", "raw_account_number", "iban"]`) to guarantee zero direct PII transmission.
7. **Minimum Local Data Sample Contribution**:
   - Evaluates `member_sample_counts` against `min_data_samples_per_member` (default: $\ge 100$ records) to eliminate free-rider institutions attempting to benefit from federated weights without proportional data contribution.

If any invariant fails, the engine raises `ConsortiumPolicyViolation` and aborts round execution.

---

## 🛠️ 5. Programmatic Implementation Example & Negative Authorization Boundaries

### 5.1 Programmatic Workflow with Authentic Institutional Context

Direct service operations require an authentic cryptographic authorization context—either an authenticated Bearer token (`auth_token`) belonging to an active institutional principal with governance authority (`admin`, `ml_lead`, or `compliance`), or a signed [`TrustedInternalContext`](../backend/app/application/services/model_registry.py) from the automated consortium coordinator:

```python
from app.infrastructure.security.auth_service import AuthenticationService
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import MemberRole, ProposalAction, ProposalStatus
from app.domain.consortium_policy import ConsortiumPolicyEngine, ConsortiumPolicyConfig

# 1. Authenticate an authorized, previously provisioned institutional account and issue a token bundle
# In production, automatic demo account seeding is disabled; accounts must be provisioned in advance.
auth_svc = AuthenticationService.get_instance()
# Authenticate institutional user previously provisioned with 'bank_a' affiliation and 'ml_lead' role
success, user, msg, _ = auth_svc.authenticate("lead_ml_a", "SecurePassword123!", client_ip="10.0.1.5")
if not success or user is None:
    raise RuntimeError(f"Authentication failed: {msg}")
token_bundle = auth_svc.create_token_bundle(user)
auth_token = token_bundle.access_token

# 2. Initialize governance service
service = ConsortiumGovernanceService()

# 3. Create consortium with authentic institutional authority
consortium = service.create_consortium(
    consortium_id="eu_aml_network",
    name="European AML Network",
    founder_bank_id="bank_a",
    quorum_ratio=0.51,
    max_epsilon=4.0,
    auth_token=auth_token,
)
assert consortium.members["bank_a"].role == MemberRole.FOUNDER

# 4. Propose admitting Bank B
proposal = service.propose_membership_change(
    consortium_id="eu_aml_network",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.ADD_MEMBER,
    auth_token=auth_token,
)
# Automatically approved (1/1 founder vote = 100% >= 51%)
assert proposal.status == ProposalStatus.APPROVED
assert "bank_b" in consortium.members

# 5. Enforce FL round preconditions with cross-border and sample validation
engine = ConsortiumPolicyEngine(
    config=ConsortiumPolicyConfig(
        min_active_members=2,
        allow_cross_border_sharing=False,
        allowed_regions=["EU", "EEA"],
        min_data_samples_per_member=100,
    )
)
is_valid, reasons = engine.validate_fl_round_preconditions(
    consortium=consortium,
    participating_banks=["bank_a", "bank_b"],
    round_epsilon=2.0,
    architecture="PyTorch_MLP",
    participant_regions={"bank_a": "EU", "bank_b": "EU"},
    member_sample_counts={"bank_a": 500, "bank_b": 750},
)
assert is_valid is True
```

### 5.2 Negative Authorization Boundaries & Fail-Closed Invariants

In strict adherence to the `AGENTS.md` runtime truth invariant, `_verify_service_actor_bank` enforces non-bypassable negative authorization boundaries:

1. **Unauthenticated Call Rejection**:
   - Calling governance mutations (`create_consortium`, `propose_membership_change`, `vote_on_proposal`) without `auth_token` or `trusted_context` raises `PermissionError`:
     ```text
     Authentication required: Direct service operation requires a valid Bearer auth_token or trusted internal context.
     ```
2. **Actor Identity Impersonation Guard**:
   - Providing a caller-supplied `actor="alice"` string without an authentic Bearer token raises:
     ```text
     Caller-supplied actor identity ('alice') cannot establish authority without valid authentication.
     ```
   - If an `actor` argument is supplied alongside an `auth_token`, it must strictly match the token's authenticated subject (`username`); token-identity divergence raises `PermissionError`.
3. **Cross-Bank Institutional Boundary**:
   - A valid token issued to a user at `bank_b` cannot execute actions on behalf of `bank_a`:
     ```text
     Cross-bank direct service operation rejected: Principal 'user_b' from institution 'bank_b' cannot perform governance actions on behalf of institution 'bank_a'.
     ```
4. **Inactive Principal Invalidation**:
   - The user account must be actively provisioned in `DurableAccountStore` (`is_active=True` and `status == "active"`). Suspended, locked out, or deleted users are rejected.
5. **Governance Role Gating**:
   - The principal's assigned roles must intersect with the governance whitelist `{"admin", "ml_lead", "compliance"}`. Non-governance roles (`analyst`, `auditor`, `external_evaluator`) fail closed with `PermissionError`.

---

## 🔍 6. Authorized Cross-Bank Intelligence & Case Evidence Ledger

The Cross-Bank Intelligence Service ([`CrossBankIntelligenceService`](../backend/app/application/services/cross_bank_intelligence.py)) operationalizes consortium membership by allowing authorized financial institutions to query and exchange privacy-preserving intelligence without pooling cleartext customer records or leaking counterpart PII.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│               AUTHORIZED CROSS-BANK INTELLIGENCE & EVIDENCE ANCHORING                   │
│                                                                                        │
│   [ Analyst (Bank A) ]                                                                 │
│           │                                                                            │
│           ▼ POST /api/v1/intelligence/cross-bank/query                                 │
│   ┌──────────────────────────────────────────────────────────────────────────────┐     │
│   │ 1. Governance Boundary Evaluation                                            │     │
│   │    - Requesting bank verified active member of consortium?                   │     │
│   │    - Consortium state is ACTIVE?                                             │     │
│   │    - Target counterpart bank active and not suspended/evicted?               │     │
│   │    - Cross-consortium boundary isolation respected?                          │     │
│   └──────────────────────────────────────┬───────────────────────────────────────┘     │
│                                          │ Pass                                        │
│                                          ▼                                             │
│   ┌──────────────────────────────────────────────────────────────────────────────┐     │
│   │ 2. Privacy & Attestation Boundary (Zero Raw PII)                             │     │
│   │    - Software Emulation vs Hardware TEE Boundary:                            │     │
│   │      If require_hardware_attestation=True and no physical enclave: FAIL CLOSED │   │
│   │    - Exact & Fuzzy Entity Matching (MinHash LSH & DH-PSI)                    │     │
│   │    - Output Sanitization: Strips raw names, tax IDs, accounts, phone numbers │     │
│   │    - Produces Institution-Scoped Sanitized Risk Signals                      │     │
│   └──────────────────────────────────────┬───────────────────────────────────────┘     │
│                                          │ Verified Signal                             │
│                                          ▼                                             │
│   [ Analyst Investigation Workbench ] ──► POST /api/v1/cases/{id}/evidence/cross-bank  │
│                                          │                                             │
│                                          ▼                                             │
│   ┌──────────────────────────────────────────────────────────────────────────────┐     │
│   │ 3. Cryptographic Evidence Ledger Anchoring                                   │     │
│   │    - Verified signal hashed into SHA-256 evidence entry                      │     │
│   │    - Linked to institution-owned case with provenance metadata               │     │
│   │    - Enforces Four-Eyes supervisor review and terminal immutability          │     │
│   └──────────────────────────────────────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 6.1 Service Endpoints & Dual-Routing Topology

Endpoints are registered with dual-routing prefixes (`/api/v1/intelligence/cross-bank` and `/v1/intelligence/cross-bank`):

| Method | Path | Authorization & Preconditions | Execution Guarantee |
| :--- | :--- | :--- | :--- |
| `POST` | `/query` | Bearer token with `analyst`, `investigator`, or `admin` role; active membership in requested consortium. | Executes privacy-preserving entity resolution against authorized consortium partners; returns sanitized risk signals. |
| `POST` | `/attach-to-case` | Bearer token; owning bank matches authenticated tenant; target case exists and is not in a terminal state. | Records immutable SHA-256 evidence record in `EvidenceRegistryService` linked to the investigation case. |
| `GET` | `/consortiums/{consortium_id}/policy` | Bearer token; authenticated institution must be an active member of `consortium_id`. | Returns active consortium data-sharing policy, permitted disclosure fields, and minimum confidence thresholds. |
| `POST` | `/api/v1/cases/{case_id}/evidence/cross-bank-intelligence` | Case subresource route; verified ownership and state check. | Forwards validated cross-bank intelligence signal into case evidence ledger. |

### 6.2 Truthful Hardware vs. Software Execution Boundary

In strict compliance with `AGENTS.md` Anti-Fabrication rules:
- **Software Simulation:** The standard simulation mode runs in-process commutative Diffie-Hellman PSI and MinHash LSH fuzzy matching over explicitly isolated synthetic datasets. Simulation requires explicit opt-in (`CFI_ENABLE_CROSS_BANK_SIMULATION=true`). Signals generated in this mode are labeled with `execution_mode: "SIMULATED_RESEARCH"` and `is_synthetic: True`. Research signals are preserved strictly in the non-operational `research_case_evidence_store` and can **never** be registered in the authoritative case evidence ledger or consumed by SAR/regulatory pipelines.
- **Fail-Closed Hardware Boundary:** If an institutional caller sets `require_hardware_attestation: True`, the service refuses to fabricate hardware measurements and immediately raises `RuntimeError` (`HTTP 412 Precondition Failed`), preventing unverified execution from masquerading as a physical hardware enclave.

### 6.3 Authentic Two-Bank Isolated Worker Architecture (DH-PSI)

Milestone 4 introduces genuine multi-process institutional separation through [`IsolatedBankWorkerNode`](../backend/app/application/services/isolated_matching_service.py) and [`CrossBankMatchingCoordinator`](../backend/app/application/services/isolated_matching_service.py):

1. **Storage Vault Separation:** Each participating bank worker operates strictly within its own isolated storage vault (`storage/{bank_id}/private_entities/`), with cross-tenant file loads failing closed (`PermissionError`).
2. **KMS Tenant Session Isolation:** Each bank node accesses cryptographic keys strictly through a permanently bound [`TenantKMSSession`](../backend/app/application/services/kms_service.py). Cross-tenant key probes fail closed with access control violations.
3. **2048-bit MODP Diffie-Hellman PSI:** Private Set Intersection executes in 3 stages:
   - *Stage 1:* Bank A hashes local entity to group and blinds with private exponent $a$: $c = H(x)^a \pmod p$.
   - *Stage 2:* Bank B verifies Bank A's HMAC signature and fresh nonce, applies exponent $b$ to get double-blinded query $D = c^b = H(x)^{ab} \pmod p$, blinds its own vault entities $B_j = H(y_j)^b \pmod p$, and permutes them cryptographically.
   - *Stage 3:* Bank A verifies Bank B's signature, blinds counterpart elements with exponent $a$ ($E_j = B_j^a \pmod p$), and computes exact set intersection $D \in \{E_j\}$.
4. **Attestation & Evidence Anchoring:** Generates a [`VerifiedMatchReceipt`](../backend/app/application/services/isolated_matching_service.py) with dual worker signatures and coordinator attestation. Only verified operational signals (`VERIFIED_OPERATIONAL`) are eligible for attachment to the authoritative case evidence ledger.

### 6.4 Explicit Operational Data-Sharing Grant Governance

To eliminate permissive global fallbacks:
- Operational cross-bank intelligence queries require an explicit operational data-sharing grant registered via `register_operational_data_sharing_grant(consortium_id, grant_id, authority, revision, ...)`.
- The grant must possess valid governing authority (e.g. FFIEC, BaFin, FCA) and monotonic revision history ($\ge 1$).
- In the absence of an active operational grant, operational queries fail closed (`ConsortiumSecurityError`).
- Authoritative institutional jurisdiction context is strictly mandatory; absence of verified jurisdiction terminates execution immediately to prevent unlawful cross-border data transfer.

---

## 🧪 7. Automated Unit & Integration Test Suite Parity

The consortium governance, policy enforcement, and cross-bank intelligence modules are verified across **53 automated tests**:

```bash
python -m pytest \
  backend/tests/unit/test_consortium_governance.py \
  backend/tests/unit/test_consortium_governance_hardening.py \
  backend/tests/unit/test_consortium_policy.py \
  backend/tests/unit/test_consortium_policy_hardening.py \
  backend/tests/unit/test_cross_bank_intelligence_workflow.py \
  backend/tests/integration/test_cross_bank_intelligence_integration.py -v
# 53 passed (100% Pass)
```

| Test File | Test Function | Verification Scope |
| :--- | :--- | :--- |
| `test_consortium_governance.py` | `test_consortium_creation_and_founder` | Consortium initialization, founder role assignment, and active status. |
| `test_consortium_governance.py` | `test_proposal_voting_quorum_approval` | Automatic creator voting, single-member quorum approval, and member activation. |
| `test_consortium_governance.py` | `test_multi_member_voting_and_eviction` | Multi-member voting, pending state under $2/3$ threshold, and `REMOVE_MEMBER` eviction. |
| `test_consortium_governance_hardening.py` | `test_consortium_input_validations` | Input bound validations (empty ID, name, non-finite quorum ratio, non-positive $\epsilon$). |
| `test_consortium_governance_hardening.py` | `test_consortium_member_voting_rights_and_roles` | Role permissions and voting eligibility (`FOUNDER`, `FULL_MEMBER`, `OBSERVER`). |
| `test_consortium_governance_hardening.py` | `test_membership_proposal_invariants` | Bounds checking, positive TTL enforcement, and temporal expiration evaluation. |
| `test_consortium_governance_hardening.py` | `test_weighted_voting_quorum_calculation` | Mathematical weighted stake voting power distribution and majority threshold passage. |
| `test_consortium_governance_hardening.py` | `test_observer_bank_cannot_sponsor_or_vote` | Enforcement of zero voting power and proposal sponsorship prohibition for observers. |
| `test_consortium_governance_hardening.py` | `test_proposal_policy_update_action` | `UPDATE_POLICY` proposal lifecycle, dynamic parameter execution, and key whitelisting. |
| `test_consortium_governance_hardening.py` | `test_cancel_proposal_lifecycle` | Sponsor-initiated proposal cancellation prior to quorum resolution. |
| `test_consortium_governance_hardening.py` | `test_proposal_rejection_due_to_unreachable_quorum` | Early termination and rejection when against votes make quorum mathematically impossible. |
| `test_consortium_governance_hardening.py` | `test_duplicate_and_invalid_member_actions` | Guard against adding existing members or evicting non-existent members. |
| `test_consortium_governance_hardening.py` | `test_dynamic_quorum_manager_validations` | `DynamicQuorumManager` threshold and window bounds checking. |
| `test_consortium_governance_hardening.py` | `test_consortium_service_thread_safety` | Concurrency lock integrity under multi-threaded vote submission. |
| `test_consortium_governance_hardening.py` | `test_list_proposals_and_members` | Proposal filtering, status queries, and membership roster directory. |
| `test_consortium_policy.py` | `test_consortium_policy_validation_success` | Successful validation when all quorum, $\epsilon$ cap, and whitelist rules pass. |
| `test_consortium_policy.py` | `test_consortium_policy_blocks_insufficient_quorum` | Blocks FL round when participating bank count is below minimum threshold. |
| `test_consortium_policy.py` | `test_consortium_policy_blocks_exceeded_epsilon` | Rejects round proposal exceeding global DP privacy expenditure cap. |
| `test_consortium_policy.py` | `test_consortium_policy_blocks_unapproved_bank` | Rejects unapproved or evicted bank nodes attempting to participate. |
| `test_consortium_policy_hardening.py` | `test_policy_config_validation_invalid_bounds` | Invalid configuration parameters rejection (`min_active_members < 1`, $\epsilon \le 0$). |
| `test_consortium_policy_hardening.py` | `test_duplicate_participating_banks_rejected` | Prevents Sybil attacks from spoofing participant quorum with duplicate bank IDs. |
| `test_consortium_policy_hardening.py` | `test_cross_border_sharing_blocked_by_default` | Rejects cross-border round execution without explicit consortium waiver. |
| `test_consortium_policy_hardening.py` | `test_cross_border_sharing_allowed_with_waiver` | Permits compliant cross-border federation when sovereign waiver is activated. |
| `test_consortium_policy_hardening.py` | `test_disallowed_region_rejected` | Rejects unapproved jurisdiction nodes participating in training rings. |
| `test_consortium_policy_hardening.py` | `test_restricted_features_blocked` | Rejects round attempts sharing raw PII attributes (SSN, IBAN). |
| `test_consortium_policy_hardening.py` | `test_member_sample_counts_enforced` | Prevents free-rider participation by requiring minimum local sample volume. |
| `test_consortium_policy_hardening.py` | `test_direct_cross_border_helper` | Validates `validate_cross_border_sharing` helper and DP enforcement safeguards. |
| `test_cross_bank_intelligence_workflow.py` | `test_authorized_consortium_query_returns_sanitized_signals` | End-to-end authorized query yielding sanitized signals without leaking raw PII. |
| `test_cross_bank_intelligence_workflow.py` | `test_unauthorized_non_member_bank_rejected` | Rejects query when requesting bank is not an admitted consortium member. |
| `test_cross_bank_intelligence_workflow.py` | `test_revoked_member_access_rejected` | Blocks queries from revoked, evicted, or suspended institutions. |
| `test_cross_bank_intelligence_workflow.py` | `test_cross_consortium_isolation_enforced` | Ensures queries cannot span across distinct, unlinked consortiums. |
| `test_cross_bank_intelligence_workflow.py` | `test_fail_closed_on_unverified_hardware_attestation` | Enforces fail-closed exception when hardware TEE attestation is required but unverified. |
| `test_cross_bank_intelligence_workflow.py` | `test_attach_evidence_to_active_case_succeeds` | Verifies attaching cross-bank signal to active investigation case ledger. |
| `test_cross_bank_intelligence_workflow.py` | `test_attach_evidence_to_terminal_case_rejected` | Enforces terminal case immutability when attempting to attach new evidence. |
| `test_cross_bank_intelligence_integration.py` | `test_query_endpoint_authorized_and_unauthorized_flow` | End-to-end REST API query flow asserting 200 OK for members and 403 for non-members. |
| `test_cross_bank_intelligence_integration.py` | `test_query_endpoint_fails_closed_when_hardware_attestation_required` | Asserts REST API returns 412 Precondition Failed when real enclave attestation is requested. |
| `test_cross_bank_intelligence_integration.py` | `test_attach_cross_bank_evidence_endpoint_flow` | REST API evidence attachment flow updating case ledger with SHA-256 evidence record. |
| `test_cross_bank_intelligence_integration.py` | `test_attach_evidence_to_terminal_case_via_api_returns_409` | REST API terminal case immutability check returning 409 Conflict. |
| `test_cross_bank_intelligence_integration.py` | `test_consortium_policy_endpoint` | REST API retrieval of active consortium data-sharing policy rules. |

