import { useState, useEffect, useRef, useCallback } from 'react';
import type {
  CommandError,
  GenerationInput,
  GenerationResultResponse,
  GenerationSnapshot,
} from './types';
import {
  getGenerationSnapshot,
  selectOutputFolder,
  setSessionKey,
  startGeneration,
  cancelGeneration,
  getGenerationResult,
  getGenerationPreview,
  revealGenerationOutput,
  resolveCloseDecision,
  subscribeGenerationState,
} from './native';
import { isRunActive, validatePrompt } from './generationState';

export interface UseGenerationReturn {
  snapshot: GenerationSnapshot;
  mode: 'example' | 'prompt';
  setMode: (mode: 'example' | 'prompt') => void;
  prompt: string;
  setPrompt: (prompt: string) => void;
  isKeyEditorOpen: boolean;
  setIsKeyEditorOpen: (open: boolean) => void;
  keyInput: string;
  setKeyInput: (key: string) => void;
  saveKey: (key: string) => Promise<void>;
  clearKey: () => Promise<void>;
  selectFolder: () => Promise<void>;
  startRun: () => Promise<void>;
  cancelRun: () => Promise<void>;
  activeResult: GenerationResultResponse | null;
  previewBlobUrl: string | null;
  clearBlobUrl: () => void;
  revealFolder: () => Promise<void>;
  resolveClose: (decision: 'stay' | 'cancel_and_close') => Promise<void>;
  actionError: CommandError | null;
  clearActionError: () => void;
  clearKeyDraft: () => void;
  isSubmitting: boolean;
  isSubscribed: boolean;
  retrySubscription: () => Promise<void>;
}

const DEFAULT_SNAPSHOT: GenerationSnapshot = {
  revision: 0,
  nativeAvailable: false,
  keyConfigured: false,
  output: null,
  run: null,
};

