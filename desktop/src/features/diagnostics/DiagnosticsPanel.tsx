import React, { useEffect, useRef } from 'react';
import { X, Activity } from 'lucide-react';
import { FOUNDATION_DIAGNOSTICS } from './diagnostics';

export interface DiagnosticsPanelProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly triggerRef?: React.RefObject<HTMLElement | null>;
}

export const DiagnosticsPanel: React.FC<DiagnosticsPanelProps> = ({
  isOpen,
  onClose,
  triggerRef,
}) => {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousActiveElement = useRef<Element | null>(null);

  useEffect(() => {
    if (!isOpen) return;

    previousActiveElement.current = document.activeElement;
    const triggerElement = triggerRef?.current;

    // Focus close button on open
    const timer = setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 10);

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }

      if (event.key === 'Tab' && panelRef.current) {
        const focusableElements = panelRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusableElements.length === 0) return;

        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];

        if (event.shiftKey) {
          if (document.activeElement === firstElement) {
            event.preventDefault();
            lastElement?.focus();
          }
        } else {
          if (document.activeElement === lastElement) {
            event.preventDefault();
            firstElement?.focus();
          }
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('keydown', handleKeyDown);
      // Return focus to trigger or previous active element
      if (triggerElement) {
        triggerElement.focus();
      } else if (previousActiveElement.current instanceof HTMLElement) {
        previousActiveElement.current.focus();
      }
    };
  }, [isOpen, onClose, triggerRef]);

  if (!isOpen) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="diagnostics-title"
        className="w-full max-w-lg rounded-xl border border-slate-200 bg-white shadow-xl overflow-hidden"
      >
        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50 text-blue-600 border border-blue-200">
              <Activity className="h-4 w-4" aria-hidden="true" />
            </div>
            <div>
              <h2 id="diagnostics-title" className="text-base font-bold text-slate-800">
                Diagnostics & Settings
              </h2>
              <p className="text-xs text-slate-500">Baseline status & environment checks</p>
            </div>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label="Close diagnostics"
            className="cursor-pointer rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-200 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="px-6 py-2 divide-y divide-slate-100" role="list">
          {FOUNDATION_DIAGNOSTICS.map((item) => (
            <div key={item.id} className="py-3" role="listitem">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-slate-700">{item.label}</span>
                <span
                  className={`inline-flex items-center rounded-lg px-2.5 py-0.5 text-xs font-medium ${
                    item.status === 'unavailable'
                      ? 'border border-amber-200 bg-amber-50 text-amber-800'
                      : 'border border-slate-200 bg-slate-100 text-slate-700'
                  }`}
                >
                  {item.value}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-600">{item.hint}</p>
            </div>
          ))}
        </div>

        <div className="flex justify-end border-t border-slate-200 bg-slate-50 px-6 py-3">
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded-xl bg-slate-200 px-4 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
};
