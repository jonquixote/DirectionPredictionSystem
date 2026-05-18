import { useState, useEffect, useCallback } from 'react';
import { endpoints } from '../../api/endpoints';
import { Layers, GitMerge, Loader2, AlertCircle, ChevronDown, ChevronRight } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, Legend, ReferenceLine } from 'recharts';
import { ModelFilterEditor } from './ModelFilterEditor';

export const ModelComparison = () => {
  const [registry, setRegistry] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Track which rows have the Gates panel open (keyed by model name)
  const [expandedGates, setExpandedGates] = useState<Record<string, boolean>>({});

  const fetchRegistry = useCallback(async () => {
    let mounted = true;
    try {
      setLoading(true);
      const data = await endpoints.getModelsRegistry();
      if (mounted) {
         setRegistry(data?.data || data || []);
         setError(null);
      }
    } catch (err: any) {
      if (mounted) setError(err.message || 'Error fetching model registry');
    } finally {
      if (mounted) setLoading(false);
    }
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    fetchRegistry();
  }, [fetchRegistry]);

  const toggleGates = (name: string) => {
    setExpandedGates((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  return (
    <div className="h-full flex flex-col p-6 overflow-y-auto animate-in fade-in duration-500 space-y-8 relative">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
         <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--color-text)] flex items-center">
              <Layers className="w-6 h-6 mr-3 text-[var(--color-primary)]" />
              Model Comparison
            </h1>
            <p className="text-[var(--color-text-muted)] text-sm mt-1">Registry of active models, hyperparameter configurations, and head-to-head performance.</p>
         </div>
      </div>

         <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm flex flex-col">
               <div className="flex justify-between items-center mb-6">
                  <h3 className="font-semibold text-lg flex items-center">
                     Cross-Model Accuracy Timeline
                  </h3>
               </div>
               <div className="flex-1 min-h-[350px]">
                  <ResponsiveContainer width="100%" height="100%">
                     <LineChart data={[
                        { week: 'Week 1', H60_V1: 52.1, H60_V3: 54.5, H300: 51.2 },
                        { week: 'Week 2', H60_V1: 52.8, H60_V3: 54.2, H300: 52.5 },
                        { week: 'Week 3', H60_V1: 51.9, H60_V3: 55.1, H300: 53.8 },
                        { week: 'Week 4', H60_V1: 50.4, H60_V3: 54.8, H300: 54.0 },
                        { week: 'Week 5', H60_V1: 49.8, H60_V3: 55.4, H300: 54.7 },
                        { week: 'Week 6', H60_V1: 48.2, H60_V3: 56.1, H300: 55.2 },
                     ]} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--color-border)" opacity={0.3} />
                        <XAxis dataKey="week" tick={{fill: 'var(--color-text-faint)'}} axisLine={false} tickLine={false} />
                        <YAxis tick={{fill: 'var(--color-text-faint)'}} axisLine={false} tickLine={false} domain={[45, 60]} tickFormatter={v => `${v}%`} />
                        <RechartsTooltip 
                           contentStyle={{ backgroundColor: 'var(--color-surface-2)', borderColor: 'var(--color-border)', borderRadius: '8px' }}
                           itemStyle={{ fontFamily: 'var(--font-mono)' }}
                           formatter={(val: any) => [`${val}%`, 'Accuracy']}
                        />
                        <Legend verticalAlign="top" height={36} />
                        <ReferenceLine y={51.5} stroke="var(--color-error)" strokeDasharray="3 3" strokeWidth={1} label={{ value: 'Gate (51.5%)', fill: 'var(--color-error)', fontSize: 10 }} />
                        <Line type="monotone" dataKey="H60_V1" stroke="var(--color-orange)" strokeWidth={2} dot={{r:3}} />
                        <Line type="monotone" dataKey="H60_V3" stroke="var(--color-blue)" strokeWidth={2.5} dot={{r:3}} />
                        <Line type="monotone" dataKey="H300" stroke="var(--color-purple)" strokeWidth={2} dot={{r:3}} />
                     </LineChart>
                  </ResponsiveContainer>
               </div>
            </div>

            <div className="lg:col-span-1 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm flex flex-col">
               <div className="flex justify-between items-center mb-6">
                  <h3 className="font-semibold text-lg flex items-center">
                     <GitMerge className="w-5 h-5 mr-2 text-[var(--color-primary)]"/>
                     Model Registry
                  </h3>
               </div>
         
         <div className="min-h-[200px] overflow-auto">
            {loading ? (
              <div className="flex h-[200px] items-center justify-center flex-col text-[var(--color-text-muted)]">
                 <Loader2 className="w-8 h-8 animate-spin mb-4 text-[var(--color-primary)]" />
                 Loading registry...
              </div>
            ) : error ? (
              <div className="flex justify-center flex-col h-[200px] items-center">
                 <div className="text-[var(--color-error)] flex items-center bg-[var(--color-error)]/10 p-4 rounded-lg border border-[var(--color-error)]/20">
                    <AlertCircle className="w-5 h-5 mr-3" />
                    {error}
                 </div>
              </div>
            ) : (
               <table className="w-full text-left text-sm border-separate border-spacing-y-2">
                 <thead>
                   <tr>
                     <th className="py-2 px-4 font-medium text-[var(--color-text-muted)] border-b border-[var(--color-border)]">Version</th>
                     <th className="py-2 px-4 font-medium text-[var(--color-text-muted)] border-b border-[var(--color-border)]">Trained Date</th>
                     <th className="py-2 px-4 font-medium text-[var(--color-text-muted)] border-b border-[var(--color-border)]">AUC (Train/Val/Test)</th>
                     <th className="py-2 px-4 font-medium text-[var(--color-text-muted)] border-b border-[var(--color-border)] w-16 text-center">Gates</th>
                   </tr>
                 </thead>
                 <tbody>
                   {registry.map((r, i) => {
                      const versionStr = r.name || r.version || 'unknown';
                      const isV1 = versionStr.includes('v1') || versionStr.includes('V1');
                      const is300 = versionStr.includes('300');
                      const colorClass = isV1 ? 'border-[var(--color-orange)]' : is300 ? 'border-[var(--color-purple)]' : 'border-[var(--color-blue)]';
                      const isOpen = Boolean(expandedGates[versionStr]);
                      return (
                       <>
                         <tr key={i} className={`hover:bg-[var(--color-surface-offset)] bg-[var(--color-surface)] shadow-sm cursor-pointer transition-colors border-l-4 ${colorClass}`}>
                            <td className="py-3 px-4 font-mono font-bold">{versionStr}</td>
                            <td className="py-3 px-4 font-mono text-[var(--color-text-muted)] text-xs">{r.trained_date || 'N/A'}</td>
                            <td className="py-3 px-4 font-mono text-[var(--color-text-muted)] text-xs whitespace-nowrap">
                              {r.auc_train?.toFixed(4) || '—'} / {r.auc_val?.toFixed(4) || '—'} / {r.auc_test?.toFixed(4) || '—'}
                            </td>
                            <td className="py-3 px-4 text-center">
                              <button
                                onClick={() => toggleGates(versionStr)}
                                title="Edit gate filters"
                                className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium text-[var(--color-text-muted)] hover:text-[var(--color-primary)] hover:bg-[var(--color-primary)]/10 transition-colors"
                              >
                                {isOpen
                                  ? <ChevronDown className="w-3.5 h-3.5" />
                                  : <ChevronRight className="w-3.5 h-3.5" />}
                                Gates
                              </button>
                            </td>
                         </tr>
                         {isOpen && (
                           <tr key={`${i}-gates`}>
                             <td colSpan={4} className="px-2 pb-3">
                               <ModelFilterEditor
                                 model={{
                                   name: versionStr,
                                   live_eligible: r.live_eligible,
                                   filter_config: r.filter_config,
                                 }}
                                 onSaved={fetchRegistry}
                               />
                             </td>
                           </tr>
                         )}
                       </>
                      );
                   })}
                   {registry.length === 0 && (
                      <tr>
                         <td colSpan={4} className="py-8 text-center text-[var(--color-text-faint)] italic">No models found in registry.</td>
                      </tr>
                   )}
                 </tbody>
               </table>
            )}
         </div>
       </div>
     </div>
    </div>
  );
};
