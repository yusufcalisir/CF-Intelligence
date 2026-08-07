# CFI Smart Contracts Suite 📜

This directory contains the automated Ethereum/EVM Smart Contract architecture for **Consortium Incentive Settlement** within the Privacy-Preserving Cross-Bank Fraud Detection platform.

## Architecture & Responsibilities

- **`ConsortiumIncentiveSettlement.sol`**:
  - Manages CBDC / Fiat-backed Stablecoin (e.g., `e-TRY`, `USDC`) incentive pools.
  - Distributes payouts to participating banks based on cryptographic **Leave-One-Out (LOO) Federated Shapley Values**.
  - Links distributions directly to on-chain SHA-256 audit proof hashes (`auditProofHash`).
  - Implements **Malicious Participant Quarantine Governance** (e.g. gradient poisoning / adversarial node quarantine).

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
