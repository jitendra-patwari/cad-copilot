import React from 'react';
import { Layers, Play, AlertCircle, Loader2 } from 'lucide-react';
import type { UseBatchReturn } from './useBatch';
import { isBatchRunActive, getEligibleCount } from './batchState';
import { BatchSourceCard } from './BatchSourceCard';
import { BatchOperationCard } from './BatchOperationCard';
import { BatchOutputCard } from './BatchOutputCard';
import { BatchProgressCard } from './BatchProgressCard';
import { BatchResultCard } from './BatchResultCard';

interface BatchPageProps {
  batch: UseBatchReturn;
  isOtherBusy?: boolean;
}

export const BatchPage: React.FC<BatchPageProps> = ({ batch, isOtherBusy = false }) => {
  const [showConfigAfterResult, setShowConfigAfterResult] = React.useState(false);
  const isRunning = isBatchRunActive(batch.snapshot.run?.state);
  const eligibleFilesCount = getEligibleCount(batch.snapshot.source, batch.selectedOperation);
  const exceedsMaxFiles = eligibleFilesCount > batch.maxFiles;
  const canStart =
    batch.isSubscribed &&
    batch.snapshot.nativeAvailable &&
    !isRunning &&
    !batch.isSubmitting &&
    !batch.isSelectionPending &&
    !isOtherBusy &&
    batch.snapshot.source !== null &&
    batch.snapshot.output !== null &&
    batch.selectedFormats.length > 0 &&
    eligibleFilesCount > 0 &&
    !exceedsMaxFiles &&
    batch.snapshot.run?.cleanup !== 'incomplete';

  const hasResult =
    batch.activeResult !== null &&
    batch.snapshot.run?.state === 'terminal' &&
    batch.activeResult.requestId === batch.snapshot.run.requestId;
  const isEarlyTerminal =
    batch.snapshot.run?.cleanup !== 'incomplete' &&
    (!batch.activeResult || batch.activeResult.requestId !== batch.snapshot.run?.requestId) &&
    batch.snapshot.run?.state === 'terminal';

  // When a batch run starts, reset configuration view so results appear automatically upon completion
  React.useEffect(() => {
    if (isRunning) {
      setShowConfigAfterResult(false);
    }
  }, [isRunning]);

  const handleStartRun = () => {
    setShowConfigAfterResult(false);
    batch.startRun();
  };

  let statusAnnouncement = '';
  if (isRunning) {
    statusAnnouncement = `Batch operation in progress: ${batch.snapshot.run?.completedFiles ?? 0} of ${batch.snapshot.run?.totalFiles ?? 0} files processed.`;
  } else if (hasResult && batch.activeResult) {
    const accepted = batch.activeResult.summary.accepted;
    const needsAttention = batch.activeResult.summary.total - accepted;
    statusAnnouncement = `Batch operation finished: ${accepted} accepted, ${needsAttention} need attention.`;
  } else if (batch.snapshot.run?.state === 'terminal') {
    const run = batch.snapshot.run;
    if (run.engineStatus === 'cancelled') {
      statusAnnouncement = 'Batch operation was cancelled.';
    } else if (run.engineStatus === 'failed') {
      statusAnnouncement = `Batch operation failed: ${run.reason ?? 'Internal engine error.'}`;
    }
  }

  return (
    <div className="space-y-6">
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {statusAnnouncement}
      </div>

      {/* Header Panel */}
      <div className="flex items-center justify-between border-b border-slate-200 pb-5">
        <div className="flex items-center gap-3">
          <span className="p-2 bg-blue-500/10 text-blue-600 rounded-xl">
            <Layers className="h-5 w-5" aria-hidden="true" />
          </span>
          <h1 className="text-2xl font-bold tracking-tight text-slate-800">
            Batch Operations & Export
          </h1>
        </div>
      </div>

      {/* Native Unavailable Banner */}
      {!batch.snapshot.nativeAvailable && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3.5 text-xs text-amber-900 flex items-start gap-3">
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
        <div
          role="alert"
          className="rounded-xl border border-rose-200 bg-rose-50 p-3.5 text-xs text-rose-900 flex items-start gap-3"
        >
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
        <div
          role="alert"
          className="rounded-xl border border-rose-200 bg-rose-50 p-3.5 text-xs text-rose-900 flex items-start justify-between gap-3"
        >
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

      {/* Side-by-Side Batch Workspace Grid */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left Column: Batch Specification & Controls */}
        <div className="space-y-6 lg:col-span-6">
          <section
            aria-label="Batch Controls"
            className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
          >
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-700 pb-4 border-b border-slate-100">
              Batch Specification
            </h2>
            <div className="mt-4 space-y-5">
              <BatchSourceCard
                source={batch.snapshot.source}
                sourceFiles={batch.sourceFiles}
                disabled={isRunning || batch.isSubmitting || batch.isSelectionPending}
                onSelectSource={batch.selectSource}
                initialExpanded={true}
                embedded={true}
              />

              <BatchOutputCard
                output={batch.snapshot.output}
                disabled={isRunning || batch.isSubmitting || batch.isSelectionPending}
                onSelectOutput={batch.selectOutput}
              />

              {/* Action & Status Row */}
              {(!batch.isSubscribed && batch.snapshot.nativeAvailable) || exceedsMaxFiles ? (
                <div
                  className={`rounded-lg p-3 text-xs ${
                    !batch.isSubscribed && batch.snapshot.nativeAvailable
                      ? 'border border-amber-200 bg-amber-50 text-amber-800'
                      : 'border border-rose-200 bg-rose-50 text-rose-800'
                  }`}
                >
                  {!batch.isSubscribed && batch.snapshot.nativeAvailable ? (
                    <div className="flex items-center justify-between gap-2">
                      <span>
                        Event subscription disconnected. Reconnect to enable batch execution.
                      </span>
                      <button
                        type="button"
                        onClick={batch.retrySubscription}
                        className="font-semibold text-amber-900 hover:underline cursor-pointer"
                      >
                        Reconnect
                      </button>
                    </div>
                  ) : (
                    <p className="font-medium">
                      Eligible file count ({eligibleFilesCount}) exceeds maximum files limit (
                      {batch.maxFiles}). Adjust the max files cap or select fewer files.
                    </p>
                  )}
                </div>
              ) : null}

              {/* Submit Action */}
              <div className="flex flex-col sm:flex-row items-end sm:items-center justify-between gap-3 pt-1">
                {isOtherBusy ? (
                  <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                    A CAD model generation is currently running. Batch processing is available once
                    it finishes.
                  </p>
                ) : eligibleFilesCount > 0 && !exceedsMaxFiles ? (
                  <span className="text-xs text-slate-500">
                    {eligibleFilesCount} files ready for {batch.selectedFormats.length} format
                    export(s).
                  </span>
                ) : (
                  <div />
                )}

                <button
                  type="button"
                  disabled={!canStart}
                  onClick={handleStartRun}
                  className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 transition-all cursor-pointer shrink-0"
                >
                  {batch.isSubmitting ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Starting...
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4 fill-white" />
                      Start Batch Run
                    </>
                  )}
                </button>
              </div>
            </div>
          </section>
        </div>

        {/* Right Column: Execution Configuration OR Progress OR Results */}
        <div className="space-y-6 lg:col-span-6">
          {isRunning && batch.snapshot.run ? (
            <BatchProgressCard run={batch.snapshot.run} onCancel={batch.cancelRun} />
          ) : hasResult && !showConfigAfterResult ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  Run Completed
                </span>
                <button
                  type="button"
                  onClick={() => setShowConfigAfterResult(true)}
                  className="cursor-pointer text-xs font-semibold text-blue-600 hover:text-blue-800 underline"
                >
                  Configure New Run
                </button>
              </div>
              <BatchResultCard
                result={batch.activeResult!}
                onRevealOutput={() => batch.revealOutput(batch.activeResult!.requestId)}
              />
            </div>
          ) : isEarlyTerminal && batch.snapshot.run && !showConfigAfterResult ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  Run Terminated
                </span>
                <button
                  type="button"
                  onClick={() => setShowConfigAfterResult(true)}
                  className="cursor-pointer text-xs font-semibold text-blue-600 hover:text-blue-800 underline"
                >
                  Configure Batch
                </button>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
                  <div className="flex items-center gap-2">
                    <AlertCircle className="h-5 w-5 text-amber-500" aria-hidden="true" />
                    <div>
                      <h2 className="text-sm font-bold text-slate-900">
                        Batch Execution Terminated
                      </h2>
                      <p className="text-xs text-slate-500">
                        Request ID:{' '}
                        <span className="font-mono text-slate-700">
                          {batch.snapshot.run.requestId}
                        </span>
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
            </div>
          ) : (
            <div className="space-y-4">
              {(hasResult || isEarlyTerminal) && (
                <div className="flex items-center justify-between border-b border-slate-200 pb-2">
                  <span className="text-xs font-semibold text-slate-700">Batch Configuration</span>
                  <button
                    type="button"
                    onClick={() => setShowConfigAfterResult(false)}
                    className="cursor-pointer text-xs font-semibold text-blue-600 hover:text-blue-800 underline"
                  >
                    Back to Results
                  </button>
                </div>
              )}

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
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
