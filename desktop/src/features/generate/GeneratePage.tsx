import React from 'react';
import { Sparkles, Info, Sliders } from 'lucide-react';

export const GeneratePage: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-1 border-b border-slate-800 pb-5">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-sky-500/10 px-2.5 py-0.5 text-xs font-medium text-sky-400 ring-1 ring-sky-500/20">
            <Sparkles className="h-3 w-3" aria-hidden="true" />
            Generation Workspace
          </span>
        </div>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-white">CAD Model Generation</h1>
        <p className="text-sm text-slate-400">
          Parametric Solid Edge CAD modeling from natural language prompts and geometric
          specifications.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Foundation Status Card */}
        <section aria-label="Workflow Status" className="space-y-4 lg:col-span-7">
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
              Workspace Status
            </h2>
            <p className="mt-1 text-xs text-slate-400">
              Application foundation and execution bridge state.
            </p>

            <div className="mt-4 rounded-lg border border-dashed border-slate-700/80 bg-slate-950/40 p-6 text-center">
              <Sparkles className="mx-auto h-8 w-8 text-slate-600" aria-hidden="true" />
              <p className="mt-2 text-sm font-medium text-slate-300">
                Generation Workflow Unavailable
              </p>
              <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-slate-400">
                Interactive single-part CAD generation requires a connected local execution bridge.
                The application foundation and navigation shell are active, while live model
                generation controls remain unavailable.
              </p>
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
            <div className="flex items-start gap-3">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" aria-hidden="true" />
              <div className="space-y-1 text-xs text-slate-400">
                <p className="font-medium text-slate-300">Automation Environment</p>
                <p>
                  CAD generation requires a local Siemens Solid Edge installation with COM
                  automation enabled on Windows. Model parameters and feature instructions are
                  executed directly within the CAD environment.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Verified Geometric Scope Card */}
        <section aria-label="Geometric Capabilities" className="lg:col-span-5">
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
            <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
              <Sliders className="h-4 w-4 text-sky-400" aria-hidden="true" />
              <h2 className="text-sm font-semibold text-slate-200">Supported Geometry Scope</h2>
            </div>
            <div className="mt-3 space-y-3 text-xs text-slate-400">
              <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
                <p className="font-medium text-slate-300">Base Geometries</p>
                <p className="mt-0.5 text-slate-500">
                  Parametric rectangular prisms, cylinders, and deterministic spur gears.
                </p>
              </div>
              <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
                <p className="font-medium text-slate-300">Localized Features</p>
                <p className="mt-0.5 text-slate-500">
                  Circular holes, rectangular cutouts, slot cutouts, and rectangular pads.
                </p>
              </div>
              <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
                <p className="font-medium text-slate-300">Artifact & Manifest Publication</p>
                <p className="mt-0.5 text-slate-500">
                  Native CAD part (.par) publication and canonical execution run manifests.
                </p>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};
