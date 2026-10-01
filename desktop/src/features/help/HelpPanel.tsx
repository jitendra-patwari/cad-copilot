import React, { useEffect, useRef, useState } from 'react';
import { X, HelpCircle, Box, Shield, Layers, MessageSquare } from 'lucide-react';
import { ALL_HELP_SECTIONS, type HelpSection } from './helpContent';

export interface HelpPanelProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly triggerRef?: React.RefObject<HTMLElement | null>;
}

export const HelpPanel: React.FC<HelpPanelProps> = ({ isOpen, onClose, triggerRef }) => {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousActiveElement = useRef<Element | null>(null);
  const [activeSectionId, setActiveSectionId] = useState<string>('geometry_scope');
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const triggerElementRef = useRef(triggerRef?.current);
  triggerElementRef.current = triggerRef?.current;

  useEffect(() => {
    if (!isOpen) return;

    previousActiveElement.current = document.activeElement;
    const triggerElement = triggerElementRef.current;

    const timer = setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 10);

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
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
      if (triggerElement) {
        triggerElement.focus();
      } else if (previousActiveElement.current instanceof HTMLElement) {
        previousActiveElement.current.focus();
      }
    };
  }, [isOpen]);

  if (!isOpen) {
    return null;
  }

  const activeSection: HelpSection =
    ALL_HELP_SECTIONS.find((s) => s.id === activeSectionId) ?? ALL_HELP_SECTIONS[0];

  const getSectionIcon = (id: string) => {
    switch (id) {
      case 'geometry_scope':
        return <Box className="h-4 w-4" aria-hidden="true" />;
      case 'automation_environment':
        return <Shield className="h-4 w-4" aria-hidden="true" />;
      case 'batch_architecture':
        return <Layers className="h-4 w-4" aria-hidden="true" />;
      case 'prompting_guide':
      default:
        return <MessageSquare className="h-4 w-4" aria-hidden="true" />;
    }
  };

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
        aria-labelledby="help-title"
        className="flex h-[560px] w-full max-w-3xl flex-col rounded-xl border border-slate-200 bg-white shadow-xl overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50 text-blue-600 border border-blue-200">
              <HelpCircle className="h-4 w-4" aria-hidden="true" />
            </div>
            <h2 id="help-title" className="text-base font-bold text-slate-800">
              Help
            </h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label="Close help"
            className="cursor-pointer rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-200 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Modal Body: Left Sidebar Tabs + Right Scrollable Content */}
        <div className="flex flex-1 overflow-hidden">
          {/* Vertical Navigation Tabs Sidebar */}
          <nav
            aria-label="Help Navigation"
            className="w-56 shrink-0 border-r border-slate-200 bg-slate-50/80 p-3 space-y-1 overflow-y-auto"
          >
            {ALL_HELP_SECTIONS.map((section) => {
              const isActive = activeSectionId === section.id;
              return (
                <button
                  key={section.id}
                  type="button"
                  onClick={() => setActiveSectionId(section.id)}
                  aria-pressed={isActive}
                  className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-semibold transition-colors cursor-pointer text-left ${
                    isActive
                      ? 'bg-blue-100 text-blue-800 font-bold'
                      : 'text-slate-600 hover:bg-slate-200/60 hover:text-slate-900'
                  }`}
                >
                  <span className={isActive ? 'text-blue-700' : 'text-slate-500'}>
                    {getSectionIcon(section.id)}
                  </span>
                  <span className="truncate">{section.title}</span>
                </button>
              );
            })}
          </nav>

          {/* Section Content Area (Vertical Scroll Only) */}
          <div
            className="flex-1 overflow-y-auto p-6"
            role="region"
            aria-label={
              activeSection.id === 'geometry_scope' ? 'Geometric Capabilities' : activeSection.title
            }
          >
            <div className="space-y-4">
              <div>
                <h3 className="text-sm font-bold text-slate-900">{activeSection.title}</h3>
                <p className="mt-0.5 text-xs text-slate-500">{activeSection.description}</p>
              </div>

              <div className="space-y-3 pt-2">
                {activeSection.items.map((item, idx) => (
                  <div
                    key={idx}
                    className="rounded-lg border border-slate-200 bg-slate-50/70 p-3.5 space-y-1"
                  >
                    <p className="text-xs font-semibold text-slate-800">{item.heading}</p>
                    <p className="text-xs text-slate-600 leading-relaxed">{item.text}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
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
