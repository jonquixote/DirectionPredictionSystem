import { useState } from 'react';
import { Calculator, ArrowRight, Info } from 'lucide-react';

const erfc = (x: number) => {
  const t = 1.0 / (1.0 + 0.5 * Math.abs(x));
  const tau = t * Math.exp(-x * x - 1.26551223 + 1.00002368 * t + 0.37409196 * t * t + 0.09678418 * t * t * t - 0.18628806 * Math.pow(t, 4) + 0.27886807 * Math.pow(t, 5) - 1.13520398 * Math.pow(t, 6) + 1.48851587 * Math.pow(t, 7) - 0.82215223 * Math.pow(t, 8) + 0.17087277 * Math.pow(t, 9));
  if (x >= 0) return tau;
  return 2.0 - tau;
};

const calcZTest = (wins: number, total: number, p0 = 0.5) => {
  if (total === 0) return { z: 0, p: 1 };
  const p = wins / total;
  const z = (p - p0) / Math.sqrt((p0 * (1 - p0)) / total);
  const pValue = erfc(Math.abs(z) / Math.sqrt(2));
  return { z, p: pValue };
};

const calcWilson = (wins: number, total: number, z = 1.96) => {
  if (total === 0) return [0, 0];
  const p = wins / total;
  const denominator = 1 + (z * z) / total;
  const center = p + (z * z) / (2 * total);
  const spread = z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * Math.pow(total, 2)));
  return [(center - spread) / denominator, (center + spread) / denominator];
};

export const StatisticalTools = () => {
  const [wins, setWins] = useState<number>(54);
  const [total, setTotal] = useState<number>(100);

  const { z, p } = calcZTest(wins, total);
  const [ciLow, ciHigh] = calcWilson(wins, total);
  const winRate = total > 0 ? (wins / total) * 100 : 0;

  return (
    <div className="h-full flex flex-col p-6 overflow-y-auto animate-in fade-in duration-500 space-y-6 relative">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
         <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--color-text)] flex items-center">
              <Calculator className="w-6 h-6 mr-3 text-[var(--color-primary)]" />
              Statistical Tools
            </h1>
            <p className="text-[var(--color-text-muted)] text-sm mt-1">Manual calculator for Binomial Z-Tests and Wilson Confidence Intervals to evaluate potential edge.</p>
         </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-5xl">
         {/* Inputs */}
         <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm">
            <h3 className="font-semibold text-lg mb-6">Simulation Inputs</h3>
            <div className="space-y-6">
               <div>
                  <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">Total Trials (N)</label>
                  <input type="number" value={total} onChange={(e) => setTotal(Number(e.target.value))} className="w-full bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md px-4 py-2 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)] font-mono" />
               </div>
               <div>
                  <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">Successful Outcomes</label>
                  <input type="number" value={wins} onChange={(e) => setWins(Number(e.target.value))} className="w-full bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md px-4 py-2 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)] font-mono" />
               </div>
            </div>
            
            <div className="mt-8 p-4 bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded-lg">
               <div className="flex items-center text-sm text-[var(--color-text-muted)] mb-2">
                  <Info className="w-4 h-4 mr-2" /> Win Rate
               </div>
               <div className="text-2xl font-mono font-bold text-[var(--color-primary)]">{winRate.toFixed(2)}%</div>
            </div>
         </div>

         {/* Outputs */}
         <div className="flex flex-col space-y-6">
            <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm flex-1">
               <h3 className="font-semibold text-lg mb-6">Z-Test (Against null = 50%)</h3>
               <div className="grid grid-cols-2 gap-4">
                  <div className="p-4 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-center">
                     <div className="text-sm text-[var(--color-text-muted)] mb-1">Z-Score</div>
                     <div className="text-2xl font-mono text-[var(--color-text)]">{z.toFixed(3)}</div>
                  </div>
                  <div className={`p-4 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-center ${p < 0.05 ? 'shadow-[0_0_15px_rgba(45,212,191,0.1)] border-[var(--color-primary)]/50' : ''}`}>
                     <div className="text-sm text-[var(--color-text-muted)] mb-1">p-value</div>
                     <div className={`text-2xl font-mono ${p < 0.05 ? 'text-[var(--color-primary)] font-bold' : 'text-[var(--color-text)]'}`}>{p.toFixed(4)}</div>
                  </div>
               </div>
               <p className="text-sm text-[var(--color-text-muted)] mt-4">
                  {p < 0.05 ? <span className="text-[var(--color-primary)] font-medium">Statistically Significant.</span> : "Not statistically significant at the 95% confidence level."}
               </p>
            </div>

            <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm flex-1">
               <h3 className="font-semibold text-lg mb-6 flex justify-between">
                  <span>Wilson Score Interval</span>
                  <span className="text-xs bg-[var(--color-surface-offset)] border border-[var(--color-border)] px-2 py-1 rounded text-[var(--color-text-muted)] font-mono">95% CI</span>
               </h3>
               <div className="flex items-center justify-between p-4 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg mb-4">
                  <div className="text-2xl font-mono text-[var(--color-error)]">{(ciLow * 100).toFixed(2)}%</div>
                  <ArrowRight className="text-[var(--color-text-faint)] w-5 h-5 mx-4" />
                  <div className="text-2xl font-mono text-[var(--color-success)]">{(ciHigh * 100).toFixed(2)}%</div>
               </div>
               <p className="text-sm text-[var(--color-text-muted)] mt-2">
                  We can be 95% confident the true win rate lies within this boundary.
               </p>
            </div>
         </div>
      </div>
    </div>
  );
};
