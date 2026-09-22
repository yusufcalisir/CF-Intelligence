# CFI Smart Contracts Suite 📜

This directory contains the automated Ethereum/EVM Smart Contract architecture for **Consortium Incentive Settlement** within the Privacy-Preserving Cross-Bank Fraud Detection platform.

## Architecture & Responsibilities

- **`ConsortiumIncentiveSettlement.sol`**:
  - Manages CBDC / Fiat-backed Stablecoin (e.g., `e-TRY`, `USDC`) incentive pools.
  - Distributes payouts to participating banks based on cryptographic **Leave-One-Out (LOO) Federated Shapley Values**.
  - Links distributions directly to on-chain SHA-256 audit proof hashes (`auditProofHash`).
  - Implements **Malicious Participant Quarantine & Slashing Governance** (e.g. gradient poisoning / adversarial node quarantine).

- **`GnosisSafeMultiSigCoordinator.sol`**:
  - Implements **2-of-3 Threshold Multi-Signature Governance** for Consortium FL operations, eliminating Single Points of Failure (SPOF).
  - Enforces multi-trustee consensus for critical actions: incentive distribution, participant quarantine, pool deposits, and coordinator key rotations.
  - Supports full proposal lifecycles: creation, confirmation, execution, and confirmation revocation with strict access controls.

## Automated Verification & Test Coverage

The smart contract suite is validated by **31 automated Hardhat tests** with a clean, zero-vulnerability dependency perimeter:
- `test/ConsortiumIncentiveSettlement.test.js`: 19 tests covering deployment, pool deposits, Shapley payout distribution, double-claim prevention, quarantine enforcement, and Byzantine slashing.
- `test/GnosisSafeMultiSigCoordinator.test.js`: 12 tests covering 2-of-3 threshold initialization, proposal creation, multi-signature confirmation, threshold execution, and confirmation revocation.
- **Supply Chain Security**: 0 npm vulnerabilities via audited dependency overrides and local zero-dependency stubbing (`@ethersproject/signing-key`).

## Quick Start & Testing

### 1. Install Dependencies
```bash
npm install
```

### 2. Compile Solidity Smart Contracts
```bash
npm run compile
```

### 3. Run Automated Hardhat Test Suite
```bash
npm run test
```

### 4. Deploy to Local Blockchain Node / Testnet
```bash
# Local Hardhat Node
npm run deploy:local

# Sepolia Testnet
npm run deploy:sepolia
```
