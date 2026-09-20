import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, renderHook, act, waitFor } from '@testing-library/react';
import { mockIPC, clearMocks } from '@tauri-apps/api/mocks';
import { App } from '../../src/app/App';
import {
  isBatchRunActive,
  isBatchTerminal,
  getEligibleCount,
  formatPhase,
  formatBytes,
} from '../../src/features/batch/batchState';
import { BatchSourceCard } from '../../src/features/batch/BatchSourceCard';
import { BatchOperationCard } from '../../src/features/batch/BatchOperationCard';
import { BatchOutputCard } from '../../src/features/batch/BatchOutputCard';
import { BatchProgressCard } from '../../src/features/batch/BatchProgressCard';
import { BatchResultCard } from '../../src/features/batch/BatchResultCard';
import { BatchPage } from '../../src/features/batch/BatchPage';
import { useBatch } from '../../src/features/batch/useBatch';
import type {
  BatchResultResponse,
  BatchRunSnapshot,
  BatchSnapshot,
  BatchSourceSelection,
  BatchOutputSelection,
} from '../../src/features/batch/types';
import type { UseBatchReturn } from '../../src/features/batch/useBatch';

describe('Batch Workspace Formats and Operations', () => {
  it('renders canonical batch operations and verified formats', () => {
    render(<App />);

    const batchBtn = screen.getByRole('button', { name: /^batch/i });
    fireEvent.click(batchBtn);

    // Verify operations
    expect(screen.getByText('3D CAD Export')).toBeInTheDocument();
    expect(screen.getByText('Drawing Publication')).toBeInTheDocument();

    // Verify 3D formats
    expect(screen.getByText('.step')).toBeInTheDocument();
    expect(screen.getByText('.stl')).toBeInTheDocument();
    expect(screen.getByText('.x_t')).toBeInTheDocument();

    // Verify drawing formats
    expect(screen.getByText('.pdf')).toBeInTheDocument();
    expect(screen.getByText('.dxf')).toBeInTheDocument();

    // Verify unverified or unsupported formats are NOT present
    const batchContent =
      screen.getByRole('region', { name: /supported format targets/i }).textContent ?? '';
    expect(batchContent).not.toContain('.iges');
    expect(batchContent).not.toContain('.dwg');
    expect(batchContent).not.toContain('AP214');
  });
});

describe('batchState pure helpers', () => {
  it('identifies active and terminal batch states', () => {
    expect(isBatchRunActive('starting')).toBe(true);
    expect(isBatchRunActive('running')).toBe(true);
    expect(isBatchRunActive('cancelling')).toBe(true);
    expect(isBatchRunActive('terminal')).toBe(false);
    expect(isBatchRunActive(null)).toBe(false);

    expect(isBatchTerminal('terminal')).toBe(true);
    expect(isBatchTerminal('running')).toBe(false);
    expect(isBatchTerminal(null)).toBe(false);
  });

  it('calculates eligible file count per operation', () => {
    const source: BatchSourceSelection = {
      selectionId: 'src-001',
      displayRoot: 'C:/models',
      parCount: 5,
      psmCount: 2,
      asmCount: 3,
      dftCount: 4,
      skippedCount: 1,
      previewFiles: [],
    };

    expect(getEligibleCount(source, 'export_3d')).toBe(10); // 5 + 2 + 3
    expect(getEligibleCount(source, 'publish_drawing')).toBe(4);
    expect(getEligibleCount(null, 'export_3d')).toBe(0);
  });

  it('formats execution phases', () => {
    expect(formatPhase('batch_started')).toBe('Batch Started');
    expect(formatPhase('file_started')).toBe('Processing File');
    expect(formatPhase('format_started')).toBe('Exporting Format');
    expect(formatPhase('format_finished')).toBe('Format Finished');
    expect(formatPhase('file_finished')).toBe('File Finished');
    expect(formatPhase('batch_finished')).toBe('Batch Finished');
    expect(formatPhase(null)).toBe('Idle');
  });

  it('formats byte sizes cleanly and handles edge cases', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(-100)).toBe('0 B');
    expect(formatBytes(NaN)).toBe('0 B');
    expect(formatBytes(Infinity)).toBe('0 B');
    expect(formatBytes(1024)).toBe('1.0 KB');
    expect(formatBytes(1048576)).toBe('1.0 MB');
    expect(formatBytes(1073741824)).toBe('1.0 GB');
  });
});

describe('BatchSourceCard', () => {
  it('renders empty state when no source is selected', () => {
    render(
      <BatchSourceCard source={null} sourceFiles={[]} disabled={false} onSelectSource={vi.fn()} />
    );

    expect(screen.getByText(/no source directory or files selected/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /select folder/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /select files/i })).toBeInTheDocument();
  });

  it('renders source selection details and expandable file list with filtering', () => {
    const source: BatchSourceSelection = {
      selectionId: 'src-123',
      displayRoot: 'C:/cad/models',
      parCount: 2,
      psmCount: 1,
      asmCount: 0,
      dftCount: 1,
      skippedCount: 1,
      previewFiles: ['part1.par', 'part2.par', 'sheet.psm', 'draft.dft'],
    };

    render(
      <BatchSourceCard
        source={source}
        sourceFiles={source.previewFiles}
        disabled={false}
        onSelectSource={vi.fn()}
      />
    );

    expect(screen.getByText('C:/cad/models')).toBeInTheDocument();
    expect(screen.getByText(/par: 2/i)).toBeInTheDocument();
    expect(screen.getByText(/psm: 1/i)).toBeInTheDocument();
    expect(screen.getByText(/dft: 1/i)).toBeInTheDocument();
    expect(screen.getByText(/unsupported\/skipped: 1/i)).toBeInTheDocument();

    // Expand inventory
    const expandBtn = screen.getByRole('button', { name: /view file inventory/i });
    fireEvent.click(expandBtn);

    expect(screen.getByText('part1.par')).toBeInTheDocument();
    expect(screen.getByText('draft.dft')).toBeInTheDocument();

    // Filter files
    const filterInput = screen.getByPlaceholderText(/filter files/i);
    fireEvent.change(filterInput, { target: { value: 'sheet' } });

    expect(screen.getByText('sheet.psm')).toBeInTheDocument();
    expect(screen.queryByText('part1.par')).not.toBeInTheDocument();
  });
});

