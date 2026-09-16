import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App } from '../../src/app/App';

describe('Application Shell Navigation', () => {
  it('renders CAD Copilot header and default Generate view', () => {
    render(<App />);

    expect(screen.getByText('CAD Copilot')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { level: 1, name: /cad model generation/i })
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
      screen.getByRole('heading', { level: 1, name: /cad model generation/i })
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

  it('does not render premature operational controls in Generate view', () => {
    render(<App />);

    const main = screen.getByRole('main');
    expect(main.querySelectorAll('textarea, input, select, canvas, button')).toHaveLength(0);

    const mainText = main.textContent?.toLowerCase() ?? '';
    expect(mainText).not.toContain('viewport');
    expect(mainText).not.toContain('gemini-2');
  });

  it('does not render premature operational controls in Batch view', () => {
    render(<App />);

    const batchBtn = screen.getByRole('button', { name: /^batch/i });
    fireEvent.click(batchBtn);

    const main = screen.getByRole('main');
    expect(
      main.querySelectorAll('textarea, input, select, canvas, button, table, form')
    ).toHaveLength(0);

    const mainText = main.textContent?.toLowerCase() ?? '';
    expect(mainText).not.toContain('start batch');
    expect(mainText).not.toContain('browse folder');
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

  it('supports accessible navigation and diagnostics opening when sidebar is collapsed', async () => {
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
      screen.getByRole('heading', { level: 1, name: /cad model generation/i })
    ).toBeInTheDocument();
    expect(generateBtn).toHaveAttribute('aria-current', 'page');

    // Opening settings/diagnostics panel while collapsed works as expected
    const diagnosticsBtn = screen.getByRole('button', {
      name: /open settings and diagnostics/i,
    });
    expect(diagnosticsBtn).toBeInTheDocument();
    expect(diagnosticsBtn).toHaveAttribute('aria-label', 'Open settings and diagnostics');

    diagnosticsBtn.focus();
    expect(document.activeElement).toBe(diagnosticsBtn);
    fireEvent.click(diagnosticsBtn);

    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const closeBtn = screen.getByRole('button', { name: /close diagnostics/i });
    // Await initial delayed focus moving inside dialog
    await waitFor(() => {
      expect(document.activeElement).toBe(closeBtn);
    });

    // Closing diagnostics dismisses modal and restores focus to trigger button
    fireEvent.click(closeBtn);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(document.activeElement).toBe(diagnosticsBtn);
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
});
