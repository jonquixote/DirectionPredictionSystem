import { useLiveStore } from '../../store/liveStore';
import { useSettingsStore } from '../../store/settingsStore';
import { Bell, Sun, Moon } from 'lucide-react';

export const TopBar = () => {
  const { connected } = useLiveStore();
  const { theme, setTheme, timezone, setTimezone } = useSettingsStore();

  const toggleTheme = () => setTheme(theme === 'dark' ? 'light' : 'dark');
  const toggleTz = () => setTimezone(timezone === 'UTC' ? 'local' : 'UTC');

  return (
    <header className="h-[60px] flex items-center justify-between px-4 border-b border-[var(--color-border)] bg-[var(--color-surface)] shadow-md z-10">
      <div className="flex items-center space-x-4">
        <div className="font-display font-bold text-[var(--color-primary)] text-xl tracking-tight leading-none px-2 py-1 rounded-sm bg-[var(--color-primary-highlight)]">
          dps ::
        </div>
        <h1 className="text-lg font-semibold tracking-wide">polymarket-ofi</h1>

        {/* Live Status Indicator */}
        <div className="flex items-center space-x-2 ml-6 bg-[var(--color-surface-offset)] px-3 py-1.5 rounded-full border border-[var(--color-border)]">
          {connected ? (
            <>
              <div className="w-2.5 h-2.5 rounded-full bg-[var(--color-primary)] animate-pulse shadow-[0_0_8px_var(--color-primary)]"></div>
              <span className="text-xs font-mono font-medium tracking-wider text-[var(--color-primary)]">LIVE</span>
            </>
          ) : (
            <>
              <div className="w-2.5 h-2.5 rounded-full bg-[var(--color-error)]"></div>
              <span className="text-xs font-mono font-medium tracking-wider text-[var(--color-error)]">OFFLINE</span>
            </>
          )}
        </div>
      </div>

      <div className="flex items-center space-x-3">
        <button className="relative p-2 rounded-full hover:bg-[var(--color-surface-offset)] transition-colors">
          <Bell className="w-5 h-5 text-[var(--color-text-muted)]" />
          <span className="absolute top-1 right-1 w-2.5 h-2.5 bg-[var(--color-error)] rounded-full border border-[var(--color-surface)]"></span>
        </button>

        <div className="h-6 w-px bg-[var(--color-divider)] mx-2"></div>

        <button 
          onClick={toggleTz}
          className="px-3 py-1.5 text-xs font-mono rounded bg-[var(--color-surface-offset)] border border-[var(--color-border)] hover:bg-[var(--color-surface-dynamic)] transition-colors w-20 text-center"
        >
          {timezone === 'UTC' ? 'UTC' : 'LOCAL'}
        </button>

        <button 
          onClick={toggleTheme}
          className="p-2 rounded-full hover:bg-[var(--color-surface-offset)] transition-colors text-[var(--color-text-muted)] hover:text-white"
        >
          {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
        </button>
      </div>
    </header>
  );
};
