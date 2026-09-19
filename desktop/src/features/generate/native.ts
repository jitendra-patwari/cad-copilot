import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import type {
  CommandError,
  GenerationInput,
  GenerationOutputSelection,
  GenerationResultResponse,
  GenerationSnapshot,
} from './types';

export function isTauriAvailable(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

const FALLBACK_SNAPSHOT: GenerationSnapshot = {
  revision: 0,
  nativeAvailable: false,
  keyConfigured: false,
  output: null,
  run: null,
};

export async function getGenerationSnapshot(): Promise<GenerationSnapshot> {
  if (!isTauriAvailable()) {
    return FALLBACK_SNAPSHOT;
  }
  return invoke<GenerationSnapshot>('generation_snapshot');
}

export async function selectOutputFolder(): Promise<GenerationOutputSelection> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Native folder selection is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<GenerationOutputSelection>('generation_select_output');
}

export async function setSessionKey(key: string | null): Promise<{ configured: boolean }> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Session key configuration is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<{ configured: boolean }>('generation_set_key', { request: { key } });
}

export async function startGeneration(
  selectionId: string,
  input: GenerationInput
): Promise<GenerationSnapshot> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Model generation requires the desktop application runtime.',
    } satisfies CommandError;
  }
  return invoke<GenerationSnapshot>('generation_start', {
    request: { selectionId, input },
  });
}

export async function cancelGeneration(requestId: string): Promise<GenerationSnapshot> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Generation cancellation is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<GenerationSnapshot>('generation_cancel', {
    request: { requestId },
  });
}

export async function getGenerationResult(requestId: string): Promise<GenerationResultResponse> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Result retrieval is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<GenerationResultResponse>('generation_result', {
    request: { requestId },
  });
}

export async function getGenerationPreview(requestId: string): Promise<Uint8Array> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Preview retrieval is only available in the desktop application.',
    } satisfies CommandError;
  }
  const data = await invoke<ArrayBuffer | number[]>('generation_preview', {
    request: { requestId },
  });
  return data instanceof ArrayBuffer ? new Uint8Array(data) : new Uint8Array(data);
}

export async function revealGenerationOutput(requestId: string): Promise<{ revealed: boolean }> {
  if (!isTauriAvailable()) {
    throw {
      code: 'NATIVE_UNAVAILABLE',
      message: 'Output directory reveal is only available in the desktop application.',
    } satisfies CommandError;
  }
  return invoke<{ revealed: boolean }>('generation_reveal', {
    request: { requestId },
  });
}

export async function resolveCloseDecision(
  requestId: string,
  decision: 'stay' | 'cancel_and_close'
): Promise<GenerationSnapshot> {
  if (!isTauriAvailable()) {
    return FALLBACK_SNAPSHOT;
  }
  return invoke<GenerationSnapshot>('generation_resolve_close', {
    request: { requestId, decision },
  });
}

export async function subscribeGenerationState(
  callback: (snapshot: GenerationSnapshot) => void
): Promise<() => void> {
  if (!isTauriAvailable()) {
    return () => {};
  }
  try {
    return await listen<GenerationSnapshot>('generation-state', (event) => {
      callback(event.payload);
    });
  } catch {
    return () => {};
  }
}
