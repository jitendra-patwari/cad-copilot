import React, { useState } from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  FolderOpen,
  FileCheck,
  Search,
  Layers,
} from 'lucide-react';
import type { BatchResultResponse, BatchRowCategory } from './types';
import { formatBytes } from './batchState';

interface BatchResultCardProps {
  result: BatchResultResponse;
  onRevealOutput: () => void;
}

export const BatchResultCard: React.FC<BatchResultCardProps> = ({ result, onRevealOutput }) => {
  const [filterQuery, setFilterQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');

  const { summary, rows } = result;

  const filteredRows = rows.filter((r) => {
    if (categoryFilter !== 'all' && r.category !== categoryFilter) {
      return false;
    }
    if (filterQuery.trim()) {
      return r.file.toLowerCase().includes(filterQuery.trim().toLowerCase());
    }
    return true;
  });

  const getStatusBadge = () => {
    switch (result.engineStatus) {
      case 'completed':
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-800">
            <CheckCircle2 className="h-3.5 w-3.5" /> Completed
          </span>
        );
      case 'cancelled':
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800">
            <AlertTriangle className="h-3.5 w-3.5" /> Cancelled
          </span>
        );
      case 'rejected':
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2.5 py-0.5 text-xs font-semibold text-rose-800">
            <XCircle className="h-3.5 w-3.5" /> Rejected
          </span>
        );
      case 'failed':
      default:
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2.5 py-0.5 text-xs font-semibold text-rose-800">
            <XCircle className="h-3.5 w-3.5" /> Failed
          </span>
        );
    }
  };

  const getCategoryBadge = (cat: BatchRowCategory) => {
    switch (cat) {
      case 'succeeded':
        return (
          <span className="rounded bg-emerald-100 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-800">
            Succeeded
          </span>
        );
      case 'partial':
        return (
          <span className="rounded bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-800">
            Partial
          </span>
        );
      case 'failed':
        return (
          <span className="rounded bg-rose-100 px-2 py-0.5 text-[10px] font-bold uppercase text-rose-800">
            Failed
          </span>
        );
      case 'cancelled':
        return (
          <span className="rounded bg-slate-200 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-700">
            Cancelled
          </span>
        );
      case 'unprocessed':
      default:
        return (
          <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-500">
            Unprocessed
          </span>
        );
    }
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-5">
      {/* Header & Status */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-4">
        <div className="flex items-center gap-2.5">
          <Layers className="h-5 w-5 text-blue-600" aria-hidden="true" />
          <div>
            <h2 className="text-base font-bold text-slate-900">Batch Execution Results</h2>
            <p className="text-xs text-slate-500">
              Request ID: <span className="font-mono text-slate-700">{result.requestId}</span>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {getStatusBadge()}
          <button
            type="button"
            onClick={onRevealOutput}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50"
          >
            <FolderOpen className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
            Open Output Folder
          </button>
        </div>
      </div>

      {/* Summary Metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-6 gap-2 text-center">
        <div className="rounded-lg bg-slate-50 p-2 border border-slate-200/70">
          <span className="text-[10px] font-bold uppercase text-slate-500">Total</span>
          <p className="text-base font-bold text-slate-900">{summary.total}</p>
        </div>
        <div className="rounded-lg bg-emerald-50 p-2 border border-emerald-200/70">
          <span className="text-[10px] font-bold uppercase text-emerald-600">Succeeded</span>
          <p className="text-base font-bold text-emerald-800">{summary.accepted}</p>
        </div>
        <div className="rounded-lg bg-amber-50 p-2 border border-amber-200/70">
          <span className="text-[10px] font-bold uppercase text-amber-600">Partial</span>
          <p className="text-base font-bold text-amber-800">{summary.partial}</p>
        </div>
        <div className="rounded-lg bg-rose-50 p-2 border border-rose-200/70">
          <span className="text-[10px] font-bold uppercase text-rose-600">Failed</span>
          <p className="text-base font-bold text-rose-800">{summary.failed}</p>
        </div>
        <div className="rounded-lg bg-slate-100 p-2 border border-slate-200">
          <span className="text-[10px] font-bold uppercase text-slate-600">Cancelled</span>
          <p className="text-base font-bold text-slate-800">{summary.cancelled}</p>
        </div>
        <div className="rounded-lg bg-slate-50 p-2 border border-slate-200/70">
          <span className="text-[10px] font-bold uppercase text-slate-400">Unprocessed</span>
          <p className="text-base font-bold text-slate-600">{summary.unprocessed}</p>
        </div>
      </div>

      {/* Manifest Status Banner */}
      {result.manifestState === 'validated' && (
        <div className="flex items-center gap-2 rounded-lg bg-emerald-50/70 px-3 py-2 text-xs text-emerald-900 border border-emerald-200">
          <FileCheck className="h-4 w-4 text-emerald-600 shrink-0" />
          <span>
            Run manifest validated and published to output root:{' '}
            <span className="font-mono text-emerald-950 font-semibold">{result.manifestPath}</span>
          </span>
        </div>
      )}
      {result.manifestState === 'unavailable' && (
        <div className="flex items-center gap-2 rounded-lg bg-amber-50/70 px-3 py-2 text-xs text-amber-900 border border-amber-200">
          <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
          <span>
            Run manifest is unavailable or could not be published; file result accounting is
            preserved.
          </span>
        </div>
      )}

      {/* Reason Banner if failed or cancelled */}
      {result.reason && (
        <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-700 border border-slate-200 space-y-1">
          <span className="font-semibold text-slate-900">Diagnostic Details: </span>
          <span>{result.reason}</span>
        </div>
      )}

      {/* Filter and Table Controls */}
      <div className="space-y-3 pt-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="relative w-full sm:w-64">
            <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              placeholder="Search results by filename..."
              aria-label="Search results by filename"
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              className="w-full rounded-md border border-slate-300 bg-white py-1 pl-8 pr-3 text-xs text-slate-800 placeholder-slate-400 focus:border-blue-500 focus:outline-none"
            />
          </div>

          <div className="flex flex-wrap items-center gap-1 text-xs">
            <span className="text-slate-500 text-[11px] mr-1">Filter:</span>
            {['all', 'succeeded', 'partial', 'failed', 'cancelled', 'unprocessed'].map((cat) => (
              <button
                key={cat}
                type="button"
                aria-pressed={categoryFilter === cat}
                onClick={() => setCategoryFilter(cat)}
                className={`rounded px-2 py-0.5 text-[11px] font-medium capitalize transition-colors ${
                  categoryFilter === cat
                    ? 'bg-blue-600 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>

        {/* Results HTML Table */}
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table
            className="min-w-full divide-y divide-slate-200 text-left text-xs"
            aria-label="Batch execution results"
          >
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-3 py-2 font-semibold">File</th>
                <th className="px-3 py-2 font-semibold">Status</th>
                <th className="px-3 py-2 font-semibold">Formats Attempted</th>
                <th className="px-3 py-2 font-semibold">Artifacts Produced</th>
                <th className="px-3 py-2 font-semibold">Diagnostics</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 bg-white">
              {filteredRows.map((row, idx) => (
                <tr key={idx} className="hover:bg-slate-50/50">
                  <td className="px-3 py-2 font-mono text-[11px] font-medium text-slate-800">
                    {row.file}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">{getCategoryBadge(row.category)}</td>
                  <td className="px-3 py-2 uppercase text-[11px] text-slate-600">
                    {row.attemptedFormats.join(', ') || '—'}
                  </td>
                  <td className="px-3 py-2 text-[11px] text-slate-600">
                    {row.successfulArtifacts.length > 0 ? (
                      <div className="space-y-0.5">
                        {row.successfulArtifacts.map((art, aIdx) => (
                          <div key={aIdx} className="font-mono text-[10px] text-slate-700">
                            {art.relativePath
                              ? `${art.relativePath} (${formatBytes(art.sizeBytes)})`
                              : `${art.format.toUpperCase()} (unverified location)`}
                          </div>
                        ))}
                      </div>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="px-3 py-2 text-[11px]">
                    {row.diagnosticCodes.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {row.diagnosticCodes.map((code, cIdx) => (
                          <span
                            key={cIdx}
                            className="rounded bg-rose-50 px-1.5 py-0.5 font-mono text-[10px] text-rose-700 border border-rose-100"
                          >
                            {code}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-slate-400">None</span>
                    )}
                  </td>
                </tr>
              ))}
              {filteredRows.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-slate-500">
                    No matching file records found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
