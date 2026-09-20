import { useState, useEffect, useRef, useCallback } from 'react';
import type {
  BatchResultResponse,
  BatchSnapshot,
  CloseDecision,
  CommandError,
  SourceSelectMode,
} from './types';
import {
  getBatchResult,
  getBatchSnapshot,
  revealBatchOutput,
  resolveBatchCloseDecision,
  selectBatchOutput,
  selectBatchSource,
  startBatch,
  cancelBatch,
  subscribeBatchState,
} from './native';
import { getEligibleCount, isBatchRunActive, isBatchTerminal } from './batchState';

export interface UseBatchReturn {
  snapshot: BatchSnapshot;
  sourceFiles: string[];
  selectedOperation: 'export_3d' | 'publish_drawing';
  setOperation: (operation: 'export_3d' | 'publish_drawing') => void;
  selectedFormats: string[];
  toggleFormat: (formatId: string) => void;
  continueOnError: boolean;
  setContinueOnError: (val: boolean) => void;
  maxFiles: number;
  setMaxFiles: (val: number) => void;
  activeResult: BatchResultResponse | null;
  actionError: CommandError | null;
  clearActionError: () => void;
  isSubmitting: boolean;
  isSubscribed: boolean;
  retrySubscription: () => Promise<void>;
  selectSource: (mode: SourceSelectMode) => Promise<void>;
  selectOutput: () => Promise<void>;
  startRun: () => Promise<void>;
  cancelRun: () => Promise<void>;
  revealOutput: (targetRequestId?: string) => Promise<void>;
  resolveClose: (decision: CloseDecision) => Promise<void>;
}

const DEFAULT_SNAPSHOT: BatchSnapshot = {
  revision: 0,
  nativeAvailable: false,
  source: null,
  output: null,
  run: null,
};

