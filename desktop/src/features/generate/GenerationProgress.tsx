import React from 'react';
import { Loader2, CheckCircle2, Circle, XCircle, AlertTriangle } from 'lucide-react';
import type { GenerationRunSnapshot } from './types';
import { GENERATION_PHASES } from './generationState';

interface GenerationProgressProps {
  run: GenerationRunSnapshot;
  cancelRun: () => Promise<void>;
}

export const GenerationProgress: React.FC<GenerationProgressProps> = ({ run, cancelRun }) => {
  const isCancelling = run.state === 'cancelling';
  const currentPhaseIndex = GENERATION_PHASES.findIndex((p) => p.phase === run.phase);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
      <div className="flex items-center justify-between border-b border-slate-100 pb-3">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75"></span>
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-blue-600"></span>
            </span>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-700">
              Active Generation Run
            </h2>
          </div>
          <p className="font-mono text-[11px] text-slate-500">
            Request ID: <span className="text-slate-800">{run.requestId}</span>
          </p>
        </div>

        <button
          type="button"
          onClick={cancelRun}
          disabled={isCancelling}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-rose-600 shadow-sm hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-500 disabled:opacity-50"
        >
          <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
          {isCancelling ? 'Cancelling...' : 'Cancel Generation'}
        </button>
      </div>

      {isCancelling && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
            <div>
              <p className="font-semibold">Cancellation In Progress</p>
              <p className="mt-0.5 leading-relaxed text-amber-700">
                A cancellation signal has been delivered. Waiting for Siemens Solid Edge and Python
                worker teardown routines to cleanly release document locks.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* 4-Phase Progress Timeline */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-4 sm:gap-3 pt-1">
        {GENERATION_PHASES.map((step, index) => {
          const isPassed = currentPhaseIndex > index;
          const isCurrent = currentPhaseIndex === index;

          return (
            <div
              key={step.phase}
              className={`rounded-lg border p-3 transition-colors ${
                isCurrent
                  ? 'border-blue-300 bg-blue-50/50 shadow-xs'
                  : isPassed
                    ? 'border-emerald-200 bg-emerald-50/30'
                    : 'border-slate-200 bg-slate-50/40 text-slate-400'
              }`}
            >
              <div className="flex items-center gap-2">
                {isCurrent ? (
                  <Loader2 className="h-4 w-4 animate-spin text-blue-600" aria-hidden="true" />
                ) : isPassed ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
                ) : (
                  <Circle className="h-4 w-4 text-slate-300" aria-hidden="true" />
                )}
                <span
                  className={`text-xs font-semibold ${
                    isCurrent ? 'text-blue-900' : isPassed ? 'text-emerald-900' : 'text-slate-500'
                  }`}
                >
                  {step.label}
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-tight text-slate-500">{step.description}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
};
