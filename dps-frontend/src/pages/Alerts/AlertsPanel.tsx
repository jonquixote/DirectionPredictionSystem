import { useState, useEffect } from 'react';
import { endpoints } from '../../api/endpoints';
import { BadgeAlert, Loader2, AlertCircle, CheckCircle2, Info, AlertTriangle } from 'lucide-react';
import { format } from 'date-fns';

export const AlertsPanel = () => {
  const [alerts, setAlerts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetchAlerts = async () => {
      try {
        setLoading(true);
        const data = await endpoints.getActiveAlerts({ limit: 100 });
        if (mounted) {
           setAlerts(data.data || []);
           setError(null);
        }
      } catch (err: any) {
        if (mounted) setError(err.message || 'Error fetching active alerts');
      } finally {
        if (mounted) setLoading(false);
      }
    };
    fetchAlerts();
    return () => { mounted = false; };
  }, []);

  return (
    <div className="h-full flex flex-col p-6 overflow-y-auto animate-in fade-in duration-500 space-y-8 relative">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
         <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--color-text)] flex items-center">
              <BadgeAlert className="w-6 h-6 mr-3 text-[var(--color-primary)]" />
              System Alerts & Monitoring
            </h1>
            <p className="text-[var(--color-text-muted)] text-sm mt-1">Active alerts, thresholds, and suppression effectiveness tracking.</p>
         </div>
      </div>

      <div className="flex-1 min-h-[400px] bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 shadow-sm overflow-auto">
         <div className="flex justify-between items-center mb-6">
            <h3 className="font-semibold text-lg flex items-center">
               Active Alerts Log
            </h3>
         </div>
         
         <div className="min-h-[200px]">
            {loading ? (
              <div className="flex h-[200px] items-center justify-center flex-col text-[var(--color-text-muted)]">
                 <Loader2 className="w-8 h-8 animate-spin mb-4 text-[var(--color-primary)]" />
                 Loading alerts...
              </div>
            ) : error ? (
              <div className="flex justify-center flex-col h-[200px] items-center">
                 <div className="text-[var(--color-error)] flex items-center bg-[var(--color-error)]/10 p-4 rounded-lg border border-[var(--color-error)]/20">
                    <AlertCircle className="w-5 h-5 mr-3" />
                    {error}
                 </div>
              </div>
            ) : (
               <div className="space-y-3">
                 {alerts.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-12 text-[var(--color-text-muted)] bg-[var(--color-surface-offset)] border border-[var(--color-border)] rounded-lg border-dashed">
                       <CheckCircle2 className="w-10 h-10 mb-3 text-[var(--color-success)] opacity-50"/>
                       <span>No active alerts. System nominal.</span>
                    </div>
                 ) : (
                    alerts.map((alert, i) => {
                       const isCritical = alert.severity === 'CRITICAL';
                       const isWarn = alert.severity === 'WARN';
                       return (
                         <div key={i} className={`p-4 rounded-lg border ${isCritical ? 'bg-[var(--color-error)]/10 border-[var(--color-error)]/30 text-[var(--color-error)]' : isWarn ? 'bg-[var(--color-warning)]/10 border-[var(--color-warning)]/30 text-[var(--color-warning)]' : 'bg-[var(--color-blue)]/5 border-[var(--color-blue)]/20 text-[var(--color-blue)]'} flex items-start`}>
                            <div className="mt-0.5 mr-3 flex-shrink-0">
                               {isCritical ? <AlertCircle className="w-5 h-5" /> : isWarn ? <AlertTriangle className="w-5 h-5" /> : <Info className="w-5 h-5" />}
                            </div>
                            <div className="flex-1">
                               <div className="flex justify-between items-start">
                                  <h4 className="font-bold font-mono tracking-tight">{alert.type}</h4>
                                  <span className="text-xs font-mono opacity-70">
                                     {format(new Date(alert.timestamp_ms), 'MMM dd, HH:mm:ss')}
                                  </span>
                               </div>
                               <p className="mt-1 text-sm tracking-wide opacity-90">{alert.message}</p>
                               {alert.context && Object.keys(alert.context).length > 0 && (
                                  <div className="mt-3 bg-black/30 p-2.5 rounded text-xs font-mono opacity-80 backdrop-blur-sm shadow-inner break-all">
                                     {JSON.stringify(alert.context, null, 2)}
                                  </div>
                               )}
                            </div>
                         </div>
                       )
                    })
                 )}
               </div>
            )}
         </div>
      </div>
    </div>
  );
};
