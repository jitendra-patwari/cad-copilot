import React from 'react';
import { Sparkles, Box } from 'lucide-react';
import type { UseGenerationReturn } from './useGeneration';
import { GenerateForm } from './GenerateForm';
import { GenerationProgress } from './GenerationProgress';
import { GenerationResult } from './GenerationResult';
import { isRunActive, isTerminalState } from './generationState';

export interface GeneratePageProps {
  generation: UseGenerationReturn;
  isOtherBusy?: boolean;
}

export const GeneratePage: React.FC<GeneratePageProps> = ({ generation, isOtherBusy = false }) => {
  const {
    snapshot,
    mode,
    setMode,
    prompt,
    setPrompt,
    isKeyEditorOpen,
    setIsKeyEditorOpen,
    keyInput,
    setKeyInput,
    saveKey,
    clearKey,
    selectFolder,
    startRun,
    cancelRun,
    activeResult,
    previewBlobUrl,
    clearBlobUrl,
    revealFolder,
    actionError,
    clearActionError,
    isSubmitting,
    isSubscribed,
    retrySubscription,
  } = generation;

  const run = snapshot.run;
  const showProgress = isRunActive(run?.state);
  const showResult = isTerminalState(run?.state);

  let statusAnnouncement = '';
  if (showProgress) {
    statusAnnouncement = 'Generation running in Siemens Solid Edge.';
  } else if (run?.state === 'succeeded') {
    statusAnnouncement = 'Generation completed successfully. 3D models ready.';
  } else if (run?.state === 'failed') {
    statusAnnouncement = `Generation run failed: ${run.reason ?? 'Internal engine error.'}`;
  } else if (run?.state === 'cancelled') {
    statusAnnouncement = 'Generation run was cancelled.';
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
            <Sparkles className="h-5 w-5" aria-hidden="true" />
          </span>
          <h1 className="text-2xl font-bold tracking-tight text-slate-800">Design Workspace</h1>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left Column: Model Specification & Controls */}
        <div className="space-y-6 lg:col-span-6">
          <section
            aria-label="Generation Controls"
            className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
          >
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-700 pb-4 border-b border-slate-100">
              Model Specification
            </h2>
            <div className="mt-4">
              <GenerateForm
                mode={mode}
                setMode={setMode}
                prompt={prompt}
                setPrompt={setPrompt}
                output={snapshot.output}
                selectFolder={selectFolder}
                keyConfigured={snapshot.keyConfigured}
                isKeyEditorOpen={isKeyEditorOpen}
                setIsKeyEditorOpen={setIsKeyEditorOpen}
                keyInput={keyInput}
                setKeyInput={setKeyInput}
                saveKey={saveKey}
                clearKey={clearKey}
                startRun={startRun}
                isRunActive={isRunActive(run?.state) || run?.resultAccess === 'checking'}
                isSubmitting={isSubmitting}
                actionError={actionError}
                clearActionError={clearActionError}
                isSubscribed={isSubscribed}
                retrySubscription={retrySubscription}
                isOtherBusy={isOtherBusy}
              />
            </div>
          </section>
        </div>

        {/* Right Column: Live Status, CAD Geometry Preview & Outcome */}
        <div className="space-y-6 lg:col-span-6">
          {showProgress && run ? (
            <section aria-label="Generation Progress">
              <GenerationProgress run={run} cancelRun={cancelRun} />
            </section>
          ) : showResult && run ? (
            <section aria-label="Generation Result">
              <GenerationResult
                run={run}
                result={activeResult}
                previewBlobUrl={previewBlobUrl}
                revealFolder={revealFolder}
                onPreviewDecodeError={clearBlobUrl}
              />
            </section>
          ) : (
            <section
              aria-label="CAD Geometry Preview"
              className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center shadow-2xs space-y-3"
            >
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-blue-50 text-blue-600 border border-blue-100">
                <Box className="h-6 w-6" aria-hidden="true" />
              </div>
              <div className="space-y-1">
                <h2 className="text-sm font-bold text-slate-800">CAD Geometry Preview</h2>
                <p className="mx-auto max-w-sm text-xs text-slate-500 leading-relaxed">
                  Your generated 3D CAD preview, part files (.par, .step, .stl), and run manifests
                  will appear here.
                </p>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
};
