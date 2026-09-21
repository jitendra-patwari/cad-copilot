import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App } from '../../src/app/App';
import { DiagnosticsPanel } from '../../src/features/diagnostics/DiagnosticsPanel';

describe('Settings & Diagnostics Surface', () => {
  it('is closed by default and opens when trigger button is clicked', () => {
    render(<App />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    const triggerBtn = screen.getByRole('button', {
      name: /open settings and diagnostics/i,
    });
    fireEvent.click(triggerBtn);

    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { level: 2, name: /diagnostics & settings/i })
    ).toBeInTheDocument();
  });

  it('displays all 5 honest foundation status rows', () => {
    render(<App />);

    const triggerBtn = screen.getByRole('button', {
      name: /open settings and diagnostics/i,
    });
    fireEvent.click(triggerBtn);

    expect(screen.getByText('Output Directory')).toBeInTheDocument();
    expect(screen.getByText('Not selected')).toBeInTheDocument();

    expect(screen.getByText('Solid Edge Automation')).toBeInTheDocument();
    expect(screen.getByText('Not checked')).toBeInTheDocument();

    expect(screen.getByText('Gemini AI Integration')).toBeInTheDocument();
    expect(screen.getByText('Not configured')).toBeInTheDocument();

    expect(screen.getByText('AI Model')).toBeInTheDocument();
    expect(screen.getByText('Not loaded')).toBeInTheDocument();

    expect(screen.getByText('Python CAD Engine')).toBeInTheDocument();
    expect(screen.getByText('Not connected')).toBeInTheDocument();
  });

  it('closes via close icon button and dismiss button', () => {
    render(<App />);

    const triggerBtn = screen.getByRole('button', {
      name: /open settings and diagnostics/i,
    });

    // Open & close with close icon button
    fireEvent.click(triggerBtn);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const closeBtn = screen.getByRole('button', { name: /close diagnostics/i });
    fireEvent.click(closeBtn);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // Open & close with Dismiss button
    fireEvent.click(triggerBtn);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const dismissBtn = screen.getByRole('button', { name: /dismiss/i });
    fireEvent.click(dismissBtn);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes when pressing the Escape key', () => {
    render(<App />);

    const triggerBtn = screen.getByRole('button', {
      name: /open settings and diagnostics/i,
    });
    fireEvent.click(triggerBtn);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('traps focus with Tab/Shift+Tab and returns focus to trigger button when closed', async () => {
    render(<App />);

    const triggerBtn = screen.getByRole('button', {
      name: /open settings and diagnostics/i,
    });
    triggerBtn.focus();
    expect(document.activeElement).toBe(triggerBtn);

    fireEvent.click(triggerBtn);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const closeBtn = screen.getByRole('button', { name: /close diagnostics/i });
    const dismissBtn = screen.getByRole('button', { name: /dismiss/i });

    // 1. Await initial delayed focus moving inside dialog
    await waitFor(() => {
      expect(document.activeElement).toBe(closeBtn);
    });

    // 2. Test Shift+Tab wrapping from first element (closeBtn) to last element (dismissBtn)
    fireEvent.keyDown(document, { key: 'Tab', code: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(dismissBtn);

    // 3. Test Tab wrapping from last element (dismissBtn) to first element (closeBtn)
    fireEvent.keyDown(document, { key: 'Tab', code: 'Tab', shiftKey: false });
    expect(document.activeElement).toBe(closeBtn);

    // 4. Close dialog
    fireEvent.click(closeBtn);

    // 5. Verify focus is genuinely restored to trigger button
    await waitFor(() => {
      expect(document.activeElement).toBe(triggerBtn);
    });
  });

  it('contains no secret input fields, server configuration, or license fields', () => {
    render(<App />);

    const triggerBtn = screen.getByRole('button', {
      name: /open settings and diagnostics/i,
    });
    fireEvent.click(triggerBtn);

    const dialog = screen.getByRole('dialog');
    const inputs = dialog.querySelectorAll('input, textarea, select');
    expect(inputs).toHaveLength(0);

    const dialogText = dialog.textContent?.toLowerCase() ?? '';
    expect(dialogText).not.toContain('api key');
    expect(dialogText).not.toContain('server url');
    expect(dialogText).not.toContain('license');
    expect(dialogText).not.toContain('password');
    expect(dialogText).not.toContain('telemetry');
    expect(dialogText).not.toContain('account');
    expect(dialogText).not.toContain('save');
  });

  it('renders truthful engine build provenance for source and bundled builds', () => {
    const { rerender } = render(
      <DiagnosticsPanel
        isOpen={true}
        onClose={() => {}}
        engineBuild={{ version: '0.1.0', provenance: 'source_build' }}
      />
    );

    expect(screen.getByText('Python CAD Engine')).toBeInTheDocument();
    expect(screen.getByText('v0.1.0 (Source)')).toBeInTheDocument();
    expect(
      screen.getByText('Declared source compatibility engine configuration.')
    ).toBeInTheDocument();

    rerender(
      <DiagnosticsPanel
        isOpen={true}
        onClose={() => {}}
        engineBuild={{ version: '0.1.0', provenance: 'bundled_build' }}
        lastRunEngineVersion="0.1.0"
      />
    );

    expect(screen.getByText('v0.1.0 (Bundled)')).toBeInTheDocument();
    expect(
      screen.getByText('Build-stamped bundled engine verified in last run.')
    ).toBeInTheDocument();
  });

  it('formats Solid Edge automation build without duplicate prefix', () => {
    const { rerender } = render(
      <DiagnosticsPanel isOpen={true} onClose={() => {}} lastRunCadBuild="226.00.00.106" />
    );

    expect(screen.getByText('Solid Edge 226.00.00.106')).toBeInTheDocument();

    rerender(
      <DiagnosticsPanel
        isOpen={true}
        onClose={() => {}}
        lastRunCadBuild="Solid Edge 2026 (226.00.00.106)"
      />
    );

    expect(screen.getByText('Solid Edge 2026 (226.00.00.106)')).toBeInTheDocument();
    expect(screen.queryByText(/Solid Edge Solid Edge/)).not.toBeInTheDocument();
  });
});
