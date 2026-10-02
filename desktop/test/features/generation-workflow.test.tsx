import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, renderHook, act, waitFor } from '@testing-library/react';
import { mockIPC, clearMocks } from '@tauri-apps/api/mocks';
import {
  validatePrompt,
  formatBytes,
  isRunActive,
  isTerminalState,
} from '../../src/features/generate/generationState';
import { GenerateForm } from '../../src/features/generate/GenerateForm';
import { GeneratePage } from '../../src/features/generate/GeneratePage';
import { GenerationProgress } from '../../src/features/generate/GenerationProgress';
import { GenerationResult } from '../../src/features/generate/GenerationResult';
import { useGeneration } from '../../src/features/generate/useGeneration';
import type { UseGenerationReturn } from '../../src/features/generate/useGeneration';
import type {
  GenerationResultResponse,
  GenerationRunSnapshot,
  GenerationSnapshot,
} from '../../src/features/generate/types';

describe('generationState unit tests', () => {
  it('validates prompts according to code point and byte bounds', () => {
    expect(validatePrompt('').valid).toBe(false);
    expect(validatePrompt('   ').valid).toBe(false);

    const normal = validatePrompt('Create a spur gear with 24 teeth.');
    expect(normal.valid).toBe(true);

    // 8,000 code points is valid
    const maxPrompt = 'a'.repeat(8000);
    expect(validatePrompt(maxPrompt).valid).toBe(true);

    // 8,001 code points is rejected
    const overMax = 'a'.repeat(8001);
    const overRes = validatePrompt(overMax);
    expect(overRes.valid).toBe(false);
    expect(overRes.error).toContain('8,000 characters');

    // Supplementary character outside BMP (surrogate pairs in UTF-16, single code points)
    // '🚀' (U+1F680): .length is 2 UTF-16 code units, but 1 Unicode code point.
    const supplementaryPrompt = '🚀'.repeat(8000);
    expect(validatePrompt(supplementaryPrompt).valid).toBe(true);

    // 8,001 supplementary code points (16,002 UTF-16 code units) is rejected
    const overSupplementary = '🚀'.repeat(8001);
    const overSuppRes = validatePrompt(overSupplementary);
    expect(overSuppRes.valid).toBe(false);
    expect(overSuppRes.error).toContain('8,000 characters');
  });

  it('formats byte counts cleanly', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1024)).toBe('1.0 KB');
    expect(formatBytes(1048576)).toBe('1.0 MB');
    expect(formatBytes(5242880)).toBe('5.0 MB');
  });

  it('correctly identifies active and terminal states', () => {
    expect(isRunActive('starting')).toBe(true);
    expect(isRunActive('running')).toBe(true);
    expect(isRunActive('cancelling')).toBe(true);
    expect(isRunActive('succeeded')).toBe(false);
    expect(isRunActive('failed')).toBe(false);

    expect(isTerminalState('succeeded')).toBe(true);
    expect(isTerminalState('rejected')).toBe(true);
    expect(isTerminalState('failed')).toBe(true);
    expect(isTerminalState('cancelled')).toBe(true);
    expect(isTerminalState('running')).toBe(false);
  });
});

