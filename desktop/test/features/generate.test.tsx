import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { App } from '../../src/app/App';

describe('Generate Workspace Geometry Scope', () => {
  it('renders verified geometric capabilities and avoids unpromoted operations', () => {
    render(<App />);

    // Verify verified base geometries and features are present
    expect(screen.getByText(/rectangular prisms/i)).toBeInTheDocument();
    expect(screen.getByText(/circular holes/i)).toBeInTheDocument();
    expect(screen.getByText(/slot cutouts/i)).toBeInTheDocument();
    expect(screen.getByText(/rectangular pads/i)).toBeInTheDocument();
    expect(screen.getByText(/spur gears/i)).toBeInTheDocument();

    // Verify unpromoted revolves and hole patterns are strictly absent
    const textContent =
      screen.getByRole('region', { name: /geometric capabilities/i }).textContent?.toLowerCase() ??
      '';
    expect(textContent).not.toContain('revolve');
    expect(textContent).not.toContain('hole pattern');
  });
});
