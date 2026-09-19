import React from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Slash,
  FolderOpen,
  Image as ImageIcon,
  FileBox,
  Layers,
  FileCode,
} from 'lucide-react';
import type { GenerationResultResponse, GenerationRunSnapshot } from './types';
import { formatBytes } from './generationState';

interface GenerationResultProps {
  run: GenerationRunSnapshot;
  result: GenerationResultResponse | null;
  previewBlobUrl: string | null;
  revealFolder: () => Promise<void>;
  onPreviewDecodeError?: () => void;
}

export const GenerationResult: React.FC<GenerationResultProps> = ({
  run,
  result,
  previewBlobUrl,
  revealFolder,
  onPreviewDecodeError,
}) => {
  const isSucceeded = run.state === 'succeeded';
  const isRejected = run.state === 'rejected';
  const isFailed = run.state === 'failed';
  const isCancelled = run.state === 'cancelled';

  const [decodeError, setDecodeError] = React.useState(false);

  React.useEffect(() => {
    setDecodeError(false);
  }, [previewBlobUrl]);

  const handleImageError = React.useCallback(() => {
    setDecodeError(true);
    onPreviewDecodeError?.();
  }, [onPreviewDecodeError]);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-5">
      {/* Outcome Header Banner */}
      <div
        className={`rounded-xl border p-4 ${
          isSucceeded
            ? 'border-emerald-200 bg-emerald-50/60 text-emerald-950'
            : isRejected
              ? 'border-amber-200 bg-amber-50/60 text-amber-950'
              : isFailed
                ? 'border-rose-200 bg-rose-50/60 text-rose-950'
                : 'border-slate-300 bg-slate-100 text-slate-800'
        }`}
      >
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-3">
            {isSucceeded && (
              <CheckCircle2
                className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600"
                aria-hidden="true"
              />
            )}
            {isRejected && (
              <AlertTriangle
                className="mt-0.5 h-5 w-5 shrink-0 text-amber-600"
                aria-hidden="true"
              />
            )}
            {isFailed && (
              <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" aria-hidden="true" />
            )}
            {isCancelled && (
              <Slash className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" aria-hidden="true" />
            )}

            <div className="space-y-0.5">
              <h2 className="text-sm font-semibold">
                {isSucceeded && 'CAD Model Successfully Generated'}
                {isRejected && 'CAD Generation Rejected'}
                {isFailed && 'CAD Generation Failed'}
                {isCancelled && 'CAD Generation Cancelled'}
              </h2>
              <p className="font-mono text-xs opacity-80">Request ID: {run.requestId}</p>
              {run.reason && (
                <p className="mt-1 text-xs leading-relaxed opacity-90">{run.reason}</p>
              )}
            </div>
          </div>

          {isSucceeded && (
            <button
              type="button"
              onClick={revealFolder}
              className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-300 bg-white px-3 py-1.5 text-xs font-semibold text-emerald-800 shadow-sm hover:bg-emerald-50"
            >
              <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
              Show in Folder
            </button>
          )}
        </div>
      </div>

      {/* Warnings List */}
      {run.warnings && run.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50/50 p-3">
          <p className="text-xs font-semibold text-amber-900">Execution Warnings</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-amber-800">
            {run.warnings.map((w, idx) => (
              <li key={idx}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Published Artifacts Table */}
      {isSucceeded && !result && run.resultAccess === 'checking' && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 text-center text-xs text-slate-600">
          <div className="mx-auto h-5 w-5 animate-spin rounded-full border-2 border-blue-600 border-t-transparent" />
          <p className="mt-2 font-medium">Verifying published artifacts and run manifest...</p>
        </div>
      )}

      {isSucceeded && !result && run.resultAccess === 'unavailable' && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-800">
          <p className="font-semibold">Published Result Unavailable</p>
          <p className="mt-0.5">
            Artifact verification deadline exceeded or manifest sidecar was unavailable. Output
            files remain accessible via "Show in Folder".
          </p>
        </div>
      )}

      {isSucceeded && result && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-700">
              Published CAD Artifacts
            </h3>
            <span className="text-xs text-slate-500">
              {result.artifacts.length} files verified against manifest
            </span>
          </div>

          <div className="overflow-hidden rounded-lg border border-slate-200">
            <table className="min-w-full divide-y divide-slate-200 text-left text-xs">
              <thead className="bg-slate-50 text-slate-600 font-semibold">
                <tr>
                  <th scope="col" className="px-3 py-2">
                    Format
                  </th>
                  <th scope="col" className="px-3 py-2">
                    Filename
                  </th>
                  <th scope="col" className="px-3 py-2 text-right">
                    Size
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white text-slate-800">
                {result.artifacts.map((art) => (
                  <tr key={art.filename} className="hover:bg-slate-50/70">
                    <td className="whitespace-nowrap px-3 py-2 font-medium">
                      <div className="flex items-center gap-1.5">
                        {art.format === 'par' && <FileBox className="h-3.5 w-3.5 text-blue-600" />}
                        {art.format === 'step' && (
                          <Layers className="h-3.5 w-3.5 text-indigo-600" />
                        )}
                        {art.format === 'stl' && (
                          <FileCode className="h-3.5 w-3.5 text-emerald-600" />
                        )}
                        {art.format === 'jpg' && (
                          <ImageIcon className="h-3.5 w-3.5 text-amber-600" />
                        )}
                        <span className="uppercase">{art.format}</span>
                      </div>
                    </td>
                    <td className="font-mono text-slate-700 px-3 py-2" title={art.path}>
                      {art.filename}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-slate-600">
                      {formatBytes(art.sizeBytes)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Static JPEG Preview */}
      {isSucceeded && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-700">
            Rendered Preview
          </h3>

          {previewBlobUrl && !decodeError ? (
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-900/5 p-2 text-center">
              <img
                src={previewBlobUrl}
                alt="Solid Edge Rendered CAD Preview"
                className="mx-auto max-h-80 w-auto rounded-lg object-contain shadow-xs"
                onError={handleImageError}
              />
            </div>
          ) : run.resultAccess === 'checking' ? (
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 text-center text-xs text-slate-500">
              <ImageIcon
                className="mx-auto h-6 w-6 text-slate-300 animate-pulse"
                aria-hidden="true"
              />
              <p className="mt-1">Verifying preview image...</p>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-6 text-center text-xs text-slate-500">
              <ImageIcon className="mx-auto h-6 w-6 text-slate-300" aria-hidden="true" />
              <p className="mt-1">
                {decodeError
                  ? 'Preview image could not be decoded by browser.'
                  : 'Static preview unavailable for this generation run.'}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Manifest Summary Details */}
      {isSucceeded && result?.manifestSummary && (
        <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 space-y-2 text-xs">
          <p className="font-semibold text-slate-800">Execution Provenance & Environment</p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-slate-600 sm:grid-cols-3">
            <div>
              <span className="text-slate-400">Provenance: </span>
              <span className="font-medium text-slate-800">
                {result.manifestSummary.provenanceKind}
              </span>
            </div>
            {result.manifestSummary.sourceId && (
              <div>
                <span className="text-slate-400">Source: </span>
                <span className="font-medium text-slate-800">
                  {result.manifestSummary.sourceId}
                </span>
              </div>
            )}
            {result.manifestSummary.operationsExecuted != null && (
              <div>
                <span className="text-slate-400">Operations: </span>
                <span className="font-semibold text-slate-800">
                  {result.manifestSummary.operationsExecuted} executed
                </span>
              </div>
            )}
            {result.manifestSummary.cadRuntimeVersion && (
              <div>
                <span className="text-slate-400">Solid Edge Build: </span>
                <span className="font-mono text-slate-800">
                  {result.manifestSummary.cadRuntimeVersion}
                </span>
              </div>
            )}
            <div>
              <span className="text-slate-400">Schema: </span>
              <span className="font-mono text-slate-800">
                {result.manifestSummary.schemaVersion}
              </span>
            </div>
            {result.manifestSummary.planSha256 && (
              <div>
                <span className="text-slate-400">Plan SHA-256: </span>
                <span
                  className="font-mono text-slate-800"
                  title={result.manifestSummary.planSha256}
                >
                  {result.manifestSummary.planSha256.substring(0, 8)}…
                </span>
              </div>
            )}
            {result.manifestSummary.promptSha256 && (
              <div>
                <span className="text-slate-400">Prompt SHA-256: </span>
                <span
                  className="font-mono text-slate-800"
                  title={result.manifestSummary.promptSha256}
                >
                  {result.manifestSummary.promptSha256.substring(0, 8)}…
                </span>
              </div>
            )}
          </div>
          {result.manifestSummary.diagnostics && result.manifestSummary.diagnostics.length > 0 && (
            <div className="mt-3 border-t border-slate-200/80 pt-2.5">
              <p className="font-semibold text-slate-700">
                Diagnostics ({result.manifestSummary.diagnostics.length})
              </p>
              <ul className="mt-1 space-y-1">
                {result.manifestSummary.diagnostics.map((diag, idx) => (
                  <li key={idx} className="flex items-start gap-1.5 text-slate-700">
                    <span className="font-medium text-slate-500 uppercase text-[10px]">
                      [{diag.severity}]
                    </span>
                    <span className="font-mono text-slate-600">{diag.code}:</span>
                    <span>{diag.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
