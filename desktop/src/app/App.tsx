import React, { useState, useRef } from 'react';
import { AppShell } from './AppShell';
import type { ViewMode } from './navigation';
import { GeneratePage } from '../features/generate/GeneratePage';
import { BatchPage } from '../features/batch/BatchPage';
import { DiagnosticsPanel } from '../features/diagnostics/DiagnosticsPanel';

export const App: React.FC = () => {
  const [currentView, setCurrentView] = useState<ViewMode>('generate');
  const [isDiagnosticsOpen, setIsDiagnosticsOpen] = useState(false);
  const diagnosticsTriggerRef = useRef<HTMLButtonElement>(null);

  return (
    <>
      <AppShell
        currentView={currentView}
        onViewChange={setCurrentView}
        onOpenDiagnostics={() => setIsDiagnosticsOpen(true)}
        diagnosticsTriggerRef={diagnosticsTriggerRef}
      >
        {currentView === 'generate' && <GeneratePage />}
        {currentView === 'batch' && <BatchPage />}
      </AppShell>

      <DiagnosticsPanel
        isOpen={isDiagnosticsOpen}
        onClose={() => setIsDiagnosticsOpen(false)}
        triggerRef={diagnosticsTriggerRef}
      />
    </>
  );
};
