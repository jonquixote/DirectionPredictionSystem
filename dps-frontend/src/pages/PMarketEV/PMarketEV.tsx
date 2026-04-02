import { useState, useEffect } from 'react';
import { endpoints } from '../../api/endpoints';
import { ActivitySquare, Loader2, AlertCircle } from 'lucide-react';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ZAxis, ReferenceLine, Legend } from 'recharts';

export const PMarketEV = () => {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetchDivergence = async () => {
      try {
        setLoading(true);
        // Temporarily fetching predictions to extract p_market vs p_model divergence locally until /api/pmarket routes are exposed
        const preds = await endpoints.getPredictions({ limit: 1000 });
        if (mounted) {
           const mapped = preds.data
             .filter(p => p.p_market !== null && p.p_market > 0)
             .map(p => ({
               id: p.prediction_id,
               p_model: p.pred_proba,
               p_market: p.p_market,
               divergence: p.divergence,
               outcome: p.outcome
             }));
           setData(mapped);
           setError(null);
        }
      } catch (err: any) {
        if (mounted) setError(err.message || 'Error fetching divergence data');
      } finally {
        if (mounted) setLoading(false);
      }
    };
    fetchDivergence();
    return () => { mounted = false; };
  }, []);

  const correctData = data.filter(d => d.outcome === 'correct');
  const incorrectData = data.filter(d => d.outcome === 'incorrect');
  const unresolvedData = data.filter(d => d.outcome === 'unresolved');

  return (
    <div className="h-full flex flex-col p-6 overflow-y-auto animate-in fade-in duration-500 space-y-8 relative">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
         <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--color-text)] flex items-center">
              <ActivitySquare className="w-6 h-6 mr-3 text-[var(--color-primary)]" />
              p_market Edge Analysis
            </h1>
            <p className="text-[var(--color-text-muted)] text-sm mt-1">Isolating Expected Value (EV) by mapping model probabilities against the live market.</p>
         </div>
      </div>

      <div className="flex-1 flex flex-col min-h-[500px] bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 shadow-sm">
         <div className="flex justify-between items-center mb-6">
            <h3 className="font-semibold text-lg flex items-center">
               Divergence Scatter Plot
            </h3>
         </div>
         
         <div className="flex-1 min-h-[400px]">
            {loading ? (
              <div className="flex h-full items-center justify-center flex-col text-[var(--color-text-muted)]">
                 <Loader2 className="w-8 h-8 animate-spin mb-4 text-[var(--color-primary)]" />
                 Loading scatter coordinates...
              </div>
            ) : error ? (
              <div className="flex justify-center items-center h-full">
                 <div className="text-[var(--color-error)] flex items-center bg-[var(--color-error)]/10 p-4 rounded-lg border border-[var(--color-error)]/20">
                    <AlertCircle className="w-5 h-5 mr-3" />
                    {error}
                 </div>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                 <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                   <CartesianGrid strokeDasharray="3 3" opacity={0.3} stroke="var(--color-border)"/>
                   <XAxis type="number" dataKey="p_market" name="p_market" domain={[0, 1]} tick={{fill: 'var(--color-text-faint)'}} axisLine={false} tickLine={false} label={{ value: 'p_market (Market Pricing)', position: 'bottom', fill: 'var(--color-text-muted)' }}/>
                   <YAxis type="number" dataKey="p_model" name="p_model" domain={[0, 1]} tick={{fill: 'var(--color-text-faint)'}} axisLine={false} tickLine={false} label={{ value: 'p_model (H60 / H300)', angle: -90, position: 'left', fill: 'var(--color-text-muted)' }}/>
                   <ZAxis type="number" range={[40, 40]} /> {/* fixed size node */}
                   
                   {/* Equilibrium Line */}
                   <ReferenceLine segment={[{x: 0, y: 0}, {x: 1, y: 1}]} stroke="var(--color-text-faint)" strokeDasharray="5 5" strokeOpacity={0.5} label={{ value: 'Efficient Frontier', fill: 'var(--color-text-faint)', fontSize: 10 }} />
                   
                   <Tooltip 
                      cursor={{ strokeDasharray: '3 3', stroke: 'var(--color-border)' }}
                      contentStyle={{ backgroundColor: 'var(--color-surface-2)', borderColor: 'var(--color-border)', borderRadius: '8px' }}
                      itemStyle={{ fontFamily: 'var(--font-mono)' }}
                      formatter={(val: any) => (val as number).toFixed(4)}
                   />
                   <Legend verticalAlign="top" height={36}/>
                   <Scatter name="Wins" data={correctData} fill="var(--color-success)" fillOpacity={0.6} />
                   <Scatter name="Losses" data={incorrectData} fill="var(--color-error)" fillOpacity={0.6} />
                   <Scatter name="Open" data={unresolvedData} fill="var(--color-text-muted)" fillOpacity={0.3} />
                 </ScatterChart>
              </ResponsiveContainer>
            )}
         </div>
      </div>
    </div>
  );
};
