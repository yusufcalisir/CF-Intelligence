import type { Case, CaseSummary, Evidence, CaseNote, CaseEvent } from '../../api/types';

let caseIdCounter = 1;

/**
 * Creates a deterministic mock Case object with optional overrides.
 */
export function createMockCase(overrides: Partial<Case> = {}): Case {
  const id = overrides.id || `CASE-${String(caseIdCounter++).padStart(4, '0')}`;
  const status = overrides.status || 'investigating';
  const priority = overrides.priority || 'high';

  const defaultNotes: CaseNote[] = [
    {
      id: `NOTE-${id}-01`,
      case_id: id,
      author: 'senior_analyst_01',
      content: 'Initial KYC review confirms mismatch in declared income versus transaction volume.',
      created_at: '2026-08-14T09:30:00Z',
    },
  ];

  const defaultTimeline: CaseEvent[] = [
    {
      event_type: 'created',
      description: 'Case auto-generated from Critical Alert cluster',
      actor: 'system',
      timestamp: '2026-08-14T08:00:00Z',
      metadata: { source: 'automated_detection' },
    },
    {
      event_type: 'assigned',
      description: 'Assigned to senior_analyst_01',
      actor: 'triage_lead',
      timestamp: '2026-08-14T08:15:00Z',
      metadata: { assignee: 'senior_analyst_01' },
    },
  ];

  return {
    id,
    title: overrides.title || `Suspicious Structuring Pattern Investigation (${id})`,
    status,
    priority,
    assigned_to: overrides.assigned_to !== undefined ? overrides.assigned_to : 'senior_analyst_01',
    alert_ids: overrides.alert_ids || [`ALT-${id}-01`, `ALT-${id}-02`],
    evidence_ids: overrides.evidence_ids || [`EVD-${id}-01`],
    notes: overrides.notes || defaultNotes,
    timeline: overrides.timeline || defaultTimeline,
    created_at: '2026-08-14T08:00:00Z',
    updated_at: '2026-08-14T09:30:00Z',
    closed_at: status.startsWith('closed_') ? '2026-08-14T11:00:00Z' : null,
    total_risk_score: overrides.total_risk_score ?? 890,
    duration_hours: overrides.duration_hours ?? 3.0,
    is_open: !status.startsWith('closed_'),
    ...overrides,
  };
}

/**
 * Creates a list of mock CaseSummary objects.
 */
export function createMockCaseList(count: number, overrides: Partial<CaseSummary> = {}): CaseSummary[] {
  return Array.from({ length: count }, (_, i) => {
    const id = `CASE-SUMMARY-${String(i + 1).padStart(3, '0')}`;
    return {
      id,
      title: `Consortium AML Case ${i + 1}`,
      status: i % 2 === 0 ? 'investigating' : 'pending_review',
      priority: i === 0 ? 'critical' : 'high',
      assigned_to: 'analyst_01',
      total_risk_score: 750 + i * 20,
      alert_count: 3 + i,
      evidence_count: 2,
      created_at: '2026-08-14T08:00:00Z',
      is_open: true,
      ...overrides,
    };
  });
}

/**
 * Creates a mock Evidence ledger entry with cryptographic SHA-256 hash.
 */
export function createMockEvidence(caseId: string, overrides: Partial<Evidence> = {}): Evidence {
  const id = overrides.id || `EVD-${caseId}-01`;
  return {
    id,
    case_id: caseId,
    evidence_type: overrides.evidence_type || 'ledger_proof',
    title: overrides.title || 'Cryptographic Merkle Proof of Inter-Bank Layering',
    file_path: overrides.file_path || `/evidence/vault/${caseId}_proof.json`,
    content_hash: overrides.content_hash || 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    uploaded_by: overrides.uploaded_by || 'cryptographic_ledger_daemon',
    uploaded_at: '2026-08-14T08:30:00Z',
    ...overrides,
  };
}