describe('GenerateForm component', () => {
  it('renders Example mode by default with disabled submit when no output is selected', () => {
    const startRunMock = vi.fn();
    const selectFolderMock = vi.fn();

    render(
      <GenerateForm
        keepPartOpen={false}
        setKeepPartOpen={vi.fn()}
        mode="example"
        setMode={vi.fn()}
        prompt=""
        setPrompt={vi.fn()}
        output={null}
        selectFolder={selectFolderMock}
        keyConfigured={false}
        isKeyEditorOpen={false}
        setIsKeyEditorOpen={vi.fn()}
        keyInput=""
        setKeyInput={vi.fn()}
        saveKey={vi.fn()}
        clearKey={vi.fn()}
        startRun={startRunMock}
        isRunActive={false}
        isSubmitting={false}
        actionError={null}
        clearActionError={vi.fn()}
      />
    );

    expect(screen.getByText('Conceptual 24-Tooth Spur Gear')).toBeInTheDocument();
    expect(screen.getByText('No output directory selected')).toBeInTheDocument();

    const submitBtn = screen.getByRole('button', { name: /run cad generation/i });
    expect(submitBtn).toBeDisabled();

    const chooseDirBtn = screen.getByRole('button', { name: /choose directory/i });
    fireEvent.click(chooseDirBtn);
    expect(selectFolderMock).toHaveBeenCalledTimes(1);
  });

  it('switches to Prompt mode and toggles inline key editor', () => {
    const setModeMock = vi.fn();
    const setIsKeyEditorOpenMock = vi.fn();
    const setPromptMock = vi.fn();

    const { rerender } = render(
      <GenerateForm
        keepPartOpen={false}
        setKeepPartOpen={vi.fn()}
        mode="example"
        setMode={setModeMock}
        prompt="Design an L-bracket"
        setPrompt={setPromptMock}
        output={{ selectionId: 'sel_1', displayPath: 'C:\\test_output' }}
        selectFolder={vi.fn()}
        keyConfigured={false}
        isKeyEditorOpen={false}
        setIsKeyEditorOpen={setIsKeyEditorOpenMock}
        keyInput=""
        setKeyInput={vi.fn()}
        saveKey={vi.fn()}
        clearKey={vi.fn()}
        startRun={vi.fn()}
        isRunActive={false}
        isSubmitting={false}
        actionError={null}
        clearActionError={vi.fn()}
      />
    );

    // Click the Prompt to CAD tab button to trigger mode switch
    const promptTabBtn = screen.getByRole('button', { name: /prompt to cad/i });
    fireEvent.click(promptTabBtn);
    expect(setModeMock).toHaveBeenCalledWith('prompt');

    // Rerender with mode="prompt" and key editor open
    rerender(
      <GenerateForm
        keepPartOpen={false}
        setKeepPartOpen={vi.fn()}
        mode="prompt"
        setMode={setModeMock}
        prompt="Design an L-bracket"
        setPrompt={setPromptMock}
        output={{ selectionId: 'sel_1', displayPath: 'C:\\test_output' }}
        selectFolder={vi.fn()}
        keyConfigured={false}
        isKeyEditorOpen={true}
        setIsKeyEditorOpen={setIsKeyEditorOpenMock}
        keyInput=""
        setKeyInput={vi.fn()}
        saveKey={vi.fn()}
        clearKey={vi.fn()}
        startRun={vi.fn()}
        isRunActive={false}
        isSubmitting={false}
        actionError={null}
        clearActionError={vi.fn()}
      />
    );

    expect(screen.getByLabelText(/geometric specification prompt/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/enter gemini api key/i)).toBeInTheDocument();
    expect(screen.getByText(/key required for prompt/i)).toBeInTheDocument();

    const cancelBtn = screen.getByRole('button', { name: /cancel/i });
    fireEvent.click(cancelBtn);
    expect(setIsKeyEditorOpenMock).toHaveBeenCalledWith(false);
  });

  it('disables submit and renders reconnect control when native event subscription is disconnected', () => {
    const retryMock = vi.fn();

    render(
      <GenerateForm
        keepPartOpen={false}
        setKeepPartOpen={vi.fn()}
        mode="example"
        setMode={vi.fn()}
        prompt=""
        setPrompt={vi.fn()}
        output={{ selectionId: 'sel_1', displayPath: 'C:\\test_output' }}
        selectFolder={vi.fn()}
        keyConfigured={false}
        isKeyEditorOpen={false}
        setIsKeyEditorOpen={vi.fn()}
        keyInput=""
        setKeyInput={vi.fn()}
        saveKey={vi.fn()}
        clearKey={vi.fn()}
        startRun={vi.fn()}
        isRunActive={false}
        isSubmitting={false}
        actionError={{
          code: 'EVENT_SUBSCRIPTION_FAILED',
          message: 'Failed to subscribe to native generation state events.',
        }}
        clearActionError={vi.fn()}
        isSubscribed={false}
        retrySubscription={retryMock}
      />
    );

    // Verify submit button is disabled
    const submitBtn = screen.getByRole('button', { name: /run cad generation/i });
    expect(submitBtn).toBeDisabled();

    // Verify disconnected warning banner
    expect(screen.getByText(/event listener disconnected/i)).toBeInTheDocument();

    // Verify retry connection button in error banner
    const retryBtn = screen.getByRole('button', { name: /retry connection/i });
    fireEvent.click(retryBtn);
    expect(retryMock).toHaveBeenCalledTimes(1);

    // Verify reconnect button in warning banner
    const reconnectBtn = screen.getByRole('button', { name: /reconnect/i });
    fireEvent.click(reconnectBtn);
    expect(retryMock).toHaveBeenCalledTimes(2);
  });

  it('recovers from a rejected native listener via retrySubscription in useGeneration', async () => {
    let shouldFailListen = true;
    const eventListenerIds: Record<string, number[]> = {};

    const invokeSpy = vi.fn(async (cmd: string, payload?: unknown) => {
      const p = payload as Record<string, unknown> | undefined;
      if (cmd === 'plugin:event|listen') {
        if (shouldFailListen) {
          throw new Error('IPC listener permission denied');
        }
        const event = p?.event as string;
        const handlerId = p?.handler as number;
        if (!eventListenerIds[event]) {
          eventListenerIds[event] = [];
        }
        eventListenerIds[event].push(handlerId);
        return Math.floor(Math.random() * 1000);
      }
      if (cmd === 'plugin:event|unlisten') {
        return;
      }
      if (cmd === 'generation_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          keyConfigured: false,
          output: { selectionId: 'sel_1', displayPath: 'C:\\test_out' },
          run: null,
        } satisfies GenerationSnapshot;
      }
      if (cmd === 'generation_start') {
        return {
          revision: 2,
          nativeAvailable: true,
          keyConfigured: false,
          output: null,
          run: {
            requestId: 'gen_test_1',
            state: 'running',
            engineStatus: null,
            phase: 'generation_started',
            reason: null,
            resultAccess: 'none',
            cleanup: 'no_failure_observed',
            closeRequested: false,
            warnings: [],
          },
        } satisfies GenerationSnapshot;
      }
      return;
    });

    mockIPC(invokeSpy);

    try {
      const { result } = renderHook(() => useGeneration());

      // 1. Initial subscription fails
      await waitFor(() => {
        expect(result.current.isSubscribed).toBe(false);
        expect(result.current.actionError?.code).toBe('EVENT_SUBSCRIPTION_FAILED');
      });

      // 2. Calling startRun while unsubscribed is blocked
      await act(async () => {
        await result.current.startRun();
      });
      expect(invokeSpy).not.toHaveBeenCalledWith('generation_start', expect.anything());
      expect(result.current.actionError?.code).toBe('SUBSCRIPTION_REQUIRED');

      // 3. Recover via retrySubscription
      shouldFailListen = false;
      await act(async () => {
        await result.current.retrySubscription();
      });

      await waitFor(() => {
        expect(result.current.isSubscribed).toBe(true);
        expect(result.current.actionError).toBeNull();
      });

      // 4. Now startRun succeeds
      await act(async () => {
        await result.current.startRun();
      });
      expect(invokeSpy).toHaveBeenCalledWith('generation_start', expect.anything());
    } finally {
      clearMocks();
    }
  });
});

