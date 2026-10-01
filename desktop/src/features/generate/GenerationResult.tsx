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

      {/* Static JPEG Preview under CAD Geometry Preview header */}
      {isSucceeded && (
        <div className="space-y-3">
          <div className="flex items-center justify-between border-b border-slate-100 pb-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-800">
              CAD Geometry Preview
            </h3>
            {result && (
              <span className="text-[11px] text-slate-500 font-medium">
                {result.artifacts.length} format{result.artifacts.length === 1 ? '' : 's'} verified
              </span>
            )}
          </div>

          {previewBlobUrl && !decodeError ? (
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-900/5 p-2 text-center">
              <img
                src={previewBlobUrl}
                alt="Solid Edge Rendered CAD Preview"
                className="mx-auto max-h-64 sm:max-h-72 w-auto rounded-lg object-contain shadow-xs"
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

      {/* Published Format Badges */}
      {isSucceeded && result && (
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            {result.artifacts.map((art) => (
              <span
                key={art.filename}
                title={art.filename}
                data-testid={`artifact-${art.format}`}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-semibold text-slate-800 shadow-2xs"
              >
                {art.format === 'par' && <FileBox className="h-3.5 w-3.5 text-blue-600" />}
                {art.format === 'step' && <Layers className="h-3.5 w-3.5 text-indigo-600" />}
                {art.format === 'stl' && <FileCode className="h-3.5 w-3.5 text-emerald-600" />}
                {art.format === 'jpg' && <ImageIcon className="h-3.5 w-3.5 text-amber-600" />}
                <span className="uppercase">{art.format}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
