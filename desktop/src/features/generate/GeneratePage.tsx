import React from 'react';
import { Sparkles, Info, Sliders } from 'lucide-react';

export const GeneratePage: React.FC = () => {
  return (
    <div className="space-y-6">
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
          Parametric Solid Edge CAD modeling from natural language prompts and geometric
          specifications.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Foundation Status Card */}
        <section aria-label="Workflow Status" className="space-y-4 lg:col-span-7">
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-700">
              Workspace Status
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Application foundation and execution bridge state.
            </p>

            <div className="mt-4 rounded-xl border border-dashed border-slate-300 bg-slate-50/70 p-6 text-center">
              <Sparkles className="mx-auto h-8 w-8 text-slate-400" aria-hidden="true" />
              <p className="mt-2 text-sm font-semibold text-slate-800">
                Generation Workflow Unavailable
              </p>
              <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-slate-500">
                Interactive single-part CAD generation requires a connected local execution bridge.
                The application foundation and navigation shell are active, while live model
                generation controls remain unavailable.
              </p>
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-start gap-3">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" aria-hidden="true" />
              <div className="space-y-1 text-xs text-slate-600">
                <p className="font-semibold text-slate-800">Automation Environment</p>
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
