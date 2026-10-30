import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'child_process';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '../..');

const GATEWAY_URL = process.env.CFI_GATEWAY_URL || 'http://127.0.0.1:80';
const BACKEND_URL = process.env.CFI_BACKEND_URL || 'http://127.0.0.1:8000';
const COORDINATOR_URL = process.env.CFI_COORDINATOR_URL || 'http://127.0.0.1:8200';
const WORKER_A_URL = process.env.CFI_WORKER_A_URL || 'http://127.0.0.1:8101';
const WORKER_B_URL = process.env.CFI_WORKER_B_URL || 'http://127.0.0.1:8102';
const WORKER_C_URL = process.env.CFI_WORKER_C_URL || 'http://127.0.0.1:8103';

function loadConsortiumEnv(): Record<string, string> {
  const envPath = process.env.CFI_CONSORTIUM_ENV_FILE
    ? path.resolve(process.env.CFI_CONSORTIUM_ENV_FILE)
    : path.join(REPO_ROOT, '.env.consortium');

  if (!fs.existsSync(envPath)) {
    throw new Error(
      `Fail-closed: Required '.env.consortium' file not found at ${envPath}. ` +
        `Generate secrets and bootstrap the consortium before running live browser acceptance tests.`,
    );
  }

  const env: Record<string, string> = {};
  const raw = fs.readFileSync(envPath, 'utf-8');
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue;
    const idx = trimmed.indexOf('=');
    const key = trimmed.slice(0, idx).trim();
    const val = trimmed.slice(idx + 1).trim();
    env[key] = val;
  }
  return env;
}

function getRequiredSecret(env: Record<string, string>, key: string): string {
  const val = (process.env[key] || env[key] || '').trim();
  if (!val || val.length < 16 || val.toLowerCase().startsWith('replace_with_')) {
    throw new Error(`Fail-closed: Missing or invalid non-predictable secret '${key}' in .env.consortium.`);
  }
  return val;
}

function signEd25519Canonical(rawPrivateKeyHex: string, messageUtf8: string): string {
  const rawPriv = Buffer.from(rawPrivateKeyHex.trim(), 'hex');
  if (rawPriv.length !== 32) {
    throw new Error(`Expected 32-byte Ed25519 seed, got ${rawPriv.length} bytes.`);
  }
  // PKCS#8 DER prefix for a 32-byte Ed25519 private key seed (RFC 8410)
  const pkcs8Prefix = Buffer.from('302e020100300506032b657004220420', 'hex');
  const pkcs8Der = Buffer.concat([pkcs8Prefix, rawPriv]);
  const privateKey = crypto.createPrivateKey({
    key: pkcs8Der,
    format: 'der',
    type: 'pkcs8',
  });
  const sig = crypto.sign(null, Buffer.from(messageUtf8, 'utf-8'), privateKey);
  return sig.toString('hex');
}

async function assertServiceHealthy(name: string, url: string): Promise<void> {
  const resp = await fetch(url, { method: 'GET' });
  if (!resp.ok) {
    throw new Error(`Fail-closed: Service '${name}' at ${url} returned HTTP ${resp.status}.`);
  }
}

async function signInThroughBrowserModal(
  page: Page,
  username: string,
  password: string,
  expectedTenantBadge: string,
): Promise<void> {
  const signInBtn = page.getByTestId('institutional-sign-in-btn');
  await expect(signInBtn).toBeVisible();
  await signInBtn.click();

  const modal = page.getByTestId('institutional-login-modal');
  await expect(modal).toBeVisible();

  await page.getByTestId('input-institutional-username').fill(username);
  await page.getByTestId('input-institutional-password').fill(password);

  const loginRespPromise = page.waitForResponse(
    (resp) => resp.url().includes('/api/v1/auth/login') && resp.request().method() === 'POST',
  );
  await page.getByTestId('btn-submit-institutional-login').click();
  const loginResp = await loginRespPromise;
  expect(loginResp.status()).toBe(200);

  await expect(modal).toBeHidden();
  await expect(page.getByTestId('active-institutional-session')).toContainText(username);
  await expect(page.getByTestId('active-session-tenant')).toHaveText(expectedTenantBadge);
}

