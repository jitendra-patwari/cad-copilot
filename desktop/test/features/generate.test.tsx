import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { App } from '../../src/app/App';

describe('Generate Workspace Geometry Scope', () => {
  it('offers an unchecked keep-open checkbox that the user can toggle', () => {
    render(<App />);
    const checkbox = screen.getByRole('checkbox', { name: /keep part open in solid edge/i });
    expect(checkbox).not.toBeChecked();
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();
  });

  it('renders verified geometric capabilities and avoids unpromoted operations in Help panel', () => {
    render(<App />);

    // Open Help panel via sidebar button
    const helpBtn = screen.getByRole('button', { name: /open help and documentation/i });
    fireEvent.click(helpBtn);

    // Verify verified base geometries and features are present
    expect(screen.getByText(/rectangular blocks/i)).toBeInTheDocument();
    expect(screen.getByText(/circular through-holes/i)).toBeInTheDocument();
    expect(screen.getByText(/slot cutouts/i)).toBeInTheDocument();
    expect(screen.getByText(/rectangular mounting pads/i)).toBeInTheDocument();
    expect(screen.getByText(/spur gears/i)).toBeInTheDocument();

    // Verify unpromoted revolves and hole patterns are strictly absent
    const textContent =
      screen.getByRole('region', { name: /geometric capabilities/i }).textContent?.toLowerCase() ??
      '';
    expect(textContent).not.toContain('revolve');
    expect(textContent).not.toContain('hole pattern');
  });

  it('renders CAD Geometry Preview placeholder in the right column when idle', () => {
    render(<App />);

    expect(screen.getByRole('region', { name: /cad geometry preview/i })).toBeInTheDocument();
    expect(screen.getByText(/your generated 3d cad preview, part files/i)).toBeInTheDocument();
  });
});
