import React, { useState, useEffect, useRef } from 'react';
import {
  Sparkles,
  FolderOpen,
  Key,
  Eye,
  EyeOff,
  CheckCircle2,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Cog,
  FileText,
} from 'lucide-react';
import type { CommandError, GenerationOutputSelection } from './types';

interface GenerateFormProps {
  mode: 'example' | 'prompt';
  setMode: (mode: 'example' | 'prompt') => void;
  prompt: string;
  setPrompt: (prompt: string) => void;
  output: GenerationOutputSelection | null;
  selectFolder: () => Promise<void>;
  keyConfigured: boolean;
  isKeyEditorOpen: boolean;
  setIsKeyEditorOpen: (open: boolean) => void;
  keyInput: string;
  setKeyInput: (key: string) => void;
  saveKey: (key: string) => Promise<void>;
  clearKey: () => Promise<void>;
  startRun: () => Promise<void>;
  isRunActive: boolean;
  isSubmitting: boolean;
  actionError: CommandError | null;
  clearActionError: () => void;
  isSubscribed?: boolean;
  retrySubscription?: () => Promise<void>;
}

export const GenerateForm: React.FC<GenerateFormProps> = ({
  mode,
  setMode,
  prompt,
  setPrompt,
  output,
  selectFolder,
  keyConfigured,
  isKeyEditorOpen,
  setIsKeyEditorOpen,
  keyInput,
  setKeyInput,
  saveKey,
  clearKey,
  startRun,
  isRunActive,
  isSubmitting,
  actionError,
  clearActionError,
  isSubscribed = true,
  retrySubscription,
}) => {
  const [showKeyText, setShowKeyText] = useState(false);
  const keyInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isKeyEditorOpen) {
      setShowKeyText(false);
    }
  }, [isKeyEditorOpen]);

  const openAndFocusKeyEditor = () => {
    setIsKeyEditorOpen(true);
    setTimeout(() => {
      keyInputRef.current?.focus();
    }, 50);
  };

  const promptLength = Array.from(prompt).length;
  const isPromptValid = mode === 'example' || (prompt.trim().length > 0 && promptLength <= 8000);
  const canSubmit =
    isSubscribed &&
    !isRunActive &&
    !isSubmitting &&
    output !== null &&
    isPromptValid &&
    (mode === 'example' || keyConfigured);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (canSubmit) {
      startRun();
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Mode Selector Tabs */}
      <div className="flex rounded-lg border border-slate-200 bg-slate-100 p-1">
        <button
          type="button"
          onClick={() => {
            setMode('example');
            clearActionError();
          }}
          disabled={isRunActive}
          className={`flex flex-1 items-center justify-center gap-2 rounded-md py-2 text-xs font-semibold transition-colors ${
            mode === 'example'
              ? 'bg-white text-blue-700 shadow-sm'
              : 'text-slate-600 hover:text-slate-900 disabled:opacity-50'
          }`}
        >
          <Cog className="h-4 w-4" aria-hidden="true" />
          Conceptual Spur Gear (Example)
          <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-800">
            Key-free
          </span>
        </button>

        <button
          type="button"
          onClick={() => {
            setMode('prompt');
            clearActionError();
          }}
          disabled={isRunActive}
          className={`flex flex-1 items-center justify-center gap-2 rounded-md py-2 text-xs font-semibold transition-colors ${
            mode === 'prompt'
              ? 'bg-white text-blue-700 shadow-sm'
              : 'text-slate-600 hover:text-slate-900 disabled:opacity-50'
          }`}
        >
          <FileText className="h-4 w-4" aria-hidden="true" />
          Prompt to CAD (Gemini)
        </button>
      </div>

      {/* Mode Content */}
      {mode === 'example' ? (
        <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
          <div className="flex items-start gap-3">
            <Cog className="mt-0.5 h-5 w-5 shrink-0 text-blue-600" aria-hidden="true" />
            <div className="space-y-1">
              <h3 className="text-xs font-semibold text-slate-900">Canonical 24-Tooth Spur Gear</h3>
              <p className="text-xs leading-relaxed text-slate-600">
                Executes a fully deterministic parametric CAD plan generating a spur gear with 24
                teeth, 20° pressure angle, module 2.0 (48mm pitch diameter), and central shaft hole.
                Runs locally in Siemens Solid Edge with zero API keys or external network requests.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <div className="flex items-center justify-between pb-1.5">
              <label htmlFor="prompt-input" className="text-xs font-semibold text-slate-800">
                Geometric Specification Prompt
              </label>
              <div className="flex items-center gap-2">
                {!keyConfigured && (
                  <button
                    type="button"
                    onClick={openAndFocusKeyEditor}
                    disabled={isRunActive}
                    className="text-[11px] font-semibold text-blue-600 hover:text-blue-800 hover:underline"
                  >
                    Configure Gemini
                  </button>
                )}
                <span
                  className={`text-[11px] font-medium ${
                    promptLength > 8000 ? 'text-rose-600' : 'text-slate-500'
                  }`}
                >
                  {promptLength.toLocaleString()} / 8,000 characters
                </span>
              </div>
            </div>
            <textarea
              id="prompt-input"
              rows={4}
              value={prompt}
              onChange={(e) => {
                setPrompt(e.target.value);
                clearActionError();
              }}
              disabled={isRunActive}
              placeholder="e.g., Create an aluminum mounting bracket with 4 mounting holes and a central clearance slot..."
              className="w-full rounded-lg border border-slate-300 p-3 text-xs text-slate-900 placeholder:text-slate-400 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600 disabled:bg-slate-100 disabled:text-slate-500"
            />
          </div>

          {/* Inline Expandable Gemini Key Manager */}
          <div className="rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm">
            <button
              type="button"
              onClick={() => setIsKeyEditorOpen(!isKeyEditorOpen)}
              disabled={isRunActive}
              className="flex w-full items-center justify-between text-left focus:outline-none"
              aria-expanded={isKeyEditorOpen}
            >
              <div className="flex items-center gap-2">
                <Key className="h-4 w-4 text-slate-600" aria-hidden="true" />
                <span className="text-xs font-semibold text-slate-800">
                  Google Gemini API Configuration
                </span>
                {keyConfigured ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 border border-emerald-200">
                    <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                    Configured (Session memory)
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700 border border-amber-200">
                    <AlertCircle className="h-3 w-3" aria-hidden="true" />
                    Key required for prompt
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {!keyConfigured && !isKeyEditorOpen && (
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => {
                      e.stopPropagation();
                      openAndFocusKeyEditor();
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.stopPropagation();
                        openAndFocusKeyEditor();
                      }
                    }}
                    className="rounded bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-700 border border-blue-200 hover:bg-blue-100 cursor-pointer"
                  >
                    Configure Gemini
                  </span>
                )}
                {isKeyEditorOpen ? (
                  <ChevronUp className="h-4 w-4 text-slate-500" />
                ) : (
                  <ChevronDown className="h-4 w-4 text-slate-500" />
                )}
              </div>
            </button>

            {isKeyEditorOpen && (
              <div className="mt-3 space-y-3 border-t border-slate-100 pt-3">
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <label htmlFor="gemini-api-key-input" className="sr-only">
                      Google Gemini API Key
                    </label>
                    <input
                      ref={keyInputRef}
                      id="gemini-api-key-input"
                      type={showKeyText ? 'text' : 'password'}
                      value={keyInput}
                      onChange={(e) => setKeyInput(e.target.value)}
                      autoComplete="off"
                      spellCheck={false}
                      placeholder={
                        keyConfigured ? 'Replace session key...' : 'Enter Gemini API key...'
                      }
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 pr-9 text-xs text-slate-900 placeholder:text-slate-400 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600"
                    />
                    <button
                      type="button"
                      onClick={() => setShowKeyText(!showKeyText)}
                      className="absolute right-2.5 top-2.5 text-slate-400 hover:text-slate-600"
                      aria-label={showKeyText ? 'Hide key' : 'Show key'}
                    >
                      {showKeyText ? (
                        <EyeOff className="h-3.5 w-3.5" />
                      ) : (
                        <Eye className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>

                  <button
                    type="button"
                    onClick={() => saveKey(keyInput)}
                    disabled={!keyInput.trim()}
                    className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    Save
                  </button>

                  <button
                    type="button"
                    onClick={() => setIsKeyEditorOpen(false)}
                    className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                  >
                    Cancel
                  </button>

                  {keyConfigured && (
                    <button
                      type="button"
                      onClick={() => clearKey()}
                      className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                    >
                      Clear
                    </button>
                  )}
                </div>

                <p className="text-[11px] leading-relaxed text-slate-500">
                  <span className="font-semibold text-slate-700">Privacy Notice:</span> Your prompt
                  is transmitted to Google Gemini to formulate geometric CAD instructions. CAD model
                  generation, Siemens Solid Edge execution, and exported artifacts remain 100% local
                  on your machine. The key is kept strictly in-memory and cleared when closed.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Output Directory Selection Row */}
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-0.5">
            <span className="text-xs font-semibold text-slate-800">Target Output Directory</span>
            <div className="flex items-center gap-1.5 text-xs text-slate-500">
              <FolderOpen className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
              {output ? (
                <span className="font-mono text-slate-800 break-all">{output.displayPath}</span>
              ) : (
                <span className="italic text-slate-400">No output directory selected</span>
              )}
            </div>
          </div>

          <button
            type="button"
            onClick={selectFolder}
            disabled={isRunActive}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50"
          >
            <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
            {output ? 'Change Directory' : 'Choose Directory'}
          </button>
        </div>
      </div>

      {/* Action Error Banner */}
      {actionError && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" aria-hidden="true" />
              <div className="space-y-0.5">
                <p className="font-semibold">{actionError.code}</p>
                <p>{actionError.message}</p>
              </div>
            </div>
            {actionError.code === 'EVENT_SUBSCRIPTION_FAILED' && retrySubscription && (
              <button
                type="button"
                onClick={retrySubscription}
                className="cursor-pointer text-xs font-semibold text-rose-700 hover:text-rose-900 underline whitespace-nowrap"
              >
                Retry Connection
              </button>
            )}
          </div>
        </div>
      )}

      {/* Disconnected Indicator */}
      {!isSubscribed && (
        <div className="flex items-center justify-between rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <span>Event listener disconnected. Runs are paused until connection recovers.</span>
          {retrySubscription && (
            <button
              type="button"
              onClick={retrySubscription}
              className="cursor-pointer font-semibold text-amber-900 hover:underline"
            >
              Reconnect
            </button>
          )}
        </div>
      )}

      {/* Submit Action */}
      <div className="flex justify-end pt-1">
        <button
          type="submit"
          disabled={!canSubmit}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Sparkles className="h-4 w-4" aria-hidden="true" />
          {isSubmitting ? 'Initiating Generation...' : 'Run CAD Generation'}
        </button>
      </div>
    </form>
  );
};
