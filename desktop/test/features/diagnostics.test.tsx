import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App } from '../../src/app/App';

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

  it('returns focus to trigger button when closed', async () => {
    render(<App />);

    const triggerBtn = screen.getByRole('button', {
      name: /open settings and diagnostics/i,
    });
    triggerBtn.focus();
    expect(document.activeElement).toBe(triggerBtn);

    fireEvent.click(triggerBtn);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const closeBtn = screen.getByRole('button', { name: /close diagnostics/i });
    fireEvent.click(closeBtn);

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
    const inputs = dialog.querySelectorAll('input');
    expect(inputs).toHaveLength(0);

    const dialogText = dialog.textContent?.toLowerCase() ?? '';
    expect(dialogText).not.toContain('api key');
    expect(dialogText).not.toContain('server url');
    expect(dialogText).not.toContain('license');
    expect(dialogText).not.toContain('password');
  });
});
