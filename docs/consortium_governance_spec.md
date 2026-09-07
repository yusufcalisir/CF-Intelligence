# 🏛️ Federated Consortium Governance & Membership Protocol Specification

The Federated Consortium Governance engine (`ConsortiumGovernanceService`) enables multi-bank alliances (e.g. European AML Network, Nordic Fraud Defense Consortium) to establish democratic, quorum-based governance rules for collaborative fraud detection.

---

## 📌 Architectural Governance Flow & Quorum Mechanics

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
│                                           ▼  propose_membership_change()               │
│                                1. Proposal: `PENDING`                                  │
│                                   (Target: bank_b, Action: ADD_MEMBER)                 │
│                                           │                                            │
│                                           ├───► cast_vote(bank_id, VoteType.FOR)       │
│                                           │                                            │
│                                           ▼  Evaluate Quorum: Votes >= K/N             │
│                               ┌───────────────────────┐                                │
│                               │ 2. Quorum Reached?    │                                │
│                               └───────────────────────┘                                │
│                                      │          │                                      │
│                            >= 51%    │          │ < 51% or Voting Window Closes        │
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

---

## ⚖️ Member Roles & Permissions

| Role Name | Assignment Method | Governance Privileges |
| :--- | :--- | :--- |
| **`FOUNDER`** | Assigned to the alliance creator upon consortium initialization. | Full voting rights, proposal creation, initial parameter calibration. |
| **`FULL_MEMBER`** | Admitted via democratic $K/N$ proposal vote. | Full voting rights, proposal creation, participating in FL rounds. |
| **`OBSERVER`** | Admitted for read-only regulatory or audit monitoring. | Telemetry inspection only; zero voting rights on membership proposals. |

---

## 📑 Proposal Actions & Lifecycle States

```python
class ProposalAction(str, Enum):
    ADD_MEMBER = "ADD_MEMBER"
    EVICT_MEMBER = "EVICT_MEMBER"
    UPDATE_POLICY = "UPDATE_POLICY"
    UPDATE_PRIVACY_BUDGET = "UPDATE_PRIVACY_BUDGET"

class ProposalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
```

1. **Member Onboarding (`ADD_MEMBER`)**:
   - Requires $K/N$ member quorum.
   - Automatically provisions mTLS certificate handles and initializes isolated tenant storage.
2. **Malicious Node Eviction (`EVICT_MEMBER`)**:
   - If an institution exhibits repeated Byzantine poisoning, collusion, or SLA breach, members vote to evict.
   - Upon approval, the node is quarantined, and confidential federated unlearning (`federated_unlearning_engine.py`) erases historical parameter footprints via Lineage Subtraction.
3. **Privacy Budget Adjustment (`UPDATE_PRIVACY_BUDGET`)**:
   - Modifies the global Differential Privacy expenditure cap ($\epsilon_{max}$). Prevents runaway privacy budget depletion.

---

## 🛠️ Code Implementation Example

```python
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import MemberRole, ProposalAction, ProposalStatus, VoteType

service = ConsortiumGovernanceService()

# 1. Create consortium
consortium = service.create_consortium(
    consortium_id="eu_aml_network",
    name="European AML Network",
    founder_bank_id="bank_a",
    quorum_ratio=0.51,
    max_epsilon=4.0,
)
assert consortium.members["bank_a"].role == MemberRole.FOUNDER

# 2. Propose adding Bank B
proposal = service.propose_membership_change(
    consortium_id="eu_aml_network",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.ADD_MEMBER,
)
# Automatically approved since 1/1 founder vote = 100% >= 51%
assert proposal.status == ProposalStatus.APPROVED
assert "bank_b" in consortium.members
```

---

## 🧪 Automated Unit Test Suite

```bash
pytest backend/tests/unit/test_consortium_governance.py -v
```

**Verification Results:**
- `test_consortium_creation_and_founder`: `PASSED`
- `test_proposal_voting_quorum_approval`: `PASSED` (Verifies automatic state application)
- `test_multi_member_voting_and_eviction`: `PASSED` (Multi-member quorum and eviction verified)
