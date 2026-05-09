'use client';
import { useEffect, useState } from 'react';

interface Issue {
  category: string;
  check: string;
  status: 'warn' | 'fail';
  detail: string;
}

interface HealthData {
  status: string;
  issue_count: number;
  issues: Issue[];
  latest_run: string;
  runs: Array<{
    run_date: string;
    overall_status: string;
    summary: { pass: number; warn: number; fail: number };
  }>;
}

const STATUS_COLOR: Record<string, string> = {
  pass: 'bg-emerald-50 border-emerald-200 text-emerald-800',
  warn: 'bg-amber-50 border-amber-200 text-amber-800',
  fail: 'bg-rose-50 border-rose-200 text-rose-800',
};

const STATUS_ICON: Record<string, string> = {
  pass: '✓',
  warn: '⚠',
  fail: '✗',
};

const STATUS_DOT: Record<string, string> = {
  pass: 'bg-emerald-500',
  warn: 'bg-amber-400',
  fail: 'bg-rose-500',
};

const CATEGORY_LABEL: Record<string, string> = {
  freshness: 'Freshness',
  coverage: 'Coverage',
  distribution: 'Distribution',
  sentinels: 'Sentinels',
  cross_table: 'Consistency',
};

function daysSince(dateStr: string): number {
  if (!dateStr) return 999;
  const d = new Date(dateStr);
  const now = new Date();
  return Math.floor((now.getTime() - d.getTime()) / 86400000);
}

export default function PipelineHealthBanner() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    fetch('/api/pipeline-health', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : null)
      .then((d: HealthData | null) => { if (d) setHealth(d); })
      .catch(() => {});
  }, []);

  // Don't render if no data or all clear
  if (!health || health.status === 'no_data') return null;
  if (health.status === 'pass' && !expanded) return (
    <button
      onClick={() => setExpanded(true)}
      className="flex items-center gap-2 px-3 py-1.5 rounded border bg-emerald-50 border-emerald-200 text-emerald-700 text-[10px] font-mono tracking-wider hover:bg-emerald-100 transition-colors w-full"
    >
      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
      PIPELINE HEALTH — ALL CLEAR
      <span className="ml-auto text-emerald-500 opacity-60">last run {health.latest_run}</span>
    </button>
  );

  const fails = health.issues.filter(i => i.status === 'fail');
  const warns = health.issues.filter(i => i.status === 'warn');
  const overallStatus = health.status || 'pass';
  const colorClass = STATUS_COLOR[overallStatus] || STATUS_COLOR.warn;
  const staleDays = daysSince(health.latest_run);

  return (
    <div className={`rounded border ${colorClass} text-[11px] font-mono`}>
      {/* Header row */}
      <button
        className="w-full flex items-center gap-2 px-3 py-2 text-left"
        onClick={() => setExpanded(e => !e)}
      >
        <span className={`w-1.5 h-1.5 rounded-full inline-block ${STATUS_DOT[overallStatus] || 'bg-gray-400'}`} />
        <span className="font-semibold tracking-wider">
          PIPELINE HEALTH {STATUS_ICON[overallStatus]}
        </span>
        {fails.length > 0 && (
          <span className="bg-rose-500 text-white text-[9px] px-1.5 py-0.5 rounded-full font-bold">
            {fails.length} FAIL
          </span>
        )}
        {warns.length > 0 && (
          <span className="bg-amber-400 text-amber-900 text-[9px] px-1.5 py-0.5 rounded-full font-bold">
            {warns.length} WARN
          </span>
        )}
        <span className="ml-auto opacity-60 text-[10px]">
          {staleDays === 0 ? 'today' : staleDays === 1 ? 'yesterday' : `${staleDays}d ago`}
          {' · '}{expanded ? 'collapse ▲' : 'details ▼'}
        </span>
      </button>

      {/* Expanded issues */}
      {expanded && (
        <div className="border-t border-current border-opacity-20 px-3 py-2 space-y-1">
          {health.issues.length === 0 ? (
            <p className="opacity-70">No issues detected. All checks passing.</p>
          ) : (
            health.issues.map((issue, idx) => (
              <div key={idx} className="flex items-start gap-2">
                <span className={`mt-0.5 shrink-0 ${issue.status === 'fail' ? 'text-rose-600' : 'text-amber-600'}`}>
                  {STATUS_ICON[issue.status]}
                </span>
                <div className="flex-1 min-w-0">
                  <span className="font-semibold">{CATEGORY_LABEL[issue.category] ?? issue.category}</span>
                  <span className="opacity-60 mx-1">·</span>
                  <span className="opacity-80">{issue.check.replace(/_/g, ' ')}</span>
                  <span className="opacity-50 ml-2">— {issue.detail}</span>
                </div>
              </div>
            ))
          )}

          {/* History strip */}
          {health.runs.length > 1 && (
            <div className="pt-2 flex items-center gap-1.5 opacity-70">
              <span className="text-[9px] tracking-wider mr-1">7-DAY HISTORY</span>
              {health.runs.slice(0, 7).map(run => (
                <span
                  key={run.run_date}
                  title={`${run.run_date}: ${run.overall_status}`}
                  className={`w-3 h-3 rounded-sm inline-block ${STATUS_DOT[run.overall_status] || 'bg-gray-300'}`}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
