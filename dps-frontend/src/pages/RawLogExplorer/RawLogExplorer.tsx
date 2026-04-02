import { useState, useEffect } from 'react';
import { JsonView, darkStyles, defaultStyles } from 'react-json-view-lite';
import 'react-json-view-lite/dist/index.css';
import { Terminal, Search, Loader2 } from 'lucide-react';
import { useSettingsStore } from '../../store/settingsStore';
import { endpoints } from '../../api/endpoints';

export const RawLogExplorer = () => {
  const { theme } = useSettingsStore();
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedLog, setSelectedLog] = useState<any | null>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
     let mounted = true;
     const fetchLogs = async () => {
        try {
           setLoading(true);
           const resp = await endpoints.getRawLogs({ limit: 100 });
           if (mounted && resp?.data) {
              setLogs(resp.data);
           }
           setError(null);
        } catch (err: any) {
           if (mounted) setError(err.message || 'Error fetching logs');
        } finally {
           if (mounted) setLoading(false);
        }
     };
     fetchLogs();
     return () => { mounted = false; };
  }, []);

  const filteredLogs = logs.filter(log => {
     const text = searchTerm.toLowerCase();
     const eventName = log.component ? String(log.component).toLowerCase() : '';
     return (
        String(log.id).toLowerCase().includes(text) || 
        eventName.includes(text) ||
        String(log.level).toLowerCase().includes(text) ||
        String(log.message).toLowerCase().includes(text)
     );
  });

  return (
    <div className="h-full flex flex-col p-6 overflow-y-auto animate-in fade-in duration-500 space-y-6 relative">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
         <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--color-text)] flex items-center">
              <Terminal className="w-6 h-6 mr-3 text-[var(--color-primary)]" />
              Raw Log Explorer
            </h1>
            <p className="text-[var(--color-text-muted)] text-sm mt-1">Interactive diagnostics of system trace telemetry directly from the FastAPI core.</p>
         </div>
         <div className="flex items-center space-x-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-md px-3 py-1.5 focus-within:ring-1 focus-within:ring-[var(--color-primary)]">
            <Search className="w-4 h-4 text-[var(--color-text-muted)]" />
            <input 
              type="text" 
              placeholder="Search logs..." 
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="bg-transparent border-none outline-none text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] w-40 md:w-64"
            />
         </div>
      </div>

      <div className="flex-1 flex flex-col md:flex-row gap-6 min-h-[500px]">
         {loading ? (
            <div className="w-full flex justify-center items-center h-full text-[var(--color-primary)]">
               <Loader2 className="w-8 h-8 animate-spin" />
            </div>
         ) : error ? (
            <div className="w-full flex justify-center items-center h-full text-[var(--color-error)] border border-[var(--color-error)]/20 rounded-xl bg-[var(--color-error)]/5">
               <span className="font-mono text-sm">{error}</span>
            </div>
         ) : (
            <>
         {/* List View */}
         <div className="w-full md:w-1/3 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl shadow-sm flex flex-col">
            <div className="p-4 border-b border-[var(--color-border)] bg-[var(--color-surface-offset)]/50 rounded-t-xl">
               <span className="font-semibold text-sm">Recent Events</span>
            </div>
            <div className="flex-1 overflow-y-auto">
               <ul className="divide-y divide-[var(--color-border)]/50">
                  {filteredLogs.map(log => (
                     <li 
                       key={log.id} 
                       onClick={() => setSelectedLog(log)}
                       className={`p-3 cursor-pointer transition-colors hover:bg-[var(--color-surface-offset)] ${selectedLog?.id === log.id ? 'bg-[var(--color-surface-dynamic)] border-l-4 border-[var(--color-primary)]' : 'border-l-4 border-transparent'}`}
                     >
                        <div className="flex justify-between items-center mb-1">
                           <span className={`text-xs font-bold ${log.level === 'ERROR' ? 'text-[var(--color-error)]' : log.level === 'WARNING' ? 'text-[var(--color-warning)]' : 'text-[var(--color-text-muted)]'}`}>
                              {log.level}
                           </span>
                           <span className="text-[10px] font-mono text-[var(--color-text-faint)]">
                              {log.timestamp ? log.timestamp.split('T')[1]?.split('.')[0] : new Date(log.timestamp_ms || Date.now()).toISOString().split('T')[1].split('.')[0]}
                           </span>
                        </div>
                        <div className="text-sm font-mono truncate text-[var(--color-text)]">{log.event}</div>
                     </li>
                  ))}
               </ul>
            </div>
         </div>

         {/* JSON Viewer */}
         <div className="w-full md:w-2/3 bg-[#0d1117] border border-[var(--color-border)] rounded-xl shadow-inner flex flex-col">
            <div className="p-4 border-b border-[var(--color-border)]/20 bg-black/20 rounded-t-xl flex justify-between items-center">
               <span className="font-mono text-xs text-[var(--color-primary)]">{selectedLog ? selectedLog.id : 'No log selected'}</span>
               <Terminal className="w-4 h-4 text-[var(--color-text-faint)]" />
            </div>
            <div className="flex-1 overflow-auto p-4 text-sm font-mono">
               {selectedLog ? (
                  <JsonView 
                    data={selectedLog} 
                    shouldExpandNode={() => true} 
                    style={theme === 'dark' ? darkStyles : defaultStyles} 
                  />
               ) : (
                  <div className="h-full flex items-center justify-center text-[var(--color-text-faint)] italic">
                     Select a log from the sidebar to inspect its JSON structure.
                  </div>
               )}
            </div>
         </div>
            </>
         )}
      </div>
    </div>
  );
};
