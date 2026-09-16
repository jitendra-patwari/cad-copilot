import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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

  it('does not render rejected or premature controls (no login, mock mode, or image upload)', () => {
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
