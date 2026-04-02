import { useState, useEffect } from 'react';
import { useReactTable, getCoreRowModel, flexRender, getPaginationRowModel } from '@tanstack/react-table';
import { format } from 'date-fns';
import type { TradeRecord } from '../../types';
import { Copy, X, SlidersHorizontal, ArrowUpRight, ArrowDownRight, ActivitySquare, Loader2, AlertCircle } from 'lucide-react';
import { endpoints } from '../../api/endpoints';


// Utility Badges (Shared with Predictions theoretically, but kept here for self-containment)
const SymbolBadge = ({ symbol }: { symbol: string }) => {
  const colors: any = {
    'BTCUSDT': 'bg-[var(--color-primary)]/10 text-[var(--color-primary)] border-[var(--color-primary)]/20',
    'SOLUSDT': 'bg-[var(--color-purple)]/10 text-[var(--color-purple)] border-[var(--color-purple)]/20',
    'ETHUSDT': 'bg-[var(--color-blue)]/10 text-[var(--color-blue)] border-[var(--color-blue)]/20',
  };
  return <span className={`px-2 py-0.5 rounded text-xs font-mono border shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] ${colors[symbol] || colors['BTCUSDT']}`}>{symbol.replace('USDT', '')}</span>
};

const ModelBadge = ({ model }: { model: string }) => {
  const colors: any = {
    'h60_v1': 'text-[var(--color-orange)] bg-[var(--color-orange)]/10 border border-[var(--color-orange)]/20 px-1.5 py-0.5 rounded shadow-sm',
    'h60_v3': 'text-[var(--color-blue)] bg-[var(--color-blue)]/10 border border-[var(--color-blue)]/20 px-1.5 py-0.5 rounded shadow-sm',
    'h300': 'text-[var(--color-purple)] bg-[var(--color-purple)]/10 border border-[var(--color-purple)]/20 px-1.5 py-0.5 rounded shadow-sm',
  };
  return <span className={`font-mono text-[11px] font-bold ${colors[model]}`}>{model.toUpperCase()}</span>
};

