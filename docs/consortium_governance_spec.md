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

$$\mathrm{ratio}_{\mathrm{for}} = \frac{\lvert \mathcal{V}_{\mathrm{for}} \rvert}{N_{\mathrm{members}}}, \quad \mathrm{ratio}_{\mathrm{against}} = \frac{\lvert \mathcal{V}_{\mathrm{against}} \rvert}{N_{\mathrm{members}}}$$

where $\lvert \mathcal{V}_{\mathrm{for}} \rvert$ is the tally of affirmative votes (`votes_for`), $\lvert \mathcal{V}_{\mathrm{against}} \rvert$ is the tally of dissenting votes (`votes_against`), and $N_{\mathrm{members}}$ is the active voting member count.

- **Approval Condition:** If $\mathrm{ratio}_{\mathrm{for}} \ge \theta_{\mathrm{quorum}}$ (where $\theta_{\mathrm{quorum}}$ is the required quorum ratio, e.g. $0.51$ or $0.66$), the proposal transitions immediately to `APPROVED` and its action is executed.
- **Rejection Condition:** If $\mathrm{ratio}_{\mathrm{against}} > (1.0 - \theta_{\mathrm{quorum}})$, the proposal transitions to `REJECTED`.

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

### Weighted Quorum Evaluation Engine

Votes are evaluated based on institutions' allocated stake weights ($\mathrm{power}_{\mathrm{vote}} \ge 0.0$):

$$
\begin{aligned}
\mathrm{ratio}_{\mathrm{for}} &= \frac{\sum_{b \in \mathcal{V}_{\mathrm{for}}} \mathrm{power}(b)}{\sum_{m \in \mathcal{M}_{\mathrm{voting}}} \mathrm{power}(m)} \\
\mathrm{ratio}_{\mathrm{against}} &= \frac{\sum_{b \in \mathcal{V}_{\mathrm{against}}} \mathrm{power}(b)}{\sum_{m \in \mathcal{M}_{\mathrm{voting}}} \mathrm{power}(m)}
\end{aligned}
$$

- **Approval Rule**: If $\mathrm{ratio}_{\mathrm{for}} \ge \theta_{\mathrm{quorum}}$, status transitions to `APPROVED` and action is executed.
- **Early Rejection Rule**: If $\mathrm{ratio}_{\mathrm{against}} > (1.0 - \theta_{\mathrm{quorum}})$, reaching quorum is mathematically impossible; status immediately transitions to `REJECTED`.
- **TTL Expiration**: If $t_{\mathrm{elapsed}} \ge t_{\mathrm{TTL}}$ (default 24 hours), status transitions to `EXPIRED` and the voting window closes.
- **Sponsor Cancellation**: The proposing bank can voluntarily withdraw a pending proposal before resolution, transitioning state to `CANCELLED`.
- **Role Hierarchy**: `OBSERVER` institutions have $\mathrm{power} = 0.0$ and cannot sponsor proposals or cast votes.

---

## 🛡️ 4. Pre-Round Policy Enforcement Engine & Dynamic Sharing Rules

Prior to launching any federated training round, [`ConsortiumPolicyEngine`](../backend/app/domain/consortium_policy.py) evaluates 7 pre-flight invariants:

1. **Quorum Member Count & Sybil Duplicate Prevention**:
   - Verifies that unique active participating banks meet or exceed `min_active_members` (default: 2 banks).
   - Enforces Sybil protection by rejecting participation manifests containing duplicate bank identities (`unique_banks != participating_banks`).
2. **Differential Privacy Expenditure Cap**:
   - Asserts that proposed round noise budget satisfies $\epsilon_{\text{round}} \le \min(\text{consortium}.\epsilon_{\max}, \text{policy}.\epsilon_{\max})$.
   - Validates that $\epsilon_{\text{round}}$ is positive and mathematically finite ($\epsilon > 0.0, \epsilon \neq \infty, \epsilon \neq \text{NaN}$).
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

## 🛠️ 5. Programmatic Implementation Example

```python
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import MemberRole, ProposalAction, ProposalStatus
from app.domain.consortium_policy import ConsortiumPolicyEngine, ConsortiumPolicyConfig

# 1. Initialize governance service
service = ConsortiumGovernanceService()

# 2. Create consortium
consortium = service.create_consortium(
    consortium_id="eu_aml_network",
    name="European AML Network",
    founder_bank_id="bank_a",
    quorum_ratio=0.51,
    max_epsilon=4.0,
)
assert consortium.members["bank_a"].role == MemberRole.FOUNDER

# 3. Propose admitting Bank B
proposal = service.propose_membership_change(
    consortium_id="eu_aml_network",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.ADD_MEMBER,
)
# Automatically approved (1/1 founder vote = 100% >= 51%)
assert proposal.status == ProposalStatus.APPROVED
assert "bank_b" in consortium.members

# 4. Enforce FL round preconditions with cross-border and sample validation
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

---

## 🧪 6. Automated Unit Test Suite Parity

The consortium governance and policy enforcement modules are verified across **34 automated unit tests**:

```bash
python -m pytest \
  backend/tests/unit/test_consortium_governance.py \
  backend/tests/unit/test_consortium_governance_hardening.py \
  backend/tests/unit/test_consortium_policy.py \
  backend/tests/unit/test_consortium_policy_hardening.py -v
# 34 passed in 2.25s (100% Pass)
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
