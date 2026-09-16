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
      className="flex min-h-screen w-full items-center justify-center bg-slate-950 p-6 text-slate-100"
    >
      <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900/90 p-6 text-center shadow-xl backdrop-blur-sm">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-500/10 text-amber-400">
          <AlertTriangle className="h-6 w-6" aria-hidden="true" />
        </div>
        <h1 className="mt-4 text-xl font-semibold tracking-tight text-white">Application Error</h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-400">
          An unexpected error occurred in the desktop shell. No sensitive data or session details
          have been stored.
        </p>
        <div className="mt-6">
          <button
            type="button"
            onClick={handleReload}
            className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-sky-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-900"
          >
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
            <span>Reload application</span>
          </button>
        </div>
      </div>
    </div>
  );
};
