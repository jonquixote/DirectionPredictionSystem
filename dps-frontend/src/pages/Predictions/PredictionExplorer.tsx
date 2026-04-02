import { useState, useEffect } from 'react';
import { useReactTable, getCoreRowModel, flexRender, getPaginationRowModel } from '@tanstack/react-table';
import { format } from 'date-fns';
import { useSettingsStore } from '../../store/settingsStore';
import type { PredictionRecord } from '../../types';
import { Copy, X, ChevronRight, SlidersHorizontal, Loader2, AlertCircle } from 'lucide-react';
import { endpoints } from '../../api/endpoints';



// Utility Badges
const SymbolBadge = ({ symbol }: { symbol: string }) => {
  const colors: any = {
    'BTCUSDT': 'bg-[var(--color-primary)]/10 text-[var(--color-primary)] border-[var(--color-primary)]/20',
    'SOLUSDT': 'bg-[var(--color-purple)]/10 text-[var(--color-purple)] border-[var(--color-purple)]/20',
    'ETHUSDT': 'bg-[var(--color-blue)]/10 text-[var(--color-blue)] border-[var(--color-blue)]/20',
  };
  return <span className={`px-2 py-0.5 rounded text-xs font-mono border ${colors[symbol] || colors['BTCUSDT']}`}>{symbol.replace('USDT', '')}</span>
};

const ModelBadge = ({ model }: { model: string }) => {
  const colors: any = {
    'h60_v1': 'text-[var(--color-orange)]',
    'h60_v3': 'text-[var(--color-blue)]',
    'h300': 'text-[var(--color-purple)]',
  };
  return <span className={`font-mono text-xs font-bold ${colors[model]}`}>{model.toUpperCase()}</span>
};

