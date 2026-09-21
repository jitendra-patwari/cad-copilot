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
import { GenerationProgress } from '../../src/features/generate/GenerationProgress';
import { GenerationResult } from '../../src/features/generate/GenerationResult';
import { useGeneration } from '../../src/features/generate/useGeneration';
import type {
  GenerationResultResponse,
  GenerationRunSnapshot,
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

    // Emoji handling (surrogate pairs count as single code points)
    const emojiPrompt = '⚙️'.repeat(4000);
    expect(validatePrompt(emojiPrompt).valid).toBe(true);
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

    expect(screen.getByText('Canonical 24-Tooth Spur Gear')).toBeInTheDocument();
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

    render(
      <GenerateForm
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
        };
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
            cleanup: 'no_failure_observed',
            closeRequested: false,
            manifestState: 'not_applicable',
            reason: null,
          },
        };
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
  it('renders succeeded outcome, published artifacts, and manifest provenance', () => {
    const revealMock = vi.fn();
    const run: GenerationRunSnapshot = {
      requestId: 'gen_succ_456',
      state: 'succeeded',
      phase: 'response_ready',
      engineStatus: 'accepted',
      reason: null,
      resultAccess: 'ready',
      cleanup: 'no_failure_observed',
      closeRequested: false,
      warnings: ['Mesh density warning'],
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

    render(
      <GenerationResult run={run} result={result} previewBlobUrl={null} revealFolder={revealMock} />
    );

    expect(screen.getByText('CAD Model Successfully Generated')).toBeInTheDocument();
    expect(screen.getByText('model.par')).toBeInTheDocument();
    expect(screen.getByText('model.step')).toBeInTheDocument();
    expect(screen.getByText('model.stl')).toBeInTheDocument();
    expect(screen.getByText('Mesh density warning')).toBeInTheDocument();
    expect(screen.getByText('226.00.00.106')).toBeInTheDocument();
    expect(screen.getByText('1 executed')).toBeInTheDocument();
    expect(screen.getByText('01234567…')).toBeInTheDocument();
    expect(screen.getByText('Solid Edge runtime verified in normal state')).toBeInTheDocument();

    const showFolderBtn = screen.getByRole('button', { name: /show in folder/i });
    fireEvent.click(showFolderBtn);
    expect(revealMock).toHaveBeenCalledTimes(1);
  });
});
