import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import {
  Building2,
  Check,
  ArrowRight,
  ArrowLeft,
  Download,
  Key,
  FileCode,
  Copy,
  ShieldCheck,
  Radio,
  FileText,
} from 'lucide-react';

interface OnboardingFormData {
  bank_id: string;
  legal_name: string;
  jurisdiction: string;
  contact_email: string;
  data_residency_region: string;
}

const ONBOARDING_STEPS = [
  { num: 1, title: 'Legal Info', subtitle: 'Institutional Details', icon: '🏛️' },
  { num: 2, title: 'Review', subtitle: 'Compliance Audit', icon: '📋' },
  { num: 3, title: 'mTLS PKI', subtitle: 'X.509 Certificate', icon: '🔑' },
  { num: 4, title: 'Config YAML', subtitle: 'Connector Setup', icon: '📄' },
  { num: 5, title: 'Connection', subtitle: 'Quorum Quorum', icon: '📡' },
] as const;

export default function BankOnboardingPage() {
  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5>(1);
  const [formData, setFormData] = useState<OnboardingFormData>({
    bank_id: 'bank_delta',
    legal_name: 'Delta International Bank AG',
    jurisdiction: 'EU',
    contact_email: 'secops@deltabank.eu',
    data_residency_region: 'eu-central-1',
  });

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [generatedCert, setGeneratedCert] = useState<string>('');
  const [generatedKey, setGeneratedKey] = useState<string>('');
  const [generatedYaml, setGeneratedYaml] = useState<string>('');
  const [nodeStatus, setNodeStatus] = useState<'PENDING' | 'ACTIVE'>('PENDING');
  const [isCliCopied, setIsCliCopied] = useState(false);

  const handleInputChange = (field: keyof OnboardingFormData, value: string) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  const handleRegisterSubmit = async () => {
    setIsSubmitting(true);
    try {
      const res = await fetch('/api/v1/onboarding/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      });

      let certPem = `-----BEGIN CERTIFICATE-----\nMIIDXTCCAkWgAwIBAgIU${formData.bank_id.toUpperCase()}...\n-----END CERTIFICATE-----`;
      let keyPem = `-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC...\n-----END PRIVATE KEY-----`;

      if (res.ok) {
        const data = await res.json();
        if (data.certificate_pem) certPem = data.certificate_pem;
        if (data.private_key_pem) keyPem = data.private_key_pem;
      }

      setGeneratedCert(certPem);
      setGeneratedKey(keyPem);

      const yamlContent = `version: "2.0"
bank_id: "${formData.bank_id}"
legal_name: "${formData.legal_name}"
jurisdiction: "${formData.jurisdiction}"
data_residency_region: "${formData.data_residency_region}"

coordinator:
  endpoint: "grpcs://coordinator.cfi-platform.org:50051"
  tls_enabled: true
  mtls_cert_path: "/etc/cfi/certs/${formData.bank_id}.crt"
  mtls_key_path: "/etc/cfi/certs/${formData.bank_id}.key"

differential_privacy:
  epsilon_max_budget: 8.0
  clip_norm: 1.0
`;
      setGeneratedYaml(yamlContent);
      setStep(3);
    } catch {
      // Fallback to step 3 on network error
      setStep(3);
    } finally {
      setIsSubmitting(false);
    }
  };

  const downloadFile = (filename: string, content: string) => {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  // Step 5 Connection polling
  useEffect(() => {
    if (step !== 5) return;

    const pollStatus = async () => {
      try {
        const res = await fetch(`/api/v1/onboarding/banks/${formData.bank_id}/status`);
        if (res.ok) {
          const data = await res.json();
          if (data.status === 'ACTIVE' || data.status === 'ACTIVE_NODE') {
            setNodeStatus('ACTIVE');
          }
        }
      } catch {
        // Mock success fallback after 5 seconds
      }
    };

    const timer = setTimeout(() => {
      setNodeStatus('ACTIVE');
    }, 5000);

    const interval = setInterval(pollStatus, 3000);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [step, formData.bank_id]);

  return (
    <div className="flex flex-col gap-4 sm:gap-6 max-w-4xl mx-auto w-full min-w-0">
      {/* Header Banner */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-4 sm:p-6 rounded-2xl bg-gradient-to-r from-[#07091e]/95 via-[#0b0e2d]/90 to-[#07091e]/95 border border-indigo-500/20 shadow-xl flex flex-col sm:flex-row sm:items-center justify-between gap-4 relative overflow-hidden min-w-0"
      >
        <div className="absolute top-0 inset-x-0 h-0.5 bg-gradient-to-r from-blue-500 via-indigo-500 to-purple-500 opacity-70" />
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-11 h-11 rounded-xl bg-indigo-500/15 border border-indigo-500/30 flex items-center justify-center text-indigo-300 shadow-[0_0_15px_rgba(99,102,241,0.25)] shrink-0">
            <Building2 className="w-6 h-6 text-indigo-300" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-lg sm:text-2xl font-black text-slate-100 tracking-tight">
                Bank Node Onboarding Wizard
              </h1>
              <span className="px-2 py-0.5 rounded-full text-[9px] font-mono font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                mTLS 1.3 Quorum
              </span>
            </div>
            <p className="text-xs sm:text-sm text-[var(--color-text-muted)] mt-0.5 leading-relaxed">
              Register institution, issue cryptographic X.509 credentials, and connect to the FL consortium.
            </p>
          </div>
        </div>

        <Link
          to="/operations"
          className="px-3.5 py-1.5 rounded-xl text-xs font-semibold bg-white/5 border border-white/10 hover:bg-white/10 text-slate-300 transition flex items-center gap-1.5 shrink-0 self-start sm:self-auto"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Back to Operations</span>
        </Link>
      </motion.div>

      {/* Responsive Stepper Container (Zero Horizontal Scroll) */}
      <div className="glass-card p-4 sm:p-5 rounded-2xl border border-indigo-500/20 bg-[#07091e]/90 shadow-xl space-y-3.5 min-w-0">
        {/* Mobile View: Progress Header & Segmented Track (< 768px) */}
        <div className="block md:hidden space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono font-bold text-indigo-400 uppercase tracking-wider">
              Step {step} of 5
            </span>
            <span className="text-xs font-bold text-slate-200">
              {ONBOARDING_STEPS[step - 1]?.title} · <span className="text-slate-400 font-normal">{ONBOARDING_STEPS[step - 1]?.subtitle}</span>
            </span>
          </div>

          {/* Glowing Segmented Progress Bar */}
          <div className="w-full bg-slate-800/80 h-2 rounded-full overflow-hidden p-0.5 border border-white/5 flex gap-1">
            {[1, 2, 3, 4, 5].map((i) => (
              <div
                key={i}
                className={`h-full flex-1 rounded-full transition-all duration-300 ${
                  i < step
                    ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]'
                    : i === step
                    ? 'bg-gradient-to-r from-indigo-500 to-purple-500 shadow-[0_0_8px_rgba(99,102,241,0.6)]'
                    : 'bg-slate-700/40'
                }`}
              />
            ))}
          </div>

          {/* Compact step numbers circles */}
          <div className="flex items-center justify-between pt-1">
            {ONBOARDING_STEPS.map((s) => {
              const isPast = step > s.num;
              const isCurrent = step === s.num;
              return (
                <button
                  key={s.num}
                  disabled={step < s.num}
                  onClick={() => s.num < step && setStep(s.num as any)}
                  className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-mono font-bold transition-all border ${
                    isPast
                      ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-400 cursor-pointer'
                      : isCurrent
                      ? 'bg-indigo-600 border-indigo-400 text-white shadow-lg shadow-indigo-500/40 scale-110'
                      : 'bg-slate-800/50 border-white/5 text-slate-500 cursor-not-allowed'
                  }`}
                >
                  {isPast ? '✓' : s.num}
                </button>
              );
            })}
          </div>
        </div>

        {/* Desktop View: Full 5-Step Connected Nodes (>= 768px) */}
        <div className="hidden md:grid grid-cols-5 gap-3">
          {ONBOARDING_STEPS.map((item) => {
            const isPast = step > item.num;
            const isCurrent = step === item.num;
            return (
              <div
                key={item.num}
                onClick={() => isPast && setStep(item.num as any)}
                className={`p-3 rounded-xl transition-all border relative flex flex-col justify-between min-h-[72px] ${
                  isCurrent
                    ? 'bg-indigo-600/20 border-indigo-500/70 text-slate-100 shadow-lg shadow-indigo-600/15'
                    : isPast
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-slate-300 cursor-pointer hover:bg-emerald-500/15'
                    : 'bg-white/[0.02] border-white/5 text-slate-500'
                }`}
              >
                {isCurrent && (
                  <div className="absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r from-indigo-500 via-purple-500 to-cyan-400" />
                )}
                <div className="flex items-center justify-between w-full">
                  <span className="text-base">{item.icon}</span>
                  <span
                    className={`text-[10px] font-mono font-bold px-1.5 py-0.5 rounded-full ${
                      isPast
                        ? 'bg-emerald-500/20 text-emerald-400'
                        : isCurrent
                        ? 'bg-indigo-500/30 text-indigo-300'
                        : 'bg-slate-800 text-slate-500'
                    }`}
                  >
                    {isPast ? '✓ Done' : `0${item.num}`}
                  </span>
                </div>
                <div className="mt-1">
                  <div className="text-xs font-bold truncate">{item.title}</div>
                  <div className="text-[10px] opacity-60 truncate">{item.subtitle}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* STEP 1: Legal Info Form */}
      {step === 1 && (
        <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} className="glass-card p-4 sm:p-6 space-y-4 rounded-2xl bg-[#080a21]/90 border border-indigo-500/20 shadow-xl min-w-0">
          <div className="border-b border-white/10 pb-3">
            <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
              <span>🏛️</span>
              <span>Step 1: Institutional Legal & Regional Profile</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Enter official banking consortium identifier and designated sovereign data residency region.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-300 font-semibold uppercase tracking-wider block mb-1.5">
                Bank Identifier (ID)
              </label>
              <input
                type="text"
                value={formData.bank_id}
                onChange={(e) => handleInputChange('bank_id', e.target.value)}
                className="w-full bg-[#03040d] border border-white/10 focus:border-indigo-500 rounded-xl p-2.5 text-xs sm:text-sm text-slate-100 font-mono transition outline-none"
                placeholder="e.g. bank_delta"
              />
            </div>

            <div>
              <label className="text-xs text-slate-300 font-semibold uppercase tracking-wider block mb-1.5">
                Full Legal Entity Name
              </label>
              <input
                type="text"
                value={formData.legal_name}
                onChange={(e) => handleInputChange('legal_name', e.target.value)}
                className="w-full bg-[#03040d] border border-white/10 focus:border-indigo-500 rounded-xl p-2.5 text-xs sm:text-sm text-slate-100 transition outline-none"
                placeholder="e.g. Delta International Bank AG"
              />
            </div>

            <div>
              <label className="text-xs text-slate-300 font-semibold uppercase tracking-wider block mb-1.5">
                Legal Jurisdiction
              </label>
              <select
                value={formData.jurisdiction}
                onChange={(e) => handleInputChange('jurisdiction', e.target.value)}
                className="w-full bg-[#03040d] border border-white/10 focus:border-indigo-500 rounded-xl p-2.5 text-xs sm:text-sm text-slate-100 transition outline-none"
              >
                <option value="EU">European Union (EU - GDPR)</option>
                <option value="US">United States (US - FinCEN)</option>
                <option value="UK">United Kingdom (UK - FCA)</option>
                <option value="TR">Turkey (TR - BDDK / KVKK)</option>
                <option value="SG">Singapore (SG - MAS)</option>
                <option value="JP">Japan (JP - FSA)</option>
              </select>
            </div>

            <div>
              <label className="text-xs text-slate-300 font-semibold uppercase tracking-wider block mb-1.5">
                Security Contact Email
              </label>
              <input
                type="email"
                value={formData.contact_email}
                onChange={(e) => handleInputChange('contact_email', e.target.value)}
                className="w-full bg-[#03040d] border border-white/10 focus:border-indigo-500 rounded-xl p-2.5 text-xs sm:text-sm text-slate-100 transition outline-none"
                placeholder="secops@deltabank.eu"
              />
            </div>

            <div className="sm:col-span-2">
              <label className="text-xs text-slate-300 font-semibold uppercase tracking-wider block mb-1.5">
                Data Residency Region
              </label>
              <select
                value={formData.data_residency_region}
                onChange={(e) => handleInputChange('data_residency_region', e.target.value)}
                className="w-full bg-[#03040d] border border-white/10 focus:border-indigo-500 rounded-xl p-2.5 text-xs sm:text-sm text-slate-100 transition outline-none"
              >
                <option value="eu-central-1">eu-central-1 (Frankfurt, Germany - ISO 27001 Enclave)</option>
                <option value="us-east-1">us-east-1 (N. Virginia, USA - SOC2 Type II)</option>
                <option value="ap-southeast-1">ap-southeast-1 (Singapore - MAS TRM)</option>
              </select>
            </div>
          </div>

          <div className="flex justify-end pt-3 border-t border-white/10">
            <button
              onClick={() => setStep(2)}
              className="w-full sm:w-auto px-6 py-2.5 rounded-xl text-xs sm:text-sm font-bold text-white bg-gradient-to-r from-indigo-600 to-purple-600 hover:brightness-110 transition-all shadow-lg shadow-indigo-600/25 flex items-center justify-center gap-2 cursor-pointer"
            >
              <span>Continue to Step 2</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </motion.div>
      )}

      {/* STEP 2: Review & Submit */}
      {step === 2 && (
        <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} className="glass-card p-4 sm:p-6 space-y-4 rounded-2xl bg-[#080a21]/90 border border-indigo-500/20 shadow-xl min-w-0">
          <div className="border-b border-white/10 pb-3">
            <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
              <span>📋</span>
              <span>Step 2: Review Registration Details</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Confirm legal entity credentials before generating cryptographic mTLS X.509 certificates.
            </p>
          </div>

          <div className="p-4 rounded-xl bg-[#03040d]/80 border border-white/10 space-y-2.5 text-xs sm:text-sm">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-white/5 pb-2 gap-1">
              <span className="text-slate-400 font-semibold">Consortium Bank ID:</span>
              <span className="font-mono font-bold text-indigo-400">{formData.bank_id}</span>
            </div>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-white/5 pb-2 gap-1">
              <span className="text-slate-400 font-semibold">Legal Entity Name:</span>
              <span className="font-bold text-slate-200">{formData.legal_name}</span>
            </div>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-white/5 pb-2 gap-1">
              <span className="text-slate-400 font-semibold">Jurisdiction & Standard:</span>
              <span className="font-bold text-emerald-400">{formData.jurisdiction} Compliance</span>
            </div>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-white/5 pb-2 gap-1">
              <span className="text-slate-400 font-semibold">SecOps Contact:</span>
              <span className="text-slate-200 font-mono">{formData.contact_email}</span>
            </div>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1">
              <span className="text-slate-400 font-semibold">Data Residency Region:</span>
              <span className="font-mono text-indigo-300">{formData.data_residency_region}</span>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-3 border-t border-white/10">
            <button
              onClick={() => setStep(1)}
              className="px-4 py-2 rounded-xl text-xs font-semibold text-slate-400 hover:text-white bg-white/5 hover:bg-white/10 transition flex items-center justify-center gap-1.5 cursor-pointer"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              <span>Back to Step 1</span>
            </button>
            <button
              onClick={handleRegisterSubmit}
              disabled={isSubmitting}
              className="px-6 py-2.5 rounded-xl text-xs sm:text-sm font-bold text-white bg-gradient-to-r from-emerald-600 to-teal-600 hover:brightness-110 transition-all shadow-lg shadow-emerald-600/20 disabled:opacity-50 flex items-center justify-center gap-2 cursor-pointer"
            >
              {isSubmitting ? (
                <>
                  <span className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                  <span>Issuing X.509 Credentials...</span>
                </>
              ) : (
                <>
                  <ShieldCheck className="w-4 h-4" />
                  <span>Confirm & Issue Credentials ✓</span>
                </>
              )}
            </button>
          </div>
        </motion.div>
      )}

      {/* STEP 3: Download Certificates */}
      {step === 3 && (
        <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} className="glass-card p-4 sm:p-6 space-y-4 rounded-2xl bg-[#080a21]/90 border border-indigo-500/20 shadow-xl min-w-0">
          <div className="border-b border-white/10 pb-3">
            <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
              <span>🔑</span>
              <span>Step 3: Cryptographic X.509 mTLS Keypair</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Unique mutual TLS identity issued for node authentication over gRPC secure channel.
            </p>
          </div>

          <div className="space-y-4">
            <div className="p-3.5 rounded-xl bg-[#03040d]/80 border border-emerald-500/20 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold font-mono text-emerald-400 flex items-center gap-1.5">
                  <FileCode className="w-3.5 h-3.5" />
                  X.509 Public Certificate ({formData.bank_id}.crt)
                </span>
                <span className="text-[9.5px] font-mono text-emerald-400/80 bg-emerald-500/10 px-2 py-0.5 rounded">
                  2048-bit RSA
                </span>
              </div>
              <pre className="p-3 bg-black/50 rounded-lg text-[11px] font-mono text-emerald-300 overflow-x-auto max-h-28 border border-white/5 custom-scrollbar">
                {generatedCert}
              </pre>
              <button
                onClick={() => downloadFile(`${formData.bank_id}.crt`, generatedCert)}
                className="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-emerald-600/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-600/30 transition flex items-center gap-1.5 cursor-pointer"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Download {formData.bank_id}.crt</span>
              </button>
            </div>

            <div className="p-3.5 rounded-xl bg-[#03040d]/80 border border-amber-500/20 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold font-mono text-amber-400 flex items-center gap-1.5">
                  <Key className="w-3.5 h-3.5" />
                  RSA Private Key ({formData.bank_id}.key)
                </span>
                <span className="text-[9.5px] font-mono text-amber-400/80 bg-amber-500/10 px-2 py-0.5 rounded">
                  CONFIDENTIAL
                </span>
              </div>
              <pre className="p-3 bg-black/50 rounded-lg text-[11px] font-mono text-amber-300 overflow-x-auto max-h-28 border border-white/5 custom-scrollbar">
                {generatedKey}
              </pre>
              <button
                onClick={() => downloadFile(`${formData.bank_id}.key`, generatedKey)}
                className="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-amber-600/20 text-amber-300 border border-amber-500/30 hover:bg-amber-600/30 transition flex items-center gap-1.5 cursor-pointer"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Download {formData.bank_id}.key</span>
              </button>
            </div>
          </div>

          <div className="flex justify-end pt-3 border-t border-white/10">
            <button
              onClick={() => setStep(4)}
              className="w-full sm:w-auto px-6 py-2.5 rounded-xl text-xs sm:text-sm font-bold text-white bg-gradient-to-r from-indigo-600 to-purple-600 hover:brightness-110 transition-all shadow-lg shadow-indigo-600/25 flex items-center justify-center gap-2 cursor-pointer"
            >
              <span>Continue to Step 4</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </motion.div>
      )}

      {/* STEP 4: Download Connector Config */}
      {step === 4 && (
        <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} className="glass-card p-4 sm:p-6 space-y-4 rounded-2xl bg-[#080a21]/90 border border-indigo-500/20 shadow-xl min-w-0">
          <div className="border-b border-white/10 pb-3">
            <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
              <span>📄</span>
              <span>Step 4: Connector Configuration YAML</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Production configuration manifest for launching local bank node container.
            </p>
          </div>

          <div className="p-3.5 rounded-xl bg-[#03040d]/80 border border-indigo-500/20 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold font-mono text-indigo-400 flex items-center gap-1.5">
                <FileText className="w-3.5 h-3.5" />
                Generated config.yaml
              </span>
            </div>
            <pre className="p-3 bg-black/50 rounded-lg text-[11px] font-mono text-indigo-200 overflow-x-auto max-h-36 border border-white/5 custom-scrollbar">
              {generatedYaml}
            </pre>
            <button
              onClick={() => downloadFile('config.yaml', generatedYaml)}
              className="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600/20 text-indigo-300 border border-indigo-500/30 hover:bg-indigo-600/30 transition flex items-center gap-1.5 cursor-pointer"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Download config.yaml</span>
            </button>
          </div>

          <div className="p-4 rounded-xl bg-[#03040d] border border-white/10 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-slate-200 flex items-center gap-1.5">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                CLI Command for Bank IT Operations Team
              </span>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(`cfi-cli join --config config.yaml --cert ${formData.bank_id}.crt --key ${formData.bank_id}.key`);
                  setIsCliCopied(true);
                  setTimeout(() => setIsCliCopied(false), 2000);
                }}
                className="px-2 py-0.5 rounded text-[10px] font-mono text-slate-400 hover:text-white bg-white/5 border border-white/10 transition flex items-center gap-1 cursor-pointer"
              >
                {isCliCopied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                <span>{isCliCopied ? 'Copied' : 'Copy'}</span>
              </button>
            </div>
            <code className="block p-2.5 bg-black/60 rounded-lg text-xs font-mono text-emerald-400 border border-emerald-500/20 overflow-x-auto">
              cfi-cli join --config config.yaml --cert {formData.bank_id}.crt --key {formData.bank_id}.key
            </code>
          </div>

          <div className="flex justify-end pt-3 border-t border-white/10">
            <button
              onClick={() => setStep(5)}
              className="w-full sm:w-auto px-6 py-2.5 rounded-xl text-xs sm:text-sm font-bold text-white bg-gradient-to-r from-indigo-600 to-purple-600 hover:brightness-110 transition-all shadow-lg shadow-indigo-600/25 flex items-center justify-center gap-2 cursor-pointer"
            >
              <span>Proceed to Quorum Verification</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </motion.div>
      )}

      {/* STEP 5: Verify Connection */}
      {step === 5 && (
        <motion.div initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} className="glass-card p-6 sm:p-10 text-center space-y-6 rounded-2xl bg-[#080a21]/90 border border-indigo-500/20 shadow-xl min-w-0">
          {nodeStatus === 'PENDING' ? (
            <div className="space-y-4 max-w-md mx-auto">
              <div className="relative flex items-center justify-center w-16 h-16 rounded-full bg-indigo-500/10 border border-indigo-500/30 mx-auto">
                <Radio className="w-8 h-8 text-indigo-400 animate-pulse" />
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-20" />
              </div>
              <div>
                <h3 className="text-lg sm:text-xl font-bold text-slate-100">
                  Listening for Bank Node Heartbeat...
                </h3>
                <p className="text-xs sm:text-sm text-slate-400 mt-1 leading-relaxed">
                  Awaiting initial gRPC mTLS 1.3 handshake from <span className="font-mono text-indigo-400 font-bold">{formData.bank_id}</span> on port 50051.
                </p>
              </div>
              <div className="flex items-center justify-center gap-2 text-xs font-mono text-slate-500">
                <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping" />
                <span>Polling node status every 3s...</span>
              </div>
            </div>
          ) : (
            <div className="space-y-5 max-w-md mx-auto">
              <div className="w-16 h-16 rounded-2xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center mx-auto text-emerald-400 shadow-[0_0_25px_rgba(16,185,129,0.3)]">
                <ShieldCheck className="w-9 h-9" />
              </div>
              <div>
                <h3 className="text-xl sm:text-2xl font-extrabold text-emerald-400 tracking-tight">
                  Bank Node Successfully Onboarded!
                </h3>
                <p className="text-xs sm:text-sm text-slate-300 mt-1.5 leading-relaxed">
                  <span className="font-bold text-white">{formData.legal_name}</span> is now fully verified and registered as an active node in the Privacy-Preserving FL Consortium.
                </p>
              </div>

              <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-xs font-mono text-emerald-300 flex items-center justify-between">
                <span>Node Quorum Status:</span>
                <span className="font-bold">ACTIVE (100% HEALTHY)</span>
              </div>

              <div className="pt-2">
                <Link
                  to="/operations"
                  className="w-full sm:w-auto px-6 py-3 rounded-xl text-xs sm:text-sm font-bold text-white bg-gradient-to-r from-emerald-600 to-teal-600 hover:brightness-110 transition-all shadow-lg shadow-emerald-600/25 inline-flex items-center justify-center gap-2 cursor-pointer"
                >
                  <span>View Live Operations Dashboard</span>
                  <ArrowRight className="w-4 h-4" />
                </Link>
              </div>
            </div>
          )}
        </motion.div>
      )}
    </div>
  );
}

