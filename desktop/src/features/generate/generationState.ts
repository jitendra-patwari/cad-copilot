import type { RunPhase, RunState } from './types';

/**
 * Validates a prompt string for CAD generation according to engine and native constraints.
 * - Must be non-whitespace
 * - Maximum 8,000 Unicode code points (calculated via code point iteration)
 * - Maximum estimated UTF-8 size of 128 KiB
 */
export function validatePrompt(prompt: string): { valid: boolean; error?: string } {
  const trimmed = prompt.trim();
  if (!trimmed) {
    return { valid: false, error: 'Prompt must not be empty.' };
  }

  // Count Unicode code points (handling surrogate pairs correctly)
  const codePoints = Array.from(trimmed).length;
  if (codePoints > 8000) {
    return {
      valid: false,
      error: `Prompt exceeds maximum length of 8,000 characters (${codePoints.toLocaleString()} characters entered).`,
    };
  }

  // UTF-8 byte estimate
  const utf8Bytes = new TextEncoder().encode(trimmed).length;
  if (utf8Bytes > 131072) {
    return {
      valid: false,
      error: 'Prompt exceeds maximum encoded payload limit of 128 KiB.',
    };
  }

  return { valid: true };
}

/**
 * Formats byte sizes into human-readable strings (e.g., "14.2 KB", "1.5 MB").
 */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const val = bytes / Math.pow(k, i);
  return `${val.toFixed(i === 0 ? 0 : 1)} ${sizes[i]}`;
}

/**
 * Returns true if the run is in an active in-flight lifecycle state.
 */
export function isRunActive(state?: RunState | null): boolean {
  if (!state) return false;
  return state === 'starting' || state === 'running' || state === 'cancelling';
}

/**
 * Returns true if the run has reached a terminal state.
 */
export function isTerminalState(state?: RunState | null): boolean {
  if (!state) return false;
  return (
    state === 'succeeded' || state === 'rejected' || state === 'failed' || state === 'cancelled'
  );
}

/**
 * Curated phase definitions for generation progress display.
 */
export interface PhaseStep {
  phase: RunPhase;
  label: string;
  description: string;
}

export const GENERATION_PHASES: PhaseStep[] = [
  {
    phase: 'request_received',
    label: 'Request Received',
    description: 'Envelope verified and reserved by host supervisor',
  },
  {
    phase: 'request_validated',
    label: 'Plan Validated',
    description: 'Geometric specifications verified against engine rules',
  },
  {
    phase: 'generation_started',
    label: 'Generating Model',
    description: 'Executing feature operations in Siemens Solid Edge',
  },
  {
    phase: 'response_ready',
    label: 'Packaging Artifacts',
    description: 'Correlating exports, digests, and execution manifest',
  },
];
