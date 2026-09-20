import React from 'react';
import { Layers, Play, AlertCircle, Loader2, Info } from 'lucide-react';
import type { UseBatchReturn } from './useBatch';
import { isBatchRunActive, getEligibleCount } from './batchState';
import { BatchSourceCard } from './BatchSourceCard';
import { BatchOperationCard } from './BatchOperationCard';
import { BatchOutputCard } from './BatchOutputCard';
import { BatchProgressCard } from './BatchProgressCard';
import { BatchResultCard } from './BatchResultCard';

interface BatchPageProps {
  batch: UseBatchReturn;
}

export const BatchPage: React.FC<BatchPageProps> = ({ batch }) => {
  const isRunning = isBatchRunActive(batch.snapshot.run?.state);
  const eligibleFilesCount = getEligibleCount(batch.snapshot.source, batch.selectedOperation);
  const exceedsMaxFiles = eligibleFilesCount > batch.maxFiles;
  const canStart =
    batch.isSubscribed &&
    batch.snapshot.nativeAvailable &&
    !isRunning &&
    !batch.isSubmitting &&
    batch.snapshot.source !== null &&
    batch.snapshot.output !== null &&
    batch.selectedFormats.length > 0 &&
    eligibleFilesCount > 0 &&
    !exceedsMaxFiles &&
    batch.snapshot.run?.cleanup !== 'incomplete';

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-1 border-b border-slate-200 pb-5">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700">
            <Layers className="h-3 w-3" aria-hidden="true" />
            Batch Workspace
          </span>
        </div>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">
          Batch Operations & Export
        </h1>
        <p className="text-sm text-slate-500">
          Sequential multi-file CAD processing, format translations, and drawing publication.
        </p>
      </div>

      {/* Native Unavailable Banner */}
      {!batch.snapshot.nativeAvailable && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-900 flex items-start gap-3">
          <AlertCircle className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Desktop Native Bridge Unavailable</p>
            <p className="mt-0.5 text-amber-800 leading-relaxed">
              Batch conversions and file pickers require the native desktop application runtime.
              Configuration and format options are visible in preview mode, while execution remains
              disabled.
            </p>
          </div>
        </div>
      )}

      {/* Incomplete Cleanup Alert */}
      {batch.snapshot.run?.cleanup === 'incomplete' && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs text-rose-900 flex items-start gap-3">
          <AlertCircle className="h-4 w-4 text-rose-600 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Previous Run Cleanup Incomplete</p>
            <p className="mt-0.5 text-rose-800 leading-relaxed">
              The prior batch engine process required force termination or could not cleanly release
              CAD locks. New runs are blocked until the application is restarted.
            </p>
          </div>
        </div>
      )}

      {/* Action Error Banner */}
      {batch.actionError && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs text-rose-900 flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-4 w-4 text-rose-600 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold">
                Error: {batch.actionError.code}
                {batch.actionError.field ? ` (${batch.actionError.field})` : ''}
              </p>
              <p className="mt-0.5 text-rose-800">{batch.actionError.message}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {batch.actionError.code === 'EVENT_SUBSCRIPTION_FAILED' && (
              <button
                type="button"
                onClick={batch.retrySubscription}
                className="text-xs font-semibold text-rose-700 hover:text-rose-900 underline"
              >
                Retry Connection
              </button>
            )}
            <button
              type="button"
              onClick={batch.clearActionError}
              className="text-xs font-semibold text-rose-700 hover:text-rose-900"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Progress Card (Active in-flight run) */}
      {isRunning && batch.snapshot.run && (
        <BatchProgressCard run={batch.snapshot.run} onCancel={batch.cancelRun} />
      )}

      {/* Results Card (Terminal run result matching current terminal run) */}
      {batch.activeResult &&
        batch.snapshot.run?.state === 'terminal' &&
        batch.activeResult.requestId === batch.snapshot.run.requestId && (
          <BatchResultCard
            result={batch.activeResult}
            onRevealOutput={() => batch.revealOutput(batch.activeResult!.requestId)}
          />
        )}

      {/* Terminal Outcome without Artifact Results (Early failure or cancellation before results) */}
      {(!batch.activeResult || batch.activeResult.requestId !== batch.snapshot.run?.requestId) &&
        batch.snapshot.run?.state === 'terminal' && (
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
              <div className="flex items-center gap-2">
                <AlertCircle className="h-5 w-5 text-amber-500" aria-hidden="true" />
                <div>
                  <h2 className="text-sm font-bold text-slate-900">Batch Execution Terminated</h2>
                  <p className="text-xs text-slate-500">
                    Request ID:{' '}
                    <span className="font-mono text-slate-700">{batch.snapshot.run.requestId}</span>
                  </p>
                </div>
              </div>
              <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold text-slate-700 uppercase">
                {batch.snapshot.run.engineStatus ?? 'TERMINATED'}
              </span>
            </div>

            <div className="text-xs text-slate-600 space-y-1">
              <p className="font-semibold text-slate-800">Outcome Details:</p>
              <p className="leading-relaxed">
                {batch.snapshot.run.reason ??
                  (batch.snapshot.run.engineStatus === 'cancelled'
                    ? 'The batch run was cancelled before file processing began or produced artifacts.'
                    : 'The batch operation terminated before producing verifiable output artifacts.')}
              </p>
            </div>
          </div>
        )}

      {/* Event Subscription Disconnected Alert (when error banner dismissed or not showing) */}
      {!batch.isSubscribed &&
        batch.snapshot.nativeAvailable &&
        (!batch.actionError || batch.actionError.code !== 'EVENT_SUBSCRIPTION_FAILED') && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-900 flex items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <AlertCircle className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">Event Subscription Disconnected</p>
                <p className="mt-0.5 text-amber-800 leading-relaxed">
                  Native batch state updates are not connected. Reconnect to enable batch execution.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={batch.retrySubscription}
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-800 shadow-sm hover:bg-amber-100 transition-colors shrink-0"
            >
              Retry Connection
            </button>
          </div>
        )}

      {/* Configuration Cards Grid */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left column: Source, Output & Start */}
        <div className="space-y-6 lg:col-span-7">
          <BatchSourceCard
            source={batch.snapshot.source}
            sourceFiles={batch.sourceFiles}
            disabled={isRunning || batch.isSubmitting}
            onSelectSource={batch.selectSource}
          />

          <BatchOutputCard
            output={batch.snapshot.output}
            disabled={isRunning || batch.isSubmitting}
            onSelectOutput={batch.selectOutput}
          />

          {/* Start Run Action Button */}
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm flex flex-col sm:flex-row items-center justify-between gap-4">
            <div>
              <p className="text-xs font-bold text-slate-800">Ready to Process</p>
              <p
                className={`text-xs mt-0.5 ${
                  !batch.isSubscribed && batch.snapshot.nativeAvailable
                    ? 'text-amber-700 font-medium'
                    : exceedsMaxFiles
                      ? 'text-rose-600 font-medium'
                      : 'text-slate-500'
                }`}
              >
                {!batch.isSubscribed && batch.snapshot.nativeAvailable
                  ? 'Event subscription disconnected. Reconnect to enable batch execution.'
                  : exceedsMaxFiles
                    ? `Eligible file count (${eligibleFilesCount}) exceeds maximum files limit (${batch.maxFiles}). Adjust the max files cap or select fewer files.`
                    : eligibleFilesCount > 0
                      ? `${eligibleFilesCount} files ready for ${batch.selectedFormats.length} format export(s).`
                      : 'Select source files and export formats to begin.'}
              </p>
              {!batch.isSubscribed && batch.snapshot.nativeAvailable && (
                <button
                  type="button"
                  onClick={batch.retrySubscription}
                  className="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-800 underline"
                >
                  Retry Connection
                </button>
              )}
            </div>

            <button
              type="button"
              disabled={!canStart}
              onClick={batch.startRun}
              className="w-full sm:w-auto inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-xs font-bold text-white shadow-sm hover:bg-blue-700 disabled:bg-slate-200 disabled:text-slate-400 disabled:cursor-not-allowed transition-colors"
            >
              {batch.isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <Play className="h-4 w-4 fill-current" />
                  Start Batch Run
                </>
              )}
            </button>
          </div>
        </div>

        {/* Right column: Operation/Formats & Architecture info */}
        <div className="space-y-6 lg:col-span-5">
          <BatchOperationCard
            source={batch.snapshot.source}
            selectedOperation={batch.selectedOperation}
            onSelectOperation={batch.setOperation}
            selectedFormats={batch.selectedFormats}
            onToggleFormat={batch.toggleFormat}
            continueOnError={batch.continueOnError}
            onSetContinueOnError={batch.setContinueOnError}
            maxFiles={batch.maxFiles}
            onSetMaxFiles={batch.setMaxFiles}
            disabled={isRunning || batch.isSubmitting}
          />

          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-start gap-3">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" aria-hidden="true" />
              <div className="space-y-1 text-xs text-slate-600">
                <p className="font-semibold text-slate-800">Local Processing Architecture</p>
                <p>
                  Batch conversion operates sequentially through local Siemens Solid Edge automation
                  with source file integrity protection and isolated artifact outputs.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
