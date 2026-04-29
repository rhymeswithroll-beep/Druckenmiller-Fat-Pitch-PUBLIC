'use client';

import React, { useState } from 'react';
import type { ConvergenceSignal } from '@/lib/api';
import ModuleStrip from '@/components/ModuleStrip';
import { scoreColor } from '@/lib/modules';
import { cs, fg } from '@/lib/styles';

type SortKey = 'convergence_score' | 'module_count';

function convictionColor(conviction: string) {
  const u = conviction?.toUpperCase();
  if (u === 'HIGH') return 'text-emerald-600';
  if (u === 'NOTABLE' || u === 'MEDIUM') return 'text-amber-600';
  return 'text-gray-500';
}

function convictionBadgeStyle(conviction: string) {
  const u = conviction?.toUpperCase();
  if (u === 'HIGH') return { bg: 'rgba(5,150,105,0.1)', fg: '#059669' };
  if (u === 'NOTABLE' || u === 'MEDIUM') return { bg: 'rgba(217,119,6,0.1)', fg: '#d97706' };
  return { bg: 'rgba(156,163,175,0.1)', fg: '#6b7280' };
}

function exportCSV(rows: ConvergenceSignal[]) {
  const cols = ['symbol', 'conviction_level', 'convergence_score', 'module_count', 'narrative'] as const;
  const header = cols.join(',');
  const lines = rows.map(r =>
    cols.map(c => `"${String((r as any)[c] ?? '').replace(/"/g, '""')}"`).join(',')
  );
  const blob = new Blob([header + '\n' + lines.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `conviction_signals_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

interface Props {
  data: ConvergenceSignal[];
}

export default function ConvergenceHeatmap({ data }: Props) {
  const [sortBy, setSortBy] = useState<SortKey>('convergence_score');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [filterConviction, setFilterConviction] = useState<string>('HIGH');

  // Determine which conviction levels exist in data (normalized)
  const levels = Array.from(new Set(data.map(d => d.conviction_level?.toUpperCase()).filter(Boolean)));
  const filterLevels = ['HIGH', 'NOTABLE', 'WATCH'].filter(l => levels.some(lv => lv === l || (l === 'NOTABLE' && lv === 'MEDIUM')));

  const sorted = [...data].sort((a, b) => {
    const av = a[sortBy] ?? 0;
    const bv = b[sortBy] ?? 0;
    return bv - av;
  });

  const filtered = filterConviction
    ? sorted.filter(s => {
        const u = s.conviction_level?.toUpperCase();
        if (filterConviction === 'NOTABLE') return u === 'NOTABLE' || u === 'MEDIUM';
        return u === filterConviction;
      })
    : sorted;

  return (
    <div className="space-y-2">
      {/* Filter + Export bar */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-gray-400 tracking-widest uppercase">Filter:</span>
        <button
          onClick={() => setFilterConviction('')}
          className={`text-[10px] px-2 py-1 rounded-md transition-colors border ${
            filterConviction === '' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'text-gray-500 hover:bg-gray-50 border-transparent'
          }`}
        >
          All
        </button>
        {filterLevels.map(level => {
          const style = convictionBadgeStyle(level);
          const active = filterConviction === level;
          return (
            <button
              key={level}
              onClick={() => setFilterConviction(level)}
              className={`text-[10px] px-2 py-1 rounded-md transition-colors border font-semibold tracking-wide ${
                active ? 'border-current' : 'border-transparent hover:bg-gray-50 text-gray-500'
              }`}
              style={active ? { backgroundColor: style.bg, color: style.fg, borderColor: style.fg + '40' } : undefined}
            >
              {level}
            </button>
          );
        })}
        <span className="ml-auto text-[10px] text-gray-400">{filtered.length} signals</span>
        <button
          onClick={() => exportCSV(filtered)}
          className="text-[10px] px-2.5 py-1 rounded-md bg-emerald-600 text-white hover:bg-emerald-700 transition-colors font-semibold tracking-wide"
        >
          ↓ Export CSV
        </button>
      </div>

      <div className="panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500 tracking-widest uppercase">
                <th className="text-left py-3 px-4 font-normal w-20">Symbol</th>
                <th
                  className="text-right py-3 px-2 font-normal cursor-pointer hover:text-emerald-600 transition-colors w-16"
                  onClick={() => setSortBy('convergence_score')}
                >
                  Score {sortBy === 'convergence_score' ? '↓' : ''}
                </th>
                <th className="text-center py-3 px-2 font-normal w-20">Conv.</th>
                <th
                  className="text-right py-3 px-2 font-normal cursor-pointer hover:text-emerald-600 transition-colors w-12"
                  onClick={() => setSortBy('module_count')}
                >
                  Mod {sortBy === 'module_count' ? '↓' : ''}
                </th>
                <th className="text-center py-3 px-2 font-normal">
                  <span className="text-[8px]">MODULE AGREEMENT</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s, i) => {
                const isTop5 = i < 5 && !filterConviction;
                const badgeStyle = convictionBadgeStyle(s.conviction_level);
                return (
                  <React.Fragment key={s.symbol}>
                    <tr
                      className={`border-b border-gray-200/50 hover:bg-emerald-600/[0.03] transition-colors cursor-pointer ${
                        isTop5 ? 'border-l-2 border-l-emerald-600/30' : ''
                      }`}
                      {...cs({
                        backgroundColor: isTop5 ? `rgba(5,150,105,${0.01 + (5 - i) * 0.005})` : undefined,
                      })}
                      onClick={() => setExpanded(expanded === s.symbol ? null : s.symbol)}
                    >
                      <td className="py-2.5 px-4">
                        <a
                          href={`/asset/${s.symbol}`}
                          className="font-mono font-bold text-gray-900 hover:text-emerald-600 transition-colors"
                          onClick={e => e.stopPropagation()}
                        >
                          {s.symbol}
                        </a>
                      </td>
                      <td className="py-2.5 px-2 text-right">
                        <span className="font-mono font-bold" {...fg(scoreColor(s.convergence_score))}>
                          {s.convergence_score.toFixed(1)}
                        </span>
                      </td>
                      <td className="py-2.5 px-2 text-center">
                        <span
                          className="text-[9px] font-bold tracking-wider px-1.5 py-0.5 rounded"
                          style={{ backgroundColor: badgeStyle.bg, color: badgeStyle.fg }}
                        >
                          {s.conviction_level?.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-2.5 px-2 text-right font-mono text-gray-700">
                        {s.module_count}
                      </td>
                      <td className="py-2.5 px-2">
                        <ModuleStrip convergence={s} mode="compact" />
                      </td>
                    </tr>
                    {expanded === s.symbol && (
                      <tr key={`${s.symbol}-detail`} className="bg-gray-50/50">
                        <td colSpan={5} className="px-4 py-4">
                          <div className="grid grid-cols-2 gap-8">
                            <div>
                              {s.narrative && (
                                <>
                                  <div className="text-[10px] text-gray-500 tracking-wider mb-1">NARRATIVE</div>
                                  <p className="text-[11px] text-gray-700 leading-relaxed">{s.narrative}</p>
                                </>
                              )}
                            </div>
                            <div>
                              <div className="text-[10px] text-gray-500 tracking-wider mb-2">MODULE BREAKDOWN</div>
                              <ModuleStrip convergence={s} mode="expanded" />
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
