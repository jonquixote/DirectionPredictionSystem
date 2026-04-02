import { NavLink } from 'react-router-dom';
import { Activity, LayoutDashboard, History, Tally3, Library, Terminal, ActivitySquare, BadgeAlert, Layers, TableProperties, Calculator } from 'lucide-react';


interface NavItem {
  id: string;
  label: string;
  icon: React.FC<any>;
  path: string;
}

const navItems: NavItem[] = [
  { id: '1', label: 'Live Status', icon: Activity, path: '/status' },
  { id: '2', label: 'Predictions Explorer', icon: TableProperties, path: '/predictions' },
  { id: '3', label: 'Trade Explorer', icon: History, path: '/trades' },
  { id: '4', label: 'Performance Dashboard', icon: LayoutDashboard, path: '/performance' },
  { id: '5', label: 'Models Comp', icon: Layers, path: '/models' },
  { id: '6', label: 'p_market EV', icon: ActivitySquare, path: '/pmarket' },
  { id: '7', label: 'Price Chart', icon: Tally3, path: '/price' },
  { id: '8', label: 'Features', icon: Library, path: '/features' },
  { id: '11', label: 'Alerts', icon: BadgeAlert, path: '/alerts' },
  { id: '12', label: 'Raw Logs', icon: Terminal, path: '/logs' },
  { id: '13', label: 'Stats Calculator', icon: Calculator, path: '/stats' }
];

export const SideBar = ({ className }: { className?: string }) => {

  // Gate summary placeholders
  const gates = [
    { model: 'H300', status: 'LIVE', pass: true, pct: '53.2%' },
    { model: 'H60V3', status: 'WARMUP', pass: false, pct: '48.0%' }
  ];

  return (
    <aside className={`flex-col bg-[var(--color-surface)] z-10 ${className}`}>
      <nav className="flex-1 py-4 overflow-y-auto w-full px-3">
        <ul className="space-y-1 w-full">
          {navItems.map((item) => (
            <li key={item.id}>
              <NavLink 
                to={item.path}
                className={({ isActive }) => `
                  w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-200
                  ${isActive 
                    ? 'bg-[var(--color-primary-highlight)] text-[var(--color-primary)] font-medium border border-[var(--color-primary)]/20 shadow-sm' 
                    : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-offset)] hover:text-[var(--color-text)]'
                  }
                `}
              >
                <item.icon className="w-4 h-4 flex-shrink-0" />
                <span className="truncate">{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <div className="mt-auto p-4 border-t border-[var(--color-border)] bg-[var(--color-surface-offset)]">
        <div className="space-y-3">
          {gates.map(g => (
            <div key={g.model} className="text-xs">
              <div className="flex justify-between items-center mb-1">
                <span className="font-mono text-[var(--color-text)]">{g.model}</span>
                <div className="flex items-center space-x-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${g.status === 'LIVE' ? 'bg-[var(--color-primary)]' : 'bg-[var(--color-warning)]'}`}></span>
                  <span className={`font-mono ${g.status === 'LIVE' ? 'text-[var(--color-primary)]' : 'text-[var(--color-warning)]'}`}>
                    {g.status}
                  </span>
                </div>
              </div>
              <div className="flex justify-between text-[var(--color-text-muted)]">
                <span>Gate: {g.pass ? '✅' : '❌'} {g.pct}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
};
