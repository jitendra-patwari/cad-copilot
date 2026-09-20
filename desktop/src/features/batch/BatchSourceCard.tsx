import React, { useState } from 'react';
import { Folder, Files, FileCode, ChevronDown, ChevronUp, Search } from 'lucide-react';
import type { BatchSourceSelection, SourceSelectMode } from './types';

interface BatchSourceCardProps {
  source: BatchSourceSelection | null;
  sourceFiles: string[];
  disabled: boolean;
  onSelectSource: (mode: SourceSelectMode) => void;
}

export const BatchSourceCard: React.FC<BatchSourceCardProps> = ({
  source,
  sourceFiles,
  disabled,
  onSelectSource,
}) => {
  const [isListExpanded, setIsListExpanded] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const displayFiles = sourceFiles.length > 0 ? sourceFiles : (source?.previewFiles ?? []);
  const filteredFiles = searchQuery.trim()
    ? displayFiles.filter((f) => f.toLowerCase().includes(searchQuery.trim().toLowerCase()))
    : displayFiles;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">1. Source CAD Files</h2>
          <p className="text-xs text-slate-500">
            Select a folder or group of CAD files to process sequentially.
          </p>
        </div>
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
                <div className="mt-2 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <div className="relative">
                    <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
                    <input
                      type="text"
                      placeholder="Filter files..."
                      aria-label="Filter source files"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="w-full rounded-md border border-slate-300 bg-white py-1 pl-8 pr-3 text-xs text-slate-800 placeholder-slate-400 focus:border-blue-500 focus:outline-none"
                    />
                  </div>
                  <div className="max-h-48 overflow-y-auto space-y-1">
                    {filteredFiles.map((file, idx) => (
                      <div
                        key={idx}
                        className="truncate rounded bg-white px-2 py-1 font-mono text-[11px] text-slate-700 border border-slate-200/60"
                        title={file}
                      >
                        {file}
                      </div>
                    ))}
                    {filteredFiles.length === 0 && (
                      <p className="text-center text-xs text-slate-500 py-2">No matching files</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50/50 p-4 text-center">
          <p className="text-xs text-slate-500">No source directory or files selected.</p>
        </div>
      )}
    </div>
  );
};
