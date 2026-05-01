'use client';

import { useEffect, useState } from 'react';
import { useStockPanel } from '@/contexts/StockPanelContext';
import SignalBadge from '@/components/SignalBadge';

interface SectorStock {
  symbol: string;
  name: string;
  composite_score: number | null;
  signal: string | null;
  rr_ratio: number | null;
  conviction_level: string | null;
  convergence_score: number | null;
  entry_price: number | null;
  stop_loss: number | null;
  target_price: number | null;
}

interface Props {
  sector: string;
  onClose: () => void;
}

function convictionStyle(level: string | null) {
  const u = level?.toUpperCase();
  if (u === 'HIGH') return { bg: 'rgba(5,150,105,0.1)', color: '#059669' };
  if (u === 'NOTABLE' || u === 'MEDIUM') return { bg: 'rgba(217,119,6,0.1)', color: '#d97706' };
  return { bg: 'rgba(156,163,175,0.1)', color: '#6b7280' };
}

function scoreColor(score: number | null) {
  if (score == null) return 'text-gray-400';
  if (score >= 60) return 'text-emerald-600';
  if (score >= 50) return 'text-amber-600';
  return 'text-rose-500';
}

export default function SectorDrillDown({ sector, onClose }: Props) {
  const [stocks, setStocks] = useState<SectorStock[]>([]);
  const [loading, setLoading] = useState(true);
  const [convFilter, setConvFilter] = useState<string>('');
  const [signalFilter, setSignalFilter] = useState<string>('');
  const { open: openStock } = useStockPanel();

  useEffect(() => {
    setLoading(true);
    fetch(`/api/v2/sector/${encodeURIComponent(sector)}`)
      .then(r => r.json())
      .then(d => { setStocks(d.stocks || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, [sector]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // Counts for bulls/bears badges
  const bullCount = stocks.filter(s => s.signal?.includes('BUY')).length;
  const bearCount = stocks.filter(s => s.signal?.includes('SELL')).length;

  let filtered = stocks;
  if (convFilter) filtered = filtered.filter(s => s.conviction_level?.toUpperCase() === convFilter || (convFilter === 'NOTABLE' && s.conviction_level?.toUpperCase() === 'MEDIUM'));
  if (signalFilter === 'BULL') filtered = filtered.filter(s => s.signal?.includes('BUY'));
  if (signalFilter === 'BEAR') filtered = filtered.filter(s => s.signal?.includes('SELL'));

  const withSignal = filtered.filter(s => s.composite_score != null);
  const noSignal = filtered.filter(s => s.composite_score == null);
  const displayed = [...withSignal, ...noSignal];

  function exportCSV() {
    const cols = ['symbol', 'name', 'composite_score', 'signal', 'conviction_level', 'convergence_score', 'rr_ratio'];
    const header = cols.join(',');
    const lines = displayed.map(r =>
      cols.map(c => `"${String((r as any)[c] ?? '').replace(/"/g, '""')}"`).join(',')
    );
    const blob = new Blob([header + '\n' + lines.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sector_${sector.replace(/\s+/g, '_')}_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/20 backdrop-blur-[1px] z-40"
        onClick={onClose}
      />

      {/* Panel — slides in from right */}
      <div className="fixed right-0 top-0 h-full w-[680px] bg-white border-l border-gray-200 shadow-2xl z-50 flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h2 className="text-sm font-display font-bold text-gray-900">{sector}</h2>
            <p className="text-[10px] text-gray-400 tracking-wider mt-0.5">
              {loading ? 'Loading...' : `${stocks.length} stocks · ${withSignal.length} with signals`}
            </p>
          </div>
          <div className="flex items-center gap-1.5 flex-wrap justify-end">
            {/* Bulls / Bears filter */}
            <button
              onClick={() => setSignalFilter(signalFilter === 'BULL' ? '' : 'BULL')}
              className={`text-[9px] px-2 py-1 rounded-md font-semibold tracking-wider border transition-colors ${
                signalFilter === 'BULL' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'text-gray-400 border-transparent hover:bg-gray-50'
              }`}
            >
              🐂 {bullCount}
            </button>
            <button
              onClick={() => setSignalFilter(signalFilter === 'BEAR' ? '' : 'BEAR')}
              className={`text-[9px] px-2 py-1 rounded-md font-semibold tracking-wider border transition-colors ${
                signalFilter === 'BEAR' ? 'bg-rose-50 text-rose-700 border-rose-200' : 'text-gray-400 border-transparent hover:bg-gray-50'
              }`}
            >
              🐻 {bearCount}
            </button>
            <div className="w-px h-3 bg-gray-200 mx-0.5" />
            {/* Conviction filter */}
            {['HIGH', 'NOTABLE', 'WATCH'].map(level => (
              <button
                key={level}
                onClick={() => setConvFilter(convFilter === level ? '' : level)}
                className={`text-[9px] px-2 py-1 rounded-md font-semibold tracking-wider border transition-colors ${
                  convFilter === level
                    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : 'text-gray-400 border-transparent hover:bg-gray-50'
                }`}
              >
                {level}
              </button>
            ))}
            <div className="w-px h-3 bg-gray-200 mx-0.5" />
            <button
              onClick={exportCSV}
              className="text-[9px] px-2.5 py-1 rounded-md bg-emerald-600 text-white hover:bg-emerald-700 font-semibold tracking-wide transition-colors"
            >
              ↓ CSV
            </button>
            <button
              onClick={onClose}
              className="ml-1 text-gray-400 hover:text-gray-600 transition-colors text-lg leading-none"
            >
              ×
            </button>
          </div>
        </div>

        {/* Column headers */}
        <div className="grid grid-cols-[1fr_70px_80px_70px_60px] gap-2 px-5 py-2 border-b border-gray-100 text-[9px] text-gray-400 tracking-widest uppercase">
          <div>Stock</div>
          <div className="text-right">Score</div>
          <div className="text-center">Signal</div>
          <div className="text-center">Conv.</div>
          <div className="text-right">R:R</div>
        </div>

        {/* Stock list */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-40 text-gray-400 text-sm animate-pulse">
              Loading {sector}...
            </div>
          ) : displayed.length === 0 ? (
            <div className="flex items-center justify-center h-40 text-gray-400 text-sm">
              No stocks match the current filter
            </div>
          ) : (
            <div className="divide-y divide-gray-50">
              {displayed.map(stock => {
                const cs = convictionStyle(stock.conviction_level);
                return (
                  <div
                    key={stock.symbol}
                    className="grid grid-cols-[1fr_70px_80px_70px_60px] gap-2 items-center px-5 py-2.5 hover:bg-gray-50/60 transition-colors"
                  >
                    <div>
                      <button
                        onClick={() => openStock(stock.symbol)}
                        className="font-mono font-bold text-xs text-gray-900 hover:text-emerald-600 transition-colors"
                      >
                        {stock.symbol}
                      </button>
                      <div className="text-[9px] text-gray-400 truncate">{stock.name}</div>
                    </div>
                    <div className={`text-right text-xs font-mono font-bold ${scoreColor(stock.composite_score)}`}>
                      {stock.composite_score != null ? stock.composite_score.toFixed(1) : '—'}
                    </div>
                    <div className="text-center">
                      {stock.signal ? <SignalBadge signal={stock.signal} size="sm" /> : <span className="text-[9px] text-gray-300">—</span>}
                    </div>
                    <div className="text-center">
                      {stock.conviction_level ? (
                        <span
                          className="text-[9px] font-bold tracking-wider px-1.5 py-0.5 rounded"
                          style={{ backgroundColor: cs.bg, color: cs.color }}
                        >
                          {stock.conviction_level.toUpperCase()}
                        </span>
                      ) : <span className="text-[9px] text-gray-300">—</span>}
                    </div>
                    <div className="text-right text-[10px] font-mono text-gray-500">
                      {stock.rr_ratio != null ? stock.rr_ratio.toFixed(1) : '—'}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