export const TradeExplorer = () => {
  const [selectedRow, setSelectedRow] = useState<TradeRecord | null>(null);

  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    let mounted = true;
    const fetchTrades = async () => {
      try {
        setLoading(true);
        const res = await endpoints.getTrades({ limit: 50, offset: (page - 1) * 50 });
        if (mounted) {
          setTrades(res.data);
          setTotal(res.meta.total);
          setError(null);
        }
      } catch (err: any) {
        if (mounted) setError(err.message || 'Failed to fetch trades');
      } finally {
        if (mounted) setLoading(false);
      }
    };
    fetchTrades();
    return () => { mounted = false; };
  }, [page]);

  const openTrades = trades.filter(t => !t.resolved && !t.suppressed_reason);

  const columns = [
    {
      header: 'ID',
      accessorKey: 'id',
      cell: (info: any) => (
        <div className="flex items-center space-x-2 group">
           <span className="font-mono text-xs">{info.getValue()}</span>
           <button className="opacity-0 group-hover:opacity-100 text-[var(--color-text-muted)] hover:text-[var(--color-primary)] transition-opacity">
              <Copy className="w-3 h-3" />
           </button>
        </div>
      )
    },
    {
      header: 'Timestamp',
      accessorKey: 'timestamp_ms',
      cell: (info: any) => (
        <span className="font-mono text-xs text-[var(--color-text-muted)]">
          {format(new Date(info.getValue()), 'MMM dd, HH:mm:ss')}
        </span>
      ),
    },
    { header: 'Symbol', accessorKey: 'symbol', cell: (info: any) => <SymbolBadge symbol={info.getValue()} /> },
    { header: 'Model', accessorKey: 'model', cell: (info: any) => <ModelBadge model={info.getValue()} /> },
    { 
      header: 'Duration', 
      accessorKey: 'contract_duration', 
      cell: (info: any) => <span className="text-[11px] font-mono px-1.5 py-0.5 rounded border border-[var(--color-border)] bg-[var(--color-surface-offset)] text-[var(--color-text-faint)]">{info.getValue()}s</span>
    },
    {
      header: 'Direction',
      accessorKey: 'direction',
      cell: (info: any) => {
        const dir = info.getValue();
        return dir === 'up' 
          ? <span className="text-[var(--color-success)] flex items-center font-bold text-xs tracking-wider"><ArrowUpRight className="w-3.5 h-3.5 mr-0.5"/>UP</span>
          : <span className="text-[var(--color-orange)] flex items-center font-bold text-xs tracking-wider"><ArrowDownRight className="w-3.5 h-3.5 mr-0.5"/>DN</span>;
      }
    },
    { header: 'Stake', accessorKey: 'simulated_stake_usdc', cell: (info: any) => <span className="font-mono text-[var(--color-text-muted)]">${info.getValue().toFixed(2)}</span> },
    { header: 'p_market', accessorKey: 'p_market', cell: (info: any) => <span className="font-mono">{info.getValue() ? info.getValue().toFixed(4) : '—'}</span> },
    { header: 'Px Open', accessorKey: 'price_at_open', cell: (info: any) => <span className="font-mono text-[var(--color-text-muted)]">{info.getValue() ? info.getValue().toFixed(1) : '—'}</span> },
    { header: 'Px Close', accessorKey: 'price_at_close', cell: (info: any) => <span className="font-mono text-[var(--color-text-muted)]">{info.getValue() ? info.getValue().toFixed(1) : '—'}</span> },
    {
      header: 'Outcome',
      accessorKey: 'outcome',
      cell: (info: any) => {
        const outcome = info.getValue();
        return outcome === 'correct' 
               ? <span className="text-[var(--color-success)] text-xs font-mono font-bold">✓ CORRECT</span>
               : outcome === 'incorrect' 
               ? <span className="text-[var(--color-error)] text-xs font-mono font-bold">✗ INCORRECT</span>
               : <span className="text-[var(--color-primary)] text-xs font-mono font-bold flex items-center animate-pulse"><ActivitySquare className="w-3 h-3 mr-1"/> OPEN</span>;
      }
    },
    {
      header: 'Realized NE_t',
      accessorKey: 'realized_net',
      cell: (info: any) => {
        const val = info.getValue();
        if (val === null) return <span className="text-[var(--color-text-faint)] font-mono">—</span>;
        return <span className={`font-mono font-medium ${val > 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]'}`}>
          {val > 0 ? '+' : ''}${val.toFixed(2)}
        </span>;
      }
    }
  ];

  const table = useReactTable({
    data: trades,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  return (
    <div className="flex h-full relative overflow-hidden animate-in slide-in-from-bottom-2 duration-500">
      
      {/* Main Table Area */}
      <div className={`flex-1 flex flex-col h-full transition-all duration-300 ${selectedRow ? 'mr-[400px]' : ''}`}>
        
        {/* Sticky Filter Bar */}
        <div className="sticky top-0 z-10 bg-[var(--color-surface)]/90 backdrop-blur-md border-b border-[var(--color-border)] p-4 shadow-sm">
          <div className="flex items-center justify-between mb-3 text-sm">
            <div className="flex items-center space-x-2 text-[var(--color-text)] font-semibold tracking-tight">
               <SlidersHorizontal className="w-4 h-4 text-[var(--color-primary)]" />
               <span>Trade Explorer Filters</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
             <select className="bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded text-xs px-2.5 py-1.5 focus:outline-none hover:bg-[var(--color-surface-dynamic)] outline-none min-w-[100px]">
                <option>Model: All</option>
             </select>
             <select className="bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded text-xs px-2.5 py-1.5 focus:outline-none hover:bg-[var(--color-surface-dynamic)] outline-none min-w-[100px]">
                <option>Symbol: All</option>
             </select>
             <select className="bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded text-xs px-2.5 py-1.5 focus:outline-none hover:bg-[var(--color-surface-dynamic)] outline-none min-w-[100px]">
                <option>Duration: All</option>
                <option>300s</option>
                <option>900s</option>
             </select>
             <select className="bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded text-xs px-2.5 py-1.5 focus:outline-none hover:bg-[var(--color-surface-dynamic)] outline-none min-w-[110px]">
                <option>Outcome: All</option>
                <option>Correct</option>
                <option>Incorrect</option>
                <option>Unresolved</option>
             </select>
          </div>
        </div>

        {/* Floating Open Trades Banner */}
        {openTrades.length > 0 && (
          <div className="m-4 p-3 bg-gradient-to-r from-[var(--color-primary-highlight)] to-[var(--color-surface-offset)] border border-[var(--color-primary)]/20 rounded-lg shadow-sm">
            <div className="flex items-center mb-2">
               <div className="w-2 h-2 rounded-full bg-[var(--color-primary)] animate-ping mr-2"></div>
               <span className="text-sm font-medium text-[var(--color-text)]">⏳ {openTrades.length} trades currently open in this view</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
               {openTrades.slice(0, 3).map(ot => (
                  <div key={ot.id} className="bg-[var(--color-bg)]/50 backdrop-blur rounded px-3 py-2 text-xs flex justify-between items-center border border-[var(--color-border)] hover:border-[var(--color-primary)]/50 cursor-pointer transition-colors" onClick={() => setSelectedRow(ot)}>
                     <div className="flex space-x-2">
                         <span className="font-mono font-bold text-[var(--color-text)]">{ot.symbol.replace('USDT', '')}</span>
                         <span className="text-[var(--color-text-faint)]">{ot.contract_duration}s</span>
                         <span className={ot.direction === 'up' ? 'text-[var(--color-success)]' : 'text-[var(--color-orange)]'}>{ot.direction.toUpperCase()}</span>
                     </div>
                     <div className="font-mono text-[var(--color-text-muted)]">
                        opened {Math.floor((Date.now() - ot.timestamp_ms) / 60000)}m ago
                     </div>
                  </div>
               ))}
            </div>
          </div>
        )}

        {/* Table Container */}
        <div className="flex-1 overflow-auto p-4 pt-0 content-start">
           <table className="w-full text-left text-sm whitespace-nowrap">
             <thead className="sticky top-0 bg-[var(--color-surface)] z-10 shadow-sm border-b border-[var(--color-border)]">
               {table.getHeaderGroups().map(hg => (
                 <tr key={hg.id}>
                   {hg.headers.map(h => (
                     <th key={h.id} className="py-2.5 px-3 font-medium text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                     </th>
                   ))}
                 </tr>
               ))}
             </thead>
             <tbody className="divide-y divide-[var(--color-border)]/50">
               {loading && trades.length === 0 ? (
                 <tr>
                   <td colSpan={columns.length} className="text-center py-12 text-[var(--color-text-muted)]">
                     <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2 text-[var(--color-primary)]" />
                     Loading trades...
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
                  const isOpen = !r.resolved;
                  
                  return (
                    <tr 
                      key={row.id} 
                      onClick={() => setSelectedRow(r)}
                      className={`hover:bg-[var(--color-surface-offset)] cursor-pointer transition-colors ${isOpen ? 'bg-[var(--color-primary-highlight)]/30' : ''} ${selectedRow?.id === r.id ? 'bg-[var(--color-surface-dynamic)]' : ''}`}
                    >
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="py-2.5 px-3">
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
             <span>Showing {(page - 1) * 50 + 1}–{Math.min(page * 50, total)} of {total} trades</span>
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

      {/* Side Drawer for Trade Detail */}
      <div 
        className={`absolute top-0 right-0 h-full w-[400px] bg-[var(--color-surface-2)] border-l border-[var(--color-border)] shadow-2xl transition-transform duration-300 ease-in-out z-20 flex flex-col ${
          selectedRow ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {selectedRow && (
          <>
            <div className="flex items-center justify-between p-4 border-b border-[var(--color-border)]">
               <h3 className="font-mono font-medium text-lg flex items-center text-[var(--color-text)]">
                 Trade #{selectedRow.id}
               </h3>
               <button onClick={() => setSelectedRow(null)} className="text-[var(--color-text-muted)] hover:text-white p-1 rounded-full hover:bg-[var(--color-surface-dynamic)] transition-colors">
                  <X className="w-5 h-5" />
               </button>
            </div>
            
            <div className="flex-1 overflow-y-auto p-5">
              {/* NE_t Breakdown Mock */}
              <div className="mb-6 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-4">
                  <div className="text-xs text-[var(--color-text-muted)] mb-2 uppercase tracking-wider font-semibold">Realized NE_t Breakdown</div>
                  {selectedRow.resolved ? (
                    <div className="space-y-1.5 font-mono text-sm">
                        <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">Direction:</span> <span>{selectedRow.direction.toUpperCase()}</span></div>
                        <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">Outcome:</span> <span>{selectedRow.outcome.toUpperCase()}</span></div>
                        <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">p_market:</span> <span>{selectedRow.p_market?.toFixed(4)}</span></div>
                        <div className="flex justify-between"><span className="text-[var(--color-text-faint)]">Fee:</span> <span>0.0200</span></div>
                        <div className="h-px bg-[var(--color-divider)] my-2"></div>
                        <div className="flex justify-between font-bold text-[var(--color-text)]">
                           <span>Formula:</span> 
                           <span className={selectedRow.realized_net && selectedRow.realized_net > 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]'}>
                              {selectedRow.direction === 'up' && selectedRow.correct ? `+(1 - ${selectedRow.p_market?.toFixed(3)} - 0.02)` : '...'} = {selectedRow.realized_net && selectedRow.realized_net > 0 ? '+' : ''}${selectedRow.realized_net?.toFixed(3)}
                           </span>
                        </div>
                    </div>
                  ) : (
                    <div className="text-[var(--color-text-muted)] italic text-sm">Trade is currently open. Calculation available upon resolution.</div>
                  )}
              </div>

              {/* JSON Payload */}
              <div className="mb-6">
                 <div className="text-xs text-[var(--color-text-muted)] mb-2 uppercase tracking-wider font-semibold">Raw Record</div>
                 <div className="bg-[#0f1117] border border-[var(--color-border)] rounded-lg p-4 font-mono text-xs overflow-x-auto shadow-inner text-[var(--color-text-muted)] break-all whitespace-pre-wrap">
                    {JSON.stringify(selectedRow, null, 2)}
                 </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
