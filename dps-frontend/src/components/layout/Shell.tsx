import type { ReactNode } from 'react';
import { TopBar } from './TopBar';
import { SideBar } from './SideBar';

export const Shell = ({ children }: { children: ReactNode }) => {
  return (
    <div className="flex flex-col h-screen overflow-hidden bg-[var(--color-bg)] text-[var(--color-text)]">
      {/* 60px Topbar */}
      <TopBar />

      <div className="flex flex-1 overflow-hidden">
        {/* 240px Sidebar */}
        <SideBar className="hidden md:flex w-60 border-r border-[var(--color-border)]" />

        {/* Main Content Area (Scrollable) */}
        <main className="flex-1 overflow-y-auto relative">
          <div className="mx-auto min-h-full">
            {children}
          </div>
        </main>
      </div>

      {/* Bottom Nav for mobile (optional placeholder for later) */}
      <div className="md:hidden border-t border-[var(--color-border)] h-14 flex items-center justify-between px-4">
         <span>Mobile Nav</span>
      </div>
    </div>
  );
};
