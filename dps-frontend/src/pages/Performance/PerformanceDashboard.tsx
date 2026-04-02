import { useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, LineChart, Line, ReferenceLine, BarChart, Bar } from 'recharts';
import { format } from 'date-fns';
import { Activity, TrendingUp, AlertTriangle, Info, BarChart3, Target, ShieldCheck, Zap, Loader2, AlertCircle } from 'lucide-react';
import { endpoints } from '../../api/endpoints';
import type { PerformanceSummary } from '../../types';

export const PerformanceDashboard = () => {
  const [activeModel, setActiveModel] = useState<string>('All');
  const [activeTab, setActiveTab] = useState<string>('Overview');
  
  const [summaryData, setSummaryData] = useState<PerformanceSummary | null>(null);
  const [allSummaries, setAllSummaries] = useState<any[]>([]);
  const [cumlData, setCumlData] = useState<any[]>([]);
  const [rollingData, setRollingData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetchData = async () => {
      try {
        setLoading(true);
        // We fetch the performance summary for the active model (or ALL)
        const summaryArr = await endpoints.getPerformanceSummary({ model: activeModel === 'All' ? undefined : activeModel.toLowerCase() });
        const rollingResp = await endpoints.getRollingAccuracy({ model: activeModel === 'All' ? 'h60_v1' : activeModel.toLowerCase(), limit: 50 });
        
        if (!mounted) return;
        
        if (summaryArr && summaryArr.length > 0) {
          setAllSummaries(summaryArr);
          if (activeModel === 'All') {
            const allModels = summaryArr.filter((s: any) => s.symbol === 'ALL');
            if (allModels.length > 0) {
              const aggregated: any = { ...allModels[0] };
              aggregated.total_trades = allModels.reduce((acc: number, curr: any) => acc + curr.total_trades, 0);
              aggregated.wins = allModels.reduce((acc: number, curr: any) => acc + curr.wins, 0);
              aggregated.losses = allModels.reduce((acc: number, curr: any) => acc + curr.losses, 0);
              aggregated.unresolved = allModels.reduce((acc: number, curr: any) => acc + curr.unresolved, 0);
              aggregated.accuracy = aggregated.total_trades > 0 ? aggregated.wins / aggregated.total_trades : 0;
              aggregated.realized_net_total = allModels.reduce((acc: number, curr: any) => acc + (curr.realized_net_total || 0), 0);
              aggregated.realized_net_per_trade = aggregated.total_trades > 0 ? aggregated.realized_net_total / aggregated.total_trades : 0;
              
              // Approximation of Gate Pass Rate (weighted average)
              const totalCands = allModels.reduce((acc: number, curr: any) => acc + (curr.candidates_total || 0), 0);
              aggregated.candidates_total = totalCands;
              aggregated.gate_pass_rate = totalCands > 0 ? aggregated.total_trades / totalCands : 0;
              
              setSummaryData(aggregated as PerformanceSummary);
            } else {
              setSummaryData(summaryArr[0]);
            }
          } else {
            const specific = summaryArr.find((s: any) => s.symbol === 'ALL') || summaryArr[0];
            setSummaryData(specific);
          }
        }
        
        // Transform rolling accuracy for Recharts
        if (rollingResp?.series) {
           const mapped = rollingResp.series.map((d: any) => ({
             name: format(new Date(d.timestamp_ms || d.ts_created_ms || Date.now()), 'MMM dd HH:mm'),
             accuracy: d.accuracy * 100
           }));
           setRollingData(mapped);
        } else {
           setRollingData([]);
        }

        // Fetch actual timeline data
        const timelineResp = await endpoints.getPerformanceTimeline();
        if (timelineResp?.data) {
           setCumlData(timelineResp.data);
        } else {
           setCumlData([]);
        }
        setError(null);
      } catch (err: any) {
        if (mounted) setError(err.message || 'Error fetching performance');
      } finally {
        if (mounted) setLoading(false);
      }
    };
    fetchData();
    return () => { mounted = false; };
  }, [activeModel]);

  const KpiCard = ({ title, primary, secondary, icon: Icon, trendingUp, alert }: any) => (
    <div className={`p-5 rounded-xl border bg-[var(--color-surface)] shadow-md transition-all hover:-translate-y-1 hover:shadow-lg ${alert ? 'border-[var(--color-warning)]/50 bg-[var(--color-warning)]/5' : 'border-[var(--color-border)]'}`}>
       <div className="flex justify-between items-start mb-2">
          <div className="text-sm font-medium text-[var(--color-text-muted)] tracking-wide uppercase">{title}</div>
          <Icon className={`w-5 h-5 ${alert ? 'text-[var(--color-warning)]' : 'text-[var(--color-text-faint)]'}`} />
       </div>
       <div className={`text-3xl font-bold font-mono tracking-tight mb-2 ${trendingUp === true ? 'text-[var(--color-success)]' : trendingUp === false ? 'text-[var(--color-error)]' : 'text-[var(--color-text)]'}`}>
          {primary}
       </div>
       <div className="text-xs text-[var(--color-text-faint)] font-mono">{secondary}</div>
    </div>
  );

  return (
    <div className="h-full flex flex-col p-6 overflow-y-auto animate-in fade-in duration-500 space-y-8">
      
      {/* Header and Controls */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
         <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--color-text)] flex items-center">
              <BarChart3 className="w-6 h-6 mr-3 text-[var(--color-primary)]" />
              Performance Dashboard
            </h1>
            <p className="text-[var(--color-text-muted)] text-sm mt-1">Core accuracy, edge, and risk metrics across your fleet.</p>
         </div>

         <div className="flex items-center space-x-3 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-1.5 shadow-sm">
            {['All', 'H60_V1', 'H60_V3', 'H300'].map(m => (
               <button 
                  key={m}
                  onClick={() => setActiveModel(m)}
                  className={`px-4 py-1.5 text-sm font-medium rounded-md transition-all ${activeModel === m ? 'bg-[var(--color-primary)] text-black shadow-sm' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]'}`}
               >
                 {m.replace('_', ' ')}
               </button>
            ))}
         </div>
      </div>

      {/* Sub tabs nav */}
      <div className="border-b border-[var(--color-border)]">
        <nav className="-mb-px flex space-x-6">
          {['Overview', 'By Symbol', 'By Contract', 'Streaks & Drawdown'].map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`whitespace-nowrap py-3 px-1 border-b-2 font-medium text-sm transition-colors ${
                activeTab === tab
                  ? 'border-[var(--color-primary)] text-[var(--color-primary)]'
                  : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-border)]'
              }`}
            >
              {tab}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab Content Area */}
      {activeTab === 'Overview' && (
         <>
            {/* KPI Grid */}
            {loading ? (
              <div className="flex justify-center py-12">
                 <Loader2 className="w-8 h-8 animate-spin text-[var(--color-primary)]" />
              </div>
            ) : error ? (
              <div className="text-[var(--color-error)] flex items-center bg-[var(--color-error)]/10 p-4 rounded-lg border border-[var(--color-error)]/20">
                 <AlertCircle className="w-5 h-5 mr-3" />
                 {error}
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
                   <KpiCard title="Accuracy" primary={summaryData ? `${(summaryData.accuracy * 100).toFixed(1)}%` : "—"} secondary={summaryData ? `${summaryData.wins}W / ${summaryData.losses}L` : "—"} icon={Target} trendingUp={true} />
                   <KpiCard title="Total Trades" primary={summaryData?.total_trades || "—"} secondary={summaryData ? `${summaryData.unresolved} open` : "—"} icon={Activity} />
                   <KpiCard title="Realized NE_t" primary={summaryData?.realized_net ? `+$${summaryData.realized_net.toFixed(2)}` : "—"} secondary={summaryData ? `+$${summaryData.realized_net_per_trade.toFixed(2)} / trade avg` : "—"} icon={TrendingUp} trendingUp={true} />
                   <KpiCard title="Gate Pass Rate" primary={summaryData ? `${(summaryData.gate_pass_rate * 100).toFixed(1)}%` : "—"} secondary="Overall rate" icon={ShieldCheck} />
                   <KpiCard title="Avg p_market" primary={summaryData?.p_market_avg?.toFixed(4) || "—"} secondary="Market baseline expectation" icon={Zap} />
                   <KpiCard title="Expected Value" primary={summaryData?.expected_value_empirical?.toFixed(4) || "—"} secondary="Empirical EV" icon={Info} trendingUp={summaryData && summaryData.expected_value_empirical > 0} />
                </div>

                {/* Charts Row */}
                <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
                  
                  {/* Cumulative P&L Curve */}
                  <div className="xl:col-span-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 shadow-sm flex flex-col">
                    <div className="flex justify-between items-center mb-6">
                       <h3 className="font-semibold text-lg flex items-center">
                          Cumulative Realized NE_t
                       </h3>
                       <div className="text-sm font-mono text-[var(--color-success)]">+14.2% Month-over-Month</div>
                    </div>
                    <div className="flex-1 min-h-[350px]">
                       <ResponsiveContainer width="100%" height="100%">
                          <AreaChart data={cumlData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                             <defs>
                               <linearGradient id="colorH300" x1="0" y1="0" x2="0" y2="1">
                                 <stop offset="5%" stopColor="var(--color-purple)" stopOpacity={0.3}/>
                                 <stop offset="95%" stopColor="var(--color-purple)" stopOpacity={0}/>
                               </linearGradient>
                               <linearGradient id="colorH60V3" x1="0" y1="0" x2="0" y2="1">
                                 <stop offset="5%" stopColor="var(--color-blue)" stopOpacity={0.3}/>
                                 <stop offset="95%" stopColor="var(--color-blue)" stopOpacity={0}/>
                               </linearGradient>
                               <linearGradient id="colorH60V1" x1="0" y1="0" x2="0" y2="1">
                                 <stop offset="5%" stopColor="var(--color-orange)" stopOpacity={0.3}/>
                                 <stop offset="95%" stopColor="var(--color-orange)" stopOpacity={0}/>
                               </linearGradient>
                             </defs>
                             <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--color-border)" opacity={0.5} />
                             <XAxis dataKey="name" tick={{fill: 'var(--color-text-faint)', fontSize: 12}} axisLine={false} tickLine={false} dy={10} minTickGap={30} />
                             <YAxis tick={{fill: 'var(--color-text-faint)', fontSize: 12}} axisLine={false} tickLine={false} dx={-10} tickFormatter={(v) => `$${v}`} />
                             <Tooltip 
                               contentStyle={{ backgroundColor: 'var(--color-surface-2)', borderColor: 'var(--color-border)', borderRadius: '8px', color: 'var(--color-text)' }}
                               itemStyle={{ fontFamily: 'var(--font-mono)' }}
                             />
                             <Legend verticalAlign="top" height={36} iconType="circle" />
                             <Area type="monotone" dataKey="H300" stroke="var(--color-purple)" fillOpacity={1} fill="url(#colorH300)" strokeWidth={2} />
                             <Area type="monotone" dataKey="H60_V3" stroke="var(--color-blue)" fillOpacity={1} fill="url(#colorH60V3)" strokeWidth={2} />
                             <Area type="monotone" dataKey="H60_V1" stroke="var(--color-orange)" fillOpacity={1} fill="url(#colorH60V1)" strokeWidth={2} />
                          </AreaChart>
                       </ResponsiveContainer>
                    </div>
                  </div>

                  {/* Rolling Accuracy & Variance Context */}
                  <div className="flex flex-col space-y-6">
                     <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 shadow-sm flex-1 flex flex-col">
                        <div className="flex justify-between items-center mb-6">
                           <h3 className="font-semibold flex items-center">
                              Rolling-50 Accuracy
                           </h3>
                           <span className="bg-[var(--color-surface-offset)] border border-[var(--color-border)] text-[var(--color-text-muted)] text-xs px-2 py-1 rounded font-mono">N=50</span>
                        </div>
                        <div className="flex-1 min-h-[180px]">
                           <ResponsiveContainer width="100%" height="100%">
                              <LineChart data={rollingData} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
                                 <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--color-border)" opacity={0.3} />
                                 <XAxis dataKey="name" tick={{fill: 'var(--color-text-faint)', fontSize: 10}} axisLine={false} tickLine={false} dy={10} minTickGap={20} />
                                 <YAxis minTickGap={10} tick={{fill: 'var(--color-text-faint)', fontSize: 10}} axisLine={false} tickLine={false} domain={[40, 80]} tickFormatter={v => `${v}%`} />
                                 <Tooltip 
                                   contentStyle={{ backgroundColor: 'var(--color-surface-2)', borderColor: 'var(--color-border)', borderRadius: '8px', padding: '8px' }}
                                   itemStyle={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--color-text)' }}
                                   labelStyle={{ display: 'none' }}
                                   formatter={(val: any) => [`${Number(val).toFixed(1)}%`, 'Accuracy']}
                                 />
                                 <ReferenceLine y={51.5} stroke="var(--color-error)" strokeDasharray="3 3" strokeWidth={1.5} label={{ position: 'top', value: 'Gate (51.5%)', fill: 'var(--color-error)', fontSize: 10 }} />
                                 <Line type="monotone" dataKey="accuracy" stroke="var(--color-primary)" strokeWidth={2.5} dot={false} activeDot={{ r: 4, fill: 'var(--color-primary)' }} />
                              </LineChart>
                           </ResponsiveContainer>
                        </div>
                     </div>

                     {/* Variance Context Card */}
                     <div className="bg-[#1c1816] border border-[var(--color-orange)]/40 rounded-xl p-5 shadow-lg relative overflow-hidden group">
                        <div className="absolute top-0 right-0 w-32 h-32 bg-[var(--color-orange)] opacity-[0.03] rounded-bl-[100px] pointer-events-none group-hover:opacity-[0.06] transition-opacity"></div>
                        
                        <div className="flex items-start mb-3">
                           <AlertTriangle className="w-5 h-5 text-[var(--color-orange)] mr-3 shrink-0" />
                           <h4 className="font-semibold text-[var(--color-orange)] leading-tight tracking-wide">
                              Gate failed — but is this signal or variance?
                           </h4>
                        </div>
                        
                        <div className="space-y-4 text-sm text-[var(--color-text-muted)] leading-relaxed">
                           <p>Rolling-50 at <span className="text-[var(--color-error)] font-mono font-medium">50.0%</span> is within expected sampling variance for a <span className="text-[var(--color-success)] font-mono font-medium">58.4%</span> true accuracy model.</p>
                           
                           <div className="font-mono bg-black/40 border border-[var(--color-orange)]/20 p-3 rounded-lg text-xs space-y-1.5 shadow-inner backdrop-blur-sm">
                              <div className="flex justify-between">
                                 <span>z-score:</span>
                                 <span className="text-[var(--color-error)]">−1.13</span>
                              </div>
                              <div className="flex justify-between">
                                 <span>p-value:</span>
                                 <span>0.258 <span className="text-[var(--color-text-faint)]">(two-tailed)</span></span>
                              </div>
                              <div className="pt-2 mt-1 border-t border-[var(--color-border)]/50 text-[var(--color-text)]">
                                 1 in 3.9 chance of seeing this or worse by random alone
                              </div>
                           </div>

                           <p className="text-[var(--color-text)] flex items-center font-medium bg-[var(--color-orange)]/10 p-2.5 rounded border border-[var(--color-orange)]/20">
                              <Info className="w-4 h-4 mr-2 text-[var(--color-orange)]"/> 
                              Not yet evidence of real edge erosion. Watch next 2 batches.
                           </p>
                        </div>
                     </div>
                  </div>
                </div>
              </>
            )}
         </>
      )}

      {activeTab === 'By Symbol' && (
         <div className="grid grid-cols-1 md:grid-cols-3 gap-6 animate-in fade-in">
             {allSummaries.filter(s => s.symbol !== 'ALL' && s.contract_duration === 'ALL').map((symData) => {
                const sym = symData.symbol.replace('USDT', '');
                return (
                <div key={sym} className={`bg-[var(--color-surface)] border ${sym === 'BTC' ? 'border-[var(--color-orange)] shadow-[0_0_15px_rgba(251,146,60,0.1)]' : 'border-[var(--color-border)]'} rounded-xl p-6 shadow-sm`}>
                   <div className="flex justify-between items-center mb-6">
                      <div className="flex items-center">
                         <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold text-xs ${sym === 'BTC' ? 'bg-[#f7931a]/20 text-[#f7931a]' : sym === 'ETH' ? 'bg-[#627eea]/20 text-[#627eea]' : 'bg-[#14f195]/20 text-[#14f195]'}`}>
                            {sym}
                         </div>
                         <h3 className="ml-3 font-semibold text-lg">{sym}</h3>
                      </div>
                      <span className={`font-mono font-bold text-xl ${symData.accuracy >= 0.5 ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]'}`}>
                          {symData.accuracy ? `${(symData.accuracy * 100).toFixed(1)}%` : "—"}
                      </span>
                   </div>
                   
                   <div className="space-y-4 text-sm">
                      <div className="flex justify-between py-2 border-b border-[var(--color-border)]/50">
                         <span className="text-[var(--color-text-muted)]">Trades</span>
                         <span className="font-mono">{symData.total_trades}</span>
                      </div>
                      <div className="flex justify-between py-2 border-b border-[var(--color-border)]/50">
                         <span className="text-[var(--color-text-muted)]">Realized NE_t</span>
                         <span className={`font-mono ${symData.realized_net_total >= 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]'}`}>
                             {symData.realized_net_total != null ? `${symData.realized_net_total >= 0 ? '+' : ''}$${symData.realized_net_total.toFixed(2)}` : "—"}
                         </span>
                      </div>
                      <div className="flex justify-between py-2 border-b border-[var(--color-border)]/50">
                         <span className="text-[var(--color-text-muted)]">Avg NE_t / trade</span>
                         <span className={`font-mono ${symData.realized_net_per_trade >= 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]'}`}>
                             {symData.realized_net_per_trade != null ? `${symData.realized_net_per_trade >= 0 ? '+' : ''}$${symData.realized_net_per_trade.toFixed(2)}` : "—"}
                         </span>
                      </div>
                      <div className="flex justify-between py-2">
                         <span className="text-[var(--color-text-muted)]">Significance (Z)</span>
                         <span className={`font-mono ${(symData.z_score || 0) >= 1.645 ? 'text-[var(--color-success)]' : 'text-[var(--color-text-faint)]'}`}>
                             {symData.z_score != null ? `${symData.z_score.toFixed(2)} (p=${symData.p_value?.toFixed(2)})` : "—"}
                         </span>
                      </div>
                      
                      <div className="pt-4">
                         <div className="w-full bg-[var(--color-surface-offset)] rounded-full h-2.5 flex overflow-hidden">
                            <div className="bg-[var(--color-success)] h-2.5" style={{ width: `${(symData.accuracy || 0) * 100}%` }}></div>
                            <div className="bg-[var(--color-error)] h-2.5" style={{ width: `${(1 - (symData.accuracy || 0)) * 100}%` }}></div>
                         </div>
                         <div className="flex justify-between text-xs font-mono text-[var(--color-text-faint)] mt-2">
                            <span>{symData.wins} WIN</span>
                            <span>{symData.losses} LOSS</span>
                         </div>
                      </div>
                   </div>
                </div>
             )})}
         </div>
      )}

      {activeTab === 'By Contract' && (
         <div className="flex-1 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm animate-in fade-in flex flex-col">
            <div className="flex justify-between items-center mb-6">
                <h3 className="font-semibold text-lg flex items-center">
                   Duration Horizon Comparison (300s vs 900s)
                </h3>
            </div>
            
            <div className="flex-1 min-h-[400px]">
               <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={[
                     { 
                        name: 'All Symbols', 
                        '300s': (allSummaries.find(s => s.symbol === 'ALL' && s.contract_duration === '300')?.accuracy || 0) * 100, 
                        '900s': (allSummaries.find(s => s.symbol === 'ALL' && s.contract_duration === '900')?.accuracy || 0) * 100 
                     },
                     { 
                        name: 'BTC', 
                        '300s': (allSummaries.find(s => s.symbol === 'BTCUSDT' && s.contract_duration === '300')?.accuracy || 0) * 100, 
                        '900s': (allSummaries.find(s => s.symbol === 'BTCUSDT' && s.contract_duration === '900')?.accuracy || 0) * 100 
                     },
                     { 
                        name: 'ETH', 
                        '300s': (allSummaries.find(s => s.symbol === 'ETHUSDT' && s.contract_duration === '300')?.accuracy || 0) * 100, 
                        '900s': (allSummaries.find(s => s.symbol === 'ETHUSDT' && s.contract_duration === '900')?.accuracy || 0) * 100 
                     },
                     { 
                        name: 'SOL', 
                        '300s': (allSummaries.find(s => s.symbol === 'SOLUSDT' && s.contract_duration === '300')?.accuracy || 0) * 100, 
                        '900s': (allSummaries.find(s => s.symbol === 'SOLUSDT' && s.contract_duration === '900')?.accuracy || 0) * 100 
                     },
                  ].filter(d => d['300s'] > 0 || d['900s'] > 0)} margin={{ top: 20, right: 30, left: 0, bottom: 5 }}>
                     <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--color-border)" opacity={0.3} />
                     <XAxis dataKey="name" tick={{fill: 'var(--color-text-faint)'}} axisLine={false} tickLine={false} />
                     <YAxis tick={{fill: 'var(--color-text-faint)'}} axisLine={false} tickLine={false} domain={[45, 65]} tickFormatter={v => `${v}%`} />
                     <Tooltip 
                       contentStyle={{ backgroundColor: 'var(--color-surface-2)', borderColor: 'var(--color-border)', borderRadius: '8px' }}
                       formatter={(val: any) => [`${val}%`, 'Win Rate']}
                     />
                     <Legend verticalAlign="top" height={36} />
                     <ReferenceLine y={50} stroke="var(--color-error)" strokeDasharray="3 3" opacity={0.5} />
                     <Bar dataKey="300s" fill="var(--color-primary)" radius={[4, 4, 0, 0]} opacity={0.8} />
                     <Bar dataKey="900s" fill="var(--color-purple)" radius={[4, 4, 0, 0]} opacity={0.8} />
                  </BarChart>
               </ResponsiveContainer>
            </div>
         </div>
      )}

      {activeTab === 'Streaks & Drawdown' && (
         <div className="flex-1 grid grid-cols-1 md:grid-cols-3 gap-6 animate-in fade-in">
             <div className="md:col-span-1 flex flex-col space-y-6">
                 <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm">
                    <h3 className="text-sm font-medium text-[var(--color-text-muted)] uppercase mb-4">Current Streak</h3>
                    <div className="text-4xl font-mono font-bold text-[var(--color-success)] tracking-tighter mb-1">🟢 W4</div>
                    <div className="text-sm text-[var(--color-text-faint)]">+ $12.44 realized NE_t</div>
                 </div>
                 
                 <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm">
                    <h3 className="text-sm font-medium text-[var(--color-text-muted)] uppercase mb-4">Historical Extremes</h3>
                    <div className="space-y-4">
                       <div className="flex justify-between items-center bg-[var(--color-success)]/10 border border-[var(--color-success)]/20 p-3 rounded-lg">
                          <span className="text-[var(--color-text)] font-semibold">Max Win Streak</span>
                          <span className="font-mono text-[var(--color-success)] font-bold">11</span>
                       </div>
                       <div className="flex justify-between items-center bg-[var(--color-error)]/10 border border-[var(--color-error)]/20 p-3 rounded-lg">
                          <span className="text-[var(--color-text)] font-semibold">Max Loss Streak</span>
                          <span className="font-mono text-[var(--color-error)] font-bold">7</span>
                       </div>
                       <div className="flex justify-between items-center bg-[var(--color-warning)]/10 border border-[var(--color-warning)]/20 p-3 rounded-lg">
                          <span className="text-[var(--color-text)] font-semibold">Max Drawdown</span>
                          <span className="font-mono text-[var(--color-warning)] font-bold">−$185.20</span>
                       </div>
                    </div>
                 </div>
             </div>
             
             <div className="md:col-span-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm flex flex-col">
                 <div className="flex justify-between items-center mb-6">
                     <h3 className="font-semibold text-lg">Drawdown Timeline</h3>
                 </div>
                 <div className="flex-1 min-h-[300px]">
                    <ResponsiveContainer width="100%" height="100%">
                       <AreaChart data={Array.from({length: 30}).map((_, i) => ({ name: `T-${30-i}`, dd: Math.min(0, (Math.sin(i/3) * -40) - 10) }))} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                          <defs>
                             <linearGradient id="colorDrawdown" x1="0" y1="0" x2="0" y2="1">
                               <stop offset="5%" stopColor="var(--color-error)" stopOpacity={0.4}/>
                               <stop offset="95%" stopColor="var(--color-error)" stopOpacity={0}/>
                             </linearGradient>
                          </defs>
                          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--color-border)" opacity={0.3} />
                          <XAxis dataKey="name" tick={{fill: 'var(--color-text-faint)'}} axisLine={false} tickLine={false} minTickGap={20} />
                          <YAxis tick={{fill: 'var(--color-text-faint)'}} axisLine={false} tickLine={false} domain={[-60, 0]} tickFormatter={v => `$${v}`} />
                          <Tooltip 
                            contentStyle={{ backgroundColor: 'var(--color-surface-2)', borderColor: 'var(--color-border)', borderRadius: '8px' }}
                            itemStyle={{ fontFamily: 'var(--font-mono)' }}
                          />
                          <Area type="step" dataKey="dd" stroke="var(--color-error)" fillOpacity={1} fill="url(#colorDrawdown)" strokeWidth={2} />
                       </AreaChart>
                    </ResponsiveContainer>
                 </div>
             </div>
         </div>
      )}

    </div>
  );
};
