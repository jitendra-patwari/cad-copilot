/**
 * Types and contracts for CAD Copilot Generation vertical slice (FR-21).
 * Aligned 1-to-1 with native Rust DTOs in desktop/src-tauri/src/generation/types.rs.
 */

export type RunState =
  'starting' | 'running' | 'cancelling' | 'succeeded' | 'rejected' | 'failed' | 'cancelled';

export type RunPhase =
  'request_received' | 'request_validated' | 'generation_started' | 'response_ready';

export type EngineStatus = 'accepted' | 'rejected' | 'failed';

export type ResultAccess = 'none' | 'checking' | 'ready' | 'unavailable';

export type CleanupState = 'not_started' | 'pending' | 'no_failure_observed' | 'incomplete';

export interface GenerationOutputSelection {
  selectionId: string;
  displayPath: string;
}

export interface GenerationRunSnapshot {
  requestId: string;
  state: RunState;
  phase: RunPhase | null;
  engineStatus: EngineStatus | null;
  reason: string | null;
  resultAccess: ResultAccess;
  cleanup: CleanupState;
  closeRequested: boolean;
  warnings: string[];
}

export interface GenerationSnapshot {
  revision: number;
  nativeAvailable: boolean;
  keyConfigured: boolean;
  output: GenerationOutputSelection | null;
  run: GenerationRunSnapshot | null;
}

export type GenerationInput =
  { kind: 'example_plan'; exampleId: 'spur_gear' } | { kind: 'prompt_to_cad'; prompt: string };

export interface GenerationArtifactRecord {
  format: string;
  filename: string;
  path: string;
  sizeBytes: number;
}

export interface ManifestDiagnosticItem {
  severity: string;
  code: string;
  message: string;
}

export interface ManifestSummary {
  schemaVersion: string;
  provenanceKind: string;
  sourceId: string | null;
  cadRuntimeVersion: string | null;
  operationsExecuted: number;
  planSha256: string;
  promptSha256: string | null;
  warnings: string[];
  diagnostics: ManifestDiagnosticItem[];
}

export interface GenerationResultResponse {
  requestId: string;
  runFolder: string;
  artifacts: GenerationArtifactRecord[];
  hasPreview: boolean;
  manifestSummary: ManifestSummary | null;
}

export interface CommandError {
  code: string;
  message: string;
  field?: string;
}
