import React, { type ReactNode } from 'react';
import { Sparkles, Layers, SlidersHorizontal } from 'lucide-react';
import { NAV_ITEMS, type ViewMode } from './navigation';
import appIconUrl from '../assets/app-icon.svg';

export interface AppShellProps {
  readonly currentView: ViewMode;
  readonly onViewChange: (view: ViewMode) => void;
  readonly onOpenDiagnostics: () => void;
  readonly diagnosticsTriggerRef: React.RefObject<HTMLButtonElement | null>;
  readonly children: ReactNode;
}

export const AppShell: React.FC<AppShellProps> = ({
  currentView,
  onViewChange,
  onOpenDiagnostics,
  diagnosticsTriggerRef,
  children,
}) => {
  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100 antialiased">
      {/* Primary Header */}
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-slate-800 bg-slate-950/80 px-6 backdrop-blur-md">
        <div className="flex items-center gap-6">
          {/* Brand */}
          <div className="flex items-center gap-2.5">
            <img src={appIconUrl} alt="" className="h-7 w-7 rounded-lg" aria-hidden="true" />
            <div className="flex items-baseline gap-2">
              <span className="text-base font-bold tracking-tight text-white">CAD Copilot</span>
              <span className="rounded bg-slate-800/80 px-1.5 py-0.5 text-[10px] font-semibold text-slate-400">
                Desktop
              </span>
            </div>
          </div>

          {/* Primary Navigation */}
          <nav aria-label="Primary Navigation" className="flex items-center gap-1">
            {NAV_ITEMS.map((item) => {
              const isActive = currentView === item.id;
              const Icon = item.id === 'generate' ? Sparkles : Layers;

              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onViewChange(item.id)}
                  aria-current={isActive ? 'page' : undefined}
                  className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${
                    isActive
                      ? 'bg-slate-800 text-white ring-1 ring-slate-700 shadow-sm'
                      : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
                  }`}
                >
                  <Icon
                    className={`h-3.5 w-3.5 ${isActive ? 'text-sky-400' : 'text-slate-500'}`}
                    aria-hidden="true"
                  />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </nav>
        </div>

        {/* Header Right: Settings/Diagnostics Trigger */}
        <div className="flex items-center gap-2">
          <button
            ref={diagnosticsTriggerRef}
            type="button"
            onClick={onOpenDiagnostics}
            aria-label="Open settings and diagnostics"
            title="System & Settings Diagnostics"
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-slate-800 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
          >
            <SlidersHorizontal className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
            <span className="hidden sm:inline">Diagnostics</span>
          </button>
        </div>
      </header>

      {/* Main Content Area */}
      <main id="main-content" tabIndex={-1} className="flex-1 px-6 py-6 outline-none">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
};
