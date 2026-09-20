import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import type {
  BatchOutputSelection,
  BatchResultResponse,
  BatchRevealResponse,
  BatchSelectSourceResponse,
  BatchSnapshot,
  BatchStartRequest,
  CloseDecision,
  CommandError,
  SourceSelectMode,
} from './types';

export function isTauriAvailable(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

const FALLBACK_SNAPSHOT: BatchSnapshot = {
  revision: 0,
  nativeAvailable: false,
  source: null,
  output: null,
  run: null,
};

export async function getBatchSnapshot(): Promise<BatchSnapshot> {
  if (!isTauriAvailable()) {
    return FALLBACK_SNAPSHOT;
  }
  return invoke<BatchSnapshot>('batch_snapshot');
}

export async function selectBatchSource(
  mode: SourceSelectMode
): Promise<BatchSelectSourceResponse> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Native file selection is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<BatchSelectSourceResponse>('batch_select_source', {
    request: { mode },
  });
}

export async function selectBatchOutput(): Promise<BatchOutputSelection> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Native folder selection is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<BatchOutputSelection>('batch_select_output');
}

export async function startBatch(request: BatchStartRequest): Promise<BatchSnapshot> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Batch operations require the desktop application runtime.',
    } satisfies CommandError;
  }
  return invoke<BatchSnapshot>('batch_start', {
    request,
  });
}

export async function cancelBatch(requestId: string): Promise<BatchSnapshot> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Batch cancellation is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<BatchSnapshot>('batch_cancel', {
    request: { requestId },
  });
}

export async function getBatchResult(requestId: string): Promise<BatchResultResponse> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Batch result retrieval is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<BatchResultResponse>('batch_result', {
    request: { requestId },
  });
}

export async function revealBatchOutput(requestId: string): Promise<BatchRevealResponse> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Output directory reveal is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<BatchRevealResponse>('batch_reveal', {
    request: { requestId },
  });
}

export async function resolveBatchCloseDecision(
  requestId: string,
  decision: CloseDecision
): Promise<BatchSnapshot> {
  if (!isTauriAvailable()) {
    return FALLBACK_SNAPSHOT;
  }
  return invoke<BatchSnapshot>('batch_resolve_close', {
    request: { requestId, decision },
  });
}

export async function subscribeBatchState(
  callback: (snapshot: BatchSnapshot) => void
): Promise<() => void> {
  if (!isTauriAvailable()) {
    return () => {};
  }
  const unlisten = await listen<BatchSnapshot>('batch-state', (event) => {
    callback(event.payload);
  });
  return () => {
    if (isTauriAvailable()) {
      try {
        const res: unknown = unlisten();
        if (res && typeof (res as Promise<void>).catch === 'function') {
          (res as Promise<void>).catch(() => {});
        }
      } catch {
        // Ignore unlisten errors during environment teardown
      }
    }
  };
}
