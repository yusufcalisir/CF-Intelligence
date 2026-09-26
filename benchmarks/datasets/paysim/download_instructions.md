# PaySim Synthetic Financial Datasets for Fraud Detection

## Dataset Overview
- **Source**: Kaggle / Edgar Lopez-Rojas et al.
- **Reference**: Lopez-Rojas, E. A., Elmir, S., & Axelsson, S. (2016). *PaySim: A financial mobile money simulator for fraud detection*.
- **License**: Creative Commons Attribution 4.0 International (CC BY 4.0).
- **Scale**: ~6,362,620 transactions across 744 hourly simulation steps (~30 days).
- **Fraud Ratio**: ~0.129% (8,213 fraudulent transactions out of 6.36M).

## Acquisition Instructions
Due to dataset distribution licensing policies, the raw dataset file `PS_20174392719_1491204439457_log.csv` is not committed to the repository.

To download the dataset:
1. Ensure the Kaggle CLI is installed and configured (`~/.kaggle/kaggle.json` or `%USERPROFILE%\.kaggle\kaggle.json`):
   ```bash
   pip install kaggle
   ```
2. Download the dataset into this directory:
   ```bash
   kaggle datasets download -d ealaxi/paysim1 -p benchmarks/datasets/paysim/ --unzip
   ```
3. Verify that `PS_20174392719_1491204439457_log.csv` exists in `benchmarks/datasets/paysim/`.

## Expected File Structure
```
benchmarks/datasets/paysim/
├── download_instructions.md
├── PS_20174392719_1491204439457_log.csv   # Raw dataset (~470MB)
├── preprocess.py                           # Preprocessing & Dirichlet partitioner
└── validate.py                             # Verification script
```

## Running Validation & Preprocessing
```bash
python benchmarks/datasets/paysim/validate.py
python benchmarks/datasets/paysim/preprocess.py --alpha 0.5 --clients 5
```