test.describe('Consortium Live Browser Analyst & Pilot Operations Acceptance Suite', () => {
  let env: Record<string, string>;

  test.beforeAll(async () => {
    env = loadConsortiumEnv();
    getRequiredSecret(env, 'ANALYST_BANK_A_PASSWORD');
    getRequiredSecret(env, 'ANALYST_BANK_B_PASSWORD');
    getRequiredSecret(env, 'ANALYST_BANK_C_PASSWORD');
    getRequiredSecret(env, 'CONSORTIUM_OPERATOR_PASSWORD');
    getRequiredSecret(env, 'WORKER_BANK_A_SIGNING_KEY');
    getRequiredSecret(env, 'WORKER_BANK_B_SIGNING_KEY');

    await assertServiceHealthy('Gateway', `${GATEWAY_URL}/gateway-health`);
    await assertServiceHealthy('Backend API', `${BACKEND_URL}/health`);
    await assertServiceHealthy('PSI Coordinator', `${COORDINATOR_URL}/health`);
    await assertServiceHealthy('Worker Bank A', `${WORKER_A_URL}/worker/health`);
    await assertServiceHealthy('Worker Bank B', `${WORKER_B_URL}/worker/health`);
    await assertServiceHealthy('Worker Bank C', `${WORKER_C_URL}/worker/health`);
  });

  test('authenticates Bank A analyst in browser, discovers alert-linked entity and consortium capabilities, executes real multi-counterparty OpenMined PSI, attaches verified evidence, and persists across page reload', async ({
    page,
  }) => {
    const bankAPassword = getRequiredSecret(env, 'ANALYST_BANK_A_PASSWORD');

    // 1. Navigate to Case Management via the live Gateway and sign in as Bank A analyst
    await page.goto('/cases');
    await signInThroughBrowserModal(page, 'analyst_bank_a', bankAPassword, 'BANK_A');

    // 2. Ingest a fresh Bank A alert linked to target_nexus_investigation_01 and escalate it to a new Bank A case via the browser UI
    const runSuffix = `${Date.now()}`;
    const freshAlertId = `alert_consortium_nexus_bank_a_${runSuffix}`;
    execFileSync(
      'docker',
      [
        'exec',
        'cfi-api-server',
        'python',
        '-c',
        `from app.application.services.alert_service import AlertIntelligenceService, _alert_to_dict; from app.domain.enums import AlertSeverity, AlertStatus; from app.domain.investigation_entities import Alert; svc = AlertIntelligenceService(); a = Alert(id='${freshAlertId}', bank_id='bank_a', transaction_id='tx_nexus_${runSuffix}', risk_score=880.0, severity=AlertSeverity.CRITICAL, status=AlertStatus.NEW, reason_codes=['CROSS_BANK_SYNDICATE_VELOCITY', 'HIGH_RISK_CORRIDOR'], confidence=0.94, involved_entity_ids=['target_nexus_investigation_01']); svc._alert_store.set(a.id, _alert_to_dict(a))`,
      ],
      { stdio: 'pipe' },
    );

    const newCaseBtn = page.getByRole('button', { name: '+ New Case' });
    await expect(newCaseBtn).toBeVisible();
    await newCaseBtn.click();

    const uniqueCaseTitle = `Bank A Live Browser Consortium Investigation ${runSuffix}`;
    await page.locator('#case-title-input').fill(uniqueCaseTitle);
    await page.locator('#case-priority-select').selectOption('p1_critical');
    await page.locator('#case-alert-id-input').fill(freshAlertId);

    const createCaseRespPromise = page.waitForResponse(
      (resp) => resp.url().includes('/api/v1/cases') && resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Create Case' }).click();
    const createCaseResp = await createCaseRespPromise;
    expect(createCaseResp.status()).toBe(200);
    const createdCaseJson = (await createCaseResp.json()) as { id: string; bank_id: string };
    expect(createdCaseJson.id).toBeTruthy();
    expect(createdCaseJson.bank_id).toBe('bank_a');

    // 3. Open the newly created Bank A case and inspect the Cross-Bank Intelligence Workspace
    await page.goto(`/cases/${createdCaseJson.id}`);
    const workspace = page.getByTestId('cross-bank-intelligence-workspace');
    await expect(workspace).toBeVisible();
    await expect(workspace).toContainText('TENANT: BANK_A');
    await expect(workspace).toContainText('OPERATIONAL ASSURANCE');

    // 4. Verify authoritative entity discovery from alert-linked local entity registry
    const entitySelect = page.getByTestId('case-entity-select');
    await expect(entitySelect).toBeVisible();
    await expect(entitySelect).toHaveValue('target_nexus_investigation_01');
    await expect(entitySelect).toContainText('CUST-A-NEXUS-01');

    // 5. Verify authoritative consortium, counterparty, and purpose discovery
    const consortiumSelect = page.getByTestId('consortium-select');
    await expect(consortiumSelect).toBeVisible();
    await expect(consortiumSelect).toHaveValue('consortium_consortium_deployment_01');

    const toggleBankB = page.getByTestId('target-toggle-bank_b');
    const toggleBankC = page.getByTestId('target-toggle-bank_c');
    await expect(toggleBankB).toBeVisible();
    await expect(toggleBankC).toBeVisible();
    await expect(toggleBankB).toBeEnabled();
    await expect(toggleBankC).toBeEnabled();

    const purposeSelect = page.getByTestId('purpose-select');
    await expect(purposeSelect).toBeVisible();
    await expect(purposeSelect).toHaveValue('AML_COLLABORATIVE_INVESTIGATION');

    // 6. Dispatch real multi-counterparty OpenMined PSI query across Coordinator, Bank A, Bank B, and Bank C
    const dispatchBtn = page.getByTestId('dispatch-multi-query-btn');
    await expect(dispatchBtn).toBeEnabled();

    const queryMultiRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes('/api/v1/intelligence/cross-bank/query-multi') &&
        resp.request().method() === 'POST',
    );
    await dispatchBtn.click();
    const queryMultiResp = await queryMultiRespPromise;
    expect(queryMultiResp.status()).toBe(200);

    const queryData = (await queryMultiResp.json()) as {
      query_id: string;
      overall_outcome: string;
      results: Record<
        string,
        {
          signal_id: string;
          match_confirmed: boolean;
          match_category: string;
          match_confidence: number;
          sanitized_risk_level: string;
          disclosure_mode: string;
          target_bank_id: string | null;
          provenance: { protocol: string; receipt_id: string; ticket_id: string };
        }
      >;
    };

    expect(queryData.overall_outcome).toBe('COMPLETED');
    const bankBResult = queryData.results.bank_b;
    const bankCResult = queryData.results.bank_c;
    expect(bankBResult).toBeDefined();
    expect(bankCResult).toBeDefined();
    expect(bankBResult?.match_confirmed).toBe(true);
    expect(bankBResult?.match_category).toBe('PROBABILISTIC_FUZZY');
    expect(bankBResult?.disclosure_mode).toBe('ATTRIBUTED_INSTITUTIONS');
    expect(bankBResult?.provenance.protocol).toBe('OPENMINED-PSI-2.0.6-ECDH');
    expect(bankCResult?.match_confirmed).toBe(false);
    expect(bankCResult?.match_category).toBe('NO_MATCH');
    expect(bankCResult?.disclosure_mode).toBe('ANONYMIZED_SUMMARY');

    // 7. Verify UI rendering of authentic Bank B MATCH and anonymized Bank C NO_MATCH
    await expect(workspace).toContainText(queryData.query_id);
    const cardBankB = page.getByTestId('outcome-card-bank_b');
    await expect(cardBankB).toBeVisible();
    await expect(cardBankB).toContainText('BANK_B');
    await expect(cardBankB).toContainText('MATCH CONFIRMED (PROBABILISTIC_FUZZY)');
    await expect(cardBankB).toContainText('Confidence: 100%');
    await expect(cardBankB).toContainText('Risk Level: UNAVAILABLE');

    const cardAnonC = page.getByTestId('outcome-card-anonymized-1');
    await expect(cardAnonC).toBeVisible();
    await expect(cardAnonC).toContainText('Anonymous Counterparty');
    await expect(cardAnonC).toContainText('ANONYMOUS MASKED');
    await expect(cardAnonC).toContainText('VERIFIED NO MATCH');

    // 8. Attach verified signals to the case evidence dossier
    const attachBtn = page.getByTestId('attach-batch-evidence-btn');
    await expect(attachBtn).toBeEnabled();

    const attachRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes('/api/v1/intelligence/cross-bank/attach-multi-to-case') &&
        resp.request().method() === 'POST',
    );
    await attachBtn.click();
    const attachResp = await attachRespPromise;
    expect(attachResp.status()).toBe(200);

    const attachBanner = page.getByTestId('attach-success-banner');
    await expect(attachBanner).toBeVisible();
    await expect(attachBanner).toContainText('Batch Evidence Assembly: COMPLETED');
    await expect(attachBanner).toContainText('Newly Attached: 2');

    await expect(page.getByTestId('persisted-evidence-list')).toBeVisible();

    // 9. Reload the browser page and verify persisted authoritative evidence from backend storage
    await page.reload();
    const persistedList = page.getByTestId('persisted-evidence-list');
    await expect(persistedList).toBeVisible();

    const evidenceGetPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/cases/${createdCaseJson.id}/intelligence-evidence`) &&
        resp.request().method() === 'GET',
    );
    await page.getByRole('button', { name: 'Refresh Ledger' }).click();
    const evidenceGetResp = await evidenceGetPromise;
    expect(evidenceGetResp.status()).toBe(200);
    const persistedItems = (await evidenceGetResp.json()) as Array<{
      evidence_id: string;
      verification_state: string;
      content_hash: string;
      match_confirmed: boolean;
      disclosure_mode: string;
      target_bank_id: string | null;
    }>;
    expect(persistedItems.length).toBe(2);

    for (const item of persistedItems) {
      expect(item.verification_state).toBe('VERIFIED');
      expect(item.content_hash).toMatch(/^[a-f0-9]{64}$/);
      const itemCard = page.getByTestId(`evidence-item-${item.evidence_id}`);
      await expect(itemCard).toBeVisible();
      await expect(itemCard).toContainText('VERIFIED');
      await expect(itemCard).toContainText('Batch Active');
      await expect(itemCard).toContainText(item.content_hash);
    }
    await expect(persistedList).toContainText('MATCH CONFIRMED (PROBABILISTIC_FUZZY)');
    await expect(persistedList).toContainText('BANK_B');
    await expect(persistedList).toContainText('Anonymous Counterparty');
    await expect(persistedList).toContainText('ANONYMOUS MASKED');
  });

  test('enforces cross-tenant browser security boundaries and prevents Bank A analyst from accessing Bank B cases or impersonating Bank B', async ({
    page,
  }) => {
    const bankAPassword = getRequiredSecret(env, 'ANALYST_BANK_A_PASSWORD');

    await page.goto('/cases');
    await signInThroughBrowserModal(page, 'analyst_bank_a', bankAPassword, 'BANK_A');

    // 1. Attempt to navigate to Bank B's isolated case URL while authenticated as Bank A
    const forbiddenCaseRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes('/api/v1/cases/case_consortium_bank_b_isolated_01') &&
        resp.request().method() === 'GET',
    );
    await page.goto('/cases/case_consortium_bank_b_isolated_01');
    const forbiddenCaseResp = await forbiddenCaseRespPromise;
    expect(forbiddenCaseResp.status()).toBe(403);

    await expect(page.getByTestId('case-access-denied')).toBeVisible();
    await expect(page.getByTestId('case-error-detail')).toContainText(
      "Tenant 'bank_a' is not authorized to access resources belonging to 'bank_b'",
    );

    // 2. Attempt to impersonate Bank B in a cross-bank query from the browser session
    const impersonationStatus = await page.evaluate(async () => {
      const token = localStorage.getItem('cfi_token') || '';
      const resp = await fetch('/api/v1/intelligence/cross-bank/query-multi', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
          'X-Tenant-ID': 'bank_b',
        },
        body: JSON.stringify({
          consortium_id: 'consortium_consortium_deployment_01',
          requesting_bank_id: 'bank_b',
          target_bank_ids: ['bank_a'],
          query_entity_id: 'counterpart_b_nexus_match',
          purpose: 'AML_COLLABORATIVE_INVESTIGATION',
          execution_mode: 'VERIFIED_OPERATIONAL',
          use_established_psi: true,
          require_established_psi: true,
        }),
      });
      return resp.status;
    });
    expect(impersonationStatus).toBe(403);
  });

  test('reports truthful PARTIAL_FAILURE in browser when a counterparty worker container is offline without fabricating results', async ({
    page,
  }) => {
    const bankAPassword = getRequiredSecret(env, 'ANALYST_BANK_A_PASSWORD');

    await page.goto('/cases/case_consortium_nexus_bank_a_01');
    await signInThroughBrowserModal(page, 'analyst_bank_a', bankAPassword, 'BANK_A');

    const workspace = page.getByTestId('cross-bank-intelligence-workspace');
    await expect(workspace).toBeVisible();
    await expect(page.getByTestId('case-entity-select')).toHaveValue('target_nexus_investigation_01');
    await expect(page.getByTestId('target-toggle-bank_b')).toBeEnabled();
    await expect(page.getByTestId('target-toggle-bank_c')).toBeEnabled();

    // Stop Bank C worker container to induce genuine counterparty unavailability
    execFileSync('docker', ['stop', 'cfi-worker-bank-c'], { stdio: 'pipe' });
    try {
      const partialRespPromise = page.waitForResponse(
        (resp) =>
          resp.url().includes('/api/v1/intelligence/cross-bank/query-multi') &&
          resp.request().method() === 'POST',
      );
      await page.getByTestId('dispatch-multi-query-btn').click();
      const partialResp = await partialRespPromise;
      expect(partialResp.status()).toBe(200);

      const partialJson = (await partialResp.json()) as {
        overall_outcome: string;
        results: Record<string, unknown>;
        failures: Record<string, { error_category: string; error_message: string }>;
      };
      expect(partialJson.overall_outcome).toBe('PARTIAL_FAILURE');
      expect(partialJson.results.bank_b).toBeTruthy();
      expect(partialJson.results.bank_c).toBeUndefined();
      const bankCFailure = partialJson.failures.bank_c;
      expect(bankCFailure).toBeDefined();

      await expect(workspace).toContainText('PARTIAL_FAILURE');
      await expect(page.getByTestId('outcome-card-bank_b')).toContainText(
        'MATCH CONFIRMED (PROBABILISTIC_FUZZY)',
      );
      const failedCard = page.getByTestId('outcome-card-anonymized-1');
      await expect(failedCard).toBeVisible();
      await expect(failedCard).toContainText(bankCFailure?.error_category ?? '');
      await expect(failedCard).not.toContainText('MATCH CONFIRMED');
      await expect(failedCard).not.toContainText('VERIFIED NO MATCH');
    } finally {
      execFileSync('docker', ['start', 'cfi-worker-bank-c'], { stdio: 'pipe' });
      for (let i = 0; i < 20; i++) {
        try {
          const r = await fetch(`${WORKER_C_URL}/worker/health`);
          if (r.ok) break;
        } catch {
          // wait for container readiness
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
      // Reset circuit breaker state for bank_c after intentional outage test
      execFileSync(
        'docker',
        [
          'exec',
          'cfi-api-server',
          'python',
          '-c',
          "from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService; CrossBankIntelligenceService.get_instance().circuit_breaker_store.record_success('consortium_consortium_deployment_01', 'bank_a', 'bank_c')",
        ],
        { stdio: 'pipe' },
      );
    }
  });

  test('executes governed two-bank pilot lifecycle across /pilots and /pilots/:pilotId with real preflight probes, multi-party Ed25519 approvals, activation, pause/resume, telemetry, and audit dossier', async ({
    page,
  }) => {
    const operatorPassword = getRequiredSecret(env, 'CONSORTIUM_OPERATOR_PASSWORD');
    const bankAPassword = getRequiredSecret(env, 'ANALYST_BANK_A_PASSWORD');
    const bankBPassword = getRequiredSecret(env, 'ANALYST_BANK_B_PASSWORD');
    const privKeyA = getRequiredSecret(env, 'WORKER_BANK_A_SIGNING_KEY');
    const privKeyB = getRequiredSecret(env, 'WORKER_BANK_B_SIGNING_KEY');

    // 1. Navigate to /pilots and sign in as the independent Consortium Operator
    await page.goto('/pilots');
    await signInThroughBrowserModal(page, 'operator_consortium', operatorPassword, 'CONSORTIUM');

    // 2. Provision a new governed two-bank pilot bound to the real Docker topology
    await page.getByRole('button', { name: 'Provision Pilot' }).click();
    await expect(
      page.getByRole('heading', { name: 'Provision Governed Two-Bank Controlled Pilot' }),
    ).toBeVisible();

    const pilotId = `pilot_consortium_live_${Math.floor(Date.now() / 1000)}`;
    await page.getByPlaceholder('e.g. pilot_bilateral_2026_q4').fill(pilotId);
    await page.getByPlaceholder('e.g. cfi-consortium').fill('consortium_consortium_deployment_01');

    // Wait for consortium metadata resolution or fill member institutions
    await expect(page.getByText(/Consortium European Pilot Deployment \(ACTIVE\)/i)).toBeVisible();
    const formSelects = page.locator('form select');
    await formSelects.nth(0).selectOption('bank_a');
    await formSelects.nth(1).selectOption('bank_b');
    await formSelects.nth(2).selectOption('AML_COLLABORATIVE_INVESTIGATION');

    await page.getByPlaceholder('e.g. eu-central-1').fill('EU');
    await page
      .getByPlaceholder('Optional Grant Reference')
      .fill('grant_consortium_deployment_ab');
    await page
      .getByPlaceholder('https://coordinator.consortium...')
      .fill('http://coordinator:8200');
    await page.getByPlaceholder('https://worker-a.bank...').fill('http://worker-bank-a:8100');
    await page.getByPlaceholder('https://worker-b.bank...').fill('http://worker-bank-b:8100');

    const provisionRespPromise = page.waitForResponse(
      (resp) => resp.url().endsWith('/api/v1/pilots') && resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Provision Controlled Pilot' }).click();
    const provisionResp = await provisionRespPromise;
    expect([200, 201]).toContain(provisionResp.status());

    // 3. Verify automatic navigation to /pilots/:pilotId and execute live preflight readiness probes
    await expect(page).toHaveURL(new RegExp(`/pilots/${pilotId}$`));
    await expect(page.getByRole('heading', { name: pilotId })).toBeVisible();

    await page.getByRole('button', { name: /Preflight Readiness/i }).click();
    const preflightRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/pilots/${pilotId}/preflight`) &&
        resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Run Preflight Verification' }).click();
    const preflightResp = await preflightRespPromise;
    expect(preflightResp.status()).toBe(200);
    const preflightPilot = (await preflightResp.json()) as {
      status: string;
      scope_digest: string;
      revision: number;
    };
    expect(preflightPilot.status).toBe('AWAITING_APPROVAL');

    await expect(page.getByText('All Preflight Readiness Probes Passed')).toBeVisible();
    await expect(page.getByText('CONSORTIUM_ACTIVE')).toBeVisible();
    await expect(page.getByText('OPERATIONAL_GRANT_VALID')).toBeVisible();
    await expect(page.getByText('INSTITUTIONAL_KEYS_ENROLLED')).toBeVisible();

    // 4. Switch to Approvals tab and submit independent cryptographic approvals for Bank A, Bank B, and Operator
    await page.getByRole('button', { name: /Institutional Approvals/i }).click();
    await expect(
      page.getByRole('heading', { name: 'Independent Bilateral & Operator Approvals' }),
    ).toBeVisible();

    const scopeDigest = preflightPilot.scope_digest;
    const revision = preflightPilot.revision || 1;

    // 4a. Bank A Compliance Approval (signed with Bank A Ed25519 key)
    await signInThroughBrowserModal(page, 'analyst_bank_a', bankAPassword, 'BANK_A');
    await expect(page.locator('form select')).toHaveValue('bank_a');
    const sigBankA = signEd25519Canonical(
      privKeyA,
      `PILOT_APPROVAL:${pilotId}:${revision}:${scopeDigest}:bank_a:analyst_bank_a`,
    );
    await page
      .getByPlaceholder('128-char hex Ed25519 signature computed over canonical approval bytes above')
      .fill(sigBankA);
    const approveARespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/pilots/${pilotId}/approve`) &&
        resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Record Authorized Approval' }).click();
    const approveAResp = await approveARespPromise;
    expect(approveAResp.status()).toBe(200);

    // 4b. Bank B Compliance Approval (signed with Bank B Ed25519 key)
    await signInThroughBrowserModal(page, 'analyst_bank_b', bankBPassword, 'BANK_B');
    await expect(page.locator('form select')).toHaveValue('bank_b');
    const sigBankB = signEd25519Canonical(
      privKeyB,
      `PILOT_APPROVAL:${pilotId}:${revision}:${scopeDigest}:bank_b:analyst_bank_b`,
    );
    await page
      .getByPlaceholder('128-char hex Ed25519 signature computed over canonical approval bytes above')
      .fill(sigBankB);
    const approveBRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/pilots/${pilotId}/approve`) &&
        resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Record Authorized Approval' }).click();
    const approveBResp = await approveBRespPromise;
    expect(approveBResp.status()).toBe(200);

    // 4c. Consortium Operator Approval -> transitions pilot to AUTHORIZED
    await signInThroughBrowserModal(page, 'operator_consortium', operatorPassword, 'CONSORTIUM');
    await expect(page.locator('form select')).toHaveValue('OPERATOR');
    const approveOpRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/pilots/${pilotId}/approve`) &&
        resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Record Authorized Approval' }).click();
    const approveOpResp = await approveOpRespPromise;
    expect(approveOpResp.status()).toBe(200);
    await expect(page.getByText('FULLY AUTHORIZED', { exact: true })).toBeVisible();

    // 5. Switch to Operational Controls tab and activate the authorized pilot
    await page.getByRole('button', { name: /Operational Controls/i }).click();
    const activateRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/pilots/${pilotId}/activate`) &&
        resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: /Activate Pilot/i }).click();
    const activateResp = await activateRespPromise;
    expect(activateResp.status()).toBe(200);
    await expect(page.getByText('Operational Queries ENABLED — Active Enclave')).toBeVisible();

    // 6. Test Emergency Pause and Resume in Operational Controls tab
    await page.getByRole('button', { name: /Emergency Pause/i }).click();
    await page
      .getByPlaceholder(/Document the regulatory, operational, or compliance reason/i)
      .fill('Scheduled operational readiness verification drill.');
    const pauseRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/pilots/${pilotId}/pause`) &&
        resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Confirm & Execute' }).click();
    const pauseResp = await pauseRespPromise;
    expect(pauseResp.status()).toBe(200);
    await expect(
      page.getByText('Operational Queries BLOCKED — Emergency Pause Active'),
    ).toBeVisible();

    await page.getByRole('button', { name: /Resume Pilot/i }).click();
    const resumeRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/pilots/${pilotId}/resume`) &&
        resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Confirm & Execute' }).click();
    const resumeResp = await resumeRespPromise;
    expect(resumeResp.status()).toBe(200);
    await expect(page.getByText('Operational Queries ENABLED — Active Enclave')).toBeVisible();

    // 7. Inspect Telemetry tab and Audit Dossier tab
    await page.getByRole('button', { name: /Telemetry & Execution/i }).click();
    await expect(
      page.getByRole('heading', { name: 'Live Two-Bank Operational Telemetry' }),
    ).toBeVisible();
    await expect(page.getByText('Grant Valid')).toBeVisible();

    await page.getByRole('button', { name: /Audit & Dossier/i }).click();
    const dossierRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/pilots/${pilotId}/dossier`) &&
        resp.request().method() === 'GET',
    );
    await page.getByRole('button', { name: 'Load Audit Dossier' }).click();
    const dossierResp = await dossierRespPromise;
    expect(dossierResp.status()).toBe(200);
    await expect(
      page.getByRole('heading', { name: 'Authoritative Dossier Payload Preview' }),
    ).toBeVisible();
    await expect(page.getByText('PILOT_PROVISIONED').first()).toBeVisible();
    await expect(page.getByText('PILOT_ACTIVATED').first()).toBeVisible();
  });

  test('enforces browser institutional session isolation across principal switching, purges investigation session storage, and fails closed for unauthorized tenants', async ({
    page,
  }) => {
    const bankAPassword = getRequiredSecret(env, 'ANALYST_BANK_A_PASSWORD');
    const bankBPassword = getRequiredSecret(env, 'ANALYST_BANK_B_PASSWORD');

    // 1. Sign in as Bank A analyst
    await page.goto('/cases');
    await signInThroughBrowserModal(page, 'analyst_bank_a', bankAPassword, 'BANK_A');

    // 2. Ingest alert and create a new Bank A case
    const runSuffix = `${Date.now()}`;
    const freshAlertId = `alert_isolation_${runSuffix}`;
    execFileSync(
      'docker',
      [
        'exec',
        'cfi-api-server',
        'python',
        '-c',
        `from app.application.services.alert_service import AlertIntelligenceService, _alert_to_dict; from app.domain.enums import AlertSeverity, AlertStatus; from app.domain.investigation_entities import Alert; svc = AlertIntelligenceService(); a = Alert(id='${freshAlertId}', bank_id='bank_a', transaction_id='tx_iso_${runSuffix}', risk_score=850.0, severity=AlertSeverity.HIGH, status=AlertStatus.NEW, reason_codes=['ISOLATION_TEST'], confidence=0.91, involved_entity_ids=['iso_entity_01']); svc._alert_store.set(a.id, _alert_to_dict(a))`,
      ],
      { stdio: 'pipe' },
    );

    const newCaseBtn = page.getByRole('button', { name: '+ New Case' });
    await expect(newCaseBtn).toBeVisible();
    await newCaseBtn.click();
    await page.locator('#case-title-input').fill(`Isolation Case Bank A ${runSuffix}`);
    await page.locator('#case-priority-select').selectOption('p1_critical');
    await page.locator('#case-alert-id-input').fill(freshAlertId);
    const createCaseRespPromise = page.waitForResponse(
      (resp) => resp.url().includes('/api/v1/cases') && resp.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Create Case' }).click();
    const createCaseResp = await createCaseRespPromise;
    expect(createCaseResp.status()).toBe(200);
    const createdCaseJson = (await createCaseResp.json()) as { id: string; bank_id: string };
    expect(createdCaseJson.id).toBeTruthy();
    const caseId = createdCaseJson.id;

    // 3. Open the newly created case
    await page.goto(`/cases/${caseId}`);

    // 4. Generate Copilot narrative to populate investigation session storage
    const generateCopilotBtn = page.getByRole('button', { name: /Generate Copilot SAR Draft/i });
    if (await generateCopilotBtn.isVisible()) {
      await generateCopilotBtn.click();
      await expect(page.getByText(/FinCEN SAR Narrative Preview/i)).toBeVisible();
    }

    // Verify session storage contains case copilot item tagged with bank_a
    const storedBeforeSwitch = await page.evaluate((cid) => {
      return sessionStorage.getItem(`cfi_copilot_case_${cid}`);
    }, caseId);
    if (storedBeforeSwitch) {
      expect(storedBeforeSwitch).toContain('"__cached_tenant_id":"bank_a"');
    }

    // 5. Switch principal to Bank B via header authentication modal
    await signInThroughBrowserModal(page, 'analyst_bank_b', bankBPassword, 'BANK_B');

    // 6. Verify Bank A investigation session storage is completely purged
    const storedAfterSwitch = await page.evaluate((cid) => {
      return sessionStorage.getItem(`cfi_copilot_case_${cid}`);
    }, caseId);
    expect(storedAfterSwitch).toBeNull();

    // 7. Verify Bank B cannot access Bank A case detail (renders case-access-denied / 403)
    await page.goto(`/cases/${caseId}`);
    await expect(page.getByTestId('case-access-denied')).toBeVisible();

    // 8. Sign out entirely (unauthenticated)
    const signOutBtn = page.getByTestId('institutional-sign-out-btn');
    if (await signOutBtn.isVisible()) {
      await signOutBtn.click();
    }

    // 9. Verify unauthenticated access to the case detail fails closed (401 / case-access-denied)
    await page.goto(`/cases/${caseId}`);
    await expect(page.getByTestId('case-access-denied')).toBeVisible();
  });
});

