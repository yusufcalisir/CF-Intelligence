import { motion } from 'framer-motion';
import type { BankInfo } from '../../api/types';
import { BANK_COLORS } from '../../api/types';
import { formatPercent, formatNumber } from '../../utils/formatters';

interface BankCardProps {
  bank: BankInfo;
  index: number;
}

function getBankLogo(bankId: string) {
  if (bankId === 'bank_a') {
    return (
      <svg style={{ width: 20, height: 20, color: '#fff' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 21h18" /><path d="M19 21v-8" /><path d="M5 21v-8" />
        <path d="M10 21v-8" /><path d="M14 21v-8" /><path d="M3 13h18" />
        <path d="M12 3L3 10h18L12 3z" />
      </svg>
    );
  }
  if (bankId === 'bank_b') {
    return (
      <svg style={{ width: 20, height: 20, color: '#fff' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2L2 7l10 5 10-5-10-5z" />
        <path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" /><path d="M12 12v10" />
      </svg>
    );
  }
  return (
    <svg style={{ width: 20, height: 20, color: '#fff' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <circle cx="12" cy="11" r="3" /><path d="M12 14v4" />
    </svg>
  );
}

export default function BankCard({ bank, index }: BankCardProps) {
  const color = BANK_COLORS[bank.id] ?? '#6366f1';

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: index * 0.1 }}
      style={{
        background: 'linear-gradient(135deg, rgba(15,22,41,0.9) 0%, rgba(8,12,24,0.8) 100%)',
        border: '1px solid rgba(99,102,241,0.14)',
        borderRadius: '0.75rem',
        padding: '1rem',
        transition: 'border-color 0.2s, box-shadow 0.2s',
        backdropFilter: 'blur(8px)',
      }}
      whileHover={{ boxShadow: '0 0 20px rgba(99,102,241,0.1)', borderColor: 'rgba(99,102,241,0.3)' }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '0.625rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
          <div
            style={{
              width: 36, height: 36, borderRadius: '0.5rem',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: `linear-gradient(135deg, ${color}, ${color}88)`,
              flexShrink: 0,
            }}
          >
            {getBankLogo(bank.id)}
          </div>
          <div>
            <h3 style={{ margin: 0, fontSize: '0.75rem', fontWeight: 600, color: '#e2e8f0', lineHeight: 1.3 }}>{bank.name}</h3>
            <p style={{ margin: 0, fontSize: '10px', color: '#64748b', textTransform: 'capitalize' }}>{bank.tier} bank</p>
          </div>
        </div>
        <span className="status-dot status-dot--active" style={{ marginTop: 4, flexShrink: 0 }} />
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem', fontSize: '0.75rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '11px', color: '#64748b' }}>Transactions</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: '#e2e8f0' }}>{formatNumber(bank.default_transactions)}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '11px', color: '#64748b' }}>Fraud Rate</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: '#f43f5e' }}>{formatPercent(bank.default_fraud_ratio)}</span>
        </div>
      </div>

      {/* Fraud Pattern */}
      <div style={{ marginTop: '0.625rem', paddingTop: '0.5rem', borderTop: '1px solid rgba(30,42,74,0.8)' }}>
        <p style={{ margin: 0, fontSize: '10px', color: '#64748b', lineHeight: 1.5 }}>
          <span style={{ color: '#94a3b8', fontWeight: 500 }}>Pattern: </span>
          {bank.fraud_pattern}
        </p>
      </div>

      {/* Characteristics */}
      <div style={{ marginTop: '0.5rem', display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
        {bank.characteristics.slice(0, 2).map((c) => (
          <span
            key={c}
            style={{
              fontSize: '9px',
              padding: '1px 6px',
              borderRadius: '999px',
              background: 'rgba(99,102,241,0.08)',
              color: '#94a3b8',
              border: '1px solid rgba(99,102,241,0.15)',
            }}
          >
            {c}
          </span>
        ))}
      </div>
    </motion.div>
  );
}
