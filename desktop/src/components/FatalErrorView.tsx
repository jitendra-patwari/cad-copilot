import React from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';

export interface FatalErrorViewProps {
  readonly onReload?: () => void;
}

export const FatalErrorView: React.FC<FatalErrorViewProps> = ({ onReload }) => {
  const handleReload = () => {
    if (onReload) {
      onReload();
    } else {
      window.location.reload();
    }
  };

  return (
    <div
      role="alert"
      className="flex min-h-screen w-full items-center justify-center bg-slate-50 p-6 text-slate-900"
    >
      <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 text-center shadow-xl">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-amber-200 bg-amber-50 text-amber-600">
          <AlertTriangle className="h-6 w-6" aria-hidden="true" />
        </div>
        <h1 className="mt-4 text-xl font-bold tracking-tight text-slate-900">Application Error</h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-500">
          An unexpected error occurred in the desktop shell. No sensitive data or session details
          have been stored.
        </p>
        <div className="mt-6">
          <button
            type="button"
            onClick={handleReload}
            className="cursor-pointer inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
            <span>Reload application</span>
          </button>
        </div>
      </div>
    </div>
  );
};
