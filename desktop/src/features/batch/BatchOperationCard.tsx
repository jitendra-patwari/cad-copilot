import React from 'react';
import { FileOutput, CheckSquare, Square, Sliders } from 'lucide-react';
import { CANONICAL_OPERATIONS, getEligibleCount } from './batchState';
import type { BatchSourceSelection } from './types';

interface BatchOperationCardProps {
  source: BatchSourceSelection | null;
  selectedOperation: 'export_3d' | 'publish_drawing';
  onSelectOperation: (op: 'export_3d' | 'publish_drawing') => void;
  selectedFormats: string[];
  onToggleFormat: (formatId: string) => void;
  continueOnError: boolean;
  onSetContinueOnError: (val: boolean) => void;
  maxFiles: number;
  onSetMaxFiles: (val: number) => void;
  disabled: boolean;
}

export const BatchOperationCard: React.FC<BatchOperationCardProps> = ({
  source,
  selectedOperation,
  onSelectOperation,
  selectedFormats,
  onToggleFormat,
  continueOnError,
  onSetContinueOnError,
  maxFiles,
  onSetMaxFiles,
  disabled,
}) => {
  const currentOpGroup = CANONICAL_OPERATIONS.find((op) => op.id === selectedOperation)!;
  const eligibleCount = getEligibleCount(source, selectedOperation);
  const excludedCount = source
    ? selectedOperation === 'export_3d'
      ? source.dftCount
      : source.parCount + source.psmCount + source.asmCount
    : 0;

  return (
    <div
      role="region"
      aria-label="Supported Format Targets"
      className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-5"
    >
      <div className="flex items-center gap-2 border-b border-slate-200 pb-3">
        <FileOutput className="h-4 w-4 text-blue-600" aria-hidden="true" />
        <h2 className="text-sm font-semibold text-slate-900">2. Operation & Format Targets</h2>
      </div>

      {/* Operation Selection */}
      <div className="space-y-2">
        <label className="text-xs font-semibold text-slate-700">Select Batch Operation</label>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {CANONICAL_OPERATIONS.map((op) => {
            const isSelected = selectedOperation === op.id;
            return (
              <button
                key={op.id}
                type="button"
                aria-label={op.operation}
                aria-pressed={isSelected}
                disabled={disabled}
                onClick={() => onSelectOperation(op.id)}
                className={`flex flex-col items-start rounded-lg border p-3 text-left transition-all ${
                  isSelected
                    ? 'border-blue-600 bg-blue-50/50 shadow-sm'
                    : 'border-slate-200 bg-white hover:border-slate-300'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                <div className="flex w-full items-center justify-between">
                  <span
                    className={`text-xs font-bold ${
                      isSelected ? 'text-blue-900' : 'text-slate-800'
                    }`}
                  >
                    {op.operation}
                  </span>
                  {isSelected && (
                    <span className="rounded-full bg-blue-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                      Selected
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[11px] text-slate-500 leading-normal">{op.description}</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {op.formats.map((f) => (
                    <span
                      key={f.id}
                      className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600 border border-slate-200/80"
                    >
                      {f.ext}
                    </span>
                  ))}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Format Checkboxes for Active Operation */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-1">
          <label className="text-xs font-semibold text-slate-700">Export Formats</label>
          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            <span>
              {eligibleCount} eligible {eligibleCount === 1 ? 'file' : 'files'} for{' '}
              {currentOpGroup.operation}
            </span>
            {excludedCount > 0 && (
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">
                ({excludedCount} {selectedOperation === 'export_3d' ? 'drawing' : '3D model'}{' '}
                {excludedCount === 1 ? 'file' : 'files'} excluded)
              </span>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {currentOpGroup.formats.map((fmt) => {
            const isChecked = selectedFormats.includes(fmt.id);
            return (
              <button
                key={fmt.id}
                type="button"
                aria-label={`Toggle ${fmt.name} format`}
                aria-pressed={isChecked}
                disabled={disabled}
                onClick={() => onToggleFormat(fmt.id)}
                className={`flex items-start gap-2.5 rounded-lg border p-2.5 text-left transition-all ${
                  isChecked
                    ? 'border-blue-500 bg-blue-50/40 text-blue-900'
                    : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                {isChecked ? (
                  <CheckSquare className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />
                ) : (
                  <Square className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                )}
                <div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold">{fmt.name}</span>
                    <span className="font-mono text-[10px] text-slate-500">({fmt.id})</span>
                  </div>
                  <p className="text-[10px] text-slate-500 leading-tight mt-0.5">{fmt.note}</p>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Options: Continue on Error & Max Files */}
      <div className="border-t border-slate-100 pt-3 space-y-3">
        <div className="flex items-center gap-2">
          <Sliders className="h-3.5 w-3.5 text-slate-500" />
          <span className="text-xs font-semibold text-slate-700">Execution Options</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <label className="flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
            <input
              type="checkbox"
              disabled={disabled}
              checked={continueOnError}
              onChange={(e) => onSetContinueOnError(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
            />
            <span>Continue on single-file errors</span>
          </label>

          <div className="flex items-center gap-2">
            <label htmlFor="max-files-input" className="text-xs text-slate-600 whitespace-nowrap">
              Max files cap:
            </label>
            <input
              id="max-files-input"
              type="number"
              min={1}
              max={500}
              disabled={disabled}
              value={maxFiles}
              onChange={(e) => {
                const val = parseInt(e.target.value, 10);
                if (!isNaN(val)) {
                  onSetMaxFiles(Math.max(1, Math.min(500, val)));
                }
              }}
              className="w-20 rounded border border-slate-300 px-2 py-1 text-xs text-slate-800 focus:border-blue-500 focus:outline-none disabled:opacity-50"
            />
            <span className="text-[10px] text-slate-400">(1–500)</span>
          </div>
        </div>
      </div>
    </div>
  );
};
