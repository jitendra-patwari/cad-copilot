import React from 'react';
import { Loader2, Ban, Activity } from 'lucide-react';
import type { BatchRunSnapshot } from './types';
import { formatPhase } from './batchState';

interface BatchProgressCardProps {
  run: BatchRunSnapshot;
  onCancel: () => void;
}

export const BatchProgressCard: React.FC<BatchProgressCardProps> = ({ run, onCancel }) => {
  const isCancelling = run.state === 'cancelling';
  const percent =
    run.totalFiles > 0 ? Math.min(100, Math.round((run.completedFiles / run.totalFiles) * 100)) : 0;

  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50/40 p-6 shadow-sm space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-blue-200 pb-3">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-blue-600 animate-pulse" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-slate-900">Batch Execution in Progress</h2>
          <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-semibold text-blue-800">
            {formatPhase(run.phase)}
          </span>
        </div>

        <button
          type="button"
          disabled={isCancelling}
          onClick={onCancel}
          className="inline-flex items-center gap-1.5 rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 shadow-sm hover:bg-rose-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isCancelling ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin text-rose-600" aria-hidden="true" />
              Cancelling...
            </>
          ) : (
            <>
              <Ban className="h-3.5 w-3.5 text-rose-600" aria-hidden="true" />
              Cancel Batch
            </>
          )}
        </button>
      </div>

      {/* Progress Bar */}
      <div className="space-y-1.5">
        <div className="flex justify-between text-xs font-medium text-slate-700">
          <span>
            Processed: {run.completedFiles} of {run.totalFiles} files
          </span>
          <span>{percent}%</span>
        </div>
        <div
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Batch execution progress"
          className="h-2.5 w-full overflow-hidden rounded-full bg-blue-100"
        >
          <div
            className="h-full rounded-full bg-blue-600 transition-all duration-300"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>

      {/* Current File & Current Format */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div className="rounded-lg bg-white p-3 border border-blue-100 space-y-1">
          <span className="text-[11px] font-semibold text-slate-500">Current File:</span>
          <p
            className="truncate font-mono text-xs text-slate-800"
            title={run.currentFile ?? 'Initializing...'}
          >
            {run.currentFile ?? 'Initializing...'}
          </p>
        </div>
        <div className="rounded-lg bg-white p-3 border border-blue-100 space-y-1">
          <span className="text-[11px] font-semibold text-slate-500">Active Format:</span>
          <p className="font-mono text-xs uppercase text-slate-800">
            {run.currentFormat ? run.currentFormat.toUpperCase() : 'Pending...'}
          </p>
        </div>
      </div>
    </div>
  );
};
