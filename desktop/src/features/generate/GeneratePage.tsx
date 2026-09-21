import React from 'react';
import { Sparkles, Info, Sliders } from 'lucide-react';
import type { UseGenerationReturn } from './useGeneration';
import { GenerateForm } from './GenerateForm';
import { GenerationProgress } from './GenerationProgress';
import { GenerationResult } from './GenerationResult';
import { isRunActive, isTerminalState } from './generationState';

export interface GeneratePageProps {
  generation: UseGenerationReturn;
}

export const GeneratePage: React.FC<GeneratePageProps> = ({ generation }) => {
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

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="flex flex-col gap-1 border-b border-slate-200 pb-5">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700">
            <Sparkles className="h-3 w-3" aria-hidden="true" />
            Generation Workspace
          </span>
        </div>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">
          CAD Model Generation
        </h1>
        <p className="text-sm text-slate-500">
          Parametric Siemens Solid Edge CAD modeling from natural language prompts and geometric
          specifications.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Main Interactive Workflow Column */}
        <div className="space-y-6 lg:col-span-7">
          {/* Generation Input Form Card */}
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
              />
            </div>
          </section>

          {/* Active Generation Progress Section */}
          {showProgress && run && (
            <section aria-label="Generation Progress">
              <GenerationProgress run={run} cancelRun={cancelRun} />
            </section>
          )}

          {/* Terminal Generation Result Section */}
          {showResult && run && (
            <section aria-label="Generation Result">
              <GenerationResult
                run={run}
                result={activeResult}
                previewBlobUrl={previewBlobUrl}
                revealFolder={revealFolder}
                onPreviewDecodeError={clearBlobUrl}
              />
            </section>
          )}

          {/* Environment Status Card */}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-start gap-3">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" aria-hidden="true" />
              <div className="space-y-1 text-xs text-slate-600">
                <p className="font-semibold text-slate-800">Local Automation Environment</p>
                <p>
                  Model generation communicates directly with Siemens Solid Edge on Windows via COM
                  automation. Output files (.par, .step, .stl, .jpg) and manifests are written
                  exclusively to your chosen output directory.
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Verified Geometric Scope Card (Preserved Invariant) */}
        <section aria-label="Geometric Capabilities" className="lg:col-span-5">
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-center gap-2 border-b border-slate-200 pb-3">
              <Sliders className="h-4 w-4 text-blue-600" aria-hidden="true" />
              <h2 className="text-sm font-semibold text-slate-800">Supported Geometry Scope</h2>
            </div>
            <div className="mt-3 space-y-3 text-xs">
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <p className="font-semibold text-slate-800">Base Geometries</p>
                <p className="mt-0.5 text-slate-500">
                  Parametric rectangular prisms, cylinders, and deterministic spur gears.
                </p>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <p className="font-semibold text-slate-800">Localized Features</p>
                <p className="mt-0.5 text-slate-500">
                  Circular holes, rectangular cutouts, slot cutouts, and rectangular pads.
                </p>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <p className="font-semibold text-slate-800">Artifact & Manifest Publication</p>
                <p className="mt-0.5 text-slate-500">
                  Native CAD part (.par) publication, STEP AP214/242, STL meshes, and canonical
                  execution run manifests.
                </p>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};
