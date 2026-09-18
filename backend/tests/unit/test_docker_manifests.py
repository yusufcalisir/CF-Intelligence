"""Unit tests for Docker deployment manifests, secrets generator, and Compose spec."""

from __future__ import annotations

import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from scripts.generate_secrets import generate_env_file  # noqa: E402


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
    assert "POSTGRES_PASSWORD=" in content
    assert "SECRET_KEY=" in content
    assert "REDIS_PASSWORD=" in content


def test_docker_compose_manifest_structure() -> None:
    """Verify master docker-compose.yml contains all 5 core production services."""
    compose_path = root_dir / "docker-compose.yml"
    assert compose_path.exists()

    content = compose_path.read_text(encoding="utf-8")
    for svc in ["gateway:", "frontend:", "backend:", "postgres:", "redis:"]:
        assert svc in content, f"Service {svc} missing from docker-compose.yml"


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