export function useBatch(): UseBatchReturn {
  const [snapshot, setSnapshot] = useState<BatchSnapshot>(DEFAULT_SNAPSHOT);
  const [sourceFiles, setSourceFiles] = useState<string[]>([]);
  const [selectedOperation, setSelectedOperation] = useState<'export_3d' | 'publish_drawing'>(
    'export_3d'
  );
  const [selectedFormats, setSelectedFormats] = useState<string[]>(['step', 'stl', 'parasolid']);
  const [continueOnError, setContinueOnError] = useState<boolean>(true);
  const [maxFiles, setMaxFiles] = useState<number>(100);
  const [activeResult, setActiveResult] = useState<BatchResultResponse | null>(null);
  const [actionError, setActionError] = useState<CommandError | null>(null);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [isSubscribed, setIsSubscribed] = useState<boolean>(false);

  const currentRevisionRef = useRef<number>(0);
  const fetchedResultRequestIdRef = useRef<string | null>(null);
  const currentRunRequestIdRef = useRef<string | null>(null);
  const subscriptionSeqRef = useRef<number>(0);
  const activeUnsubRef = useRef<(() => void) | null>(null);

  const clearActionError = useCallback(() => {
    setActionError(null);
  }, []);

  const clearTransientError = useCallback(() => {
    setActionError((prev) => (prev?.code === 'EVENT_SUBSCRIPTION_FAILED' ? prev : null));
  }, []);

  const applySnapshot = useCallback((newSnap: BatchSnapshot) => {
    if (newSnap.revision > currentRevisionRef.current) {
      currentRevisionRef.current = newSnap.revision;
      setSnapshot(newSnap);
      // If the incoming snapshot has a run with a different request ID than activeResult,
      // clear the stale activeResult immediately so it cannot linger into the new run.
      if (newSnap.run?.requestId) {
        const incomingId = newSnap.run.requestId;
        setActiveResult((prev) => (prev && prev.requestId === incomingId ? prev : null));
      }
    }
  }, []);

  // Mount subscription and initial snapshot load: register listener first, then fetch snapshot
  const initSubscription = useCallback(async () => {
    const seq = ++subscriptionSeqRef.current;
    if (activeUnsubRef.current) {
      activeUnsubRef.current();
      activeUnsubRef.current = null;
    }
    try {
      const unsub = await subscribeBatchState((eventSnap) => {
        if (subscriptionSeqRef.current === seq) {
          applySnapshot(eventSnap);
        }
      });
      // If a newer subscription attempt was started or component was unmounted/cleaned up:
      if (subscriptionSeqRef.current !== seq) {
        unsub();
        return;
      }
      activeUnsubRef.current = unsub;
      setIsSubscribed(true);
      setActionError((prev) => (prev?.code === 'EVENT_SUBSCRIPTION_FAILED' ? null : prev));
    } catch {
      if (subscriptionSeqRef.current !== seq) {
        return;
      }
      setIsSubscribed(false);
      setActionError({
        code: 'EVENT_SUBSCRIPTION_FAILED',
        message: 'Failed to subscribe to native batch state events.',
      });
      return;
    }

    try {
      const initialSnap = await getBatchSnapshot();
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

  useEffect(() => {
    initSubscription();

    return () => {
      cancelActiveSubscription();
    };
  }, [initSubscription, cancelActiveSubscription]);

  // Fetch terminal result automatically when terminal state reached
  useEffect(() => {
    let isMounted = true;
    const run = snapshot.run;
    currentRunRequestIdRef.current = run?.requestId ?? null;

    if (
      run &&
      run.state === 'terminal' &&
      run.requestId &&
      fetchedResultRequestIdRef.current !== run.requestId
    ) {
      const targetRequestId = run.requestId;
      fetchedResultRequestIdRef.current = targetRequestId;
      getBatchResult(targetRequestId)
        .then((res) => {
          if (
            isMounted &&
            currentRunRequestIdRef.current === targetRequestId &&
            res.requestId === targetRequestId
          ) {
            setActiveResult(res);
          }
        })
        .catch((err: CommandError) => {
          if (isMounted && err.code !== 'RESULT_ACCESS_UNAVAILABLE') {
            setActionError(err);
          }
        });
    }

    return () => {
      isMounted = false;
    };
  }, [snapshot.run]);

  const setOperation = useCallback((operation: 'export_3d' | 'publish_drawing') => {
    setSelectedOperation(operation);
    if (operation === 'export_3d') {
      setSelectedFormats(['step', 'stl', 'parasolid']);
    } else {
      setSelectedFormats(['pdf', 'dxf']);
    }
  }, []);

  const toggleFormat = useCallback((formatId: string) => {
    setSelectedFormats((prev) => {
      if (prev.includes(formatId)) {
        return prev.filter((f) => f !== formatId);
      } else {
        return [...prev, formatId];
      }
    });
  }, []);

  const selectSource = useCallback(
    async (mode: SourceSelectMode) => {
      clearTransientError();
      setIsSubmitting(true);
      try {
        const resp = await selectBatchSource(mode);
        setSourceFiles(resp.files);
        // Native state is updated and emitted via batch-state; also fetch snapshot
        const snap = await getBatchSnapshot();
        applySnapshot(snap);
      } catch (err: unknown) {
        const e = err as CommandError;
        if (e?.code !== 'SELECTION_CANCELLED') {
          setActionError(e);
        }
      } finally {
        setIsSubmitting(false);
      }
    },
    [clearTransientError, applySnapshot]
  );

  const selectOutput = useCallback(async () => {
    clearTransientError();
    setIsSubmitting(true);
    try {
      await selectBatchOutput();
      const snap = await getBatchSnapshot();
      applySnapshot(snap);
    } catch (err: unknown) {
      const e = err as CommandError;
      if (e?.code !== 'SELECTION_CANCELLED') {
        setActionError(e);
      }
    } finally {
      setIsSubmitting(false);
    }
  }, [clearTransientError, applySnapshot]);

  const startRun = useCallback(async () => {
    clearActionError();
    if (snapshot.run && isBatchRunActive(snapshot.run.state)) {
      setActionError({
        code: 'RUN_ACTIVE',
        message: 'A batch operation is already in progress.',
      });
      return;
    }
    if (snapshot.run?.cleanup === 'incomplete') {
      setActionError({
        code: 'RUN_ACTIVE',
        message: 'Cannot start a new batch run while prior run cleanup is incomplete.',
      });
      return;
    }
    if (!isSubscribed) {
      setActionError({
        code: 'EVENT_SUBSCRIPTION_FAILED',
        message: 'Cannot start batch run without an active event subscription.',
      });
      return;
    }
    if (!snapshot.source) {
      setActionError({
        code: 'SOURCE_UNAVAILABLE',
        message: 'Please select a source folder or files before starting.',
      });
      return;
    }
    if (!snapshot.output) {
      setActionError({
        code: 'OUTPUT_UNAVAILABLE',
        message: 'Please select an output folder before starting.',
      });
      return;
    }
    if (selectedFormats.length === 0) {
      setActionError({
        code: 'NO_FORMATS_SELECTED',
        message: 'Please select at least one export format.',
      });
      return;
    }
    const eligibleCount = getEligibleCount(snapshot.source, selectedOperation);
    if (eligibleCount === 0) {
      setActionError({
        code: 'NO_ELIGIBLE_FILES',
        message:
          selectedOperation === 'export_3d'
            ? 'No 3D CAD files (.par, .psm, .asm) found in source selection.'
            : 'No drawing files (.dft) found in source selection.',
      });
      return;
    }
    if (eligibleCount > maxFiles) {
      setActionError({
        code: 'TOO_MANY_FILES',
        message: `Eligible file count (${eligibleCount}) exceeds maximum files limit (${maxFiles}).`,
      });
      return;
    }

    setIsSubmitting(true);
    try {
      const snap = await startBatch({
        sourceSelectionId: snapshot.source.selectionId,
        outputSelectionId: snapshot.output.selectionId,
        operation: selectedOperation,
        formats: selectedFormats,
        continueOnError,
        maxFiles,
      });
      // Clear prior results only if the active result does not already belong to this new run
      // (a fast terminal event and result fetch may have already completed while startBatch was in flight)
      const newRequestId = snap.run?.requestId;
      setActiveResult((prev) => (prev && prev.requestId === newRequestId ? prev : null));
      if (fetchedResultRequestIdRef.current !== newRequestId) {
        fetchedResultRequestIdRef.current = null;
      }
      applySnapshot(snap);
    } catch (err: unknown) {
      setActionError(err as CommandError);
    } finally {
      setIsSubmitting(false);
    }
  }, [
    clearActionError,
    isSubscribed,
    snapshot.run,
    snapshot.source,
    snapshot.output,
    selectedFormats,
    selectedOperation,
    continueOnError,
    maxFiles,
    applySnapshot,
  ]);

  const cancelRun = useCallback(async () => {
    const run = snapshot.run;
    if (!run || !isBatchRunActive(run.state)) return;
    clearActionError();
    try {
      const snap = await cancelBatch(run.requestId);
      applySnapshot(snap);
    } catch (err: unknown) {
      setActionError(err as CommandError);
    }
  }, [snapshot.run, clearActionError, applySnapshot]);

  const revealOutput = useCallback(
    async (targetRequestId?: string) => {
      const run = snapshot.run;
      const reqId = targetRequestId ?? run?.requestId;
      if (!reqId) return;
      if (targetRequestId && run && run.requestId !== targetRequestId) {
        return;
      }
      if (run && !isBatchTerminal(run.state)) {
        return;
      }
      clearActionError();
      try {
        await revealBatchOutput(reqId);
      } catch (err: unknown) {
        setActionError(err as CommandError);
      }
    },
    [snapshot.run, clearActionError]
  );

  const resolveClose = useCallback(
    async (decision: CloseDecision) => {
      const run = snapshot.run;
      if (!run) return;
      try {
        const snap = await resolveBatchCloseDecision(run.requestId, decision);
        applySnapshot(snap);
      } catch (err: unknown) {
        setActionError(err as CommandError);
      }
    },
    [snapshot.run, applySnapshot]
  );

  return {
    snapshot,
    sourceFiles,
    selectedOperation,
    setOperation,
    selectedFormats,
    toggleFormat,
    continueOnError,
    setContinueOnError,
    maxFiles,
    setMaxFiles,
    activeResult,
    actionError,
    clearActionError,
    isSubmitting,
    isSubscribed,
    retrySubscription: initSubscription,
    selectSource,
    selectOutput,
    startRun,
    cancelRun,
    revealOutput,
    resolveClose,
  };
}