export function useGeneration(): UseGenerationReturn {
  const [snapshot, setSnapshot] = useState<GenerationSnapshot>(DEFAULT_SNAPSHOT);
  const [mode, setModeState] = useState<'example' | 'prompt'>('example');
  const [prompt, setPrompt] = useState<string>('');
  const [isKeyEditorOpen, setIsKeyEditorOpenState] = useState<boolean>(false);
  const [keyInput, setKeyInput] = useState<string>('');
  const [activeResult, setActiveResult] = useState<GenerationResultResponse | null>(null);
  const [previewBlobUrl, setPreviewBlobUrl] = useState<string | null>(null);
  const [actionError, setActionError] = useState<CommandError | null>(null);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [isSubscribed, setIsSubscribed] = useState<boolean>(false);
  const activeUnsubRef = useRef<(() => void) | null>(null);
  const subscriptionSeqRef = useRef<number>(0);

  const clearKeyDraft = useCallback(() => {
    setKeyInput('');
    setIsKeyEditorOpenState(false);
  }, []);

  const setMode = useCallback(
    (newMode: 'example' | 'prompt') => {
      setModeState(newMode);
      if (newMode === 'prompt' && !snapshot.keyConfigured) {
        setIsKeyEditorOpenState(true);
      } else if (newMode === 'example') {
        clearKeyDraft();
      }
    },
    [snapshot.keyConfigured, clearKeyDraft]
  );

  const setIsKeyEditorOpen = useCallback((open: boolean) => {
    setIsKeyEditorOpenState(open);
    if (!open) {
      setKeyInput('');
    }
  }, []);

  const currentRevisionRef = useRef<number>(0);
  const previewUrlRef = useRef<string | null>(null);
  const inFlightRequestIdRef = useRef<string | null>(null);
  const fetchedRequestIdRef = useRef<string | null>(null);

  const clearBlobUrl = useCallback(() => {
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
      setPreviewBlobUrl(null);
    }
  }, []);

  const clearActionError = useCallback(() => {
    setActionError((prev) => (prev?.code === 'EVENT_SUBSCRIPTION_FAILED' ? prev : null));
  }, []);

  // Update snapshot safely with monotonic revision check
  const applySnapshot = useCallback((newSnap: GenerationSnapshot) => {
    if (newSnap.revision > currentRevisionRef.current) {
      currentRevisionRef.current = newSnap.revision;
      setSnapshot(newSnap);
    }
  }, []);

  const initSubscription = useCallback(async () => {
    const seq = ++subscriptionSeqRef.current;
    if (activeUnsubRef.current) {
      activeUnsubRef.current();
      activeUnsubRef.current = null;
    }
    try {
      const unsub = await subscribeGenerationState((eventSnap) => {
        if (subscriptionSeqRef.current === seq) {
          applySnapshot(eventSnap);
        }
      });
      if (subscriptionSeqRef.current !== seq) {
        unsub();
        return;
      }
      activeUnsubRef.current = unsub;
      setIsSubscribed(true);
      setActionError((prev) =>
        prev?.code === 'EVENT_SUBSCRIPTION_FAILED' || prev?.code === 'SUBSCRIPTION_REQUIRED'
          ? null
          : prev
      );
    } catch {
      if (subscriptionSeqRef.current !== seq) {
        return;
      }
      setIsSubscribed(false);
      setActionError({
        code: 'EVENT_SUBSCRIPTION_FAILED',
        message: 'Failed to subscribe to native generation state events.',
      });
      return;
    }

    try {
      const initialSnap = await getGenerationSnapshot();
      if (subscriptionSeqRef.current === seq) {
        applySnapshot(initialSnap);
      }
    } catch {
      // Handled by default fallback snapshot
    }
  }, [applySnapshot]);

  const cancelActiveSubscription = useCallback(() => {
    subscriptionSeqRef.current += 1;
    if (activeUnsubRef.current) {
      activeUnsubRef.current();
      activeUnsubRef.current = null;
    }
  }, []);

  // Initial mount: load snapshot & subscribe to native generation events
  useEffect(() => {
    initSubscription();

    return () => {
      cancelActiveSubscription();
      clearBlobUrl();
    };
  }, [initSubscription, cancelActiveSubscription, clearBlobUrl]);

  const runState = snapshot.run?.state;
  const runRequestId = snapshot.run?.requestId;

  // When run succeeds, fetch result & preview if not yet retrieved
  useEffect(() => {
    if (runState !== 'succeeded' || !runRequestId) {
      return;
    }

    if (
      fetchedRequestIdRef.current === runRequestId ||
      inFlightRequestIdRef.current === runRequestId
    ) {
      return;
    }

    const reqId = runRequestId;
    inFlightRequestIdRef.current = reqId;

    async function fetchResult() {
      try {
        const result = await getGenerationResult(reqId);
        if (inFlightRequestIdRef.current !== reqId) return;
        fetchedRequestIdRef.current = reqId;
        setActiveResult(result);

        if (result.hasPreview) {
          try {
            const bytes = await getGenerationPreview(reqId);
            if (inFlightRequestIdRef.current !== reqId) return;
            clearBlobUrl();
            const blob = new Blob([bytes], { type: 'image/jpeg' });
            const url = URL.createObjectURL(blob);
            previewUrlRef.current = url;
            setPreviewBlobUrl(url);
          } catch (prevErr) {
            console.warn('Preview image load failed (preview unavailable):', prevErr);
          }
        }
      } catch (err) {
        if (inFlightRequestIdRef.current !== reqId) return;
        console.error('Failed to fetch generation result:', err);
        setActionError(err as CommandError);
      } finally {
        if (inFlightRequestIdRef.current === reqId) {
          inFlightRequestIdRef.current = null;
        }
      }
    }

    fetchResult();
  }, [runState, runRequestId, clearBlobUrl]);

  const selectFolder = useCallback(async () => {
    setActionError(null);
    try {
      const selection = await selectOutputFolder();
      setSnapshot((prev) => ({
        ...prev,
        output: selection,
      }));
    } catch (e) {
      const err = e as CommandError;
      if (err.code !== 'SELECTION_CANCELLED') {
        setActionError(err);
      }
    }
  }, []);

  const saveKey = useCallback(
    async (rawKey: string) => {
      setActionError(null);
      try {
        const res = await setSessionKey(rawKey.trim() || null);
        setSnapshot((prev) => ({
          ...prev,
          keyConfigured: res.configured,
        }));
        setKeyInput('');
        setIsKeyEditorOpen(false);
      } catch (e) {
        setActionError(e as CommandError);
      }
    },
    [setIsKeyEditorOpen]
  );

  const clearKey = useCallback(async () => {
    setActionError(null);
    try {
      const res = await setSessionKey(null);
      setSnapshot((prev) => ({
        ...prev,
        keyConfigured: res.configured,
      }));
      setKeyInput('');
      setIsKeyEditorOpen(false);
    } catch (e) {
      setActionError(e as CommandError);
    }
  }, [setIsKeyEditorOpen]);

  const startRun = useCallback(async () => {
    setActionError(null);
    if (!isSubscribed) {
      setActionError({
        code: 'SUBSCRIPTION_REQUIRED',
        message: 'Native event subscription is unavailable. Please retry after state reconnects.',
      });
      return;
    }

    if (isRunActive(snapshot.run?.state) || snapshot.run?.resultAccess === 'checking') {
      setActionError({
        code: 'RUN_ACTIVE',
        message: 'A generation run is already in progress.',
      });
      return;
    }

    if (!snapshot.output) {
      setActionError({
        code: 'OUTPUT_SELECTION_REQUIRED',
        message: 'Please select an output directory before starting generation.',
      });
      return;
    }

    let input: GenerationInput;
    if (mode === 'example') {
      input = { kind: 'example_plan', exampleId: 'spur_gear' };
    } else {
      const validation = validatePrompt(prompt);
      if (!validation.valid) {
        setActionError({
          code: 'INVALID_INPUT',
          message: validation.error ?? 'Invalid prompt.',
        });
        return;
      }
      if (!snapshot.keyConfigured) {
        setIsKeyEditorOpen(true);
        setActionError({
          code: 'KEY_REQUIRED',
          message: 'Gemini API key is required for prompt-to-CAD generation.',
        });
        return;
      }
      input = { kind: 'prompt_to_cad', prompt: prompt.trim() };
    }

    setIsSubmitting(true);

    try {
      const newSnap = await startGeneration(snapshot.output.selectionId, input);
      clearBlobUrl();
      setActiveResult(null);
      inFlightRequestIdRef.current = null;
      fetchedRequestIdRef.current = null;
      applySnapshot(newSnap);
    } catch (e) {
      setActionError(e as CommandError);
    } finally {
      setIsSubmitting(false);
    }
  }, [
    snapshot.run?.state,
    snapshot.run?.resultAccess,
    snapshot.output,
    snapshot.keyConfigured,
    mode,
    prompt,
    isSubscribed,
    clearBlobUrl,
    applySnapshot,
    setIsKeyEditorOpen,
  ]);

  const cancelRun = useCallback(async () => {
    const run = snapshot.run;
    if (!run) return;
    const reqId = run.requestId;
    if (!reqId) return;

    try {
      const newSnap = await cancelGeneration(reqId);
      applySnapshot(newSnap);
    } catch (e) {
      setActionError(e as CommandError);
    }
  }, [snapshot.run, applySnapshot]);

  const revealFolder = useCallback(async () => {
    const run = snapshot.run;
    if (!run) return;
    const reqId = run.requestId;
    if (!reqId) return;

    try {
      await revealGenerationOutput(reqId);
    } catch (e) {
      setActionError(e as CommandError);
    }
  }, [snapshot.run]);

  const resolveClose = useCallback(
    async (decision: 'stay' | 'cancel_and_close') => {
      const run = snapshot.run;
      if (!run) return;
      const reqId = run.requestId;
      if (!reqId) return;

      try {
        const newSnap = await resolveCloseDecision(reqId, decision);
        applySnapshot(newSnap);
      } catch (e) {
        setActionError(e as CommandError);
      }
    },
    [snapshot.run, applySnapshot]
  );

  return {
    snapshot,
    mode,
    setMode,
    prompt,
    setPrompt,
    isKeyEditorOpen,
    setIsKeyEditorOpen,
    keyInput,
    setKeyInput,
    saveKey,
    clearKey,
    selectFolder,
    startRun,
    cancelRun,
    activeResult,
    previewBlobUrl,
    clearBlobUrl,
    revealFolder,
    resolveClose,
    actionError,
    clearActionError,
    clearKeyDraft,
    isSubmitting,
    isSubscribed,
    retrySubscription: initSubscription,
  };
}