describe('BatchOperationCard', () => {
  it('allows switching operations and toggling formats', () => {
    const onSelectOp = vi.fn();
    const onToggleFmt = vi.fn();
    const onSetContinue = vi.fn();
    const onSetMax = vi.fn();

    render(
      <BatchOperationCard
        source={null}
        selectedOperation="export_3d"
        onSelectOperation={onSelectOp}
        selectedFormats={['step', 'stl']}
        onToggleFormat={onToggleFmt}
        continueOnError={true}
        onSetContinueOnError={onSetContinue}
        maxFiles={500}
        onSetMaxFiles={onSetMax}
        disabled={false}
      />
    );

    // Switch to Drawing Publication
    const drawingBtn = screen.getByText('Drawing Publication');
    fireEvent.click(drawingBtn);
    expect(onSelectOp).toHaveBeenCalledWith('publish_drawing');

    // Toggle format
    const stepBtn = screen.getByRole('button', { name: /toggle step format/i });
    fireEvent.click(stepBtn);
    expect(onToggleFmt).toHaveBeenCalledWith('step');

    // Max files change
    const maxInput = screen.getByLabelText(/max files cap/i);
    fireEvent.change(maxInput, { target: { value: '100' } });
    expect(onSetMax).toHaveBeenCalledWith(100);
  });
});

describe('BatchOutputCard', () => {
  it('renders output selection and immutability notice', () => {
    const output: BatchOutputSelection = {
      selectionId: 'out-456',
      displayPath: 'C:/cad/exports',
    };

    render(<BatchOutputCard output={output} disabled={false} onSelectOutput={vi.fn()} />);

    expect(screen.getByText('C:/cad/exports')).toBeInTheDocument();
    expect(screen.getByText(/source immutability & collision safety/i)).toBeInTheDocument();
  });
});

