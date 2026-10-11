"""Adversarial Verification Suite: Multi-Process Concurrency & Durability.

Covers adversarial requirements ADV-08 through ADV-10:
- ADV-08: Two processes race to complete the same job differently.
- ADV-09: Two workers race to record distinct valid observations simultaneously; no lost update.
- ADV-10: Kill a process during a transaction and verify recovery.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.training_provenance_service import (
    TrustedTrainingJobRecordSource,
    reset_default_training_job_record_source,
)


class TestTrainingJobConcurrencyAdversarial:
    """Adversarial concurrency suite using genuine operating-system processes with distinct PIDs."""

    def setup_method(self) -> None:
        reset_default_training_job_record_source()

    def teardown_method(self) -> None:
        reset_default_training_job_record_source()

    def test_adv_08_two_processes_race_to_complete_differently(self, tmp_path: Path) -> None:
        """ADV-08: Two independent OS processes race to complete the same job with conflicting evidence.

        Exactly one process commits; the other fails with a conflict error.
        Both processes observe the single committed authoritative state.
        """
        db_path = str(tmp_path / "adv_08_race.db")
        source = TrustedTrainingJobRecordSource(store_path=db_path)

        job_id = "job_adv_08_race"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_08",
            simulation_id="sim_adv_08",
            expected_rounds=1,
        )
        source.start_training_job(job_id=job_id)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=500,
            observed_loss=0.30,
        )

        script_template = """
import os, sys, json, time
sys.path.insert(0, os.getcwd())
from app.application.services.training_provenance_service import (
    TrustedTrainingJobRecordSource,
    TrainingProvenanceSecurityError,
)
from app.application.services.data_generator import FEATURE_NAMES

db_path = sys.argv[1]
job_id = sys.argv[2]
artifact_sha = sys.argv[3]
delay = float(sys.argv[4])

time.sleep(delay)
source = TrustedTrainingJobRecordSource(store_path=db_path)

pid = os.getpid()
status = "UNKNOWN"
err_msg = ""
try:
    rec = source.complete_training_job(
        job_id=job_id,
        executed_rounds=1,
        sample_count=500,
        observed_metrics={"loss": 0.30},
        artifact_sha256=artifact_sha,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=list(FEATURE_NAMES),
    )
    status = "COMMITTED"
except TrainingProvenanceSecurityError as e:
    status = "CONFLICT_REJECTED"
    err_msg = str(e)
except Exception as e:
    status = "ERROR"
    err_msg = str(e)

print(json.dumps({"pid": pid, "status": status, "err": err_msg, "artifact": artifact_sha}))
"""
        script_file = str(tmp_path / "worker_complete_race.py")
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(script_template)

        sha_a = "a" * 64
        sha_b = "b" * 64

        proc_env = dict(os.environ, PYTHONPATH=os.getcwd())

        # Launch two processes simultaneously
        p1 = subprocess.Popen(
            [sys.executable, script_file, db_path, job_id, sha_a, "0.0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.getcwd(),
            env=proc_env,
        )
        p2 = subprocess.Popen(
            [sys.executable, script_file, db_path, job_id, sha_b, "0.0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.getcwd(),
            env=proc_env,
        )

        out1_raw, err1 = p1.communicate(timeout=15)
        out2_raw, err2 = p2.communicate(timeout=15)

        assert out1_raw.strip(), f"Process 1 failed with stderr: {err1}"
        assert out2_raw.strip(), f"Process 2 failed with stderr: {err2}"

        res1 = json.loads(out1_raw.strip().splitlines()[-1])
        res2 = json.loads(out2_raw.strip().splitlines()[-1])

        # Verify distinct OS process PIDs
        assert res1["pid"] != os.getpid()
        assert res2["pid"] != os.getpid()
        assert res1["pid"] != res2["pid"]

        statuses = [res1["status"], res2["status"]]
        assert "COMMITTED" in statuses, f"Expected one committed completion: {statuses}"
        assert "CONFLICT_REJECTED" in statuses, f"Expected one conflict rejected: {statuses}"

        # Inspect persisted database state: only the winner is committed
        final_rec = source.get_completed_job_by_id(job_id)
        assert final_rec is not None
        assert final_rec.status == "COMPLETED"
        winner_sha = sha_a if res1["status"] == "COMMITTED" else sha_b
        assert final_rec.artifact_sha256 == winner_sha

    def test_adv_09_two_workers_race_to_record_distinct_observations_no_lost_update(self, tmp_path: Path) -> None:
        """ADV-09: Two independent processes concurrently write distinct valid observations.

        Both authorized, nonconflicting observations must survive (no lost updates).
        """
        db_path = str(tmp_path / "adv_09_obs_race.db")
        source = TrustedTrainingJobRecordSource(store_path=db_path)

        job_id = "job_adv_09_obs"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_09",
            simulation_id="sim_adv_09",
            expected_rounds=1,
            permitted_workers=["BANK_A", "BANK_B"],
        )
        source.start_training_job(job_id=job_id, worker_id="BANK_A")

        script_template = """
import os, sys, json
sys.path.insert(0, os.getcwd())
from app.application.services.training_provenance_service import TrustedTrainingJobRecordSource

db_path = sys.argv[1]
job_id = sys.argv[2]
bank_id = sys.argv[3]
sample_count = int(sys.argv[4])
observed_loss = float(sys.argv[5])

