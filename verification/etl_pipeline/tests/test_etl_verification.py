import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent.parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.application.services.etl_service import RealWorldETLPipeline
import numpy as np


def test_etl_verification_end_to_end():
    etl = RealWorldETLPipeline(salt="verification_salt_999")

    X = np.random.randn(500, 8)
    y = (np.random.rand(500) < 0.2).astype(int)

    partitions = etl.partition_dirichlet(X, y, num_banks=3, alpha=0.5, seed=123)

    assert len(partitions) == 3
    assert sum(len(p["y"]) for p in partitions) == 500
