import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import {
  Sliders,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  ExternalLink,
  ChevronDown,
  ArrowLeft,
  Wand2,
  Check,
} from 'lucide-react';
import { CounterfactualReport } from '../types';
import { fetchCounterfactual } from '../services/api';
import { useAlert, useAlerts } from '../api/queries';
import { BANK_NAMES } from '../api/types';

export interface CounterfactualWorkbenchProps {
  initialAlertId?: string;
  initialAmount?: number;
  initialVelocity?: number;
  initialMerchantRisk?: number;
}

export const CounterfactualWorkbench: React.FC<CounterfactualWorkbenchProps> = ({
  initialAlertId,
  initialAmount,
  initialVelocity,
  initialMerchantRisk,
}) => {
  // 1. URL Search Params Resolution
  const [searchParams, setSearchParams] = useSearchParams();
  const urlAlertId = searchParams?.get('alert_id') || '';
  const [selectedAlertId, setSelectedAlertId] = useState<string>(
    initialAlertId || urlAlertId || ''
  );

  // Synchronize when URL search param ?alert_id=... updates externally
  useEffect(() => {
    if (urlAlertId && urlAlertId !== selectedAlertId) {
      setSelectedAlertId(urlAlertId);
    }
  }, [urlAlertId, selectedAlertId]);

  // 2. Load Real Alert Feed & Active Alert Telemetry
  const alertsQuery = useAlerts();
  const alertsList = useMemo(
    () => (Array.isArray(alertsQuery.data) ? alertsQuery.data : []),
    [alertsQuery.data]
  );

  // Effective alert identifier: selected -> first from alerts list -> initial/url fallback
  const effectiveAlertId =
    selectedAlertId ||
    (alertsList.length > 0 ? alertsList[0]?.id : '') ||
    initialAlertId ||
    urlAlertId ||
    '';

  const alertQuery = useAlert(effectiveAlertId || undefined);
  const activeAlert = (alertQuery.data && typeof alertQuery.data === 'object' && !Array.isArray(alertQuery.data) && (alertQuery.data as any).id)
    ? alertQuery.data
    : null;
  const isAlertLoading = alertQuery.isLoading;

  // Synchronize to the first active alert in the consortium feed if selected alert is absent
  useEffect(() => {
    if (!urlAlertId && !initialAlertId && alertsList.length > 0) {
      if (!selectedAlertId || !alertsList.some((a: any) => a.id === selectedAlertId)) {
        const firstAlert = alertsList[0];
        if (firstAlert?.id) {
          setSelectedAlertId(firstAlert.id);
        }
      }
    }
  }, [alertsList, urlAlertId, initialAlertId, selectedAlertId]);

  // 3. Interactive Signal Sliders
  const [amount, setAmount] = useState<number>(initialAmount ?? 15000);
  const [velocity, setVelocity] = useState<number>(initialVelocity ?? 28);
  const [merchantRisk, setMerchantRisk] = useState<number>(initialMerchantRisk ?? 0.95);
  const [targetScore, setTargetScore] = useState<number>(350.0);
  const [report, setReport] = useState<CounterfactualReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Calibrate baseline values when activeAlert loads or user switches alert
  useEffect(() => {
    if (activeAlert) {
      const baseScore = activeAlert.risk_score;
      if (baseScore >= 800) {
        setAmount(25000);
        setVelocity(32);
        setMerchantRisk(0.95);
      } else if (baseScore >= 600) {
        setAmount(15000);
        setVelocity(24);
        setMerchantRisk(0.80);
      } else {
        setAmount(6000);
        setVelocity(12);
        setMerchantRisk(0.40);
      }
      setReport(null);
      setError(null);
    }
  }, [activeAlert]);

  // Quick Alert Switcher Dropdown State & Click-Outside Dismiss
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  const handleSelectAlert = (newId: string) => {
    setSelectedAlertId(newId);
    setSearchParams({ alert_id: newId });
    setIsDropdownOpen(false);
  };

  // Auto-scroll to remediation report when simulation completes
  const reportRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (report && reportRef.current) {
      const timer = setTimeout(() => {
        if (reportRef.current && typeof reportRef.current.scrollIntoView === 'function') {
          reportRef.current.scrollIntoView({
            behavior: 'smooth',
            block: 'start',
          });
        }
      }, 60);
      return () => clearTimeout(timer);
    }
  }, [report]);

  const calculateDynamicScore = () => {
    const baseAmountRisk = Math.min(300, (amount / 20000) * 300);
    const velocityRisk = Math.min(250, (velocity / 30) * 250);
    const merchRisk = merchantRisk * 250;
    return Math.round(Math.min(990, baseAmountRisk + velocityRisk + merchRisk + 120));
  };

  const currentScore = calculateDynamicScore();
  const isSuspicious = currentScore >= (targetScore + 50);

  const handleSimulateCounterfactual = async () => {
    setLoading(true);
    setError(null);
    try {
      const targetAlertId = selectedAlertId || effectiveAlertId || 'alt_1001';
      const res = await fetchCounterfactual({
        alert_id: targetAlertId,
        target_score: targetScore,
        amount,
        velocity,
        merchant_risk: merchantRisk,
      });
      setReport(res);
    } catch (err: any) {
      setError(err?.message || 'Failed to simulate counterfactual path');
    } finally {
      setLoading(false);
    }
  };

  const handleResetSliders = () => {
    setAmount(15000);
    setVelocity(28);
    setMerchantRisk(0.95);
    setTargetScore(350.0);
    setReport(null);
    setError(null);
  };

  const handleApplySuggestedValues = () => {
    if (!report?.changes) return;
    for (const change of report.changes) {
      const feat = change.feature.toLowerCase();
      const suggested = Number(change.suggested_value);
      if (isNaN(suggested)) continue;
      if (feat.includes('amount')) {
        setAmount(Math.round(suggested));
      } else if (feat.includes('velocity')) {
        setVelocity(Math.round(suggested));
      } else if (feat.includes('merchant')) {
        setMerchantRisk(suggested > 1 ? Number((suggested / 100).toFixed(2)) : Number(suggested.toFixed(2)));
      }
    }
  };

  const baselineRiskScore = activeAlert?.risk_score ?? 820;
  const scoreDelta = baselineRiskScore - currentScore;
  const bankName = activeAlert?.bank_id
    ? BANK_NAMES[activeAlert.bank_id] || activeAlert.bank_id
    : 'Consortium Node';

  const currentSelectedAlert = useMemo(
    () => alertsList.find((a: any) => a.id === effectiveAlertId) || activeAlert,
    [alertsList, effectiveAlertId, activeAlert]
  );

  const displayAlertLabel = useMemo(() => {
    if (!effectiveAlertId) return 'Select Target Alert';
    const shortId =
      effectiveAlertId.length > 18
        ? `${effectiveAlertId.slice(0, 8)}...${effectiveAlertId.slice(-4)}`
        : effectiveAlertId;
    const bank = currentSelectedAlert?.bank_id
      ? BANK_NAMES[currentSelectedAlert.bank_id] || currentSelectedAlert.bank_id
      : '';
    return bank ? `${shortId} (${bank})` : shortId;
  }, [effectiveAlertId, currentSelectedAlert]);

  return (
    <div className="space-y-5 sm:space-y-6 w-full max-w-full min-w-0 overflow-x-hidden">
      {/* Top Breadcrumb & Return to Alerts Link */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 w-full min-w-0">
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <Link
            to="/alerts"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs font-semibold text-slate-300 hover:text-white hover:border-slate-700 transition-all shrink-0"
            title="Return to Consortium Alerts Feed"
          >
            <ArrowLeft className="h-3.5 w-3.5 shrink-0" />
            <span>Back to Alerts Feed</span>
          </Link>
          <span className="text-xs text-slate-600 shrink-0">/</span>
          <span className="text-xs font-mono font-semibold text-cyan-400 truncate max-w-[140px] sm:max-w-none">
            {effectiveAlertId || 'Select Target Alert'}
          </span>
        </div>

        {/* Quick Alert Switcher Dropdown */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-1.5 sm:gap-2 w-full sm:w-auto min-w-0">
          <span className="text-xs text-slate-400 font-medium shrink-0">Target Alert:</span>
          <div ref={dropdownRef} className="relative w-full sm:w-72 md:w-80 min-w-0">
            {/* Native hidden select for form & automated test query parity */}
            <select
              value={effectiveAlertId}
              onChange={(e) => handleSelectAlert(e.target.value)}
              className="sr-only"
              aria-label="Select Target Alert for Simulation"
              tabIndex={-1}
            >
              {alertsList.length > 0 ? (
                alertsList.map((alt) => (
                  <option key={alt.id} value={alt.id}>
                    {alt.id.length > 18 ? `${alt.id.slice(0, 8)}...${alt.id.slice(-4)}` : alt.id} ({BANK_NAMES[alt.bank_id] || alt.bank_id} - Score: {alt.risk_score})
                  </option>
                ))
              ) : (
                <option value={effectiveAlertId || 'alt_1001'}>
                  {effectiveAlertId ? (effectiveAlertId.length > 18 ? `${effectiveAlertId.slice(0, 8)}...${effectiveAlertId.slice(-4)}` : effectiveAlertId) : 'alt_1001'}
                </option>
              )}
            </select>

            {/* Custom Interactive Dropdown Button */}
            <button
              type="button"
              onClick={() => setIsDropdownOpen((prev) => !prev)}
              aria-label="Target Alert Selector"
              aria-haspopup="listbox"
              aria-expanded={isDropdownOpen}
              className="w-full flex items-center justify-between gap-2 bg-slate-900/90 border border-slate-700 hover:border-cyan-500/50 text-slate-200 text-xs font-mono rounded-lg px-3 py-1.5 outline-none cursor-pointer transition-colors shadow-sm focus:border-cyan-500 min-w-0"
              title="Click to switch target alert"
            >
              <span className="truncate text-left flex-1 min-w-0">
                {displayAlertLabel}
              </span>
              <ChevronDown
                className={`h-3.5 w-3.5 text-slate-400 shrink-0 transition-transform duration-200 ${
                  isDropdownOpen ? 'rotate-180 text-cyan-400' : ''
                }`}
              />
            </button>

            {/* Custom Responsive Dropdown Menu */}
            {isDropdownOpen && (
              <div
                role="listbox"
                aria-label="Target Alert Options"
                className="absolute left-0 right-0 sm:left-auto sm:right-0 top-full mt-1.5 w-full sm:w-84 max-w-[calc(100vw-2rem)] bg-slate-900/98 backdrop-blur-xl border border-slate-700/80 rounded-xl shadow-2xl z-50 overflow-hidden divide-y divide-slate-800/60 max-h-72 overflow-y-auto"
              >
                {alertsList.length > 0 ? (
                  alertsList.map((alt) => {
                    const isSelected = alt.id === effectiveAlertId;
                    const scoreColor =
                      alt.risk_score >= 800
                        ? 'text-rose-400 bg-rose-500/10 border-rose-500/20'
                        : alt.risk_score >= 600
                        ? 'text-amber-400 bg-amber-500/10 border-amber-500/20'
                        : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20';

                    return (
                      <button
                        key={alt.id}
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        onClick={() => handleSelectAlert(alt.id)}
                        className={`w-full text-left px-3 py-2 flex items-center justify-between gap-2.5 transition-colors cursor-pointer ${
                          isSelected
                            ? 'bg-cyan-500/10 text-cyan-200'
                            : 'hover:bg-slate-800/80 text-slate-300 hover:text-white'
                        }`}
                      >
                        <div className="flex flex-col min-w-0 flex-1">
                          <div className="flex items-center gap-1.5 min-w-0">
                            <span className="font-mono text-xs font-semibold text-slate-200 truncate">
                              {alt.id.length > 18 ? `${alt.id.slice(0, 8)}...${alt.id.slice(-4)}` : alt.id}
                            </span>
                            {isSelected && (
                              <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 shrink-0" />
                            )}
                          </div>
                          <span className="text-[11px] text-slate-400 truncate">
                            {BANK_NAMES[alt.bank_id] || alt.bank_id}
                          </span>
                        </div>

                        <div className="flex items-center gap-1.5 shrink-0">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold border ${scoreColor}`}
                          >
                            Score: {alt.risk_score}
                          </span>
                          {isSelected && (
                            <Check className="h-3.5 w-3.5 text-cyan-400 shrink-0" />
                          )}
                        </div>
                      </button>
                    );
                  })
                ) : (
                  <div className="px-3 py-2.5 text-xs text-slate-400 font-mono">
                    {effectiveAlertId || 'alt_1001'} (Current Alert)
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Header Banner */}
      <div className="glass-card p-4 sm:p-5 rounded-2xl flex flex-col md:flex-row md:items-center justify-between gap-4 border border-slate-800 shadow-xl bg-gradient-to-r from-slate-950 via-[#0a0d24] to-slate-950 min-w-0">
        <div className="flex items-start sm:items-center gap-3.5 min-w-0">
          <div className="p-2.5 rounded-xl bg-gradient-to-br from-cyan-500/20 via-purple-500/20 to-indigo-500/20 text-cyan-400 border border-cyan-500/30 shadow-inner shrink-0">
            <Sliders className="h-5 w-5 sm:h-6 sm:w-6" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-bold text-base sm:text-lg text-slate-100 tracking-tight">
                Counterfactual Remediation Workbench
              </h2>
              <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30 shrink-0">
                GDPR Art. 22 XAI
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-1 leading-relaxed">
              Interactive Minimum Remediating Feature Path Simulator — Ingests live alert parameters and derives minimal actionable signal perturbations to clear AML threshold.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 self-start md:self-auto shrink-0 w-full sm:w-auto">
          <button
            onClick={handleResetSliders}
            className="w-full sm:w-auto flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-xl bg-slate-900/90 border border-slate-700 hover:border-slate-500 text-xs font-semibold text-slate-300 hover:text-white transition-all shadow-sm cursor-pointer"
            title="Reset sliders to baseline values"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            <span>Reset Parameters</span>
          </button>
        </div>
      </div>

      {/* Active Alert Context Information Card */}
      {isAlertLoading ? (
        <div
          role="status"
          aria-live="polite"
          className="p-4 rounded-xl bg-slate-900 border border-slate-800 text-xs font-mono text-slate-200 flex items-center gap-2.5 shadow-sm min-w-0"
        >
          <span className="w-3.5 h-3.5 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin shrink-0" aria-hidden="true" />
          <span className="truncate">Loading alert details and telemetry for {effectiveAlertId}...</span>
        </div>
      ) : activeAlert ? (
        <div className="p-3.5 sm:p-4 rounded-xl bg-slate-900/70 border border-indigo-500/25 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-lg min-w-0">
          <div className="space-y-1.5 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-bold text-indigo-300 font-mono break-all sm:break-normal">
                {activeAlert.id}
              </span>
              <span className="text-xs text-slate-400">•</span>
              <span className="text-xs font-semibold text-slate-200">
                {bankName}
              </span>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider shrink-0 ${
                  activeAlert.severity === 'critical'
                    ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                    : 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                }`}
              >
                {activeAlert.severity}
              </span>
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-400 flex-wrap">
              <span>Txn: <span className="font-mono text-slate-300 break-all">{activeAlert.transaction_id || 'N/A'}</span></span>
              <span>•</span>
              <span>Baseline Risk: <span className="font-mono font-bold text-rose-400">{activeAlert.risk_score} / 1000</span></span>
              <span>•</span>
              <span className="break-all sm:break-normal">Triggers: <span className="font-mono text-cyan-300">{(activeAlert.reason_codes || []).join(', ') || 'N/A'}</span></span>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0 flex-wrap w-full sm:w-auto pt-1 sm:pt-0">
            <Link
              to={`/alerts?alert_id=${encodeURIComponent(activeAlert.id)}`}
              className="flex-1 sm:flex-initial justify-center px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 transition flex items-center gap-1.5"
              title="Inspect alert in full explainability triage portal"
            >
              <span>View Full Alert</span>
              <ExternalLink className="h-3 w-3" />
            </Link>
            {activeAlert.involved_entity_ids && activeAlert.involved_entity_ids[0] && (
              <Link
                to={`/graph?entity_id=${encodeURIComponent(activeAlert.involved_entity_ids[0])}&depth=2`}
                className="flex-1 sm:flex-initial justify-center px-3 py-1.5 rounded-lg text-xs font-semibold bg-purple-600/20 hover:bg-purple-600/30 text-purple-300 border border-purple-500/30 transition flex items-center gap-1.5"
                title="Trace 2-hop ego network for suspect account"
              >
                <span>Trace Graph</span>
                <span className="text-[10px]">➔</span>
              </Link>
            )}
          </div>
        </div>
      ) : null}

      {/* Main Workbench Grid: Sliders on Left, Evaluator & Remediation Path on Right */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Interactive Feature Adjustment Sliders */}
        <div className="lg:col-span-5 glass-card rounded-2xl p-4 sm:p-5 md:p-6 border border-slate-800 space-y-5 sm:space-y-6 shadow-xl bg-slate-950/70 min-w-0">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 sm:gap-2 border-b border-slate-800 pb-3">
            <h3 className="font-bold text-xs text-slate-200 uppercase tracking-wider flex items-center gap-2">
              <span className="p-1 rounded-md bg-cyan-500/10 text-cyan-400 shrink-0">🎛️</span>
              <span className="truncate">Adjust Transaction Signals</span>
            </h3>
            <span className="text-[10px] sm:text-[11px] font-mono text-slate-400 shrink-0">
              Perturbation Matrix
            </span>
          </div>

          {/* Quick Preset Buttons */}
          <div className="space-y-1.5">
            <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
              Quick Scenario Presets
            </span>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <button
                type="button"
                onClick={() => {
                  setAmount(25000);
                  setVelocity(35);
                  setMerchantRisk(0.95);
                }}
                className="p-2.5 sm:p-2 rounded-lg bg-rose-950/30 border border-rose-500/30 hover:border-rose-500 text-left text-xs sm:text-[11px] text-rose-200 transition-all cursor-pointer flex sm:flex-col items-center sm:items-start justify-between sm:justify-start gap-1"
              >
                <div className="font-bold">Smurfing Burst</div>
                <div className="text-[10px] text-rose-300 font-mono shrink-0">$25K • 35 tx/h</div>
              </button>
              <button
                type="button"
                onClick={() => {
                  setAmount(12000);
                  setVelocity(18);
                  setMerchantRisk(0.65);
                }}
                className="p-2.5 sm:p-2 rounded-lg bg-amber-950/30 border border-amber-500/30 hover:border-amber-500 text-left text-xs sm:text-[11px] text-amber-200 transition-all cursor-pointer flex sm:flex-col items-center sm:items-start justify-between sm:justify-start gap-1"
              >
                <div className="font-bold">Wire Outlier</div>
                <div className="text-[10px] text-amber-300 font-mono shrink-0">$12K • 18 tx/h</div>
              </button>
              <button
                type="button"
                onClick={() => {
                  setAmount(1200);
                  setVelocity(4);
                  setMerchantRisk(0.20);
                }}
                className="p-2.5 sm:p-2 rounded-lg bg-emerald-950/30 border border-emerald-500/30 hover:border-emerald-500 text-left text-xs sm:text-[11px] text-emerald-200 transition-all cursor-pointer flex sm:flex-col items-center sm:items-start justify-between sm:justify-start gap-1"
              >
                <div className="font-bold">Low-Risk Retail</div>
                <div className="text-[10px] text-emerald-300 font-mono shrink-0">$1.2K • 4 tx/h</div>
              </button>
            </div>
          </div>

          {/* Slider 1: Transaction Amount */}
          <div className="space-y-2">
            <div className="flex justify-between items-center text-xs">
              <span className="text-slate-300 font-semibold flex items-center gap-1.5">
                <span>💵</span> Transaction Amount ($)
              </span>
              <span className="font-mono text-cyan-400 font-bold bg-cyan-950/40 px-2 py-0.5 rounded border border-cyan-500/30">
                ${amount.toLocaleString()}
              </span>
            </div>
            <input
              type="range"
              min="100"
              max="50000"
              step="500"
              value={amount}
              onChange={(e) => setAmount(Number(e.target.value))}
              className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
              aria-label="Transaction Amount Slider"
            />
            <div className="flex justify-between text-[10px] text-slate-400 font-mono gap-1">
              <span>$100</span>
              <span className="text-center truncate px-1">
                <span className="hidden sm:inline">$25,000 (Structuring Limit)</span>
                <span className="sm:hidden">$25K Limit</span>
              </span>
              <span>$50,000</span>
            </div>
          </div>

          {/* Slider 2: Velocity */}
          <div className="space-y-2">
            <div className="flex justify-between items-center text-xs">
              <span className="text-slate-300 font-semibold flex items-center gap-1.5">
                <span>⚡</span> Hourly Velocity (txns/hr)
              </span>
              <span className="font-mono text-cyan-400 font-bold bg-cyan-950/40 px-2 py-0.5 rounded border border-cyan-500/30">
                {velocity} txns
              </span>
            </div>
            <input
              type="range"
              min="1"
              max="40"
              step="1"
              value={velocity}
              onChange={(e) => setVelocity(Number(e.target.value))}
              className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
              aria-label="Hourly Velocity Slider"
            />
            <div className="flex justify-between text-[10px] text-slate-400 font-mono gap-1">
              <span>1 txn/h</span>
              <span className="text-center truncate px-1">
                <span className="hidden sm:inline">20 txns/h (Burst Cap)</span>
                <span className="sm:hidden">20 tx/h Cap</span>
              </span>
              <span>40 txns/h</span>
            </div>
          </div>

          {/* Slider 3: Merchant Risk */}
          <div className="space-y-2">
            <div className="flex justify-between items-center text-xs">
              <span className="text-slate-300 font-semibold flex items-center gap-1.5">
                <span>🏢</span> Merchant Risk Category
              </span>
              <span className="font-mono text-cyan-400 font-bold bg-cyan-950/40 px-2 py-0.5 rounded border border-cyan-500/30">
                {(merchantRisk * 100).toFixed(0)}% Risk
              </span>
            </div>
            <input
              type="range"
              min="0.05"
              max="0.99"
              step="0.05"
              value={merchantRisk}
              onChange={(e) => setMerchantRisk(Number(e.target.value))}
              className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
              aria-label="Merchant Risk Slider"
            />
            <div className="flex justify-between text-[10px] text-slate-400 font-mono gap-1">
              <span className="truncate">5% <span className="hidden sm:inline">(Low Risk Groceries)</span></span>
              <span>50%</span>
              <span className="truncate text-right">99% <span className="hidden sm:inline">(Crypto / High Risk MSB)</span></span>
            </div>
          </div>

          {/* Slider 4: Target Threshold */}
          <div className="space-y-2 pt-2 border-t border-slate-800/80">
            <div className="flex justify-between items-center text-xs">
              <span className="text-slate-300 font-semibold flex items-center gap-1.5">
                <span>🎯</span> Remediation Target Score
              </span>
              <span className="font-mono text-purple-400 font-bold bg-purple-950/40 px-2 py-0.5 rounded border border-purple-500/30">
                {targetScore} / 1000
              </span>
            </div>
            <input
              type="range"
              min="100"
              max="600"
              step="25"
              value={targetScore}
              onChange={(e) => setTargetScore(Number(e.target.value))}
              className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-purple-400"
              aria-label="Remediation Target Score Slider"
            />
            <div className="flex justify-between text-[10px] text-slate-400 font-mono gap-1">
              <span>100 <span className="hidden sm:inline">(Ultra Strict)</span></span>
              <span>350 <span className="hidden sm:inline">(Standard Clearing)</span></span>
              <span>600 <span className="hidden sm:inline">(Threshold)</span></span>
            </div>
          </div>

          {/* Simulation Trigger Button */}
          <button
            onClick={handleSimulateCounterfactual}
            disabled={loading}
            className="w-full py-3 rounded-xl bg-gradient-to-r from-cyan-500 via-indigo-600 to-purple-600 font-bold text-xs text-white shadow-lg shadow-indigo-600/25 hover:brightness-110 active:scale-[0.99] transition-all flex items-center justify-center gap-2 cursor-pointer disabled:opacity-50 min-h-[44px]"
          >
            {loading ? (
              <>
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                <span>Calculating Optimal Path...</span>
              </>
            ) : (
              <>
                <span>Simulate Optimal Counterfactual Path</span>
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </div>

        {/* Live Risk Score Gauge & Remediation Path */}
        <div className="lg:col-span-7 space-y-5 sm:space-y-6 min-w-0">
          {/* Gauge Status Card */}
          <div className="glass-card rounded-2xl p-4 sm:p-5 md:p-6 border border-slate-800 flex flex-col sm:flex-row items-center justify-between gap-6 shadow-xl bg-slate-950/70 min-w-0">
            <div className="space-y-2 text-center sm:text-left min-w-0">
              <span className="text-xs text-slate-400 uppercase font-bold tracking-wider flex items-center justify-center sm:justify-start gap-1.5">
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                Live Evaluated Dynamic Risk Score
              </span>
              <div className="flex items-baseline justify-center sm:justify-start gap-2">
                <span
                  className={`text-4xl sm:text-5xl font-black font-mono tracking-tight ${
                    isSuspicious ? 'text-rose-500' : 'text-emerald-400'
                  }`}
                >
                  {currentScore}
                </span>
                <span className="text-slate-400 text-sm font-semibold">/ 1000</span>
              </div>

              {/* Status and Baseline Delta */}
              <div className="flex items-center justify-center sm:justify-start gap-2 flex-wrap">
                {isSuspicious ? (
                  <span className="flex items-center gap-1.5 text-xs text-rose-400 bg-rose-500/10 px-3 py-1 rounded-full border border-rose-500/25 font-bold">
                    <AlertTriangle className="h-3.5 w-3.5" /> High Risk (Fraud Suspected)
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 text-xs text-emerald-400 bg-emerald-500/10 px-3 py-1 rounded-full border border-emerald-500/25 font-bold">
                    <ShieldCheck className="h-3.5 w-3.5" /> Cleared (Below Threshold)
                  </span>
                )}

                <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-300">
                  Target: &lt;{targetScore}
                </span>

                {scoreDelta !== 0 && (
                  <span
                    className={`text-[11px] font-mono font-bold px-2 py-0.5 rounded ${
                      scoreDelta > 0
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                        : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                    }`}
                  >
                    {scoreDelta > 0 ? `-${scoreDelta} pts` : `+${Math.abs(scoreDelta)} pts`} vs baseline
                  </span>
                )}
              </div>
            </div>

            {/* Score Radial Bar */}
            <div className="relative w-28 h-28 sm:w-32 sm:h-32 flex items-center justify-center shrink-0">
              <svg className="w-full h-full transform -rotate-90" viewBox="0 0 128 128">
                <circle
                  cx="64"
                  cy="64"
                  r="52"
                  stroke="currentColor"
                  strokeWidth="10"
                  className="text-slate-900"
                  fill="transparent"
                />
                <circle
                  cx="64"
                  cy="64"
                  r="52"
                  stroke="currentColor"
                  strokeWidth="10"
                  className={`transition-all duration-500 ease-out ${
                    isSuspicious ? 'text-rose-500' : 'text-emerald-400'
                  }`}
                  fill="transparent"
                  strokeDasharray={326}
                  strokeDashoffset={326 - 326 * (currentScore / 1000)}
                  strokeLinecap="round"
                />
              </svg>
              <div className="absolute text-center">
                <span className="font-mono font-black text-slate-100 text-base sm:text-lg">
                  {((currentScore / 1000) * 100).toFixed(0)}%
                </span>
                <div className="text-[8px] sm:text-[9px] uppercase tracking-wider text-slate-400 font-bold">
                  Risk Level
                </div>
              </div>
            </div>
          </div>

          {/* Error Banner */}
          {error && (
            <div className="bg-rose-500/10 border border-rose-500/25 text-rose-300 p-3.5 sm:p-4 rounded-xl text-xs flex items-center gap-3 min-w-0">
              <AlertTriangle className="h-5 w-5 text-rose-400 shrink-0" />
              <div className="min-w-0">
                <p className="font-bold">Counterfactual Simulation Error</p>
                <p className="text-rose-300/80 mt-0.5 break-words">{error}</p>
              </div>
            </div>
          )}

          {/* Counterfactual Remediation Path Recommendations */}
          {report && (
            <div
              ref={reportRef}
              id="remediation-action-path"
              className="glass-card rounded-2xl p-4 sm:p-5 md:p-6 border border-slate-800 space-y-4 shadow-xl bg-slate-950/70 min-w-0 scroll-mt-20 sm:scroll-mt-24 transition-all duration-300"
            >
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
                <div className="flex items-center gap-2 min-w-0">
                  <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0" />
                  <div className="min-w-0">
                    <h3 className="font-bold text-xs sm:text-sm text-slate-100 uppercase tracking-wider truncate">
                      Remediation Action Path ({report.changes.length} Required Adjustments)
                    </h3>
                    <p className="text-[11px] text-slate-400">
                      Target cleared score: <span className="font-mono font-bold text-emerald-400">{report.remediated_score}</span> (from baseline {report.original_score})
                    </p>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={handleApplySuggestedValues}
                  className="w-full sm:w-auto px-3 py-1.5 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 hover:text-emerald-100 border border-emerald-500/30 text-xs font-bold transition flex items-center justify-center gap-1.5 shrink-0 cursor-pointer"
                  title="Update sliders to suggested remediation values to clear risk score"
                >
                  <Wand2 className="h-3.5 w-3.5" />
                  <span>Apply Suggested Values</span>
                </button>
              </div>

              {/* Adjustment Changes Cards */}
              <div className="space-y-3">
                {report.changes.map((c, idx) => (
                  <div
                    key={idx}
                    className="bg-slate-900/90 p-3.5 sm:p-4 rounded-xl border border-slate-800/80 text-xs flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 min-w-0"
                  >
                    <div className="min-w-0">
                      <span className="font-mono text-cyan-400 font-bold uppercase text-[11px] tracking-wide">
                        {c.feature.replace(/_/g, ' ')}
                      </span>
                      <p className="text-slate-300 mt-1 leading-relaxed">
                        {c.description}
                      </p>
                    </div>
                    <div className="text-left sm:text-right shrink-0 pt-2 sm:pt-0 border-t sm:border-t-0 border-slate-800 flex sm:flex-col items-center sm:items-end justify-between sm:justify-start gap-1">
                      <div>
                        <span className="text-rose-400 font-mono line-through mr-2 font-semibold">
                          {String(c.original_value)}
                        </span>
                        <span className="text-emerald-400 font-mono font-black text-sm">
                          &rarr; {String(c.suggested_value)}
                        </span>
                      </div>
                      {c.delta !== undefined && (
                        <div className="text-[10px] font-mono text-slate-400">
                          Delta: {c.delta > 0 ? `+${c.delta}` : c.delta}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {/* Regulatory Narrative Summary */}
              {report.summary_text && (
                <div className="bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 p-3.5 rounded-xl text-xs leading-relaxed flex items-start gap-2 min-w-0">
                  <Sparkles className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                  <p className="min-w-0">{report.summary_text}</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default CounterfactualWorkbench;
