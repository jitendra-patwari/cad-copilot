import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  isTauriAvailable,
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
} from '../../src/features/generate/native';

describe('generation native adapter', () => {
  beforeEach(() => {
    // Default environment: standard browser without Tauri internals
    // @ts-expect-error - simulating browser environment
    delete globalThis.window.__TAURI_INTERNALS__;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('detects when Tauri runtime is unavailable in browser', () => {
    expect(isTauriAvailable()).toBe(false);
  });

  it('returns fallback snapshot when native is unavailable', async () => {
    const snapshot = await getGenerationSnapshot();
    expect(snapshot).toEqual({
      revision: 0,
      nativeAvailable: false,
      keyConfigured: false,
      output: null,
      run: null,
    });
  });

  it('rejects action commands when native is unavailable', async () => {
    await expect(selectOutputFolder()).rejects.toMatchObject({
      code: 'NATIVE_UNAVAILABLE',
    });

    await expect(setSessionKey('test-key')).rejects.toMatchObject({
      code: 'NATIVE_UNAVAILABLE',
    });

    await expect(
      startGeneration('sel_1', { kind: 'example_plan', exampleId: 'spur_gear' })
    ).rejects.toMatchObject({
      code: 'NATIVE_UNAVAILABLE',
    });

    await expect(cancelGeneration('gen_1')).rejects.toMatchObject({
      code: 'NATIVE_UNAVAILABLE',
    });

    await expect(getGenerationResult('gen_1')).rejects.toMatchObject({
      code: 'NATIVE_UNAVAILABLE',
    });

    await expect(getGenerationPreview('gen_1')).rejects.toMatchObject({
      code: 'NATIVE_UNAVAILABLE',
    });

    await expect(revealGenerationOutput('gen_1')).rejects.toMatchObject({
      code: 'NATIVE_UNAVAILABLE',
    });
  });

  it('returns safe fallback for resolveCloseDecision when native is unavailable', async () => {
    const snapshot = await resolveCloseDecision('gen_1', 'stay');
    expect(snapshot.nativeAvailable).toBe(false);
  });

  it('provides safe no-op unsubscribe when native is unavailable', async () => {
    const listener = vi.fn();
    const unsubscribe = await subscribeGenerationState(listener);
    expect(typeof unsubscribe).toBe('function');
    expect(() => unsubscribe()).not.toThrow();
    expect(listener).not.toHaveBeenCalled();
  });
});
