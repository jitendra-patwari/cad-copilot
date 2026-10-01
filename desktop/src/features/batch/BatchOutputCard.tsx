import React from 'react';
import { FolderOpen } from 'lucide-react';
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
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-0.5">
          <span className="text-xs font-semibold text-slate-800">Target Output Directory</span>
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
            {output ? (
              <span className="font-mono text-slate-800 break-all">{output.displayPath}</span>
            ) : (
              <span className="italic text-slate-400">No output directory selected</span>
            )}
          </div>
        </div>

        <button
          type="button"
          onClick={onSelectOutput}
          disabled={disabled}
          aria-label={
            output
              ? 'Change Directory (Select Output Folder)'
              : 'Choose Directory (Select Output Folder)'
          }
          className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50"
        >
          <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
          {output ? 'Change Directory' : 'Choose Directory'}
        </button>
      </div>
    </div>
  );
};
