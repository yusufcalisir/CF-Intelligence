-- ==============================================================================
-- Enterprise PostgreSQL 16 Cold-Start Initialization Script
-- Privacy-Preserving Cross-Bank Fraud Detection Consortium (CFI)
-- ==============================================================================

-- 1. Essential Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- 2. Schema Definition
CREATE SCHEMA IF NOT EXISTS cfi_fraud;

-- 3. Consortium Banks Registry
CREATE TABLE IF NOT EXISTS cfi_fraud.banks (
    bank_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    tier VARCHAR(32) NOT NULL DEFAULT 'Tier-1',
    jurisdiction VARCHAR(32) NOT NULL DEFAULT 'EU',
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    connector_type VARCHAR(32) NOT NULL DEFAULT 'PARQUET',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Federated Training Simulation Runs
CREATE TABLE IF NOT EXISTS cfi_fraud.simulations (
    simulation_id VARCHAR(64) PRIMARY KEY,
    status VARCHAR(32) NOT NULL DEFAULT 'completed',
    dataset_name VARCHAR(64) NOT NULL DEFAULT 'paysim',
    rounds_total INT NOT NULL DEFAULT 10,
    champion_auc NUMERIC(6, 4) NOT NULL DEFAULT 0.9412,
    privacy_epsilon NUMERIC(8, 4) NOT NULL DEFAULT 1.0,
    aggregation_strategy VARCHAR(32) NOT NULL DEFAULT 'krum',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Anti-Money Laundering Alerts
CREATE TABLE IF NOT EXISTS cfi_fraud.alerts (
    alert_id VARCHAR(64) PRIMARY KEY,
    transaction_id VARCHAR(64) NOT NULL,
    bank_id VARCHAR(64) REFERENCES cfi_fraud.banks(bank_id),
    severity VARCHAR(32) NOT NULL DEFAULT 'high',
    status VARCHAR(32) NOT NULL DEFAULT 'NEW',
    risk_score NUMERIC(5, 4) NOT NULL DEFAULT 0.8500,
    features JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. Four-Eyes Investigation Cases
CREATE TABLE IF NOT EXISTS cfi_fraud.cases (
    case_id VARCHAR(64) PRIMARY KEY,
    case_number VARCHAR(32) NOT NULL UNIQUE,
    priority VARCHAR(32) NOT NULL DEFAULT 'high',
    status VARCHAR(32) NOT NULL DEFAULT 'under_investigation',
    assigned_analyst VARCHAR(64),
    supervisor_approver VARCHAR(64),
    sar_narrative TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 7. Immutable Cryptographic Audit Log
CREATE TABLE IF NOT EXISTS cfi_fraud.audit_chain (
    audit_id SERIAL PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    actor_id VARCHAR(64) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    signature VARCHAR(128),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 8. Idempotent Seed Data for Consortium
INSERT INTO cfi_fraud.banks (bank_id, name, tier, jurisdiction, status, connector_type)
VALUES
    ('bank_alpha', 'Bank Alpha', 'Tier-1 Global Gateway', 'EU/TR', 'ACTIVE', 'PARQUET'),
    ('bank_beta', 'Bank Beta', 'Retail & Commercial Neo-Bank', 'EU/DE', 'ACTIVE', 'REST'),
    ('bank_gamma', 'Bank Gamma', 'Cross-Border Wire Clearing', 'EU/FR', 'ACTIVE', 'ISO20022')
ON CONFLICT (bank_id) DO UPDATE
SET updated_at = CURRENT_TIMESTAMP;

-- Grant standard permissions
GRANT ALL PRIVILEGES ON SCHEMA cfi_fraud TO CURRENT_USER;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA cfi_fraud TO CURRENT_USER;
