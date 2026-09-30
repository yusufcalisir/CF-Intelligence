# Historical Legacy Experiment (Unequal Optimization Budget)

## Provenance Notice

This directory archives the original historical Credit Card Fraud Detection benchmark artifacts produced prior to the methodological audit.

### Experimental Configuration (Legacy / Asymmetric Budget)
- **Dataset**: European Credit Card Fraud Detection (284,807 transactions, 492 fraud cases)
- **Partition**: Deliberately constructed extreme-skew scenario (Bank A: 55%, Bank B: 30%, Bank C: 15% volume with 2 fraud cases)
- **Centralized Training**: 2 epochs (~7,122 optimizer steps, 2 dataset passes)
- **Federated Training (FedAvg / FedProx)**: 5 communication rounds x 2 local epochs per client (35,620 total client optimizer steps, 10 effective dataset passes)
- **Legacy Measured Results**:
  - Centralized Pooled (2 epochs): PR-AUC = 0.7060 (0.7021–0.7060)
  - FedAvg (5 rounds x 2 local epochs): PR-AUC = 0.7788 (0.7750–0.7788)
  - FedProx: PR-AUC = 0.6652 (0.6629–0.6652)

### Identified Methodological Flaws
1. **Optimization Budget Asymmetry**: The centralized model was evaluated with only 2 training epochs, whereas FedAvg received 5 rounds x 2 local epochs (5x more training exposure).
2. **Ignored Centralized Epoch Parameter**: evaluate_centralized_pooled(epochs=4) was defined with epochs=4, but internally called _train_client_local which ignored epochs and defaulted to self.local_epochs (2).
3. **Dataset Hash Placeholder**: The artifact recorded e3b0c442... (empty-string SHA-256 placeholder) instead of streaming the SHA-256 of the physical dataset file.

These files are retained unmodified for scientific reproducibility, historical comparison, and provenance verification.
