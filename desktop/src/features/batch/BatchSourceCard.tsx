import React, { useState } from 'react';
import { Folder, Files, FileCode, ChevronDown, ChevronUp } from 'lucide-react';
import type { BatchSourceSelection, SourceSelectMode } from './types';

interface BatchSourceCardProps {
  source: BatchSourceSelection | null;
  sourceFiles: string[];
  disabled: boolean;
  onSelectSource: (mode: SourceSelectMode) => void;
  initialExpanded?: boolean;
  embedded?: boolean;
}

export const BatchSourceCard: React.FC<BatchSourceCardProps> = ({
  source,
  sourceFiles,
  disabled,
  onSelectSource,
  initialExpanded = false,
  embedded = false,
}) => {
  const [isListExpanded, setIsListExpanded] = useState(initialExpanded);

  const displayFiles = sourceFiles.length > 0 ? sourceFiles : (source?.previewFiles ?? []);

  return (
    <div
      className={
        embedded
          ? 'space-y-3'
          : 'rounded-xl border border-slate-200 bg-white p-4 sm:p-5 shadow-sm space-y-3'
      }
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-2">
        <h2 className="text-xs font-semibold text-slate-800">Source CAD Files</h2>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={disabled}
            onClick={() => onSelectSource('folder')}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Folder className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
            Select Folder
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onSelectSource('files')}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Files className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
            Select Files
          </button>
        </div>
      </div>

      {source ? (
        <div className="space-y-3">
          <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-700 space-y-1">
            <span className="font-semibold text-slate-900">Selected Source Root: </span>
            <span className="break-all font-mono text-slate-600">{source.displayRoot}</span>
          </div>

          <div className="flex flex-wrap gap-2 text-xs">
            <span className="inline-flex items-center gap-1 rounded-md bg-blue-50 px-2 py-1 font-medium text-blue-700">
              <FileCode className="h-3 w-3" /> PAR: {source.parCount}
            </span>
            <span className="inline-flex items-center gap-1 rounded-md bg-indigo-50 px-2 py-1 font-medium text-indigo-700">
              <FileCode className="h-3 w-3" /> PSM: {source.psmCount}
            </span>
            <span className="inline-flex items-center gap-1 rounded-md bg-purple-50 px-2 py-1 font-medium text-purple-700">
              <FileCode className="h-3 w-3" /> ASM: {source.asmCount}
            </span>
            <span className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-1 font-medium text-amber-700">
              <FileCode className="h-3 w-3" /> DFT: {source.dftCount}
            </span>
            {source.skippedCount > 0 && (
              <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 font-medium text-slate-600">
                Unsupported/Skipped: {source.skippedCount}
              </span>
            )}
          </div>

          {displayFiles.length > 0 && (
            <div className="pt-1">
              <button
                type="button"
                onClick={() => setIsListExpanded(!isListExpanded)}
                className="flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-800"
              >
                {isListExpanded ? (
                  <>
                    <ChevronUp className="h-3.5 w-3.5" />
                    Hide file inventory ({displayFiles.length})
                  </>
                ) : (
                  <>
                    <ChevronDown className="h-3.5 w-3.5" />
                    View file inventory ({displayFiles.length})
                  </>
                )}
              </button>

              {isListExpanded && (
                <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5">
                  <div className="max-h-60 overflow-y-auto space-y-1">
                    {displayFiles.map((file, idx) => (
                      <div
                        key={idx}
                        className="truncate rounded bg-white px-2 py-1 font-mono text-[11px] text-slate-700 border border-slate-200/60"
                        title={file}
                      >
                        {file}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center p-8 rounded-lg border border-dashed border-slate-300 bg-slate-50/50 text-center min-h-[200px]">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600 border border-blue-100 mb-2">
            <Folder className="h-5 w-5" aria-hidden="true" />
          </div>
          <p className="text-xs font-semibold text-slate-700">
            No source directory or files selected.
          </p>
          <p className="mt-1 text-[11px] text-slate-400 max-w-xs leading-normal">
            Choose a CAD project folder or select multiple model files (.par, .psm, .asm, .dft) to
            begin.
          </p>
        </div>
      )}
    </div>
  );
};