describe('GenerationProgress component', () => {
  it('displays active phases and cancellation action', () => {
    const cancelMock = vi.fn();
    const run: GenerationRunSnapshot = {
      requestId: 'gen_test_123',
      state: 'running',
      phase: 'generation_started',
      engineStatus: 'accepted',
      reason: null,
      resultAccess: 'none',
      cleanup: 'not_started',
      closeRequested: false,
      warnings: [],
    };

    render(<GenerationProgress run={run} cancelRun={cancelMock} />);

    expect(screen.getByText('Active Generation Run')).toBeInTheDocument();
    expect(screen.getByText('gen_test_123')).toBeInTheDocument();
    expect(screen.getByText('Generating Model')).toBeInTheDocument();

    const cancelBtn = screen.getByRole('button', { name: /cancel generation/i });
    fireEvent.click(cancelBtn);
    expect(cancelMock).toHaveBeenCalledTimes(1);
  });
});

describe('GenerationResult component', () => {
  it('renders succeeded outcome and published artifacts', () => {
    const revealMock = vi.fn();
    const reopenWarning =
      'CAD files were generated, but the saved part could not be left open in Solid Edge.';
    const run: GenerationRunSnapshot = {
      requestId: 'gen_succ_456',
      state: 'succeeded',
      phase: 'response_ready',
      engineStatus: 'accepted',
      reason: null,
      resultAccess: 'ready',
      cleanup: 'no_failure_observed',
      closeRequested: false,
      warnings: ['Mesh density warning', reopenWarning],
    };

    const result: GenerationResultResponse = {
      requestId: 'gen_succ_456',
      runFolder: 'C:\\output\\gen_succ_456',
      artifacts: [
        {
          format: 'par',
          filename: 'model.par',
          path: 'C:\\output\\gen_succ_456\\model.par',
          sizeBytes: 102400,
        },
        {
          format: 'step',
          filename: 'model.step',
          path: 'C:\\output\\gen_succ_456\\model.step',
          sizeBytes: 51200,
        },
        {
          format: 'stl',
          filename: 'model.stl',
          path: 'C:\\output\\gen_succ_456\\model.stl',
          sizeBytes: 25600,
        },
      ],
      hasPreview: false,
      manifestSummary: {
        schemaVersion: 'cad_copilot.run_manifest.v1',
        provenanceKind: 'example_plan',
        sourceId: 'spur_gear',
        cadRuntimeVersion: '226.00.00.106',
        operationsExecuted: 1,
        planSha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
        promptSha256: null,
        warnings: ['Mesh density warning'],
        diagnostics: [
          {
            severity: 'info',
            code: 'INFO_CAD_RUNTIME',
            message: 'Solid Edge runtime verified in normal state',
          },
        ],
      },
    };

    const { rerender } = render(
      <GenerationResult
        run={{ ...run, resultAccess: 'checking' }}
        result={null}
        previewBlobUrl={null}
        revealFolder={revealMock}
      />
    );
    expect(screen.getByText(reopenWarning)).toBeInTheDocument();
    rerender(
      <GenerationResult run={run} result={result} previewBlobUrl={null} revealFolder={revealMock} />
    );
    expect(screen.getByText(reopenWarning)).toBeInTheDocument();

    expect(screen.getByText('CAD Model Successfully Generated')).toBeInTheDocument();
    expect(screen.getByTitle('model.par')).toBeInTheDocument();
    expect(screen.getByTitle('model.step')).toBeInTheDocument();
    expect(screen.getByTitle('model.stl')).toBeInTheDocument();
    expect(screen.getByTestId('artifact-par')).toBeInTheDocument();
    expect(screen.getByTestId('artifact-step')).toBeInTheDocument();
    expect(screen.getByTestId('artifact-stl')).toBeInTheDocument();
    expect(screen.getByText('Mesh density warning')).toBeInTheDocument();

    const showFolderBtn = screen.getByRole('button', { name: /show in folder/i });
    fireEvent.click(showFolderBtn);
    expect(revealMock).toHaveBeenCalledTimes(1);
  });

  it('disables submit and displays warning when opposite workflow is busy in GenerateForm', () => {
    render(
      <GenerateForm
        keepPartOpen={false}
        setKeepPartOpen={vi.fn()}
        mode="example"
        setMode={vi.fn()}
        prompt=""
        setPrompt={vi.fn()}
        output={{ selectionId: 'out-1', displayPath: 'C:\\exports' }}
        selectFolder={vi.fn()}
        keyConfigured={false}
        isKeyEditorOpen={false}
        setIsKeyEditorOpen={vi.fn()}
        keyInput=""
        setKeyInput={vi.fn()}
        saveKey={vi.fn()}
        clearKey={vi.fn()}
        startRun={vi.fn()}
        isRunActive={false}
        isSubmitting={false}
        actionError={null}
        clearActionError={vi.fn()}
        isOtherBusy={true}
      />
    );

    const submitBtn = screen.getByRole('button', { name: /run cad generation/i });
    expect(submitBtn).toBeDisabled();
    expect(
      screen.getByText(/batch conversion operation is currently running/i)
    ).toBeInTheDocument();
  });

  it('renders role="alert" on action error banner in GenerateForm', () => {
    render(
      <GenerateForm
        keepPartOpen={false}
        setKeepPartOpen={vi.fn()}
        mode="example"
        setMode={vi.fn()}
        prompt=""
        setPrompt={vi.fn()}
        output={{ selectionId: 'out-1', displayPath: 'C:\\exports' }}
        selectFolder={vi.fn()}
        keyConfigured={false}
        isKeyEditorOpen={false}
        setIsKeyEditorOpen={vi.fn()}
        keyInput=""
        setKeyInput={vi.fn()}
        saveKey={vi.fn()}
        clearKey={vi.fn()}
        startRun={vi.fn()}
        isRunActive={false}
        isSubmitting={false}
        actionError={{ code: 'FAILED_OPERATION', message: 'Test error message' }}
        clearActionError={vi.fn()}
      />
    );

    const alertBanner = screen.getByRole('alert');
    expect(alertBanner).toBeInTheDocument();
    expect(alertBanner).toHaveTextContent('FAILED_OPERATION');
    expect(alertBanner).toHaveTextContent('Test error message');
  });

  it('preserves final result when fast run delivers terminal event before startGeneration resolves in useGeneration', async () => {
    let resolveStartGeneration!: (snap: unknown) => void;
    const startPromise = new Promise((resolve) => {
      resolveStartGeneration = resolve;
    });

    const eventListenerIds: Record<string, number[]> = {};
    const invokeSpy = vi.fn(async (cmd: string, payload?: unknown) => {
      const p = payload as Record<string, unknown> | undefined;
      if (cmd === 'plugin:event|listen') {
        const event = p?.event as string;
        const handlerId = p?.handler as number;
        if (!eventListenerIds[event]) {
          eventListenerIds[event] = [];
        }
        eventListenerIds[event].push(handlerId);
        return Math.floor(Math.random() * 1000);
      }
      if (cmd === 'plugin:event|unlisten') {
        return;
      }
      if (cmd === 'generation_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          keyConfigured: true,
          output: { selectionId: 'out-fast', displayPath: 'C:\\exports' },
          run: null,
        } satisfies GenerationSnapshot;
      }
      if (cmd === 'generation_start') {
        await startPromise;
        return {
          revision: 2,
          nativeAvailable: true,
          keyConfigured: true,
          output: { selectionId: 'out-fast', displayPath: 'C:\\exports' },
          run: {
            requestId: 'gen-fast-001',
            state: 'running',
            engineStatus: 'accepted',
            phase: 'generation_started',
            reason: null,
            resultAccess: 'none',
            cleanup: 'no_failure_observed',
            closeRequested: false,
            warnings: [],
          },
        } satisfies GenerationSnapshot;
      }
      if (cmd === 'generation_result') {
        return {
          requestId: 'gen-fast-001',
          runFolder: 'C:\\exports\\gen-fast-001',
          artifacts: [],
          hasPreview: false,
          manifestSummary: {
            schemaVersion: 'cad_copilot.run_manifest.v1',
            provenanceKind: 'example_plan',
            sourceId: 'spur_gear',
            cadRuntimeVersion: '226.00.00.106',
            operationsExecuted: 1,
            planSha256: 'abc',
            promptSha256: null,
            warnings: [],
            diagnostics: [],
          },
        };
      }
      return;
    });

    mockIPC(invokeSpy);

    try {
      const { result } = renderHook(() => useGeneration());

      await waitFor(() => {
        expect(result.current.isSubscribed).toBe(true);
      });

      // Start run in flight
      act(() => result.current.setKeepPartOpen(true));
      let runPromise: Promise<void>;
      act(() => {
        runPromise = result.current.startRun();
      });
      expect(invokeSpy).toHaveBeenCalledWith(
        'generation_start',
        expect.objectContaining({
          request: expect.objectContaining({ keepPartOpen: true }),
        })
      );

      // Deliver terminal event while startGeneration is still in flight
      await act(async () => {
        const ids = eventListenerIds['generation-state'] || [];
        ids.forEach((id) => {
          const cb = (window as unknown as Record<string, (e: unknown) => void>)[`_${id}`];
          cb?.({
            event: 'generation-state',
            id,
            payload: {
              revision: 3,
              nativeAvailable: true,
              keyConfigured: true,
              output: { selectionId: 'out-fast', displayPath: 'C:\\exports' },
              run: {
                requestId: 'gen-fast-001',
                state: 'succeeded',
                engineStatus: 'accepted',
                phase: 'response_ready',
                reason: null,
                resultAccess: 'ready',
                cleanup: 'no_failure_observed',
                closeRequested: false,
                warnings: [],
              },
            } satisfies GenerationSnapshot,
          });
        });
      });

      // Await result retrieval from the terminal event
      await waitFor(() => {
        expect(result.current.activeResult?.requestId).toBe('gen-fast-001');
      });

      // Now resolve startGeneration with revision 2 snapshot
      await act(async () => {
        resolveStartGeneration({
          revision: 2,
          nativeAvailable: true,
          keyConfigured: true,
          output: { selectionId: 'out-fast', displayPath: 'C:\\exports' },
          run: {
            requestId: 'gen-fast-001',
            state: 'running',
            engineStatus: 'accepted',
            phase: 'generation_started',
            reason: null,
            resultAccess: 'none',
            cleanup: 'no_failure_observed',
            closeRequested: false,
            warnings: [],
          },
        } satisfies GenerationSnapshot);
        await runPromise;
      });

      // CRITICAL: The fast activeResult must NOT be wiped out!
      expect(result.current.activeResult?.requestId).toBe('gen-fast-001');
    } finally {
      clearMocks();
    }
  });

  it('preserves pending result fetch when startGeneration resolves while result fetch is in flight', async () => {
    let resolveGetResult!: (res: GenerationResultResponse) => void;
    const getResultPromise = new Promise<GenerationResultResponse>((resolve) => {
      resolveGetResult = resolve;
    });

    let resolveStartGeneration!: (snap: unknown) => void;
    const startPromise = new Promise((resolve) => {
      resolveStartGeneration = resolve;
    });

    const eventListenerIds: Record<string, number[]> = {};
    const invokeSpy = vi.fn(async (cmd: string, payload?: unknown) => {
      const p = payload as Record<string, unknown> | undefined;
      if (cmd === 'plugin:event|listen') {
        const event = p?.event as string;
        const handlerId = p?.handler as number;
        if (!eventListenerIds[event]) {
          eventListenerIds[event] = [];
        }
        eventListenerIds[event].push(handlerId);
        return Math.floor(Math.random() * 1000);
      }
      if (cmd === 'plugin:event|unlisten') {
        return;
      }
      if (cmd === 'generation_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          keyConfigured: true,
          output: { selectionId: 'out-fast', displayPath: 'C:\\exports' },
          run: null,
        } satisfies GenerationSnapshot;
      }
      if (cmd === 'generation_start') {
        await startPromise;
        return {
          revision: 2,
          nativeAvailable: true,
          keyConfigured: true,
          output: { selectionId: 'out-fast', displayPath: 'C:\\exports' },
          run: {
            requestId: 'gen-pending-001',
            state: 'running',
            engineStatus: 'accepted',
            phase: 'generation_started',
            reason: null,
            resultAccess: 'none',
            cleanup: 'no_failure_observed',
            closeRequested: false,
            warnings: [],
          },
        } satisfies GenerationSnapshot;
      }
      if (cmd === 'generation_result') {
        return getResultPromise;
      }
      return;
    });

    mockIPC(invokeSpy);

    try {
      const { result } = renderHook(() => useGeneration());

      await waitFor(() => {
        expect(result.current.isSubscribed).toBe(true);
      });

      // 1. Start run in flight
      let runPromise: Promise<void>;
      act(() => {
        runPromise = result.current.startRun();
      });

      // 2. Deliver terminal event while startGeneration is still in flight
      await act(async () => {
        const ids = eventListenerIds['generation-state'] || [];
        ids.forEach((id) => {
          const cb = (window as unknown as Record<string, (e: unknown) => void>)[`_${id}`];
          cb?.({
            event: 'generation-state',
            id,
            payload: {
              revision: 3,
              nativeAvailable: true,
              keyConfigured: true,
              output: { selectionId: 'out-fast', displayPath: 'C:\\exports' },
              run: {
                requestId: 'gen-pending-001',
                state: 'succeeded',
                engineStatus: 'accepted',
                phase: 'response_ready',
                reason: null,
                resultAccess: 'ready',
                cleanup: 'no_failure_observed',
                closeRequested: false,
                warnings: [],
              },
            } satisfies GenerationSnapshot,
          });
        });
      });

      // Verify generation_result fetch was initiated
      await waitFor(() => {
        expect(invokeSpy).toHaveBeenCalledWith('generation_result', {
          request: { requestId: 'gen-pending-001' },
        });
      });
      expect(result.current.activeResult).toBeNull();

      // 3. Resolve startGeneration while generation_result fetch is still pending
      await act(async () => {
        resolveStartGeneration({
          revision: 2,
          nativeAvailable: true,
          keyConfigured: true,
          output: { selectionId: 'out-fast', displayPath: 'C:\\exports' },
          run: {
            requestId: 'gen-pending-001',
            state: 'running',
            engineStatus: 'accepted',
            phase: 'generation_started',
            reason: null,
            resultAccess: 'none',
            cleanup: 'no_failure_observed',
            closeRequested: false,
            warnings: [],
          },
        } satisfies GenerationSnapshot);
        await runPromise;
      });

      // 4. Finally resolve the pending generation_result fetch
      await act(async () => {
        resolveGetResult({
          requestId: 'gen-pending-001',
          runFolder: 'C:\\exports\\gen-pending-001',
          artifacts: [],
          hasPreview: false,
          manifestSummary: {
            schemaVersion: 'cad_copilot.run_manifest.v1',
            provenanceKind: 'example_plan',
            sourceId: 'spur_gear',
            cadRuntimeVersion: '226.00.00.106',
            operationsExecuted: 1,
            planSha256: 'abc',
            promptSha256: null,
            warnings: [],
            diagnostics: [],
          },
        });
      });

      // 5. CRITICAL: The pending fetch completion must NOT be ignored!
      await waitFor(() => {
        expect(result.current.activeResult?.requestId).toBe('gen-pending-001');
      });
    } finally {
      clearMocks();
    }
  });
});

