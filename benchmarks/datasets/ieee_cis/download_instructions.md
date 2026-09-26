# IEEE-CIS Fraud Detection Benchmark Dataset

## Dataset Overview
- **Source**: IEEE Computational Intelligence Society & Vesta Corporation (Kaggle Competition).
- **Scale**: 590,540 transaction records with 394 anonymized features (V1–V339, card types, identity features).
- **Fraud Ratio**: ~3.5% positive labels.
- **Challenge**: Extreme feature dimension, heavy categorical missingness, temporal concept drift.

## Acquisition Instructions
Due to Kaggle competition competition rules and size (~1.5GB uncompressed), raw data files are not committed to git.

To acquire:
1. Ensure the Kaggle CLI is authenticated:
   ```bash
   kaggle competitions download -c ieee-fraud-detection -p benchmarks/datasets/ieee_cis/
   ```
2. Unzip `train_transaction.csv.zip` and `train_identity.csv.zip`:
   ```bash
   unzip benchmarks/datasets/ieee_cis/train_transaction.csv.zip -d benchmarks/datasets/ieee_cis/
   unzip benchmarks/datasets/ieee_cis/train_identity.csv.zip -d benchmarks/datasets/ieee_cis/
   ```

## Expected File Structure
```
benchmarks/datasets/ieee_cis/
├── download_instructions.md
├── train_transaction.csv
├── train_identity.csv (optional)
├── preprocess.py
└── validate.py
```
