import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { mockIPC, clearMocks } from '@tauri-apps/api/mocks';
import { App } from '../../src/app/App';
import type { GenerationSnapshot } from '../../src/features/generate/types';
import type { BatchSnapshot } from '../../src/features/batch/types';

describe('Application Shell Navigation', () => {
  it('renders CAD Copilot header and default Generate view', () => {
    render(<App />);

    expect(screen.getByText('CAD Copilot')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { level: 1, name: /design workspace/i })
    ).toBeInTheDocument();
  });

  it('provides exactly two primary navigation options: Generate and Batch', () => {
    render(<App />);

    const nav = screen.getByRole('navigation', { name: /primary navigation/i });
    const buttons = nav.querySelectorAll('button');
    expect(buttons).toHaveLength(2);

    expect(screen.getByRole('button', { name: /^generate/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^batch/i })).toBeInTheDocument();
  });

  it('marks Generate button with aria-current="page" by default', () => {
    render(<App />);

    const generateBtn = screen.getByRole('button', { name: /^generate/i });
    const batchBtn = screen.getByRole('button', { name: /^batch/i });

    expect(generateBtn).toHaveAttribute('aria-current', 'page');
    expect(batchBtn).not.toHaveAttribute('aria-current');
  });

  it('switches to Batch view and back to Generate view upon user click', () => {
    render(<App />);

    const generateBtn = screen.getByRole('button', { name: /^generate/i });
    const batchBtn = screen.getByRole('button', { name: /^batch/i });

    // Switch to Batch
    fireEvent.click(batchBtn);

    expect(
      screen.getByRole('heading', { level: 1, name: /batch operations & export/i })
    ).toBeInTheDocument();
    expect(batchBtn).toHaveAttribute('aria-current', 'page');
    expect(generateBtn).not.toHaveAttribute('aria-current');

    // Switch back to Generate
    fireEvent.click(generateBtn);

    expect(
      screen.getByRole('heading', { level: 1, name: /design workspace/i })
    ).toBeInTheDocument();
    expect(generateBtn).toHaveAttribute('aria-current', 'page');
    expect(batchBtn).not.toHaveAttribute('aria-current');
  });

  it('preserves header and navigation landmark nodes without remounting during view transitions', () => {
    render(<App />);

    const header = screen.getByRole('banner');
    const nav = screen.getByRole('navigation', { name: /primary navigation/i });
    const main = screen.getByRole('main');

    expect(header).toBeInTheDocument();
    expect(nav).toBeInTheDocument();
    expect(main).toBeInTheDocument();

    const batchBtn = screen.getByRole('button', { name: /^batch/i });
    fireEvent.click(batchBtn);

    // Header and nav elements preserve their exact node instances in DOM
    expect(screen.getByRole('banner')).toBe(header);
    expect(screen.getByRole('navigation', { name: /primary navigation/i })).toBe(nav);
    expect(screen.getByRole('main')).toBe(main);
    expect(
      screen.getByRole('heading', { level: 1, name: /batch operations & export/i })
    ).toBeInTheDocument();
  });

  it('renders verified Generate workflow controls and avoids prohibited viewport and upload controls', () => {
    render(<App />);

    const main = screen.getByRole('main');
    // Operational controls exist for Generate in M6.2
    expect(screen.getByRole('button', { name: /run cad generation/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /choose directory/i })).toBeInTheDocument();

    // Prohibited controls remain strictly absent: no 3D canvas, no file upload
    expect(main.querySelectorAll('canvas')).toHaveLength(0);
    expect(main.querySelectorAll('input[type="file"]')).toHaveLength(0);

    const mainText = main.textContent?.toLowerCase() ?? '';
    expect(mainText).not.toContain('viewport');
  });

  it('renders operational controls in Batch view without prohibited elements', () => {
    render(<App />);

    const batchBtn = screen.getByRole('button', { name: /^batch/i });
    fireEvent.click(batchBtn);

    const main = screen.getByRole('main');
    // Prohibited controls remain strictly absent: no 3D canvas, no browser file upload input
    expect(main.querySelectorAll('canvas')).toHaveLength(0);
    expect(main.querySelectorAll('input[type="file"]')).toHaveLength(0);

    // Operational batch controls are present
    expect(screen.getByRole('button', { name: /start batch run/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /select folder/i })).toBeInTheDocument();
  });

  it('toggles sidebar expansion and collapse via collapse toggle button', () => {
    render(<App />);

    const aside = screen.getByRole('complementary', { name: /sidebar/i });
    expect(aside).toHaveClass('w-72');

    const toggleBtn = screen.getByRole('button', { name: /collapse sidebar/i });
    fireEvent.click(toggleBtn);

    expect(aside).toHaveClass('w-16');
    expect(screen.getByRole('button', { name: /expand sidebar/i })).toBeInTheDocument();

    // Toggle back
    fireEvent.click(screen.getByRole('button', { name: /expand sidebar/i }));
    expect(aside).toHaveClass('w-72');
    expect(screen.getByRole('button', { name: /collapse sidebar/i })).toBeInTheDocument();
  });

  it('supports accessible navigation and Help panel opening when sidebar is collapsed', async () => {
    render(<App />);

    // Collapse sidebar
    const toggleBtn = screen.getByRole('button', { name: /collapse sidebar/i });
    fireEvent.click(toggleBtn);

    const aside = screen.getByRole('complementary', { name: /sidebar/i });
    expect(aside).toHaveClass('w-16');

    // Both navigation buttons retain accessible names even when visual text spans are hidden
    const generateBtn = screen.getByRole('button', { name: /^generate/i });
    const batchBtn = screen.getByRole('button', { name: /^batch/i });
    expect(generateBtn).toBeInTheDocument();
    expect(batchBtn).toBeInTheDocument();
    expect(generateBtn).toHaveAttribute('aria-label', 'Generate');
    expect(batchBtn).toHaveAttribute('aria-label', 'Batch');

    // Clicking Batch while collapsed switches views
    fireEvent.click(batchBtn);
    expect(
      screen.getByRole('heading', { level: 1, name: /batch operations & export/i })
    ).toBeInTheDocument();
    expect(batchBtn).toHaveAttribute('aria-current', 'page');
    expect(generateBtn).not.toHaveAttribute('aria-current');

    // Focusability and view switching back to Generate
    generateBtn.focus();
    expect(document.activeElement).toBe(generateBtn);
    fireEvent.click(generateBtn);
    expect(
      screen.getByRole('heading', { level: 1, name: /design workspace/i })
    ).toBeInTheDocument();
    expect(generateBtn).toHaveAttribute('aria-current', 'page');

    // Verify Settings button is not rendered (removed dead-end surface)
    expect(
      screen.queryByRole('button', { name: /open settings and diagnostics/i })
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /settings/i })).not.toBeInTheDocument();

    // Opening Help panel while collapsed works as expected
    const helpBtn = screen.getByRole('button', {
      name: /open help and documentation/i,
    });
    expect(helpBtn).toBeInTheDocument();

    helpBtn.focus();
    expect(document.activeElement).toBe(helpBtn);
    fireEvent.click(helpBtn);

    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const closeBtn = screen.getByRole('button', { name: /close help/i });
    // Await initial delayed focus moving inside dialog
    await waitFor(() => {
      expect(document.activeElement).toBe(closeBtn);
    });

    // Closing help dismisses modal and restores focus to trigger button
    fireEvent.click(closeBtn);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(document.activeElement).toBe(helpBtn);
    });
  });

  it('supports accessible Help panel opening and dismissal from sidebar footer', async () => {
    render(<App />);

    const helpBtn = screen.getByRole('button', {
      name: /open help and documentation/i,
    });
    expect(helpBtn).toBeInTheDocument();
    expect(helpBtn).toHaveAttribute('aria-label', 'Open help and documentation');

    helpBtn.focus();
    expect(document.activeElement).toBe(helpBtn);
    fireEvent.click(helpBtn);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: /^help$/i })).toBeInTheDocument();

    const closeBtn = screen.getByRole('button', { name: /close help/i });
    await waitFor(() => {
      expect(document.activeElement).toBe(closeBtn);
    });

    // Dismissing Help restores focus to Help button
    fireEvent.click(closeBtn);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(document.activeElement).toBe(helpBtn);
    });
  });

  it('does not render rejected or premature shell controls (no login, mock mode, or image upload)', () => {
    const { container } = render(<App />);

    const textContent = container.textContent?.toLowerCase() ?? '';
    expect(textContent).not.toContain('sign in');
    expect(textContent).not.toContain('log in');
    expect(textContent).not.toContain('account');
    expect(textContent).not.toContain('subscription');
    expect(textContent).not.toContain('mock mode');
    expect(textContent).not.toContain('upload image');
  });

  it('contains no secret input fields, server configuration, or license fields anywhere in the shell', () => {
    const { container } = render(<App />);

    const inputs = container.querySelectorAll('input[type="password"], input[name*="license"]');
    expect(inputs).toHaveLength(0);

    const appText = container.textContent?.toLowerCase() ?? '';
    expect(appText).not.toContain('server url');
    expect(appText).not.toContain('license key');
    expect(appText).not.toContain('password');
    expect(appText).not.toContain('telemetry');
    expect(appText).not.toContain('account settings');
    expect(appText).not.toContain('diagnostics & settings');
  });

  it('disables Batch run button and displays busy warning in Batch view when Generation is busy in App', async () => {
    const invokeSpy = vi.fn(async (cmd: string) => {
      if (cmd === 'plugin:event|listen') {
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
          run: {
            requestId: 'gen-active-app',
            state: 'running',
            phase: 'generation_started',
            engineStatus: 'accepted',
            reason: null,
            resultAccess: 'none',
            cleanup: 'no_failure_observed',
            closeRequested: false,
            warnings: [],
          },
        } satisfies GenerationSnapshot;
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
          run: null,
        } satisfies BatchSnapshot;
      }
      return;
    });

    mockIPC(invokeSpy);

    try {
      render(<App />);

      // Switch to Batch
      const batchBtn = screen.getByRole('button', { name: /^batch/i });
      fireEvent.click(batchBtn);

      // Start Batch Run button must be disabled and warning displayed
      await waitFor(() => {
        const startBatchBtn = screen.getByRole('button', { name: /start batch run/i });
        expect(startBatchBtn).toBeDisabled();
        expect(
          screen.getByText(/a cad model generation is currently running/i)
        ).toBeInTheDocument();
      });
    } finally {
      clearMocks();
    }
  });

  it('disables Generation run button and displays busy warning in Generate view when Batch is busy in App', async () => {
    const invokeSpy = vi.fn(async (cmd: string) => {
      if (cmd === 'plugin:event|listen') {
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
          output: {
            selectionId: 'out-1',
            displayPath: 'C:/exports',
          },
          run: null,
        } satisfies GenerationSnapshot;
      }
      if (cmd === 'batch_snapshot') {
        return {
          revision: 1,
          nativeAvailable: true,
          source: null,
          output: null,
          run: {
            requestId: 'batch-active-app',
            state: 'running',
            engineStatus: null,
            phase: 'batch_started',
            totalFiles: 5,
            completedFiles: 1,
            currentFile: 'test.par',
            currentFormat: 'step',
            lastFileStatus: null,
            cleanup: 'no_failure_observed',
            closeRequested: false,
            manifestState: 'not_applicable',
            reason: null,
          },
        } satisfies BatchSnapshot;
      }
      return;
    });

    mockIPC(invokeSpy);

    try {
      render(<App />);

      // Default view is Generate. Submit button must be disabled due to isBatchBusy
      await waitFor(() => {
        const startGenBtn = screen.getByRole('button', { name: /run cad generation/i });
        expect(startGenBtn).toBeDisabled();
        expect(
          screen.getByText(/a batch conversion operation is currently running/i)
        ).toBeInTheDocument();
      });
    } finally {
      clearMocks();
    }
  });
});
