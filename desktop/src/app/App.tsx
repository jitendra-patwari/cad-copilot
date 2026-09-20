import React, { useState, useEffect, useRef } from 'react';
import { AlertTriangle } from 'lucide-react';
import { AppShell } from './AppShell';
import type { ViewMode } from './navigation';
import { GeneratePage } from '../features/generate/GeneratePage';
import { BatchPage } from '../features/batch/BatchPage';
import { DiagnosticsPanel } from '../features/diagnostics/DiagnosticsPanel';
import { useGeneration } from '../features/generate/useGeneration';
import { useBatch } from '../features/batch/useBatch';
import { isRunActive } from '../features/generate/generationState';
import { isBatchRunActive } from '../features/batch/batchState';

export const App: React.FC = () => {
  const [currentView, setCurrentView] = useState<ViewMode>('generate');
  const [isDiagnosticsOpen, setIsDiagnosticsOpen] = useState(false);
  const diagnosticsTriggerRef = useRef<HTMLButtonElement>(null);

  // Long-lived generation & batch controllers hoisted above view navigation
  const generation = useGeneration();
  const batch = useBatch();

  const handleViewChange = (view: 'generate' | 'batch') => {
    if (view !== 'generate') {
      generation.clearKeyDraft();
    }
    setCurrentView(view);
  };

  const stayButtonRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const prevActiveElementRef = useRef<Element | null>(null);

  const isGenClose =
    !!generation.snapshot.run?.closeRequested && isRunActive(generation.snapshot.run?.state);
  const isBatchClose =
    !!batch.snapshot.run?.closeRequested && isBatchRunActive(batch.snapshot.run?.state);
  const closeRequested = isGenClose || isBatchClose;

  const resolveCloseRef = useRef<(decision: 'stay' | 'cancel_and_close') => void>(() => {});

  useEffect(() => {
    resolveCloseRef.current = (decision) => {
      if (isBatchClose) {
        batch.resolveClose(decision);
      } else if (isGenClose) {
        generation.resolveClose(decision);
      }
    };
  }, [isBatchClose, isGenClose, batch, generation]);

  useEffect(() => {
    if (!closeRequested) return;

    setIsDiagnosticsOpen(false);
    prevActiveElementRef.current = document.activeElement;
    const timer = setTimeout(() => {
      stayButtonRef.current?.focus();
    }, 20);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        resolveCloseRef.current('stay');
        return;
      }

      if (e.key === 'Tab' && modalRef.current) {
        const focusable = modalRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('keydown', handleKeyDown);
      if (prevActiveElementRef.current instanceof HTMLElement) {
        prevActiveElementRef.current.focus();
      }
    };
  }, [closeRequested]);

  return (
    <>
      <AppShell
        currentView={currentView}
        onViewChange={handleViewChange}
        onOpenDiagnostics={() => setIsDiagnosticsOpen(true)}
        diagnosticsTriggerRef={diagnosticsTriggerRef}
      >
        {currentView === 'generate' && <GeneratePage generation={generation} />}
        {currentView === 'batch' && <BatchPage batch={batch} />}
      </AppShell>

      <DiagnosticsPanel
        isOpen={isDiagnosticsOpen}
        onClose={() => setIsDiagnosticsOpen(false)}
        triggerRef={diagnosticsTriggerRef}
        outputDisplayPath={
          currentView === 'batch'
            ? batch.snapshot.output?.displayPath
            : generation.snapshot.output?.displayPath
        }
        keyConfigured={generation.snapshot.keyConfigured}
        lastRunCadBuild={
          currentView === 'generate'
            ? generation.activeResult?.manifestSummary?.cadRuntimeVersion
            : null
        }
        pythonEngineConnected={
          currentView === 'batch'
            ? batch.activeResult !== null ||
              (batch.snapshot.run !== null &&
                (batch.snapshot.run.phase !== null ||
                  batch.snapshot.run.manifestState === 'validated'))
            : generation.activeResult !== null ||
              (generation.snapshot.run !== null &&
                (generation.snapshot.run.phase !== null ||
                  generation.snapshot.run.engineStatus !== null))
        }
      />

      {/* Accessible Close Confirmation Modal */}
      {closeRequested && (
        <div
          ref={modalRef}
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="close-modal-title"
        >
          <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-amber-50 text-amber-600 border border-amber-200">
                <AlertTriangle className="h-5 w-5" aria-hidden="true" />
              </div>
              <div>
                <h2 id="close-modal-title" className="text-sm font-bold text-slate-900">
                  {isBatchClose ? 'Batch Operation In Progress' : 'Generation In Progress'}
                </h2>
                <p className="text-xs text-slate-500">
                  {isBatchClose
                    ? 'A batch CAD conversion operation is currently running in Siemens Solid Edge.'
                    : 'A CAD model is currently being generated in Siemens Solid Edge.'}
                </p>
              </div>
            </div>

            <p className="text-xs leading-relaxed text-slate-600">
              {isBatchClose
                ? 'Closing CAD Copilot now will deliver a cancellation signal to the batch process and cleanly release CAD session locks. Do you want to cancel the batch operation and close, or keep running?'
                : 'Closing CAD Copilot now will deliver a cancellation signal to the engine process and cleanly release CAD session locks. Do you want to cancel the generation and close, or keep running?'}
            </p>

            <div className="flex justify-end gap-2 pt-2">
              <button
                ref={stayButtonRef}
                type="button"
                onClick={() => resolveCloseRef.current('stay')}
                className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                Keep Running
              </button>
              <button
                type="button"
                onClick={() => resolveCloseRef.current('cancel_and_close')}
                className="rounded-lg bg-rose-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-rose-700"
              >
                Cancel & Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
