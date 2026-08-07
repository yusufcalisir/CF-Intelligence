# Security & Threat Model Review — Smart Contracts Subsystem

**Subsystem:** Consortium Incentive Settlement (`ConsortiumIncentiveSettlement.sol`)  
**Date:** August 2026  
**Auditor:** Lead Blockchain Security Auditor  

---

## Evaluated Threat Vectors

### 1. Reentrancy Attacks
- **Risk:** Malicious bank contract hijacking execution flow during native token transfer.
- **Mitigation:** Checks-Effects-Interactions pattern enforced (`payout.isClaimed = true` is set BEFORE token transfer).
- **Status:** 🛡️ **MITIGATED**

### 2. Privilege Escalation / Unauthorized Coordinator Hijack
- **Risk:** Unauthorized third party calling `distributeIncentives` or `quarantineParticipant`.
- **Mitigation:** Strict `modifier onlyCoordinator` checking `msg.sender == coordinator`.
- **Status:** 🛡️ **MITIGATED**

### 3. Double-Claiming Payouts
- **Risk:** Bank calling `claimPayout` multiple times within the same epoch.
- **Mitigation:** Boolean flag `isClaimed` checked at start of function; reverts if `isClaimed == true`.
- **Status:** 🛡️ **MITIGATED**

### 4. Malicious Node Poisoning & Sybil Attacks
- **Risk:** Malicious bank injecting corrupted FL weights attempting to extract pool funds.
- **Mitigation:** Spectral SVD / LOO Shapley evaluation identifies poisonous updates off-chain; coordinator executes `quarantineParticipant` to zero payouts on-chain.
- **Status:** 🛡️ **MITIGATED**

### 5. Array Parameter Manipulation & Index Mismatches
- **Risk:** Mismatched array lengths leading to out-of-bounds reads or misallocated funds.
- **Mitigation:** Require statement enforcing `recipients.length == bankNames.length == shapleyScoresBasisPoints.length == amountsWei.length`.
- **Status:** 🛡️ **MITIGATED**