export const PredictionExplorer = () => {
  const { timezone } = useSettingsStore();
  const [selectedRow, setSelectedRow] = useState<PredictionRecord | null>(null);

  const [predictions, setPredictions] = useState<PredictionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    let mounted = true;
    const fetchPredictions = async () => {
      try {
        setLoading(true);
        const res = await endpoints.getPredictions({ limit: 50, offset: (page - 1) * 50 });
        if (mounted) {
          setPredictions(res.data);
          setTotal(res.meta.total);
          setError(null);
        }
      } catch (err: any) {
        if (mounted) setError(err.message || 'Failed to fetch predictions');
      } finally {
        if (mounted) setLoading(false);
      }
    };
    fetchPredictions();
    return () => { mounted = false; };
  }, [page]);

  const columns = [
    {
      header: 'Timestamp',
      accessorKey: 'ts_model_ran_ms',
      cell: (info: any) => (
        <span className="font-mono text-xs text-[var(--color-text-muted)]">
          {format(new Date(info.getValue()), 'yyyy-MM-dd HH:mm:ss')} {timezone}
        </span>
      ),
    },
    {
      header: 'ID',
      accessorKey: 'prediction_id',
      cell: (info: any) => (
        <div className="flex items-center space-x-2 group">
           <span className="font-mono text-xs">{info.getValue().substring(0, 8)}</span>
           <button className="opacity-0 group-hover:opacity-100 hover:text-[var(--color-primary)] transition-opacity">
              <Copy className="w-3 h-3" />
           </button>
        </div>
      ),
    },
    { header: 'Symbol', accessorKey: 'symbol', cell: (info: any) => <SymbolBadge symbol={info.getValue()} /> },
    { header: 'Model', accessorKey: 'model', cell: (info: any) => <ModelBadge model={info.getValue()} /> },
    {
      header: 'Direction',
      accessorKey: 'pred_direction',
      cell: (info: any) => {
        const dir = info.getValue();
        return dir === 'up' 
          ? <span className="text-[var(--color-success)] flex items-center"><span className="text-lg leading-none mr-1">▲</span>UP</span>
          : <span className="text-[var(--color-orange)] flex items-center"><span className="text-lg leading-none mr-1">▼</span>DN</span>;
      }
    },
    { header: 'p_model', accessorKey: 'pred_proba', cell: (info: any) => <span className="font-mono">{info.getValue().toFixed(4)}</span> },
    { header: 'p_market', accessorKey: 'p_market', cell: (info: any) => <span className="font-mono text-[var(--color-text-muted)]">{info.getValue() ? info.getValue().toFixed(4) : '—'}</span> },
    {
      header: 'Divergence',
      accessorKey: 'signed_divergence',
      cell: (info: any) => {
        const val = info.getValue();
        if (val === null) return '—';
        const isAligned = (val > 0 && info.row.original.pred_direction === 'up') || (val < 0 && info.row.original.pred_direction === 'down');
        return <span className={`font-mono ${isAligned ? 'text-[var(--color-success)]' : 'text-[var(--color-text)]'}`}>{val > 0 ? '+' : ''}{val.toFixed(4)}</span>;
      }
    },
    {
      header: 'Status',
      accessorKey: 'outcome',
      cell: (info: any) => {
        const r = info.row.original;
        if (r.suppressed_reason) return <span className="bg-[var(--color-warning)]/10 text-[var(--color-warning)] text-xs px-2 py-0.5 rounded font-mono">SUPPRESSED</span>;
        if (r.warmup) return <span className="bg-[var(--color-warning)]/10 text-[var(--color-warning)] text-xs px-2 py-0.5 rounded font-mono">WARMUP</span>;
        
        const outcome = info.getValue();
        return outcome === 'correct' 
               ? <span className="text-[var(--color-success)] text-xs font-mono flex items-center">🟢 CORRECT</span>
               : outcome === 'incorrect' 
               ? <span className="text-[var(--color-error)] text-xs font-mono flex items-center">🔴 INCORRECT</span>
               : <span className="text-[var(--color-text-faint)] text-xs font-mono flex items-center">⚫ UNRESOLVED</span>;
      }
    },
    {
      header: 'Trade',
      accessorKey: 'trade_id',
      cell: (info: any) => info.getValue() ? (
        <button className="text-xs flex items-center hover:text-[var(--color-primary)] transition-colors opacity-70 hover:opacity-100">
           #{info.getValue()} <ChevronRight className="w-3 h-3 ml-1"/>
        </button>
      ) : <span className="text-[var(--color-text-faint)]">—</span>
    }
  ];

  const table = useReactTable({
    data: predictions,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  return (
    <div className="flex h-full relative overflow-hidden animate-in fade-in duration-500">
      
      {/* Main Table Area */}
      <div className={`flex-1 flex flex-col h-full transition-all duration-300 ${selectedRow ? 'mr-[40%]' : ''}`}>
        
        {/* Sticky Filter Bar */}
        <div className="sticky top-0 z-10 bg-[var(--color-surface)]/80 backdrop-blur-md border-b border-[var(--color-border)] p-4 shadow-sm">
          <div className="flex items-center justify-between mb-3 text-sm">
            <div className="flex items-center space-x-2 text-[var(--color-text-muted)] font-medium">
               <SlidersHorizontal className="w-4 h-4" />
               <span>Filters</span>
            </div>
            <button className="text-[var(--color-primary)] hover:underline text-xs">Clear all filters</button>
          </div>
          <div className="flex flex-wrap gap-3">
             <select className="bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded text-sm px-3 py-1.5 focus:outline-none focus:border-[var(--color-primary)] hover:bg-[var(--color-surface-dynamic)] outline-none min-w-[120px]">
                <option>Model: All</option>
                <option>H60 V1</option>
                <option>H60 V3</option>
                <option>H300</option>
             </select>
             <select className="bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded text-sm px-3 py-1.5 focus:outline-none focus:border-[var(--color-primary)] hover:bg-[var(--color-surface-dynamic)] outline-none min-w-[120px]">
                <option>Symbol: All</option>
                <option>BTC</option>
                <option>SOL</option>
                <option>ETH</option>
             </select>
             <select className="bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded text-sm px-3 py-1.5 focus:outline-none focus:border-[var(--color-primary)] hover:bg-[var(--color-surface-dynamic)] outline-none min-w-[120px]">
                <option>Time: Last 24h</option>
                <option>Last 1h</option>
                <option>Last 7d</option>
                <option>All</option>
             </select>
             <div className="h-8 w-px bg-[var(--color-divider)] mx-1"></div>
             <input type="text" placeholder="Search prediction_id..." className="bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded text-sm px-3 py-1.5 focus:outline-none focus:border-[var(--color-primary)] min-w-[200px]" />
          </div>
        </div>

        {/* Table Container */}
        <div className="flex-1 overflow-auto p-4 content-start">
           <table className="w-full text-left text-sm border-separate border-spacing-y-1">
             <thead className="sticky top-0 bg-[var(--color-bg)] z-10 shadow-sm">
               {table.getHeaderGroups().map(hg => (
                 <tr key={hg.id}>
                   {hg.headers.map(h => (
                     <th key={h.id} className="py-3 px-4 font-medium text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                     </th>
                   ))}
                 </tr>
               ))}
             </thead>
             <tbody>
               {loading && predictions.length === 0 ? (
                 <tr>
                   <td colSpan={columns.length} className="text-center py-12 text-[var(--color-text-muted)]">
                     <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2 text-[var(--color-primary)]" />
                     Loading predictions...
                   </td>
                 </tr>
               ) : error ? (
                 <tr>
                   <td colSpan={columns.length} className="text-center py-12 text-[var(--color-error)]">
                     <AlertCircle className="w-6 h-6 mx-auto mb-2" />
                     {error}
                   </td>
                 </tr>
               ) : (
                 table.getRowModel().rows.map(row => {
                  const r = row.original;
                  const isCorrect = r.outcome === 'correct';
                  const isIncorrect = r.outcome === 'incorrect';
                  const isSuppressed = !!r.suppressed_reason;

                  // Apply styling based on outcome
                  let rowStyles = "hover:bg-[var(--color-surface-offset)] cursor-pointer transition-colors bg-[var(--color-surface)] shadow-sm";
                  let borderStyle = "border-l-4 border-l-transparent";

                  if (isCorrect) {
                     rowStyles += " bg-[var(--color-success)]/5";
                     borderStyle = "border-l-4 border-l-[var(--color-success)]";
                  } else if (isIncorrect) {
                     borderStyle = "border-l-4 border-l-[var(--color-error)]";
                  } else if (isSuppressed) {
                     rowStyles += " bg-[var(--color-warning)]/5";
                  }

                  return (
                    <tr 
                      key={row.id} 
                      onClick={() => setSelectedRow(r)}
                      className={`${rowStyles} ${selectedRow?.prediction_id === r.prediction_id ? 'ring-1 ring-[var(--color-primary)]' : ''}`}
                    >
                      {row.getVisibleCells().map((cell, idx) => (
                        <td key={cell.id} className={`py-3 px-4 ${idx === 0 ? borderStyle : ''}`}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  )
               })
               )}
             </tbody>
           </table>

           {/* Pagination */}
           <div className="flex items-center justify-between p-4 border-t border-[var(--color-border)] text-sm text-[var(--color-text-muted)]">
             <span>Showing {(page - 1) * 50 + 1}–{Math.min(page * 50, total)} of {total} predictions</span>
             <div className="flex items-center space-x-2">
               <button 
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1 || loading}
                  className="px-3 py-1 rounded border border-[var(--color-border)] hover:bg-[var(--color-surface-offset)] disabled:opacity-50"
               >Previous</button>
               <button 
                  onClick={() => setPage(p => p + 1)}
                  disabled={page * 50 >= total || loading}
                  className="px-3 py-1 rounded border border-[var(--color-border)] hover:bg-[var(--color-surface-offset)] disabled:opacity-50"
               >Next</button>
             </div>
           </div>
        </div>
      </div>

      {/* Side Drawer */}
      <div 
        className={`absolute top-0 right-0 h-full w-[40%] bg-[var(--color-surface-2)] border-l border-[var(--color-border)] shadow-2xl transition-transform duration-300 ease-in-out z-20 flex flex-col ${
          selectedRow ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {selectedRow && (
          <>
            <div className="flex items-center justify-between p-4 border-b border-[var(--color-border)] bg-[var(--color-surface-offset)]">
               <h3 className="font-mono font-semibold text-lg flex items-center">
                 <button onClick={() => setSelectedRow(null)} className="mr-3 p-1 rounded hover:bg-[var(--color-surface-dynamic)] transition-colors"><ChevronRight className="w-5 h-5"/></button>
                 {selectedRow.prediction_id}
               </h3>
               <button onClick={() => setSelectedRow(null)} className="text-[var(--color-text-muted)] hover:text-white p-1 rounded-full hover:bg-[var(--color-surface-dynamic)] transition-colors">
                  <X className="w-5 h-5" />
               </button>
            </div>
            
            <div className="flex-1 overflow-y-auto p-6">
              <div className="grid grid-cols-2 gap-6 mb-8">
                 <div>
                    <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wider font-semibold">Metadata</div>
                    <div className="space-y-2 text-sm">
                       <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">Model</span> <ModelBadge model={selectedRow.model} /></div>
                       <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">Symbol</span> <SymbolBadge symbol={selectedRow.symbol} /></div>
                       <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">Time</span> <span className="font-mono">{new Date(selectedRow.ts_model_ran_ms).toUTCString()}</span></div>
                    </div>
                 </div>
                 <div>
                    <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wider font-semibold">Prediction</div>
                    <div className="space-y-2 text-sm">
                       <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">Direction</span> <span className="font-mono font-bold text-[var(--color-primary)]">{selectedRow.pred_direction.toUpperCase()}</span></div>
                       <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">p_model</span> <span className="font-mono">{selectedRow.pred_proba.toFixed(4)}</span></div>
                       <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">Outcome</span> <span className="font-mono uppercase">{selectedRow.outcome}</span></div>
                    </div>
                 </div>
              </div>
              
              <div className="mb-6">
                 <div className="text-xs text-[var(--color-text-muted)] mb-3 uppercase tracking-wider font-semibold">Features Payload (Z-Scores)</div>
                 <div className="bg-[#0b0c10] border border-[var(--color-border)] rounded-lg p-4 font-mono text-sm overflow-x-auto shadow-inner text-[var(--color-primary)]">
                    {JSON.stringify(selectedRow.features, null, 2)}
                 </div>
              </div>
            </div>

            <div className="p-4 border-t border-[var(--color-border)] bg-[var(--color-surface-offset)] flex justify-between space-x-3">
               <button className="flex-1 bg-[var(--color-surface-dynamic)] hover:bg-[var(--color-border)] transition-colors text-[var(--color-text)] py-2.5 rounded font-medium text-sm flex items-center justify-center">
                  Export JSON
               </button>
               <button className="flex-1 bg-[var(--color-primary)] hover:bg-[var(--color-primary-hover)] transition-colors text-black py-2.5 rounded font-medium text-sm flex items-center justify-center shadow-lg shadow-[var(--color-primary-highlight)]">
                  View Trade {selectedRow.trade_id ? `#${selectedRow.trade_id}` : ''} <ChevronRight className="w-4 h-4 ml-1" />
               </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
