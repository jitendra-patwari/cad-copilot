import React from 'react';
import { Layers, FileOutput, Info } from 'lucide-react';

interface FormatTarget {
  readonly ext: string;
  readonly name: string;
  readonly note: string;
}

interface OperationGroup {
  readonly operation: string;
  readonly description: string;
  readonly formats: readonly FormatTarget[];
}

const CANONICAL_OPERATIONS: readonly OperationGroup[] = [
  {
    operation: '3D CAD Export',
    description: 'Solid model kernel and exchange geometry targets',
    formats: [
      { ext: '.step', name: 'STEP', note: 'Universal CAD exchange' },
      { ext: '.stl', name: 'STL', note: 'Triangulated facet mesh' },
      { ext: '.x_t', name: 'Parasolid', note: 'Native geometric kernel' },
    ],
  },
  {
    operation: 'Drawing Publication',
    description: '2D drafting, sheet documentation, and vector publication',
    formats: [
      { ext: '.pdf', name: 'Drawing PDF', note: 'Vector sheet publication' },
      { ext: '.dxf', name: 'Drawing DXF', note: '2D drafting vector exchange' },
    ],
  },
] as const;

export const BatchPage: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-1 border-b border-slate-800 pb-5">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-500/10 px-2.5 py-0.5 text-xs font-medium text-indigo-400 ring-1 ring-indigo-500/20">
            <Layers className="h-3 w-3" aria-hidden="true" />
            Batch Workspace
          </span>
        </div>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-white">
          Batch Operations & Export
        </h1>
        <p className="text-sm text-slate-400">
          Sequential multi-file CAD processing, format translations, and drawing publication.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Queue Overview Area */}
        <section aria-label="Batch Processing Queue" className="space-y-4 lg:col-span-7">
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
              Input Files & Job Queue
            </h2>
            <p className="mt-1 text-xs text-slate-400">
              Folder ingestion and sequential file execution pipeline.
            </p>

            <div className="mt-4 rounded-lg border border-dashed border-slate-700/80 bg-slate-950/40 p-6 text-center">
              <Layers className="mx-auto h-8 w-8 text-slate-600" aria-hidden="true" />
              <p className="mt-2 text-sm font-medium text-slate-300">
                Batch Processing Unavailable
              </p>
              <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-slate-400">
                Multi-file batch operations and drawing publication require a connected local
                execution bridge. The workspace layout and supported format targets are established,
                while batch queue execution remains unavailable.
              </p>
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
            <div className="flex items-start gap-3">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-indigo-400" aria-hidden="true" />
              <div className="space-y-1 text-xs text-slate-400">
                <p className="font-medium text-slate-300">Local Processing Architecture</p>
                <p>
                  Batch conversion operates sequentially through local Siemens Solid Edge automation
                  with source file integrity protection and isolated artifact outputs.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Supported Formats Card Grouped by Operation */}
        <section aria-label="Supported Format Targets" className="space-y-4 lg:col-span-5">
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
            <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
              <FileOutput className="h-4 w-4 text-indigo-400" aria-hidden="true" />
              <h2 className="text-sm font-semibold text-slate-200">Target Export Formats</h2>
            </div>
            <div className="mt-3 space-y-4">
              {CANONICAL_OPERATIONS.map((op) => (
                <div key={op.operation} className="space-y-2">
                  <div className="border-b border-slate-800/60 pb-1">
                    <h3 className="text-xs font-semibold text-slate-300">{op.operation}</h3>
                    <p className="text-[11px] text-slate-500">{op.description}</p>
                  </div>
                  <div className="divide-y divide-slate-800/40" role="list">
                    {op.formats.map((fmt) => (
                      <div
                        key={fmt.ext}
                        className="flex items-center justify-between py-1.5"
                        role="listitem"
                      >
                        <div>
                          <span className="font-mono text-xs font-semibold text-slate-200">
                            {fmt.ext}
                          </span>
                          <span className="ml-2 text-xs text-slate-400">{fmt.name}</span>
                        </div>
                        <span className="text-[11px] text-slate-500">{fmt.note}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};
