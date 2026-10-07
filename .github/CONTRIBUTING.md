# Contributing

Contributions are welcome. This section outlines how to get started.

## Development Setup

1. Clone the repo
2. Copy `.env.example` to `.env`
3. Start services: `make dev`
4. Backend runs at `http://localhost:8000`, frontend at `http://localhost:3000`

## Code Standards

- **Python**: Follows ruff linting rules. Run `make lint` before committing.
- **TypeScript**: Strict mode enabled. Run `npx tsc --noEmit` to type-check.
- **Commits**: Use conventional commit messages (`feat:`, `fix:`, `docs:`, `refactor:`).

## Running Tests

```bash
make test          # All backend tests
make test-unit     # Unit tests only
make lint          # Lint check
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes
4. Ensure tests pass and linting is clean
5. Submit a PR with a clear description

## Architecture

Read `docs/architecture.md` before making structural changes. The project follows Clean Architecture with four layers:

- **Domain** — Business entities and value objects
- **Application** — Services, schemas, interface contracts
- **Infrastructure** — Database, cache, task queue
- **Presentation** — API routers, WebSocket handlers

Dependencies flow inward: Presentation → Application → Domain. Infrastructure implements Application interfaces.

## Repository Naming & Hygiene Rules

Git-facing engineering artifacts must use domain-, architecture-, invariant-, and behavior-based terminology. Temporary task, audit, agent, investigation, or remediation campaign names must never become repository architecture or permanent engineering vocabulary.

### 1. Prohibited Temporary Campaign Identifiers
Do not introduce or retain temporary process/campaign names as permanent concepts, identifiers, tests, comments, docs, or directory/file names. Examples include (illustrative, not exhaustive):
- `Wave1`, `Wave2`, `Stage A`, `Stage B`, `Stage C`
- `Proof245`, `Residual918`, `Saturation24`
- `SBF-001`, `SBF-002`, `URC-*`, `CVG-*`, `FCF-*`, `DEV-*`
- `Runtime Truth`, `Final Certification`, `Final Closure`
- `semantic closure`, `forensic closure`, `audit remediation`, `repair campaign`
- `next agent handoff`, `previous agent`, `coding agent`

Instead, use permanent behavioral and domain concepts such as `unassessed-risk settlement hold`, `evaluation-evidence quality gate`, `dataset provenance validation`, `OAuth fail-closed authentication`, `single-class metric handling`, or `holdout rotation integrity`.

### 2. Prohibition of Prompt & Agent History Leakage
Tracked code must not contain historical process references such as:
- "the previous agent", "the next agent", "this prompt"
- "the audit requested", "the remediation task", "the previous closure"
- "Section N of the prompt", "Rule N from AGENTS", "as requested by the agent"
- "added during the audit", "fixed during Wave N"

Comments and docstrings must explain *why* an invariant exists, why a failure is fail-closed, or why a boundary exists—not narrate which agent or audit phase discovered it.

### 3. Test & Verification Naming
Test and verification functions must describe the concrete behavior or invariant being verified.
- **Forbidden**: `test_sbf_001_fix`, `test_wave2_case`, `test_final_closure`, `test_dev_06a`, `test_audit_regression`
- **Required**: `test_missing_evaluation_blocks_champion_promotion`, `test_holdout_rotation_preserves_active_round_binding`, `test_missing_oauth_configuration_fails_closed`

### 4. Production Logs & Error Messages
Production logs, telemetry, and exception messages must use operational and domain language (e.g. `UNVERIFIED_NO_EVALUATION`, `REJECTED_LOW_AUC`, `UNASSESSED_RISK_HOLD`). Never emit temporary internal campaign identifiers to operators.

### 5. Commit Messages
Commit messages must describe the engineering behavior or change, not the campaign that discovered it.
- **Forbidden**: `fix Wave2 issues`, `close SBF findings`, `final audit remediation`, `agent cleanup`
- **Required**: `Enforce fail-closed OAuth token acquisition`, `Bind model promotion to evaluated holdout evidence`, `Add persistent holdout dataset resolution`

### 6. Legitimate Scientific Provenance & Domain Terms
- **Scientific Identifiers**: Stable protocol and scientific reproducibility identifiers (e.g., `PROT-CC-REVAL-01`, `RU-CC-01`, `RU-PS-01`) are exempted when they serve experiment provenance and reproducibility records.
- **Domain & Security Vocabulary**: Do not ban legitimate domain terms such as `audit` (audit logs, audit trails, security audit events, compliance audits), `phase` (training phase), `stage` (deployment stage), `agent` (AML agentic copilot, HTTP user agent), `batch`, or `proof` (cryptographic proof-of-possession).
- **Test Doubles**: Standard testing terminology (`mock`, `fake`, `stub`, `fixture`, `synthetic`) is legitimate when technically accurate and test-scoped.

### 7. Pre-Creation Rule
Before creating or modifying any Git-tracked file, identifier, test, comment, log, or document, ask:
> *Would this name or description make complete sense to an outside engineer who has never seen the prompt, audit history, defect IDs, or agent conversation?*

If not, rewrite it using permanent domain and invariant terminology before completing the change.
