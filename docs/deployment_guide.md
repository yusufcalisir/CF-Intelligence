# 🌐 Enterprise Deployment Guide: From Single-Node On-Premises to Multi-Node Mesh

This comprehensive guide details the deployment of the **Collaborative Fraud Intelligence (CFI)** platform across two deployment models:
1. **Part 1: One-Click Enterprise On-Premises Production Stack (`docker-compose.yml`)** — The unified production stack for hosting the security gateway, frontend SPA, backend API, PostgreSQL 16, and Redis 7.2 on an enterprise bank server in under 30 seconds.
2. **Part 2: Multi-Node Network-Isolated Distributed Cluster (`docker-compose.multinode.yml`)** — The multi-container distributed topology enforcing strict subnet isolation across distinct bank institutions.

---

# Part 1: One-Click Enterprise On-Premises Stack (`docker-compose.yml`)

## 1.1 Architectural Topology

```
[ Bank Network / Corporate Browser / Core Banking ESB ]
        │
        ▼ (Port 80 / 443)
  ┌─────────────────────────────────────────────────────────────┐
  │ 1. Enterprise Nginx Gateway (`cfi-gateway`)                 │
  │    • Same-Origin Routing: eliminates browser CORS           │
  │    • WebSocket Keepalive: 86400s timeout on /ws/*           │
  │    • Hardened Headers: HSTS, CSP, X-Frame-Options: SAMEORIGIN│
  └──────────────────────┬──────────────────────────────────────┘
                         │
        ┌────────────────┴────────────────┐
        ▼ (/ and /assets/*)               ▼ (/api/* and /ws/*)
  ┌───────────────────────────┐     ┌───────────────────────────┐
  │ 2. Frontend Web Container │     │ 3. Backend API & Engine   │
  │    (`cfi-frontend`)       │     │    (`cfi-api-server`)     │
  │    • Multi-stage Alpine   │     │    • Python 3.12, Gunicorn│
  │    • HTML5 pushstate SPA  │     │    • Non-root user (1000) │
  └───────────────────────────┘     └─────────────┬─────────────┘
                                                  │
                         ┌────────────────────────┴────────────────────────┐
                         ▼ (State / Relational)                            ▼ (Cache / Events)
  ┌───────────────────────────────────────────────┐ ┌───────────────────────────────────────────────┐
  │ 4. PostgreSQL 16 Enterprise Database          │ │ 5. Redis 7.2 Cache & Message Broker           │
  │    (`cfi-postgres`)                           │ │    (`cfi-redis`)                              │
  │    • Idempotent init SQL (01-init.sql)        │ │    • Protected mode auth, AOF persistence     │
  │    • pg_isready healthcheck gating            │ │    • Memory ceiling (512MB volatile-lru)      │
  └───────────────────────────────────────────────┘ └───────────────────────────────────────────────┘
```

## 1.2 Step-by-Step Operator Instructions

### Step 1: Generate Cryptographically Secure Production Secrets
Run the platform secrets generator to populate `.env` with 256-bit random tokens:
```bash
python scripts/generate_secrets.py
```

### Step 2: Pre-Flight Configuration Verification
Assert zero Compose syntax drift and verify service manifest integrity:
```bash
python scripts/verify_docker_deployment.py
```

### Step 3: Launch Enterprise Stack
Spin up all 5 production containers:
```bash
docker compose up -d --build
```

### Step 4: Verify Live Service Health
Check container health statuses:
```bash
docker compose ps
```
All containers (`cfi-gateway`, `cfi-frontend`, `cfi-api-server`, `cfi-postgres`, `cfi-redis`) should transition to `healthy`.

---

# Part 2: Multi-Node Network-Isolated Deployment (`docker-compose.multinode.yml`)

## 2.1 Architecture Overview

```
Bank A Private Subnet (bank-a-net)          Bank B Private Subnet (bank-b-net)
┌──────────────────────────────┐            ┌──────────────────────────────┐
│  cfi-bank-client-a           │            │  cfi-bank-client-b           │
│  - Isolated DB Volume        │            │  - Isolated DB Volume        │
│  - Bank A X.509 Cert         │            │  - Bank B X.509 Cert         │
└────────────┬─────────────────┘            └──────────────┬───────────────┘
             │ consortium-net only                          │ consortium-net only
             └─────────────────────┐  ┌────────────────────┘
                                   ▼  ▼
                    ┌──────────────────────────────┐
                    │  cfi-fl-coordinator          │
                    │  - Central PKI / CA           │
                    │  - Secure Aggregator          │
                    │  - gRPC Target: :50051        │
                    └──────────────────────────────┘
```

### Key Isolation Rules
- **No Direct Inter-Bank Routing**: `cfi-bank-client-a` cannot reach `cfi-bank-client-b` directly. `bank-a-net` and `bank-b-net` are marked `internal: true`.
- **Outbound-Only Communication**: Bank client daemons initiate outbound mTLS connections to the central coordinator over `consortium-net` port 50051. Bank nodes expose no inbound listening ports to the coordinator or external entities.
- **Cryptographic Certificate Isolation**: Each participant container uses a dedicated X.509 mTLS certificate and private key stored in an isolated volume.

---

## 🚀 Step-by-Step Deployment Instructions

### Step 1: Provision Per-Node X.509 PKI Certificates

Before launching containers, generate separate mTLS certificate bundles for each node:

```bash
# Provision Coordinator PKI
python scripts/init_vault_pki.py --node-id coordinator --out-dir pki/coordinator

# Provision Bank A PKI
python scripts/init_vault_pki.py --node-id bank-a --out-dir pki/bank-a

# Provision Bank B PKI
python scripts/init_vault_pki.py --node-id bank-b --out-dir pki/bank-b
```

Each directory will contain:
- `cert.pem`: Node leaf certificate
- `key.pem`: Node RSA private key
- `ca.pem`: Consortium Root CA certificate

---

### Step 2: Validate Docker Compose Configuration

Verify that the multi-node compose file is syntactically valid:

```bash
docker compose -f docker-compose.multinode.yml config
```

---

### Step 3: Launch Multi-Node Stack

Start the coordinator and bank client containers:

```bash
docker compose -f docker-compose.multinode.yml up -d --build
```

---

### Step 4: Verify Container Status & Health

Check that all three containers are healthy and running:

```bash
docker compose -f docker-compose.multinode.yml ps
```

Verify coordinator health endpoint:

```bash
curl http://localhost:8000/health
```

---

### Step 5: Verify Network Isolation Boundaries

Test that Bank A **cannot** communicate directly with Bank B:

```bash
# Expect failure / unreachable host (proving internal subnet isolation)
docker exec cfi-bank-client-a ping -c 2 cfi-bank-client-b
```

Verify that Bank A **can** reach the central coordinator over gRPC port 50051:

```bash
docker exec cfi-bank-client-a nc -zv coordinator 50051
```

---

## 📊 Operations & Telemetry

### Inspecting Container Logs

```bash
# Coordinator logs
docker logs -f cfi-fl-coordinator

# Bank A Client Daemon logs
docker logs -f cfi-bank-client-a

# Bank B Client Daemon logs
docker logs -f cfi-bank-client-b
```

### Stopping the Stack

```bash
docker compose -f docker-compose.multinode.yml down -v
```
