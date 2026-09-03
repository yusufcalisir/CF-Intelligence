import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DatasetIngestionStudioModal } from '../DatasetIngestionStudioModal';
import * as queries from '../../../api/queries';

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

describe('DatasetIngestionStudioModal Component Test Suite', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.spyOn(queries, 'useValidateDatasetPreview').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        preview_id: 'PREV-TEST-001',
        filename: 'bank_alpha_production.csv',
        file_format: 'csv',
        inferred_delimiter: ',',
        row_count_estimate: 5000,
        detected_columns: ['timestamp', 'amount', 'source_account_id', 'destination_account_id', 'channel_type', 'is_fraud'],
        column_mappings: [
          { source_column: 'amount', target_signal: 'transaction_amount', data_type: 'float64', sample_values: [1450.0], is_required: true, confidence_score: 1.0 },
          { source_column: 'timestamp', target_signal: 'timestamp', data_type: 'string', sample_values: ['2026-09-01'], is_required: true, confidence_score: 1.0 },
          { source_column: 'is_fraud', target_signal: 'is_fraud', data_type: 'int64', sample_values: [0], is_required: true, confidence_score: 1.0 },
        ],
        schema_compliance_ratio: 1.0,
        pii_violations_detected: 0,
        pii_masked_receipt: 'ZERO-PII-VERIFIED',
      }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAuditDatasetContract').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        audit_id: 'AUD-TEST-001',
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
        ],
        overall_compliance_score: 1.0,
        fraud_ratio_detected: 0.0015,
        dirichlet_alpha_estimate: 0.52,
        drift_ks_score: 0.024,
        quarantine_csv_download_url: null,
        audit_message: '100% Great Expectations contracts passed!',
      }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useEnrollDatasetConsortium').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        enrollment_id: 'ENROLL-TEST-001',
        bank_id: 'bank_alpha',
        node_status: 'ACTIVE_TRAINING',
        records_enrolled: 5000,
        features_dimension: 9,
        partition_assigned: 'bank_alpha_custom_partition_v1',
        next_action_url: '/operations',
      }),
      isPending: false,
    } as any);
  });

  it('renders modal with title, progress indicators, and dropzone when open', () => {
    render(
      <DatasetIngestionStudioModal isOpen={true} onClose={vi.fn()} />,
      { wrapper: createWrapper() }
    );

    expect(screen.getByText('Real Dataset Ingestion Studio')).toBeInTheDocument();
    expect(screen.getByText(/CSV & Parquet Dropzone/i)).toBeInTheDocument();
    expect(screen.getByText('1. Dropzone')).toBeInTheDocument();
    expect(screen.getByText('2. Schema Mapping')).toBeInTheDocument();
    expect(screen.getByText('3. Contract Audit')).toBeInTheDocument();
  });

  it('completes full wizard: selects template -> maps schema -> audits GE contracts -> enrolls to consortium', async () => {
    const onSuccessMock = vi.fn();
    render(
      <DatasetIngestionStudioModal isOpen={true} onClose={vi.fn()} onSuccess={onSuccessMock} />,
      { wrapper: createWrapper() }
    );

    // 1. Select Bank Alpha CSV Template in Dropzone
    const templateBtn = screen.getByRole('button', { name: /Bank Alpha CSV/i });
    fireEvent.click(templateBtn);

    // 2. Assert Step 2 (Schema Alignment) is active
    await waitFor(() => {
      expect(screen.getByText('Interactive Schema & Signal Alignment')).toBeInTheDocument();
      expect(screen.getByText(/ZERO-PII-VERIFIED/i)).toBeInTheDocument();
    });

    // 3. Click Run Great Expectations Audit
    const runAuditBtn = screen.getByRole('button', { name: /Run Great Expectations Audit/i });
    fireEvent.click(runAuditBtn);

    // 4. Assert Step 3 (Great Expectations Audit Card) is rendered
    await waitFor(() => {
      expect(screen.getByText(/Great Expectations \(GE 1\.x\) Contract Audit/i)).toBeInTheDocument();
      expect(screen.getByText('100%')).toBeInTheDocument();
      expect(screen.getByText(/α = 0\.52/i)).toBeInTheDocument();
    });

    // 5. Click Allocate Consortium Node
    const allocateBtn = screen.getByRole('button', { name: /Allocate Consortium Node/i });
    fireEvent.click(allocateBtn);

    // 6. Assert Step 4 (Consortium Assignment Panel) is rendered
    await waitFor(() => {
      expect(screen.getByText('Consortium Node Allocation & Federated Pipeline Handoff')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Enroll Audited Dataset into Bank Alpha/i })).toBeInTheDocument();
    });

    // 7. Submit Enrollment
    const enrollBtn = screen.getByRole('button', { name: /Enroll Audited Dataset into Bank Alpha/i });
    fireEvent.click(enrollBtn);

    // 8. Assert Success Screen
    await waitFor(() => {
      expect(screen.getByText(/Dataset Enrolled into BANK_ALPHA Successfully!/i)).toBeInTheDocument();
      expect(onSuccessMock).toHaveBeenCalled();
    });
  });
});
