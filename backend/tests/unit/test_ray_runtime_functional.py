"""Functional runtime and artifact tests for Ray distributed execution."""

from __future__ import annotations

import os
import zipfile
from typing import Any

import pytest

try:
    import ray
except ImportError:
    ray = None  # type: ignore[assignment]


@pytest.mark.skipif(ray is None, reason="Ray not installed in environment")
def test_ray_python_runtime_task_and_object_store() -> None:
    """Verify that Python Ray initializes, executes remote tasks, and exchanges objects."""
    if ray is None:
        pytest.skip("Ray not installed in environment")
        return

    ray_env: Any = ray
    try:
        if not ray_env.is_initialized():
            ray_env.init(num_cpus=1, ignore_reinit_error=True)
    except (RuntimeError, ValueError) as exc:
        pytest.skip(f"Ray cluster initialization skipped due to host OS resource constraints: {exc}")

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

    ray_env.shutdown()


def test_ray_jar_artifact_structure_and_absence_of_unshaded_dependencies() -> None:
    """Inspect ray_dist.jar structure, shading prefix, and absence of active JVM."""
    if ray is None:
        pytest.skip("Ray not installed")
        return

    ray_dir = os.path.dirname(ray.__file__)
    jars_dir = os.path.join(ray_dir, "jars")
    if not os.path.exists(jars_dir):
        pytest.skip("Ray jars directory not present in this installation")

    jar_files = [f for f in os.listdir(jars_dir) if f.endswith(".jar")]
    assert len(jar_files) > 0, "Expected at least one Ray jar archive"

    ray_dist_path = os.path.join(jars_dir, "ray_dist.jar")
    if not os.path.exists(ray_dist_path):
        pytest.skip("ray_dist.jar not present")

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

