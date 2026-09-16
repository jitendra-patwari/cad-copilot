import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ErrorBoundary } from '../../src/components/ErrorBoundary';

const ThrowingComponent = ({ shouldThrow = true }: { shouldThrow?: boolean }) => {
  if (shouldThrow) {
    throw new Error('INTERNAL_SECRET_TOKEN_SENTINEL_XYZ123');
  }
  return <div>Healthy Child Content</div>;
};

describe('ErrorBoundary', () => {
  const originalConsoleError = console.error;

  beforeEach(() => {
    console.error = vi.fn();
  });

  afterEach(() => {
    console.error = originalConsoleError;
  });

  it('renders children when no error occurs', () => {
    render(
      <ErrorBoundary>
        <div>Normal Content</div>
      </ErrorBoundary>
    );

    expect(screen.getByText('Normal Content')).toBeInTheDocument();
  });

  it('catches render errors and renders sanitized FatalErrorView fallback', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { level: 1, name: /application error/i })
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reload application/i })).toBeInTheDocument();
  });

  it('strictly suppresses raw exception messages, tokens, and stack traces from DOM', () => {
    const { container } = render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    const renderedHtml = container.innerHTML;
    expect(renderedHtml).not.toContain('INTERNAL_SECRET_TOKEN_SENTINEL_XYZ123');
    expect(renderedHtml).not.toContain('Error:');
    expect(renderedHtml).not.toContain('at ThrowingComponent');
  });

  it('invokes onReload callback when provided and reload is clicked', () => {
    const handleReload = vi.fn();
    render(
      <ErrorBoundary onReload={handleReload}>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    const reloadButton = screen.getByRole('button', { name: /reload application/i });
    fireEvent.click(reloadButton);

    expect(handleReload).toHaveBeenCalledTimes(1);
  });

  it('triggers window.location.reload by default when no onReload callback is provided', () => {
    const originalLocation = window.location;
    const reloadMock = vi.fn();

    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...originalLocation, reload: reloadMock },
    });

    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    const reloadButton = screen.getByRole('button', { name: /reload application/i });
    fireEvent.click(reloadButton);

    expect(reloadMock).toHaveBeenCalledTimes(1);

    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    });
  });
});
