import React from 'react';
import { FolderOutput, ShieldCheck } from 'lucide-react';
import type { BatchOutputSelection } from './types';

interface BatchOutputCardProps {
  output: BatchOutputSelection | null;
  disabled: boolean;
  onSelectOutput: () => void;
}

export const BatchOutputCard: React.FC<BatchOutputCardProps> = ({
  output,
  disabled,
  onSelectOutput,
}) => {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">3. Output Directory</h2>
          <p className="text-xs text-slate-500">
            Designate the destination directory for exported artifacts and run manifest.
          </p>
        </div>
        <button
          type="button"
          disabled={disabled}
          onClick={onSelectOutput}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <FolderOutput className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
          Select Output Folder
        </button>
      </div>

      {output ? (
        <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-700 space-y-1">
          <span className="font-semibold text-slate-900">Selected Destination: </span>
          <span className="break-all font-mono text-slate-600">{output.displayPath}</span>
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50/50 p-4 text-center">
          <p className="text-xs text-slate-500">No output directory selected.</p>
        </div>
      )}

      <div className="flex items-start gap-2.5 rounded-lg bg-emerald-50/60 p-3 text-emerald-900 border border-emerald-200/60">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
        <div className="text-[11px] leading-relaxed space-y-0.5">
          <p className="font-semibold text-emerald-950">Source Immutability & Collision Safety</p>
          <p className="text-emerald-800">
            Source files are never modified. Converted artifacts are written exclusively to the
            isolated output folder, and existing output files are not replaced.
          </p>
        </div>
      </div>
    </div>
  );
};
