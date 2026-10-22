"""Unit tests for Docker deployment manifests, secrets generator, and Compose spec."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

_generate_secrets = importlib.import_module("scripts.generate_secrets")
generate_env_file = _generate_secrets.generate_env_file


def test_generate_secrets_creates_valid_env(tmp_path: Path) -> None:
    """Verify secrets generator replaces placeholders with high-entropy values."""
    src_env = root_dir / ".env.example"
    dst_env = tmp_path / ".env.test"

    generate_env_file(src_env, dst_env)
    assert dst_env.exists()

    content = dst_env.read_text(encoding="utf-8")
    assert "cfi_secure_pass_2026_change_in_production" not in content
    assert "cfi_redis_secure_pass_2026_change_in_production" not in content
    assert "admin_change_in_production" not in content
    assert "cfi_local_secret_key_2026_change_me_in_production" not in content
    assert "cfi_payload_signing_secret_2026_change_in_production" not in content
    assert "cfi_training_coordinator_token_2026_change_in_production" not in content
    assert "cfi_training_worker_token_2026_change_in_production" not in content
    assert "cfi_oidc_jwt_secret_key_2026_enterprise_change_in_production" not in content
    assert "cfi_identity_store_secret_2026_change_in_production" not in content

    assert "POSTGRES_PASSWORD=" in content
    assert "SECRET_KEY=" in content
    assert "REDIS_PASSWORD=" in content
    assert "TRAINING_JOB_DB_URL=" in content
    assert "OIDC_JWT_SIGNING_SECRET=" in content
    assert "PAYLOAD_SIGNING_SECRET=" in content
    assert "TRAINING_COORDINATOR_TOKEN=" in content
    assert "TRAINING_WORKER_TOKEN=" in content
    assert "IDENTITY_STORE_SECRET=" in content

    # Invariant: Freshly generated .env must pass production invariant validation
    from app.config import Settings

    settings = Settings(_env_file=dst_env, app_env="production")
    # Must not raise ValueError
    settings.validate_production_invariants()


def test_docker_compose_manifest_structure() -> None:
    """Verify master docker-compose.yml contains all core production services and one-shot migration service."""
    compose_path = root_dir / "docker-compose.yml"
    assert compose_path.exists()

    content = compose_path.read_text(encoding="utf-8")
    for svc in ["gateway:", "frontend:", "backend:", "postgres:", "redis:", "migrations:"]:
        assert svc in content, f"Service {svc} missing from docker-compose.yml"

    # Backend must wait for migration completion before serving traffic
    assert "migrations:" in content
    assert "condition: service_completed_successfully" in content


def test_nginx_gateway_websocket_and_security_headers() -> None:
    """Verify Nginx configuration includes long-lived WebSocket and security headers."""
    nginx_path = root_dir / "docker" / "nginx" / "nginx.conf"

    assert nginx_path.exists()

    content = nginx_path.read_text(encoding="utf-8")
    assert "proxy_read_timeout 86400s;" in content
    assert "X-Frame-Options" in content
    assert "X-Content-Type-Options" in content
    assert "proxy_pass http://backend_api;" in content
    assert "proxy_pass http://frontend_spa;" in content


def test_docker_compose_enterprise_profiles_and_healthchecks() -> None:
    """Verify enterprise profiles, authenticated healthchecks, and environment floor in docker-compose.yml."""
    compose_path = root_dir / "docker-compose.yml"
    assert compose_path.exists()
    content = compose_path.read_text(encoding="utf-8")

    # Enterprise profile services
    assert "vault:" in content
    assert "minio:" in content
    assert "mlflow:" in content
    assert "50051" in content

    # Backend environment parity
    assert "DATABASE_TYPE=postgres" in content
    assert "POSTGRES_HOST=postgres" in content
    assert "CONSORTIUM_HMAC_SALT=" in content

    # Authenticated Redis probe
    assert "--no-auth-warning" in content
    assert "redis-cli" in content
    assert "pg_isready" in content


def test_dockerfiles_security_and_grpc_port_parity() -> None:
    """Verify multi-stage Dockerfiles enforce non-root user and expose gRPC port 50051."""
    for df_path in [
        root_dir / "docker" / "Dockerfile.backend",
        root_dir / "backend" / "Dockerfile",
    ]:
        assert df_path.exists()
        content = df_path.read_text(encoding="utf-8")
        assert "USER user" in content
        assert "50051" in content
        assert "uv" in content


def test_dev_compose_parity() -> None:
    """Verify docker-compose.dev.yml includes gRPC debug port and postgres environment override."""
    dev_path = root_dir / "docker-compose.dev.yml"
    assert dev_path.exists()
    content = dev_path.read_text(encoding="utf-8")
    assert "50051:50051" in content
    assert "DATABASE_TYPE=postgres" in content
    assert "POSTGRES_HOST=postgres" in content
    assert "REDIS_HOST=redis" in content


def test_missing_secrets_fail_closed_in_production() -> None:
    """Verify that empty, placeholder, or insecure secrets strictly fail production invariants."""
    import pytest

    from app.config import Settings

    # Case 1: Insecure postgres password containing change_me
    bad_settings = Settings(
        app_env="production",
        app_debug=False,
        database_type="postgres",
        postgres_password="change_me_in_production",
        payload_signing_secret="valid_payload_signing_secret_64_chars_high_entropy_token_2026_ok",
        training_coordinator_token="valid_coord_token_64_chars_high_entropy_token_2026_ok",
        training_worker_token="valid_worker_token_64_chars_high_entropy_token_2026_ok",
        training_job_db_url="postgresql://user:pass@host:5432/db",
        oidc_jwt_signing_secret="valid_oidc_jwt_signing_secret_64_chars_high_entropy_2026_ok",
        identity_store_secret="valid_identity_store_secret_64_chars_high_entropy_2026_ok",
    )
    with pytest.raises(ValueError, match="postgres_password must be customized in production"):
        bad_settings.validate_production_invariants()

    # Case 2: Identical worker and coordinator tokens (privilege separation)
    bad_priv = Settings(
        app_env="production",
        app_debug=False,
        database_type="postgres",
        postgres_password="secure_pg_pass_2026_production_ok",
        payload_signing_secret="valid_payload_signing_secret_64_chars_high_entropy_token_2026_ok",
        training_coordinator_token="shared_identical_token_between_worker_and_coordinator",
        training_worker_token="shared_identical_token_between_worker_and_coordinator",
        training_job_db_url="postgresql://user:pass@host:5432/db",
        oidc_jwt_signing_secret="valid_oidc_jwt_signing_secret_64_chars_high_entropy_2026_ok",
        identity_store_secret="valid_identity_store_secret_64_chars_high_entropy_2026_ok",
    )
    with pytest.raises(ValueError, match="training_worker_token and training_coordinator_token must be distinct"):
        bad_priv.validate_production_invariants()

    # Case 3: Incompatible SQLite training authority URL in production
    bad_db = Settings(
        app_env="production",
        app_debug=False,
        database_type="postgres",
        postgres_password="secure_pg_pass_2026_production_ok",
        payload_signing_secret="valid_payload_signing_secret_64_chars_high_entropy_token_2026_ok",
        training_coordinator_token="valid_coord_token_64_chars_high_entropy_token_2026_ok",
        training_worker_token="valid_worker_token_64_chars_high_entropy_token_2026_ok",
        training_job_db_url="sqlite:///tmp/test.db",
        oidc_jwt_signing_secret="valid_oidc_jwt_signing_secret_64_chars_high_entropy_2026_ok",
        identity_store_secret="valid_identity_store_secret_64_chars_high_entropy_2026_ok",
    )
    with pytest.raises(ValueError, match="training_job_db_url in production must be a PostgreSQL connection URL"):
        bad_db.validate_production_invariants()


def test_standalone_helm_chart_security_and_storage_guardrails() -> None:
    """Verify helm/cfi-platform defines UID 1000, persistent storage, and single-replica staging defaults."""
    import shutil
    import subprocess

    import yaml

    helm_dir = root_dir / "helm" / "cfi-platform"
    assert (helm_dir / "Chart.yaml").exists()
    assert (helm_dir / "values.yaml").exists()
    assert (helm_dir / "templates" / "pvc.yaml").exists()

    values = yaml.safe_load((helm_dir / "values.yaml").read_text(encoding="utf-8"))
    assert values["replicaCount"] == 1
    assert values["podSecurityContext"]["runAsUser"] == 1000
    assert values["podSecurityContext"]["fsGroup"] == 1000
    assert values["persistence"]["enabled"] is True
    assert values["persistence"]["mountPath"] == "/app/storage"
    assert values["existingSecret"] == "cfi-platform-secrets"
    assert values["autoscaling"]["enabled"] is False

    # If helm CLI is present, execute real lint and template verification
    helm_bin = shutil.which("helm")
    if helm_bin:
        # Test 1: helm lint must succeed
        res_lint = subprocess.run([helm_bin, "lint", str(helm_dir)], capture_output=True, text=True, check=False)
        assert res_lint.returncode == 0, f"helm lint failed: {res_lint.stderr}"

        # Test 2: helm template staging render must succeed
        res_tmpl = subprocess.run([helm_bin, "template", "cfi-staging", str(helm_dir)], capture_output=True, text=True, check=False)
        assert res_tmpl.returncode == 0, f"helm template failed: {res_tmpl.stderr}"
        output = res_tmpl.stdout
        assert "PersistentVolumeClaim" in output
        assert "cfi-platform-storage-pvc" in output
        assert "runAsUser: 1000" in output
        assert "cfi-platform-secrets" in output

        # Test 3: Unsafe multi-replica guardrail with ReadWriteOnce must fail fast
        res_bad = subprocess.run(
            [helm_bin, "template", "cfi-bad", str(helm_dir), "--set", "replicaCount=2"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_bad.returncode != 0
        assert "Unsafe multi-replica deployment: ReadWriteOnce persistence cannot be mounted" in res_bad.stderr