describe('BatchProgressCard', () => {
  it('displays active execution progress and handles cancel', () => {
    const onCancel = vi.fn();
    const run: BatchRunSnapshot = {
      requestId: 'batch-001',
      state: 'running',
      engineStatus: null,
      phase: 'format_started',
      totalFiles: 10,
      completedFiles: 4,
      currentFile: 'assembly.asm',
      currentFormat: 'step',
      lastFileStatus: null,
      cleanup: 'no_failure_observed',
      closeRequested: false,
      manifestState: 'not_applicable',
      reason: null,
    };

    render(<BatchProgressCard run={run} onCancel={onCancel} />);

    expect(screen.getByText(/exporting format/i)).toBeInTheDocument();
    expect(screen.getByText(/processed: 4 of 10 files/i)).toBeInTheDocument();
    expect(screen.getByText('assembly.asm')).toBeInTheDocument();
    expect(screen.getByText('STEP')).toBeInTheDocument();

    const progressBar = screen.getByRole('progressbar', { name: /batch execution progress/i });
    expect(progressBar).toBeInTheDocument();
    expect(progressBar).toHaveAttribute('aria-valuenow', '40');
    expect(progressBar).toHaveAttribute('aria-valuemin', '0');
    expect(progressBar).toHaveAttribute('aria-valuemax', '100');

    const cancelBtn = screen.getByRole('button', { name: /cancel batch/i });
    fireEvent.click(cancelBtn);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe('BatchResultCard', () => {
  it('renders completed results table with accounting and manifest status', () => {
    const onReveal = vi.fn();
    const result: BatchResultResponse = {
      requestId: 'batch-req-001',
      engineStatus: 'completed',
      operation: 'export_3d',
      summary: {
        total: 2,
        accepted: 1,
        partial: 1,
        failed: 0,
        cancelled: 0,
        unprocessed: 0,
      },
      rows: [
        {
          file: 'part1.par',
          category: 'succeeded',
          attemptedFormats: ['step', 'stl'],
          successfulArtifacts: [
            { format: 'step', relativePath: 'part1.step', sizeBytes: 2048 },
            { format: 'stl', relativePath: 'part1.stl', sizeBytes: 4096 },
          ],
          diagnosticCodes: [],
        },
        {
          file: 'part2.par',
          category: 'partial',
          attemptedFormats: ['step', 'stl'],
          successfulArtifacts: [{ format: 'step', relativePath: 'part2.step', sizeBytes: 1024 }],
          diagnosticCodes: ['ARTIFACT_EXPORT_FAILED'],
        },
      ],
      manifestState: 'validated',
      manifestPath: 'C:/exports/batch-req-001.batch_manifest.json',
      reason: null,
    };

    render(<BatchResultCard result={result} onRevealOutput={onReveal} />);

    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('batch-req-001')).toBeInTheDocument();
    expect(screen.getByText(/run manifest validated/i)).toBeInTheDocument();
    expect(screen.getByText('part1.par')).toBeInTheDocument();
    expect(screen.getByText('part2.par')).toBeInTheDocument();
    expect(screen.getAllByText('Succeeded')).toHaveLength(2);
    expect(screen.getAllByText('Partial')).toHaveLength(2);
    expect(screen.getByText('ARTIFACT_EXPORT_FAILED')).toBeInTheDocument();

    // Table accessibility
    expect(screen.getByRole('table', { name: /batch execution results/i })).toBeInTheDocument();

    // Reveal output folder
    const openBtn = screen.getByRole('button', { name: /open output folder/i });
    fireEvent.click(openBtn);
    expect(onReveal).toHaveBeenCalledTimes(1);
  });

  it('filters results by category and search query', () => {
    const result: BatchResultResponse = {
      requestId: 'batch-filter-001',
      engineStatus: 'completed',
      operation: 'export_3d',
      summary: {
        total: 3,
        accepted: 1,
        partial: 1,
        failed: 1,
        cancelled: 0,
        unprocessed: 0,
      },
      rows: [
        {
          file: 'bracket.par',
          category: 'succeeded',
          attemptedFormats: ['step'],
          successfulArtifacts: [{ format: 'step', relativePath: 'bracket.step', sizeBytes: 1024 }],
          diagnosticCodes: [],
        },
        {
          file: 'plate.par',
          category: 'partial',
          attemptedFormats: ['step', 'stl'],
          successfulArtifacts: [{ format: 'step', relativePath: 'plate.step', sizeBytes: 512 }],
          diagnosticCodes: ['EXPORT_FAILED'],
        },
        {
          file: 'gear.par',
          category: 'failed',
          attemptedFormats: ['step'],
          successfulArtifacts: [],
          diagnosticCodes: ['OPEN_FAILED'],
        },
      ],
      manifestState: 'validated',
      manifestPath: 'C:/exports/manifest.json',
      reason: null,
    };

    render(<BatchResultCard result={result} onRevealOutput={vi.fn()} />);

    // Initially all 3 rows are visible
    expect(screen.getByText('bracket.par')).toBeInTheDocument();
    expect(screen.getByText('plate.par')).toBeInTheDocument();
    expect(screen.getByText('gear.par')).toBeInTheDocument();

    // Filter by 'failed' category button
    const failedFilterBtn = screen.getByRole('button', { name: /^failed$/i });
    expect(failedFilterBtn).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(failedFilterBtn);
    expect(failedFilterBtn).toHaveAttribute('aria-pressed', 'true');

    expect(screen.queryByText('bracket.par')).not.toBeInTheDocument();
    expect(screen.queryByText('plate.par')).not.toBeInTheDocument();
    expect(screen.getByText('gear.par')).toBeInTheDocument();

    // Switch back to 'all'
    const allFilterBtn = screen.getByRole('button', { name: /^all$/i });
    fireEvent.click(allFilterBtn);
    expect(screen.getByText('bracket.par')).toBeInTheDocument();

    // Filter by search query
    const searchInput = screen.getByLabelText(/search results by filename/i);
    fireEvent.change(searchInput, { target: { value: 'plate' } });

    expect(screen.queryByText('bracket.par')).not.toBeInTheDocument();
    expect(screen.getByText('plate.par')).toBeInTheDocument();
    expect(screen.queryByText('gear.par')).not.toBeInTheDocument();
  });

  it('renders unverified location placeholder when artifact relative path is empty', () => {
    const result: BatchResultResponse = {
      requestId: 'batch-unverified-001',
      engineStatus: 'completed',
      operation: 'export_3d',
      summary: {
        total: 1,
        accepted: 1,
        partial: 0,
        failed: 0,
        cancelled: 0,
        unprocessed: 0,
      },
      rows: [
        {
          file: 'part.par',
          category: 'succeeded',
          attemptedFormats: ['step'],
          successfulArtifacts: [{ format: 'step', relativePath: '', sizeBytes: 1024 }],
          diagnosticCodes: [],
        },
      ],
      manifestState: 'unavailable',
      manifestPath: null,
      reason: null,
    };

    render(<BatchResultCard result={result} onRevealOutput={vi.fn()} />);

    expect(screen.getByText(/run manifest is unavailable/i)).toBeInTheDocument();
    expect(screen.getByText('STEP (unverified location)')).toBeInTheDocument();
  });
});

describe('BatchPage integration states', () => {
  const mockBaseReturn = (overrides: Partial<UseBatchReturn> = {}): UseBatchReturn => ({
    snapshot: {
      revision: 1,
      nativeAvailable: true,
      source: null,
      output: null,
      run: null,
    },
    sourceFiles: [],
    selectedOperation: 'export_3d',
    setOperation: vi.fn(),
    selectedFormats: ['step'],
    toggleFormat: vi.fn(),
    continueOnError: true,
    setContinueOnError: vi.fn(),
    maxFiles: 100,
    setMaxFiles: vi.fn(),
    activeResult: null,
    actionError: null,
    clearActionError: vi.fn(),
    isSubmitting: false,
    isSubscribed: true,
    retrySubscription: vi.fn(),
    selectSource: vi.fn(),
    selectOutput: vi.fn(),
    startRun: vi.fn(),
    cancelRun: vi.fn(),
    revealOutput: vi.fn(),
    resolveClose: vi.fn(),
    ...overrides,
  });

  it('renders native unavailable banner when nativeAvailable is false', () => {
    const mockBatch = mockBaseReturn({
      snapshot: {
        revision: 1,
        nativeAvailable: false,
        source: null,
        output: null,
        run: null,
      },
    });

    render(<BatchPage batch={mockBatch} />);

    expect(screen.getByText(/desktop native bridge unavailable/i)).toBeInTheDocument();
    const startBtn = screen.getByRole('button', { name: /start batch run/i });
    expect(startBtn).toBeDisabled();
  });

  it('renders incomplete cleanup alert when prior run cleanup failed', () => {
    const mockBatch = mockBaseReturn({
      snapshot: {
        revision: 2,
        nativeAvailable: true,
        source: null,
        output: null,
        run: {
          requestId: 'batch-stalled',
          state: 'terminal',
          engineStatus: 'failed',
          phase: null,
          totalFiles: 0,
          completedFiles: 0,
          currentFile: null,
          currentFormat: null,
          lastFileStatus: null,
          cleanup: 'incomplete',
          closeRequested: false,
          manifestState: 'not_applicable',
          reason: 'Process force terminated',
        },
      },
    });

    render(<BatchPage batch={mockBatch} />);

    expect(screen.getByText(/previous run cleanup incomplete/i)).toBeInTheDocument();
    const startBtn = screen.getByRole('button', { name: /start batch run/i });
    expect(startBtn).toBeDisabled();
  });

  it('enables Start Batch Run button only when source, output, and eligible formats exist', () => {
    const startRunMock = vi.fn();
    const mockBatch = mockBaseReturn({
      snapshot: {
        revision: 3,
        nativeAvailable: true,
        source: {
          selectionId: 'src-valid',
          displayRoot: 'C:/cad/models',
          parCount: 3,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          previewFiles: ['p1.par', 'p2.par', 'p3.par'],
        },
        output: {
          selectionId: 'out-valid',
          displayPath: 'C:/cad/output',
        },
        run: null,
      },
      selectedFormats: ['step', 'stl'],
      startRun: startRunMock,
    });

    render(<BatchPage batch={mockBatch} />);

    const startBtn = screen.getByRole('button', { name: /start batch run/i });
    expect(startBtn).not.toBeDisabled();

    fireEvent.click(startBtn);
    expect(startRunMock).toHaveBeenCalledTimes(1);
  });

  it('disables Start Batch Run button and warns when eligible file count exceeds maxFiles cap', () => {
    const mockBatch = mockBaseReturn({
      snapshot: {
        revision: 4,
        nativeAvailable: true,
        source: {
          selectionId: 'src-many',
          displayRoot: 'C:/cad/models',
          parCount: 150,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          previewFiles: [],
        },
        output: {
          selectionId: 'out-valid',
          displayPath: 'C:/cad/output',
        },
        run: null,
      },
      selectedFormats: ['step'],
      maxFiles: 100,
    });

    render(<BatchPage batch={mockBatch} />);

    const startBtn = screen.getByRole('button', { name: /start batch run/i });
    expect(startBtn).toBeDisabled();
    expect(
      screen.getByText(/eligible file count \(150\) exceeds maximum files limit \(100\)/i)
    ).toBeInTheDocument();
  });

  it('renders terminal outcome card with reason when run finishes in terminal state without results', () => {
    const mockBatch = mockBaseReturn({
      snapshot: {
        revision: 5,
        nativeAvailable: true,
        source: null,
        output: null,
        run: {
          requestId: 'batch-failed-001',
          state: 'terminal',
          engineStatus: 'failed',
          phase: null,
          totalFiles: 5,
          completedFiles: 0,
          currentFile: null,
          currentFormat: null,
          lastFileStatus: null,
          cleanup: 'no_failure_observed',
          closeRequested: false,
          manifestState: 'unavailable',
          reason: 'Solid Edge application is unavailable or not running.',
        },
      },
      activeResult: null,
    });

    render(<BatchPage batch={mockBatch} />);

    expect(screen.getByText('Batch Execution Terminated')).toBeInTheDocument();
    expect(screen.getByText('batch-failed-001')).toBeInTheDocument();
    expect(screen.getByText(/^failed$/i)).toBeInTheDocument();
    expect(
      screen.getByText('Solid Edge application is unavailable or not running.')
    ).toBeInTheDocument();
  });
});

describe('useBatch controller lifecycle & error resilience', () => {
  let invokeSpy: ReturnType<typeof vi.fn<(cmd: string, args?: unknown) => Promise<unknown>>>;
  let eventListenerIds: Record<string, number[]>;

  beforeEach(() => {
    eventListenerIds = {};
    invokeSpy = vi.fn(async (cmd: string, args?: unknown) => {
      const payload = args as Record<string, unknown> | undefined;
      if (cmd === 'batch_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          source: null,
          output: null,
          run: null,
        };
      }
      if (cmd === 'plugin:event|listen') {
        const event = payload?.event as string;
        const handlerId = payload?.handler as number;
        if (!eventListenerIds[event]) {
          eventListenerIds[event] = [];
        }
        eventListenerIds[event].push(handlerId);
        return Math.floor(Math.random() * 1000);
      }
      if (cmd === 'plugin:event|unlisten') {
        return;
      }
      return;
    });

    mockIPC(invokeSpy);
  });

  afterEach(() => {
    clearMocks();
    vi.restoreAllMocks();
  });

  const emitEvent = (snap: BatchSnapshot) => {
    const ids = eventListenerIds['batch-state'] || [];
    ids.forEach((id) => {
      const cb = (window as unknown as Record<string, (e: unknown) => void>)[`_${id}`];
      cb?.({
        event: 'batch-state',
        id,
        payload: snap,
      });
    });
  };

  it('loads initial snapshot and handles monotonic revision updates', async () => {
    const { result } = renderHook(() => useBatch());

    // Await initial snapshot load
    await waitFor(() => {
      expect(result.current.snapshot.revision).toBe(1);
    });

    // Simulate event update with higher revision
    act(() => {
      emitEvent({
        revision: 2,
        nativeAvailable: true,
        source: {
          selectionId: 'src-1',
          displayRoot: 'C:/models',
          parCount: 2,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          previewFiles: ['p1.par'],
        },
        output: null,
        run: null,
      });
    });

    await waitFor(() => {
      expect(result.current.snapshot.revision).toBe(2);
      expect(result.current.snapshot.source?.selectionId).toBe('src-1');
    });

    // Stale event with lower revision (e.g. 1) must be ignored
    act(() => {
      emitEvent({
        revision: 1,
        nativeAvailable: true,
        source: null,
        output: null,
        run: null,
      });
    });

    expect(result.current.snapshot.revision).toBe(2);
    expect(result.current.snapshot.source?.selectionId).toBe('src-1');
  });

  it('exercises source and output pickers and initiates batch run with exactly one batch_start call', async () => {
    let currentRevision = 1;
    let currentSource: BatchSourceSelection | null = null;
    let currentOutput: BatchOutputSelection | null = null;

    invokeSpy.mockImplementation(async (cmd: string, args?: unknown) => {
      const payload = args as Record<string, unknown> | undefined;
      if (cmd === 'plugin:event|listen') {
        const event = payload?.event as string;
        const handlerId = payload?.handler as number;
        if (!eventListenerIds[event]) {
          eventListenerIds[event] = [];
        }
        eventListenerIds[event].push(handlerId);
        return Math.floor(Math.random() * 1000);
      }
      if (cmd === 'plugin:event|unlisten') {
        return;
      }
      if (cmd === 'batch_snapshot') {
        return {
          revision: currentRevision,
          nativeAvailable: true,
          source: currentSource,
          output: currentOutput,
          run: null,
        };
      }
      if (cmd === 'batch_select_source') {
        currentRevision += 1;
        currentSource = {
          selectionId: 'src-picker-001',
          displayRoot: 'C:/models/gears',
          parCount: 2,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          previewFiles: ['gear1.par', 'gear2.par'],
        };
        return {
          selectionId: 'src-picker-001',
          displayRoot: 'C:/models/gears',
          parCount: 2,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          files: ['gear1.par', 'gear2.par'],
        };
      }
      if (cmd === 'batch_select_output') {
        currentRevision += 1;
        currentOutput = {
          selectionId: 'out-picker-002',
          displayPath: 'C:/exports/step_files',
        };
        return {
          selectionId: 'out-picker-002',
          displayPath: 'C:/exports/step_files',
        };
      }
      if (cmd === 'batch_start') {
        currentRevision += 1;
        return {
          revision: currentRevision,
          nativeAvailable: true,
          source: currentSource,
          output: currentOutput,
          run: {
            requestId: 'run-picker-start',
            state: 'running',
            engineStatus: null,
            phase: 'batch_started',
            totalFiles: 2,
            completedFiles: 0,
            currentFile: 'gear1.par',
            currentFormat: 'step',
            lastFileStatus: null,
            cleanup: 'no_failure_observed',
            closeRequested: false,
            manifestState: 'not_applicable',
            reason: null,
          },
        };
      }
      return;
    });

    const { result } = renderHook(() => useBatch());

    await waitFor(() => {
      expect(result.current.isSubscribed).toBe(true);
    });

    // 1. Select source via picker
    await act(async () => {
      await result.current.selectSource('folder');
    });

    expect(invokeSpy).toHaveBeenCalledWith('batch_select_source', {
      request: { mode: 'folder' },
    });
    expect(result.current.snapshot.source?.selectionId).toBe('src-picker-001');
    expect(result.current.sourceFiles).toEqual(['gear1.par', 'gear2.par']);

    // 2. Select output via picker
    await act(async () => {
      await result.current.selectOutput();
    });

    expect(invokeSpy).toHaveBeenCalledWith('batch_select_output', expect.anything());
    expect(result.current.snapshot.output?.selectionId).toBe('out-picker-002');

    // 3. Start batch run and verify exactly one batch_start call with the picker selection IDs
    await act(async () => {
      await result.current.startRun();
    });

    expect(invokeSpy).toHaveBeenCalledWith('batch_start', {
      request: {
        sourceSelectionId: 'src-picker-001',
        outputSelectionId: 'out-picker-002',
        operation: 'export_3d',
        formats: ['step', 'stl', 'parasolid'],
        continueOnError: true,
        maxFiles: 100,
      },
    });

    // Verify batch_start was called exactly once
    const batchStartCalls = invokeSpy.mock.calls.filter(([cmd]) => cmd === 'batch_start');
    expect(batchStartCalls).toHaveLength(1);
    expect(result.current.snapshot.run?.requestId).toBe('run-picker-start');
    expect(result.current.snapshot.run?.state).toBe('running');
  });

  it('blocks startRun with TOO_MANY_FILES when eligibleCount exceeds maxFiles', async () => {
    const { result } = renderHook(() => useBatch());

    await waitFor(() => {
      expect(result.current.snapshot.revision).toBe(1);
    });

    // Setup snapshot with 150 eligible files and valid output
    act(() => {
      emitEvent({
        revision: 2,
        nativeAvailable: true,
        source: {
          selectionId: 'src-1',
          displayRoot: 'C:/models',
          parCount: 150,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          previewFiles: [],
        },
        output: {
          selectionId: 'out-1',
          displayPath: 'C:/exports',
        },
        run: null,
      });
    });

    await waitFor(() => {
      expect(result.current.snapshot.source?.parCount).toBe(150);
    });

    // maxFiles defaults to 100
    await act(async () => {
      await result.current.startRun();
    });

    expect(result.current.actionError?.code).toBe('TOO_MANY_FILES');
    // Verify batch_start was never called
    expect(invokeSpy).not.toHaveBeenCalledWith('batch_start', expect.anything());
  });

  it('preserves previous activeResult when startBatch is rejected', async () => {
    // Mock batch_result for an initial run
    const priorResult: BatchResultResponse = {
      requestId: 'run-prior',
      engineStatus: 'completed',
      operation: 'export_3d',
      summary: { total: 1, accepted: 1, partial: 0, failed: 0, cancelled: 0, unprocessed: 0 },
      rows: [],
      manifestState: 'validated',
      manifestPath: 'C:/exports/manifest.json',
      reason: null,
    };

    invokeSpy.mockImplementation((cmd: string) => {
      if (cmd === 'batch_snapshot') {
        return Promise.resolve({
          revision: 1,
          nativeAvailable: true,
          source: {
            selectionId: 'src-1',
            displayRoot: 'C:/models',
            parCount: 1,
            psmCount: 0,
            asmCount: 0,
            dftCount: 0,
            skippedCount: 0,
            previewFiles: [],
          },
          output: {
            selectionId: 'out-1',
            displayPath: 'C:/exports',
          },
          run: {
            requestId: 'run-prior',
            state: 'terminal',
            engineStatus: 'completed',
            phase: null,
            totalFiles: 1,
            completedFiles: 1,
            currentFile: null,
            currentFormat: null,
            lastFileStatus: null,
            cleanup: 'no_failure_observed',
            closeRequested: false,
            manifestState: 'validated',
            reason: null,
          },
        });
      }
      if (cmd === 'batch_result') {
        return Promise.resolve(priorResult);
      }
      if (cmd === 'batch_start') {
        return Promise.reject({
          code: 'CANNOT_START',
          message: 'Solid Edge license checkout failed.',
        });
      }
      return Promise.resolve();
    });

    const { result } = renderHook(() => useBatch());

    // Await prior result retrieval
    await waitFor(() => {
      expect(result.current.activeResult?.requestId).toBe('run-prior');
    });

    // Attempt startRun which will be rejected
    await act(async () => {
      await result.current.startRun();
    });

    // Error is set, but prior result is still preserved!
    expect(result.current.actionError?.code).toBe('CANNOT_START');
    expect(result.current.activeResult?.requestId).toBe('run-prior');
  });

  it('handles terminal failure when getBatchResult returns RESULT_ACCESS_UNAVAILABLE', async () => {
    invokeSpy.mockImplementation((cmd: string) => {
      if (cmd === 'batch_snapshot') {
        return Promise.resolve({
          revision: 1,
          nativeAvailable: true,
          source: null,
          output: null,
          run: {
            requestId: 'run-failed-early',
            state: 'terminal',
            engineStatus: 'failed',
            phase: null,
            totalFiles: 0,
            completedFiles: 0,
            currentFile: null,
            currentFormat: null,
            lastFileStatus: null,
            cleanup: 'no_failure_observed',
            closeRequested: false,
            manifestState: 'unavailable',
            reason: 'Solid Edge application could not be launched.',
          },
        });
      }
      if (cmd === 'batch_result') {
        return Promise.reject({
          code: 'RESULT_ACCESS_UNAVAILABLE',
          message: 'No result cached.',
        });
      }
      return Promise.resolve();
    });

    const { result } = renderHook(() => useBatch());

    await waitFor(() => {
      expect(result.current.snapshot.run?.requestId).toBe('run-failed-early');
    });

    // activeResult remains null, no unhandled actionError is thrown
    expect(result.current.activeResult).toBeNull();
    expect(result.current.actionError).toBeNull();
    expect(result.current.snapshot.run?.reason).toBe(
      'Solid Edge application could not be launched.'
    );
  });

  it('preserves final result when fast run delivers terminal event before startBatch resolves', async () => {
    const fastResult: BatchResultResponse = {
      requestId: 'run-fast-001',
      engineStatus: 'completed',
      operation: 'export_3d',
      summary: { total: 1, accepted: 1, partial: 0, failed: 0, cancelled: 0, unprocessed: 0 },
      rows: [
        {
          file: 'fast.par',
          category: 'succeeded',
          attemptedFormats: ['step'],
          successfulArtifacts: [{ format: 'step', relativePath: 'fast.step', sizeBytes: 1024 }],
          diagnosticCodes: [],
        },
      ],
      manifestState: 'validated',
      manifestPath: 'C:/exports/manifest.json',
      reason: null,
    };

    let resolveStartBatch!: (snap: BatchSnapshot) => void;
    const startBatchPromise = new Promise<BatchSnapshot>((resolve) => {
      resolveStartBatch = resolve;
    });

    invokeSpy.mockImplementation(async (cmd: string, payload?: unknown) => {
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
      if (cmd === 'batch_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          source: {
            selectionId: 'src-fast',
            displayRoot: 'C:/models',
            parCount: 1,
            psmCount: 0,
            asmCount: 0,
            dftCount: 0,
            skippedCount: 0,
            previewFiles: ['fast.par'],
          },
          output: {
            selectionId: 'out-fast',
            displayPath: 'C:/exports',
          },
          run: null,
        };
      }
      if (cmd === 'batch_start') {
        return startBatchPromise;
      }
      if (cmd === 'batch_result') {
        return fastResult;
      }
      return;
    });

    const { result } = renderHook(() => useBatch());

    await waitFor(() => {
      expect(result.current.snapshot.revision).toBe(1);
    });

    // Initiate startRun (which is now in flight)
    const runPromise = result.current.startRun();

    // While startBatch is still in flight, deliver a terminal event with higher revision
    await act(async () => {
      emitEvent({
        revision: 3,
        nativeAvailable: true,
        source: {
          selectionId: 'src-fast',
          displayRoot: 'C:/models',
          parCount: 1,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          previewFiles: ['fast.par'],
        },
        output: {
          selectionId: 'out-fast',
          displayPath: 'C:/exports',
        },
        run: {
          requestId: 'run-fast-001',
          state: 'terminal',
          engineStatus: 'completed',
          phase: 'batch_finished',
          totalFiles: 1,
          completedFiles: 1,
          currentFile: null,
          currentFormat: null,
          lastFileStatus: null,
          cleanup: 'no_failure_observed',
          closeRequested: false,
          manifestState: 'validated',
          reason: null,
        },
      });
    });

    // Await result retrieval from the terminal event
    await waitFor(() => {
      expect(result.current.activeResult?.requestId).toBe('run-fast-001');
    });

    // Now resolve startBatch with the initial starting snapshot (revision 2)
    await act(async () => {
      resolveStartBatch({
        revision: 2,
        nativeAvailable: true,
        source: {
          selectionId: 'src-fast',
          displayRoot: 'C:/models',
          parCount: 1,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          previewFiles: ['fast.par'],
        },
        output: {
          selectionId: 'out-fast',
          displayPath: 'C:/exports',
        },
        run: {
          requestId: 'run-fast-001',
          state: 'running',
          engineStatus: null,
          phase: 'batch_started',
          totalFiles: 1,
          completedFiles: 0,
          currentFile: 'fast.par',
          currentFormat: 'step',
          lastFileStatus: null,
          cleanup: 'no_failure_observed',
          closeRequested: false,
          manifestState: 'not_applicable',
          reason: null,
        },
      });
      await runPromise;
    });

    // CRITICAL: The final activeResult must NOT be wiped out!
    expect(result.current.activeResult?.requestId).toBe('run-fast-001');
    expect(result.current.activeResult?.summary.accepted).toBe(1);
  });

  it('disables start when event subscription fails and recovers via retrySubscription', async () => {
    let shouldFailListen = true;
    invokeSpy.mockImplementation(async (cmd: string, payload?: unknown) => {
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
      if (cmd === 'batch_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          source: {
            selectionId: 'src-1',
            displayRoot: 'C:/models',
            parCount: 2,
            psmCount: 0,
            asmCount: 0,
            dftCount: 0,
            skippedCount: 0,
            previewFiles: ['p1.par', 'p2.par'],
          },
          output: {
            selectionId: 'out-1',
            displayPath: 'C:/exports',
          },
          run: null,
        };
      }
      if (cmd === 'batch_start') {
        return {
          revision: 2,
          nativeAvailable: true,
          source: null,
          output: null,
          run: {
            requestId: 'run-rec-1',
            state: 'running',
            engineStatus: null,
            phase: 'batch_started',
            totalFiles: 2,
            completedFiles: 0,
            currentFile: 'p1.par',
            currentFormat: 'step',
            lastFileStatus: null,
            cleanup: 'no_failure_observed',
            closeRequested: false,
            manifestState: 'not_applicable',
            reason: null,
          },
        };
      }
      return;
    });

    const { result } = renderHook(() => useBatch());

    // 1. Initial subscription failure
    await waitFor(() => {
      expect(result.current.actionError?.code).toBe('EVENT_SUBSCRIPTION_FAILED');
      expect(result.current.isSubscribed).toBe(false);
    });

    // 2. Calling startRun while unsubscribed is blocked
    await act(async () => {
      await result.current.startRun();
    });
    expect(invokeSpy).not.toHaveBeenCalledWith('batch_start', expect.anything());
    expect(result.current.actionError?.code).toBe('EVENT_SUBSCRIPTION_FAILED');

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
    expect(invokeSpy).toHaveBeenCalledWith('batch_start', expect.anything());
  });

  it('clears old activeResult on new run and blocks output reveal during active run or mismatched ID', async () => {
    const run1Result: BatchResultResponse = {
      requestId: 'run-001',
      engineStatus: 'completed',
      operation: '3D CAD Export',
      summary: {
        total: 1,
        accepted: 1,
        partial: 0,
        failed: 0,
        cancelled: 0,
        unprocessed: 0,
      },
      rows: [
        {
          file: 'part1.par',
          category: 'succeeded',
          attemptedFormats: ['step'],
          successfulArtifacts: [
            {
              format: 'step',
              relativePath: 'part1.step',
              sizeBytes: 1024,
            },
          ],
          diagnosticCodes: [],
        },
      ],
      manifestState: 'validated',
      manifestPath: 'C:/exports/manifest.json',
      reason: null,
    };

    invokeSpy.mockImplementation(async (cmd: string, payload?: unknown) => {
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
      if (cmd === 'batch_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          source: {
            selectionId: 'src-1',
            displayRoot: 'C:/models',
            parCount: 1,
            psmCount: 0,
            asmCount: 0,
            dftCount: 0,
            skippedCount: 0,
            previewFiles: ['part1.par'],
          },
          output: {
            selectionId: 'out-1',
            displayPath: 'C:/exports',
          },
          run: {
            requestId: 'run-001',
            state: 'terminal',
            engineStatus: 'completed',
            phase: 'batch_finished',
            totalFiles: 1,
            completedFiles: 1,
            currentFile: null,
            currentFormat: null,
            lastFileStatus: null,
            cleanup: 'no_failure_observed',
            closeRequested: false,
            manifestState: 'validated',
            reason: null,
          },
        };
      }
      if (cmd === 'batch_result') {
        return run1Result;
      }
      if (cmd === 'batch_reveal') {
        return { acknowledged: true, target: 'C:/exports' };
      }
      return;
    });

    const { result } = renderHook(() => useBatch());

    // Wait for run-001 result to load
    await waitFor(() => {
      expect(result.current.activeResult?.requestId).toBe('run-001');
    });

    // When run-001 is terminal, revealOutput works
    await act(async () => {
      await result.current.revealOutput('run-001');
    });
    expect(invokeSpy).toHaveBeenCalledWith('batch_reveal', { request: { requestId: 'run-001' } });
    invokeSpy.mockClear();

    // Now a new run (run-002) starts and emits an active snapshot
    await act(async () => {
      emitEvent({
        revision: 2,
        nativeAvailable: true,
        source: {
          selectionId: 'src-1',
          displayRoot: 'C:/models',
          parCount: 1,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          previewFiles: ['part1.par'],
        },
        output: {
          selectionId: 'out-1',
          displayPath: 'C:/exports',
        },
        run: {
          requestId: 'run-002',
          state: 'running',
          engineStatus: null,
          phase: 'batch_started',
          totalFiles: 1,
          completedFiles: 0,
          currentFile: 'part1.par',
          currentFormat: 'step',
          lastFileStatus: null,
          cleanup: 'no_failure_observed',
          closeRequested: false,
          manifestState: 'not_applicable',
          reason: null,
        },
      });
    });

    // activeResult must be immediately cleared because run-002 is active and request ID doesn't match
    expect(result.current.activeResult).toBeNull();

    // Attempting to reveal run-001 while run-002 is active must be blocked
    await act(async () => {
      await result.current.revealOutput('run-001');
    });
    expect(invokeSpy).not.toHaveBeenCalledWith('batch_reveal', expect.anything());

    // Attempting to reveal run-002 while it is still running must also be blocked
    await act(async () => {
      await result.current.revealOutput('run-002');
    });
    expect(invokeSpy).not.toHaveBeenCalledWith('batch_reveal', expect.anything());
  });

  it('does not display in-progress close confirmation modal when batch run is in terminal state', async () => {
    invokeSpy.mockImplementation(async (cmd: string, payload?: unknown) => {
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
          keyConfigured: false,
          output: null,
          run: null,
        };
      }
      if (cmd === 'batch_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          source: null,
          output: null,
          run: {
            requestId: 'run-done',
            state: 'terminal',
            engineStatus: 'completed',
            phase: null,
            totalFiles: 1,
            completedFiles: 1,
            currentFile: null,
            currentFormat: null,
            lastFileStatus: null,
            cleanup: 'no_failure_observed',
            closeRequested: true, // Native finalization set closeRequested true but state is terminal
            manifestState: 'validated',
            reason: null,
          },
        };
      }
      return;
    });

    const { unmount } = render(<App />);

    // Switch to Batch tab
    const batchTabBtn = screen.getByRole('button', { name: /batch/i });
    fireEvent.click(batchTabBtn);

    // Wait for the terminal outcome banner to render on screen, proving batch_snapshot has loaded
    await waitFor(() => {
      expect(screen.getByText('run-done')).toBeInTheDocument();
      expect(screen.getByText('Batch Execution Terminated')).toBeInTheDocument();
    });

    // Because state is terminal, the in-progress close confirmation modal should NOT be displayed
    expect(
      screen.queryByRole('dialog', { name: /batch operation in progress/i })
    ).not.toBeInTheDocument();

    unmount();
  });

  it('handles delayed listener registration across cleanup and remount without leaking listeners', async () => {
    let listenCallCount = 0;
    const unlistenSpy1 = vi.fn();
    const unlistenSpy2 = vi.fn();
    let resolveListen1!: (id: number) => void;
    const listenPromise1 = new Promise<number>((res) => {
      resolveListen1 = res;
    });

    invokeSpy.mockImplementation(async (cmd: string, payload?: unknown) => {
      const p = payload as Record<string, unknown> | undefined;
      if (cmd === 'plugin:event|listen') {
        listenCallCount++;
        const event = p?.event as string;
        const handlerId = p?.handler as number;
        if (!eventListenerIds[event]) {
          eventListenerIds[event] = [];
        }
        eventListenerIds[event].push(handlerId);
        if (listenCallCount === 1) {
          await listenPromise1;
          return 101;
        }
        return 102;
      }
      if (cmd === 'plugin:event|unlisten') {
        const eventId = (p?.eventId ?? p?.event) as number | string;
        if (eventId === 101) unlistenSpy1();
        if (eventId === 102) unlistenSpy2();
        return;
      }
      if (cmd === 'batch_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          source: null,
          output: null,
          run: null,
        };
      }
      return;
    });

    // Mount 1
    const { unmount } = renderHook(() => useBatch());

    // Immediately unmount before listenPromise1 resolves (simulating StrictMode unmount)
    unmount();

    // Now mount 2
    const { result: result2, unmount: unmount2 } = renderHook(() => useBatch());

    // Now resolve the first delayed listener registration
    await act(async () => {
      resolveListen1(101);
    });

    // Wait for mount 2 to be subscribed
    await waitFor(() => {
      expect(result2.current.isSubscribed).toBe(true);
    });

    // Mount 1's unlisten must have been called when it resolved late
    expect(unlistenSpy1).toHaveBeenCalledTimes(1);

    // Final unmount of mount 2 cleans up listener 2
    unmount2();
    expect(unlistenSpy2).toHaveBeenCalledTimes(1);
  });

  it('keeps Retry Connection recovery available after selecting source or output when subscription failed', async () => {
    let failListen = true;
    invokeSpy.mockImplementation(async (cmd: string, payload?: unknown) => {
      const p = payload as Record<string, unknown> | undefined;
      if (cmd === 'plugin:event|listen') {
        if (failListen) {
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
      if (cmd === 'batch_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          source: null,
          output: null,
          run: null,
        };
      }
      if (cmd === 'batch_select_source') {
        return {
          selectionId: 'src-picked',
          displayRoot: 'C:/models',
          parCount: 1,
          psmCount: 0,
          asmCount: 0,
          dftCount: 0,
          skippedCount: 0,
          files: ['p1.par'],
        };
      }
      return;
    });

    const { result } = renderHook(() => useBatch());

    // Wait for initial subscription failure
    await waitFor(() => {
      expect(result.current.actionError?.code).toBe('EVENT_SUBSCRIPTION_FAILED');
      expect(result.current.isSubscribed).toBe(false);
    });

    // Selecting source must NOT wipe the subscription failure state
    await act(async () => {
      await result.current.selectSource('folder');
    });

    // isSubscribed is STILL false and actionError is retained
    expect(result.current.isSubscribed).toBe(false);
    expect(result.current.actionError?.code).toBe('EVENT_SUBSCRIPTION_FAILED');

    // Rendering BatchPage shows the Retry Connection recovery buttons
    const { unmount } = render(<BatchPage batch={result.current} />);
    const retryButtons = screen.getAllByRole('button', { name: /retry connection/i });
    expect(retryButtons.length).toBeGreaterThan(0);

    // Now clicking Retry Connection restores connection
    failListen = false;
    await act(async () => {
      fireEvent.click(retryButtons[0]);
    });

    await waitFor(() => {
      expect(result.current.isSubscribed).toBe(true);
    });

    unmount();
  });
});
