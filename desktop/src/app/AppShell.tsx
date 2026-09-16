import React, { useState, type ReactNode } from 'react';
import { Sparkles, Layers, Settings, PanelLeft } from 'lucide-react';
import { NAV_ITEMS, type ViewMode } from './navigation';
import logoUrl from '../assets/logo.png';

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
  const [isCollapsed, setIsCollapsed] = useState(false);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-50 text-slate-900 antialiased">
      <aside
        aria-label="Sidebar"
        className={`flex h-full shrink-0 flex-col border-r border-slate-200 bg-slate-100 transition-all duration-200 ${
          isCollapsed ? 'w-16' : 'w-72'
        }`}
      >
        {/* Sidebar Header with Brand & Collapse Toggle */}
        <header
          className={`flex items-center border-b border-slate-200 bg-slate-50 p-3 ${
            isCollapsed ? 'justify-center' : 'justify-between'
          }`}
        >
          {!isCollapsed && (
            <div className="flex items-center gap-2 pl-1.5">
              <img src={logoUrl} alt="" className="h-7 w-7 object-contain" aria-hidden="true" />
              <span className="text-lg font-bold tracking-tight text-slate-800">CAD Copilot</span>
            </div>
          )}
          <button
            type="button"
            onClick={() => setIsCollapsed(!isCollapsed)}
            aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="cursor-pointer rounded-md p-1.5 text-slate-500 transition-colors hover:bg-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            <PanelLeft className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>

        {/* Primary Navigation */}
        <nav aria-label="Primary Navigation" className="p-3 space-y-1">
          {NAV_ITEMS.map((item) => {
            const isActive = currentView === item.id;
            const Icon = item.id === 'generate' ? Sparkles : Layers;

            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onViewChange(item.id)}
                aria-current={isActive ? 'page' : undefined}
                aria-label={item.label}
                title={item.label}
                className={`flex w-full cursor-pointer items-center gap-3 rounded-xl py-2.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                  isCollapsed ? 'justify-center px-2' : 'px-3'
                } ${
                  isActive
                    ? 'bg-blue-100 font-semibold text-blue-700'
                    : 'text-slate-600 hover:bg-slate-200'
                }`}
              >
                <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
                {!isCollapsed && <span>{item.label}</span>}
              </button>
            );
          })}
        </nav>

        {/* Sidebar Footer: Diagnostics/Settings Trigger */}
        <div className="mt-auto border-t border-slate-200 p-3">
          <button
            ref={diagnosticsTriggerRef}
            type="button"
            onClick={onOpenDiagnostics}
            aria-label="Open settings and diagnostics"
            title="Settings & Diagnostics"
            className={`flex w-full cursor-pointer items-center gap-2 rounded-xl bg-slate-200 py-2 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
              isCollapsed ? 'justify-center px-2' : 'justify-start px-3'
            }`}
          >
            <Settings className="h-4 w-4 shrink-0" aria-hidden="true" />
            {!isCollapsed && <span>Settings</span>}
          </button>
        </div>
      </aside>

      {/* Main Content Area */}
      <main
        id="main-content"
        tabIndex={-1}
        className="flex-1 overflow-y-auto bg-slate-50 p-6 lg:p-8 outline-none"
      >
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
    </div>
  );
};
