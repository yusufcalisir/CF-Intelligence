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

$$\text{ratio}_{\text{for}} = \frac{|\text{votes\_for}|}{N_{\text{members}}}, \quad \text{ratio}_{\text{against}} = \frac{|\text{votes\_against}|}{N_{\text{members}}}$$

- **Approval Condition:** If $\text{ratio}_{\text{for}} \ge \text{required\_quorum\_ratio}$ (e.g. $0.51$ or $0.66$), the proposal transitions immediately to `APPROVED` and its action is executed.
- **Rejection Condition:** If $\text{ratio}_{\text{against}} > (1.0 - \text{required\_quorum\_ratio})$, the proposal transitions to `REJECTED`.

---

## ⚖️ 2. Member Roles & Privileges

Member institutions are assigned explicit privileges via [`MemberRole`](../backend/app/domain/consortium_governance.py):

| Role Name | Assignment Method | Governance & Training Privileges |
| :--- | :--- | :--- |
| **`FOUNDER`** | Assigned to the alliance creator upon consortium initialization. | Full voting rights ($1.0$), proposal creation, initial parameter calibration. |
| **`FULL_MEMBER`** | Admitted via democratic $K/N$ proposal vote. | Full voting rights ($1.0$), proposal creation, active participation in FL rounds. |
| **`OBSERVER`** | Admitted for read-only regulatory or compliance audit monitoring. | Telemetry inspection only; zero voting power on membership or policy proposals. |

---

## 📑 3. Proposal Actions & Lifecycle States

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
```

1. **Member Onboarding (`ADD_MEMBER`)**:
   - Requires $K/N$ member quorum.
   - Upon approval, the target institution is added as `FULL_MEMBER` and can participate in federated training.
2. **Malicious Node Eviction (`REMOVE_MEMBER`)**:
   - If an institution exhibits repeated Byzantine poisoning, collusion, or SLA breach, members vote to evict.
   - Upon approval, the node is pruned from `consortium.members`, and confidential federated unlearning ([`federated_unlearning_engine.py`](../backend/app/application/services/federated_unlearning_engine.py)) erases historical parameter footprints via Lineage Subtraction.
3. **Policy Calibration (`UPDATE_POLICY`)**:
   - Adjusts consortium-wide rules such as Differential Privacy budget caps ($\epsilon_{\max}$) or minimum participating member thresholds.

---

## 🛡️ 4. Pre-Round Policy Enforcement Engine

Prior to launching any federated training round, [`ConsortiumPolicyEngine`](../backend/app/domain/consortium_policy.py) evaluates 4 pre-flight invariants:

1. **Quorum Member Count**: Verifies that active participating banks meet or exceed `min_active_members` (default: 2 banks).
2. **Differential Privacy Expenditure Cap**: Asserts that proposed round noise budget satisfies $\epsilon_{\text{round}} \le \min(\text{consortium}.\epsilon_{\max}, \text{policy}.\epsilon_{\max})$.
3. **Active Membership Authentication**: Validates that all candidate bank nodes are active members of the consortium.
4. **Model Architecture Whitelist**: Ensures the neural network architecture is whitelisted (`PyTorch_MLP`, `GraphSAGE`, `GAT`, `LogisticRegression`).

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

# 4. Enforce FL round preconditions
engine = ConsortiumPolicyEngine(config=ConsortiumPolicyConfig(min_active_members=2))
is_valid, reasons = engine.validate_fl_round_preconditions(
    consortium=consortium,
    participating_banks=["bank_a", "bank_b"],
    round_epsilon=2.0,
    architecture="PyTorch_MLP",
)
assert is_valid is True
```

---

## 🧪 6. Automated Unit Test Suite Parity

The consortium governance and policy enforcement modules are verified across **7 automated unit tests**:

```bash
python -m pytest \
  backend/tests/unit/test_consortium_governance.py \
  backend/tests/unit/test_consortium_policy.py -v
# 7 passed in 0.99s (100% Pass)
```

| Test File | Test Function | Verification Scope |
| :--- | :--- | :--- |
| `test_consortium_governance.py` | `test_consortium_creation_and_founder` | Consortium initialization, founder role assignment, and active status. |
| `test_consortium_governance.py` | `test_proposal_voting_quorum_approval` | Automatic creator voting, single-member quorum approval, and member activation. |
| `test_consortium_governance.py` | `test_multi_member_voting_and_eviction` | Multi-member voting, pending state under $2/3$ threshold, and `REMOVE_MEMBER` eviction. |
| `test_consortium_policy.py` | `test_consortium_policy_validation_success` | Successful validation when all quorum, $\epsilon$ cap, and whitelist rules pass. |
| `test_consortium_policy.py` | `test_consortium_policy_blocks_insufficient_quorum` | Blocks FL round when participating bank count is below minimum threshold. |
| `test_consortium_policy.py` | `test_consortium_policy_blocks_exceeded_epsilon` | Rejects round proposal exceeding global DP privacy expenditure cap. |
| `test_consortium_policy.py` | `test_consortium_policy_blocks_unapproved_bank` | Rejects unapproved or evicted bank nodes attempting to participate. |
