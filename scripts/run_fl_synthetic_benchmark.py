#!/usr/bin/env python3
"""Reproducible Empirical Federated Learning Simulation Benchmark Harness.

Executes a full 5-round, 3-bank federated learning simulation using FedAvg
on Non-IID Dirichlet distributed synthetic fraud data. Evaluates true converged
ROC-AUC, PR-AUC, F1-Score, and cross-institution federated improvement.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure backend path is on sys.path
backend_path = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(backend_path))

import numpy as np
from app.config import get_settings
from app.application.services.data_generator import DataGenerator
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.metrics_service import MetricsService
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.simulation_service import SimulationService
from app.domain.value_objects import SimulationConfig
from app.domain.enums import AggregationMethod, PrivacyMechanism


def run_benchmark():
    print("=" * 80)
    print("   CF-INTELLIGENCE: FEDERATED LEARNING CONVERGED SIMULATION BENCHMARK   ")
    print("=" * 80)
    print("Initializing benchmark harness (PyTorch 3-Layer MLP, FedAvg, Non-IID synthetic)...")

    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    fl_engine = FederatedLearningEngine(settings, model_service, privacy_service)
    data_generator = DataGenerator()
    metrics_service = MetricsService()

    sim_service = SimulationService(
        settings=settings,
        simulation_repo=None,
        bank_repo=None,
        metrics_repo=None,
        data_generator=data_generator,
        fl_engine=fl_engine,
        metrics_service=metrics_service,
        model_service=model_service,
    )

    config_dict = {
        "num_rounds": 5,
        "local_epochs": 2,
        "learning_rate": 0.001,
        "batch_size": 32,
        "min_clients_per_round": 2,
        "enable_latency_simulation": False,
        "enable_dropout_simulation": False,
        "enable_reconnect_simulation": False,
        "enable_differential_privacy": False,
        "enable_secure_aggregation": False,
        "bank_a_transactions": 1500,
        "bank_b_transactions": 1500,
        "bank_c_transactions": 1500,
    }
    config = SimulationConfig(**config_dict)

    t0 = time.perf_counter()
    sim_run = sim_service.run_simulation(config)
    elapsed_sec = time.perf_counter() - t0

    print(f"\nSimulation Finished in {elapsed_sec:.2f}s | Status: {sim_run.status.value.upper()}")
    print("-" * 80)
    print(f"{'Bank ID':<12} | {'Local AUC':<12} | {'Fed AUC':<12} | {'AUC Delta':<12} | {'Fed F1':<10}")
    print("-" * 80)

    local_aucs = []
    fed_aucs = []
    fed_f1s = []

    for b in sim_run.banks:
        loc_auc = b.local_metrics.auc_roc if b.local_metrics else 0.0
        fed_auc = b.federated_metrics.auc_roc if b.federated_metrics else 0.0
        fed_f1 = b.federated_metrics.f1_score if b.federated_metrics else 0.0
        delta = fed_auc - loc_auc

        local_aucs.append(loc_auc)
        fed_aucs.append(fed_auc)
        fed_f1s.append(fed_f1)

        print(f"{b.id:<12} | {loc_auc:<12.4f} | {fed_auc:<12.4f} | {delta:+12.4f} | {fed_f1:<10.4f}")

    mean_loc_auc = float(np.mean(local_aucs))
    mean_fed_auc = float(np.mean(fed_aucs))
    mean_fed_f1 = float(np.mean(fed_f1s))
    mean_improvement = mean_fed_auc - mean_loc_auc

    print("-" * 80)
    print(f"{'MEAN':<12} | {mean_loc_auc:<12.4f} | {mean_fed_auc:<12.4f} | {mean_improvement:+12.4f} | {mean_fed_f1:<10.4f}")
    print("=" * 80)
    print(f"Empirical Converged ROC-AUC (FedAvg, 5 rounds): {mean_fed_auc:.4f} (Bank range: {min(fed_aucs):.4f} - {max(fed_aucs):.4f})")
    print(f"Empirical Mean Federated F1-Score:             {mean_fed_f1:.4f}")
    print("Benchmark complete.\n")

    return {
        "mean_local_auc": mean_loc_auc,
        "mean_fed_auc": mean_fed_auc,
        "min_fed_auc": min(fed_aucs),
        "max_fed_auc": max(fed_aucs),
        "mean_fed_f1": mean_fed_f1,
        "elapsed_sec": elapsed_sec,
    }


if __name__ == "__main__":
    run_benchmark()
