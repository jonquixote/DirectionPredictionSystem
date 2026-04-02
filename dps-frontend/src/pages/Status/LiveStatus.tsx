import { useLiveStore } from '../../store/liveStore';
import { Activity, Database, Zap, ShieldAlert, Cpu } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

export const LiveStatus = () => {
  const { connected, last_status } = useLiveStore();

  if (!connected && !last_status) {
    return (
      <div className="p-8 flex flex-col items-center justify-center text-[var(--color-text-faint)] h-full">
        <Activity className="w-12 h-12 mb-4 animate-pulse opacity-50" />
        <p className="text-lg">Waiting for WebSocket connection...</p>
        <p className="font-mono text-sm mt-2 opacity-50">ws://localhost:8765/ws/live</p>
      </div>
    );
  }

  // Fallback mocks if backend is not feeding status yet
  const mockContainers = [
    { model: 'H300', healthy: true, warmup: false, uptime_seconds: 225300, cpu_pct: 42, ram_used_mb: 6100, ram_total_mb: 8192, last_prediction_ms: Date.now() - 23000, last_prediction_age_seconds: 23 },
    { model: 'H60 V3', healthy: true, warmup: true, uptime_seconds: 225300, cpu_pct: 38, ram_used_mb: 4200, ram_total_mb: 8192, last_prediction_ms: Date.now() - 41000, last_prediction_age_seconds: 41 },
    { model: 'H60 V1', healthy: false, warmup: false, uptime_seconds: 0, cpu_pct: 0, ram_used_mb: 0, ram_total_mb: 8192, last_prediction_ms: null, last_prediction_age_seconds: null }
  ];

  const containers = last_status?.containers || mockContainers;

  return (
    <div className="p-6 space-y-6 max-w-[1600px] mx-auto animate-in fade-in duration-500">
      <header className="flex items-center justify-between">
        <h1 className="font-display text-2xl font-bold tracking-tight text-[var(--color-text)]">Live System Status</h1>
        <div className="text-xs font-mono text-[var(--color-text-muted)]">
          Last updated: {last_status ? new Date(last_status.ts).toLocaleTimeString() : new Date().toLocaleTimeString()}
        </div>
      </header>

      {/* Row 1 — Container Health Cards */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {containers.map((c: any) => (
          <div key={c.model} className="p-4 rounded-xl bg-[var(--color-surface)] border border-[var(--color-border)] shadow-sm hover:shadow-md transition-shadow relative overflow-hidden group">
            <div className="flex justify-between items-start mb-4">
              <h2 className={`font-mono font-bold text-lg tracking-tight ${
                 c.model.includes('300') ? 'text-[var(--color-purple)]' : 
                 c.model.includes('V3') ? 'text-[var(--color-blue)]' : 'text-[var(--color-orange)]'
              }`}>{c.model}</h2>
              <div className="flex items-center space-x-2">
                <span className={`w-2.5 h-2.5 rounded-full ${
                  !c.healthy ? 'bg-[var(--color-error)]' : 
                  c.warmup ? 'bg-[var(--color-warning)]' : 'bg-[var(--color-success)]'
                }`}></span>
                <span className="text-xs font-mono font-medium tracking-wider">
                  {!c.healthy ? 'OFFLINE' : c.warmup ? 'WARMUP' : 'LIVE'}
                </span>
              </div>
            </div>

            <div className="space-y-3 text-sm text-[var(--color-text-muted)]">
              <div className="flex items-center justify-between">
                <span className="flex items-center"><Cpu className="w-4 h-4 mr-2" /> CPU / RAM</span>
                <span className="font-mono">{c.cpu_pct}% / {(c.ram_used_mb/1024).toFixed(1)}GB</span>
              </div>
              <div className="flex justify-between">
                <span>Uptime</span>
                <span className="font-mono">{c.healthy ? formatDistanceToNow(Date.now() - (c.uptime_seconds * 1000)) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span>Last pred</span>
                <span className="font-mono">{c.last_prediction_ms ? formatDistanceToNow(c.last_prediction_ms) + ' ago' : '—'}</span>
              </div>
            </div>

            {/* Subtle glow effect behind card based on status */}
            <div className={`absolute top-0 right-0 w-32 h-32 blur-3xl rounded-full opacity-10 pointer-events-none transition-opacity group-hover:opacity-20 ${
              !c.healthy ? 'bg-[var(--color-error)]' : c.warmup ? 'bg-[var(--color-warning)]' : 'bg-[var(--color-success)]'
            }`}></div>
          </div>
        ))}
      </section>

      {/* Row 2 — Data Pipeline Health */}
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Feature Pipeline */}
        <div className="p-4 rounded-xl bg-[var(--color-surface)] border border-[var(--color-border)] shadow-sm">
          <h3 className="flex items-center font-semibold mb-4 text-[var(--color-text)]">
            <Database className="w-4 h-4 mr-2 text-[var(--color-primary)]" /> Feature Pipeline
          </h3>
          <div className="space-y-2 text-sm text-[var(--color-text-muted)]">
             <div className="flex justify-between"><span>Status</span> <span className="text-[var(--color-success)] font-mono">Fresh</span></div>
             <div className="flex justify-between"><span>Last Ingest</span> <span className="font-mono">14m ago</span></div>
             <div className="flex justify-between"><span>Records</span> <span className="font-mono">2,482,109</span></div>
          </div>
        </div>

        {/* p_market API */}
        <div className="p-4 rounded-xl bg-[var(--color-surface)] border border-[var(--color-border)] shadow-sm">
          <h3 className="flex items-center font-semibold mb-4 text-[var(--color-text)]">
            <Zap className="w-4 h-4 mr-2 text-[var(--color-primary)]" /> Market API
          </h3>
          <div className="space-y-2 text-sm text-[var(--color-text-muted)]">
             <div className="flex justify-between"><span>Latency</span> <span className="font-mono text-[var(--color-success)]">42ms</span></div>
             <div className="flex justify-between"><span>Last Fetch</span> <span className="font-mono">Current</span></div>
             <div className="flex justify-between"><span>Failures</span> <span className="font-mono">0</span></div>
          </div>
        </div>

        {/* EWM Preload */}
        <div className="p-4 rounded-xl bg-[var(--color-surface)] border border-[var(--color-border)] shadow-sm">
          <h3 className="flex items-center font-semibold mb-4 text-[var(--color-text)]">
             <ShieldAlert className="w-4 h-4 mr-2 text-[var(--color-primary)]" /> Gate Status Summary
          </h3>
          <div className="space-y-2 text-sm text-[var(--color-text-muted)]">
              <div className="flex justify-between items-center"><span className="font-mono">H300</span> <span className="bg-[var(--color-success)]/10 text-[var(--color-success)] px-2 py-0.5 rounded text-xs font-mono font-bold">✅ PASS</span></div>
              <div className="flex justify-between items-center"><span className="font-mono">H60V3</span> <span className="bg-[var(--color-error)]/10 text-[var(--color-error)] px-2 py-0.5 rounded text-xs font-mono font-bold">❌ FAIL</span></div>
              <div className="flex justify-between items-center"><span className="font-mono">H60V1</span> <span className="bg-[var(--color-surface-offset)] text-[var(--color-text-faint)] px-2 py-0.5 rounded text-xs font-mono font-bold">— MUTE</span></div>
          </div>
        </div>
      </section>

      {/* Row 4 — Execution Funnel (Mocked for Visual) */}
      <section className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] p-5 shadow-sm overflow-x-auto">
        <h3 className="font-semibold mb-6 flex items-center">Execution Funnel</h3>
        <div className="flex justify-between items-end min-w-[700px] h-24 mb-2">
            {[ {label: 'Predictions', val: 450}, {label: 'Structural', val: 380}, {label: 'Adverse', val: 310}, {label: 'NE_t', val: 290}, {label: 'Sanderink', val: 270}, {label: 'Executed', val: 245} ].map((stage, _, arr) => {
              const height = (stage.val / arr[0].val) * 100;
              return (
                <div key={stage.label} className="flex flex-col items-center flex-1">
                   <div 
                      className="w-16 bg-[var(--color-primary)]" 
                      style={{ height: `${height}%`, opacity: 0.2 + (0.8 * (height/100)) }}
                   ></div>
                </div>
              )
            })}
        </div>
        <div className="flex justify-between min-w-[700px] text-xs font-mono text-[var(--color-text-muted)] text-center divide-x divide-[var(--color-divider)]">
          {[ 'Predictions\n450', 'Structural\n380', 'Adverse\n310', 'NE_t\n290', 'Sanderink\n270', 'Executed\n245' ].map(l => (
            <div key={l} className="flex-1 whitespace-pre-line pt-2">{l}</div>
          ))}
        </div>
      </section>
    </div>
  );
};