source = TrustedTrainingJobRecordSource(store_path=db_path)
event = source.record_round_observation(
    job_id=job_id,
    round_number=1,
    bank_id=bank_id,
    sample_count=sample_count,
    observed_loss=observed_loss,
    worker_id=bank_id,
)

print(json.dumps({"pid": os.getpid(), "event_id": event.event_id, "bank_id": bank_id}))
"""
        script_file = str(tmp_path / "worker_record_obs.py")
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(script_template)

        proc_env = dict(os.environ, PYTHONPATH=os.getcwd())

        # Launch two processes simultaneously recording observations for Bank A and Bank B
        p1 = subprocess.Popen(
            [sys.executable, script_file, db_path, job_id, "BANK_A", "250", "0.32"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.getcwd(),
            env=proc_env,
        )
        p2 = subprocess.Popen(
            [sys.executable, script_file, db_path, job_id, "BANK_B", "350", "0.28"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.getcwd(),
            env=proc_env,
        )

        out1_raw, err1 = p1.communicate(timeout=15)
        out2_raw, err2 = p2.communicate(timeout=15)

        assert out1_raw.strip(), f"Worker A failed with stderr: {err1}"
        assert out2_raw.strip(), f"Worker B failed with stderr: {err2}"

        res1 = json.loads(out1_raw.strip().splitlines()[-1])
        res2 = json.loads(out2_raw.strip().splitlines()[-1])

        assert res1["pid"] != os.getpid()
        assert res2["pid"] != os.getpid()
        assert res1["pid"] != res2["pid"]

        # Verify both observations were persisted in the SQLite WAL store without lost updates
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT bank_id, actual_sample_count, observed_loss FROM cfi_round_observations WHERE job_id = ? ORDER BY bank_id ASC",
            (job_id,),
        )
        rows = cur.fetchall()
        conn.close()

        assert len(rows) == 2, f"Expected 2 observations, got: {rows}"
        banks = {r["bank_id"] for r in rows}
        assert banks == {"BANK_A", "BANK_B"}
        sample_counts = {r["bank_id"]: r["actual_sample_count"] for r in rows}
        assert sample_counts["BANK_A"] == 250
        assert sample_counts["BANK_B"] == 350

    def test_adv_10_process_killed_during_transaction_recovers_cleanly(self, tmp_path: Path) -> None:
        """ADV-10: Terminate a process during an active transaction; verify clean recovery and WAL consistency."""
        db_path = str(tmp_path / "adv_10_recovery.db")
        source = TrustedTrainingJobRecordSource(store_path=db_path)

        job_id = "job_adv_10_crash"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_10",
            simulation_id="sim_adv_10",
            expected_rounds=1,
        )
        source.start_training_job(job_id=job_id)

        # Worker script that opens an exclusive write transaction, modifies uncommitted state, and pauses
        script_crasher = """
import os, sys, time, sqlite3
sys.path.insert(0, os.getcwd())

db_path = sys.argv[1]
job_id = sys.argv[2]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("BEGIN IMMEDIATE")
conn.execute(
    '''
    INSERT INTO cfi_round_observations (
        event_id, job_id, training_run_id, simulation_id, worker_id, bank_id,
        round_number, attempt_number, actual_sample_count, actual_executed_epochs,
        observed_loss, metrics_json, execution_timestamp, model_update_digest,
        execution_status, payload_digest
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''',
    (
        "crash_event_01", job_id, "run_adv_10", "sim_adv_10", "CRASH_WORKER", "BANK_A",
        1, 1, 999, 1, 0.99, "{}", "2026-01-01T00:00:00Z", "", "IN_PROGRESS", "digest_crash"
    )
)
print("LOCKED_AND_DIRTY", flush=True)
time.sleep(30)
"""
        script_file = str(tmp_path / "worker_crash.py")
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(script_crasher)

        proc_env = dict(os.environ, PYTHONPATH=os.getcwd())

        p = subprocess.Popen(
            [sys.executable, script_file, db_path, job_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.getcwd(),
            env=proc_env,
        )

        # Wait until the child process acquires the transaction lock
        assert p.stdout is not None
        line = p.stdout.readline()
        assert "LOCKED_AND_DIRTY" in line

        # Kill the child process immediately while transaction is active
        p.kill()
        p.wait()

        # Reopen database in parent process: verify database recovery and integrity
        recovered_source = TrustedTrainingJobRecordSource(store_path=db_path)
        conn = sqlite3.connect(db_path)

        # 1. SQLite PRAGMA integrity_check returns 'ok'
        cur = conn.execute("PRAGMA integrity_check")
        integrity = cur.fetchone()[0]
        assert integrity == "ok"

        # 2. Uncommitted crash transaction was completely rolled back
        cur = conn.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE event_id = 'crash_event_01'")
        count = cur.fetchone()[0]
        assert count == 0
        conn.close()

        # 3. New valid observation can be recorded and committed immediately
        obs = recovered_source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=400,
            observed_loss=0.22,
        )
        assert obs.actual_sample_count == 400

        # 4. Job can be cleanly completed
        rec = recovered_source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            sample_count=400,
            observed_metrics={"loss": 0.22},
            artifact_sha256="1" * 64,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )
        assert rec.status == "COMPLETED"