describe('GeneratePage status live region', () => {
  it('announces running, succeeded, and failed states to screen readers with polite priority', () => {
    const mockGeneration: UseGenerationReturn = {
      snapshot: {
        revision: 1,
        nativeAvailable: true,
        keyConfigured: true,
        output: { selectionId: 'out-1', displayPath: 'C:\\exports' },
        run: {
          requestId: 'gen-live-001',
          state: 'running',
          phase: 'generation_started',
          engineStatus: 'accepted',
          reason: null,
          resultAccess: 'none',
          cleanup: 'no_failure_observed',
          closeRequested: false,
          warnings: [],
        },
      },
      mode: 'example',
      setMode: vi.fn(),
      prompt: '',
      setPrompt: vi.fn(),
      keepPartOpen: false,
      setKeepPartOpen: vi.fn(),
      isKeyEditorOpen: false,
      setIsKeyEditorOpen: vi.fn(),
      keyInput: '',
      setKeyInput: vi.fn(),
      clearKeyDraft: vi.fn(),
      saveKey: vi.fn(),
      clearKey: vi.fn(),
      selectFolder: vi.fn(),
      startRun: vi.fn(),
      cancelRun: vi.fn(),
      resolveClose: vi.fn(),
      activeResult: null,
      previewBlobUrl: null,
      clearBlobUrl: vi.fn(),
      revealFolder: vi.fn(),
      actionError: null,
      clearActionError: vi.fn(),
      isSubmitting: false,
      isSubscribed: true,
      retrySubscription: vi.fn(),
    };

    const { rerender } = render(<GeneratePage generation={mockGeneration} />);

    const liveRegion = screen.getByRole('status');
    expect(liveRegion).toHaveAttribute('aria-live', 'polite');
    expect(liveRegion).toHaveTextContent('Generation running in Siemens Solid Edge.');

    // Succeeded state
    rerender(
      <GeneratePage
        generation={{
          ...mockGeneration,
          snapshot: {
            ...mockGeneration.snapshot,
            run: {
              ...mockGeneration.snapshot.run!,
              state: 'succeeded',
              phase: 'response_ready',
              resultAccess: 'ready',
            },
          },
        }}
      />
    );
    expect(liveRegion).toHaveTextContent('Generation completed successfully. 3D models ready.');

    // Failed state
    rerender(
      <GeneratePage
        generation={{
          ...mockGeneration,
          snapshot: {
            ...mockGeneration.snapshot,
            run: {
              ...mockGeneration.snapshot.run!,
              state: 'failed',
              engineStatus: 'failed',
              reason: 'Solid Edge license missing',
              resultAccess: 'unavailable',
            },
          },
        }}
      />
    );
    expect(liveRegion).toHaveTextContent('Generation run failed: Solid Edge license missing');
  });
});
