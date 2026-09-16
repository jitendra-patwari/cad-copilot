import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { App } from '../../src/app/App';

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
