"""Functional runtime and artifact tests for Ray distributed execution."""

from __future__ import annotations

import logging
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest

try:
    import ray
except ImportError:
    ray = None  # type: ignore[assignment]


@pytest.mark.skipif(ray is None, reason="Ray not installed in environment")
def test_ray_python_runtime_task_and_object_store() -> None:
    """Verify that Python Ray initializes, executes remote tasks, and exchanges objects."""
    assert ray is not None
    ray_env: Any = ray
    if not ray_env.is_initialized():
        ray_env.init(num_cpus=1, include_dashboard=False, ignore_reinit_error=True)

    @ray_env.remote
    def compute_sum(a: int, b: int) -> int:
        return a + b

    @ray_env.remote
    class Counter:
        def __init__(self) -> None:
            self.val = 0

        def increment(self) -> int:
            self.val += 1
            return self.val

    try:
        # 1. Test remote task execution
        task_future = compute_sum.remote(20, 22)
        result = ray_env.get(task_future)
        assert result == 42, f"Expected 42, got {result}"

        # 2. Test stateful remote actor
        counter_actor: Any = Counter
        actor = counter_actor.remote()
        val1 = ray_env.get(actor.increment.remote())
        val2 = ray_env.get(actor.increment.remote())
        assert val1 == 1
        assert val2 == 2

        # 3. Test Object Store put/get
        data = {"model_weights": [0.1, 0.2, 0.3], "bank_id": "bank_a"}
        obj_ref = ray_env.put(data)
        recovered = ray_env.get(obj_ref)
        assert recovered == data
    finally:
        if ray_env.is_initialized():
            ray_env.shutdown()


@pytest.mark.skipif(ray is None, reason="Ray not installed in environment")
def test_flower_engine_ray_apis_and_actor_lifecycle() -> None:
    """Verify every Ray API and Flower integration path actually used by FlowerEngine works without JARs."""
    assert ray is not None
    import flwr.simulation.ray_transport.ray_actor as ra
    import ray._private.worker as rw
    from ray.exceptions import RayActorError

    from app.application.services.flower_engine import _patch_flwr_ray_actor_pool

    assert hasattr(rw, "shutdown")
    _patch_flwr_ray_actor_pool()
    assert hasattr(ra.BasicActorPool, "terminate_all_actors")
    assert hasattr(ra.VirtualClientEngineActor, "terminate")

    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if ray.is_initialized():
        ray.shutdown()

    try:
        ray.init(
            object_store_memory=128 * 1024 * 1024,
            num_cpus=1,
            include_dashboard=False,
            ignore_reinit_error=True,
            logging_level=logging.ERROR,
            _system_config={"object_store_full_delay_ms": 100},
            runtime_env={
                "sys_paths": [backend_dir],
                "env_vars": {
                    "RAY_ACCEL_ENV_VAR_OVERRIDE_ON_ZERO": "0",
                    "KMP_DUPLICATE_LIB_OK": "TRUE",
                },
            },
        )
        assert ray.is_initialized()

        @ray.remote
        class EphemeralWorker:
            def ping(self) -> str:
                return "pong"

            def terminate(self) -> None:
                import ray.actor

                ray.actor.exit_actor()

        ephemeral_worker_cls: Any = EphemeralWorker
        worker = ephemeral_worker_cls.remote()
        assert ray.get(worker.ping.remote(), timeout=15.0) == "pong"
        with pytest.raises(RayActorError):
            ray.get(worker.terminate.remote(), timeout=5.0)
    finally:
        if ray.is_initialized():
            ray.shutdown()


@pytest.mark.skipif(ray is None, reason="Ray not installed in environment")
def test_ray_jar_artifact_structure_and_absence_of_unshaded_dependencies() -> None:
    """Verify ray/jars absence in stripped container runtime, or strict shading if shipped in wheel."""
    assert ray is not None
    ray_dir = Path(ray.__file__).resolve().parent
    jars_dir = ray_dir / "jars"
    jar_files = list(ray_dir.rglob("*.jar"))

    if os.environ.get("CFI_EXPECT_RAY_JARS_ABSENT") == "1":
        assert not jars_dir.exists(), f"ray/jars directory must be absent in container runtime: {jars_dir}"
        assert jar_files == [], f"Unexpected JAR archives remained under Ray package: {jar_files}"
        return

    if not jars_dir.exists():
        # Wheel in this environment does not ship ray/jars; confirm zero residual JAR files exist.
        assert jar_files == [], f"Unexpected JAR archives found under Ray package without jars dir: {jar_files}"
        return

    ray_dist_path = jars_dir / "ray_dist.jar"
    assert ray_dist_path.exists(), f"Expected ray_dist.jar inside {jars_dir}"

    with zipfile.ZipFile(ray_dist_path, "r") as z:
        names = z.namelist()

        # Jackson classes are shaded under io/ray/shaded/com/fasterxml/jackson/
        jackson_classes = [n for n in names if "com/fasterxml/jackson" in n and n.endswith(".class")]
        guava_classes = [n for n in names if "com/google/common" in n and n.endswith(".class")]

        assert len(jackson_classes) > 0, "Expected shaded Jackson classes in ray_dist.jar"
        assert len(guava_classes) > 0, "Expected shaded Guava classes in ray_dist.jar"

        # Verify that classes are shaded under io/ray/shaded/ or located in Multi-Release JAR paths
        for cls in jackson_classes:
            is_valid = cls.startswith("io/ray/shaded/com/fasterxml/jackson/") or cls.startswith("META-INF/versions/")
            assert is_valid, (
                f"Jackson class {cls} must be shaded under io/ray/shaded/ or located in META-INF/versions/"
            )

        for cls in guava_classes:
            is_valid = cls.startswith("io/ray/shaded/com/google/common/") or cls.startswith("META-INF/versions/")
            assert is_valid, (
                f"Guava class {cls} must be shaded under io/ray/shaded/ or located in META-INF/versions/"
            )

        # Confirm absolute absence of unshaded top-level dependency classes at root
        assert not any(n.startswith("com/fasterxml/jackson/") for n in names), (
            "Found unshaded root Jackson class in ray_dist.jar"
        )
        assert not any(n.startswith("com/google/common/") for n in names), (
            "Found unshaded root Guava class in ray_dist.jar"
        )

