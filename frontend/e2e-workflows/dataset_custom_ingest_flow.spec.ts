import { test, expect } from '@playwright/test';

/**
 * End-to-End Test Suite: Real Dataset Ingestion Studio
 * Validates CSV/Parquet file ingestion, pre-flight schema mapping,
 * Great Expectations 1.x contract gating, and federated consortium allocation.
 */
test.describe('E2E Workflow: Real Dataset Drag-and-Drop Ingestor & GE Contract Gating', () => {
  test.beforeEach(async ({ page }) => {
    // Intercept dataset validate preview endpoint
    await page.route('**/api/v1/datasets/validate-preview', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          preview_id: 'PREV-E2E-PLAYWRIGHT-01',
          filename: 'bank_alpha_production.csv',
          file_format: 'csv',
          inferred_delimiter: ',',
          row_count_estimate: 5000,
          detected_columns: ['timestamp', 'amount', 'source_account_id', 'destination_account_id', 'channel_type', 'is_fraud'],
          column_mappings: [
            { source_column: 'amount', target_signal: 'transaction_amount', data_type: 'float64', sample_values: [1450.0], is_required: true, confidence_score: 1.0 },
            { source_column: 'timestamp', target_signal: 'timestamp', data_type: 'string', sample_values: ['2026-09-01'], is_required: true, confidence_score: 1.0 },
            { source_column: 'source_account_id', target_signal: 'source_account_id', data_type: 'string', sample_values: ['a9f1b2c3d4e5f607'], is_required: true, confidence_score: 1.0 },
            { source_column: 'destination_account_id', target_signal: 'destination_account_id', data_type: 'string', sample_values: ['b1c2d3e4f5a6b7c8'], is_required: false, confidence_score: 1.0 },
            { source_column: 'channel_type', target_signal: 'channel_type', data_type: 'string', sample_values: ['WIRE'], is_required: false, confidence_score: 1.0 },
            { source_column: 'is_fraud', target_signal: 'is_fraud', data_type: 'int64', sample_values: [0], is_required: true, confidence_score: 1.0 },
          ],
          schema_compliance_ratio: 0.85,
          pii_violations_detected: 0,
          pii_masked_receipt: 'ZERO-PII-VERIFIED',
        }),
      });
    });

    // Intercept contract audit endpoint
    await page.route('**/api/v1/datasets/contract-audit', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          audit_id: 'AUD-E2E-PLAYWRIGHT-01',
          bank_id: 'bank_alpha',
          status: 'passed',
          total_records: 5000,
          passed_records: 5000,
          quarantined_records: 0,
          contract_checks: [
            {
              expectation_name: 'ExpectColumnValuesToNotBeNull',
              column: 'transaction_amount',
              status: 'passed',
              observed_value: '0.0% nulls',
              expected_threshold: 'null_ratio == 0.0',
              details: 'Complete numerical integrity on transaction amounts.',
            },
            {
              expectation_name: 'ExpectColumnValuesToBeBetween',
              column: 'transaction_amount',
              status: 'passed',
              observed_value: 'Range: €12.50 to €84,500.00',
              expected_threshold: '0.01 <= amount <= 50,000,000.00',
              details: 'All amounts within statutory AML reporting bounds.',
            },
          ],
          overall_compliance_score: 1.0,
          fraud_ratio_detected: 0.0015,
          dirichlet_alpha_estimate: 0.52,
          drift_ks_score: 0.024,
          quarantine_csv_download_url: null,
          audit_message: '100% Great Expectations contracts passed! Dataset ready for bank_alpha consortium enrollment.',
        }),
      });
    });

    // Intercept consortium enroll endpoint
    await page.route('**/api/v1/datasets/consortium-enroll', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          enrollment_id: 'ENROLL-E2E-PLAYWRIGHT-01',
          bank_id: 'bank_alpha',
          node_status: 'ACTIVE_TRAINING',
          records_enrolled: 5000,
          features_dimension: 9,
          partition_assigned: 'bank_alpha_custom_partition_v1',
          next_action_url: '/operations?custom_enrolled=bank_alpha',
        }),
      });
    });
  });

  test('executes end-to-end dataset ingestion: dropzone template -> schema mapping -> GE audit -> consortium enrollment', async ({ page }) => {
    // 1. Navigate to Live Operations View
    await page.goto('/operations');
    await page.waitForLoadState('domcontentloaded');

    // 2. Click Import Dataset button
    const importBtn = page.locator('#import-custom-dataset-btn');
    await expect(importBtn).toBeVisible();
    await importBtn.click();

    // 3. Verify Dataset Ingestion Studio Modal opens
    const modal = page.locator('#dataset-ingestion-studio-modal');
    await expect(modal).toBeVisible();
    await expect(modal.locator('text=Real Dataset Ingestion Studio').first()).toBeVisible();

    // 4. Select Bank Alpha CSV Template in Dropzone
    const templateBtn = modal.locator('button:has-text("Bank Alpha CSV")');
    await expect(templateBtn).toBeVisible();
    await templateBtn.click();

    // 5. Verify Step 2: Interactive Schema Alignment
    await expect(modal.locator('text=Interactive Schema & Signal Alignment').first()).toBeVisible();
    await expect(modal.locator('text=ZERO-PII-VERIFIED').first()).toBeVisible();

    // 6. Click Run Great Expectations Audit
    const auditBtn = modal.locator('#proceed-to-contract-audit-btn');
    await expect(auditBtn).toBeVisible();
    await auditBtn.click();

    // 7. Verify Step 3: Great Expectations Contract Audit Card
    await expect(modal.locator('text=Great Expectations (GE 1.x) Contract Audit').first()).toBeVisible();
    await expect(modal.locator('text=100%').first()).toBeVisible();
    await expect(modal.locator('text=α = 0.52').first()).toBeVisible();

    // 8. Click Allocate Consortium Node
    const proceedToConsortiumBtn = modal.locator('#proceed-to-consortium-btn');
    await expect(proceedToConsortiumBtn).toBeVisible();
    await proceedToConsortiumBtn.click();

    // 9. Verify Step 4: Consortium Allocation Panel
    await expect(modal.locator('text=Consortium Node Allocation').first()).toBeVisible();
    const enrollBtn = modal.locator('#enroll-consortium-btn');
    await expect(enrollBtn).toBeVisible();
    await enrollBtn.click();

    // 10. Verify Success Confirmation & Close
    await expect(modal.locator('text=Dataset Enrolled into BANK_ALPHA Successfully!').first()).toBeVisible();
    const doneBtn = modal.locator('#ingest-modal-done-btn');
    await expect(doneBtn).toBeVisible();
    await doneBtn.click();

    // Verify modal is closed
    await expect(modal).not.toBeVisible();
  });
});
