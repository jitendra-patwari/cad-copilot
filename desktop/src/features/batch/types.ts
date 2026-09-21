/**
 * Types and contracts for CAD Copilot Batch vertical slice (FR-22).
 * Aligned 1-to-1 with native Rust DTOs in desktop/src-tauri/src/batch/types.rs.
 */

export type BatchRunState = 'starting' | 'running' | 'cancelling' | 'terminal';

export type BatchEngineStatus = 'completed' | 'cancelled' | 'rejected' | 'failed';

export type BatchPhase =
  | 'batch_started'
  | 'file_started'
  | 'format_started'
  | 'format_finished'
  | 'file_finished'
  | 'batch_finished';

export type ManifestState = 'not_applicable' | 'checking' | 'validated' | 'unavailable';

export type CleanupState = 'no_failure_observed' | 'incomplete';

export type SourceSelectMode = 'files' | 'folder';

export type CloseDecision = 'stay' | 'cancel_and_close';

export interface BatchSourceSelection {
  selectionId: string;
  displayRoot: string;
  parCount: number;
  psmCount: number;
  asmCount: number;
  dftCount: number;
  skippedCount: number;
  previewFiles: string[];
}

export interface BatchSelectSourceResponse {
  selectionId: string;
  displayRoot: string;
  parCount: number;
  psmCount: number;
  asmCount: number;
  dftCount: number;
  skippedCount: number;
  files: string[];
}

export interface BatchOutputSelection {
  selectionId: string;
  displayPath: string;
}

export interface BatchRunSnapshot {
  requestId: string;
  state: BatchRunState;
  engineStatus: BatchEngineStatus | null;
  phase: BatchPhase | null;
  totalFiles: number;
  completedFiles: number;
  currentFile: string | null;
  currentFormat: string | null;
  lastFileStatus: string | null;
  cleanup: CleanupState;
  closeRequested: boolean;
  manifestState: ManifestState;
  reason: string | null;
}

export type EngineBuildProvenance = 'source_build' | 'bundled_build';

export interface EngineBuildInfo {
  version: string;
  provenance: EngineBuildProvenance;
}

export interface BatchSnapshot {
  revision: number;
  nativeAvailable: boolean;
  source: BatchSourceSelection | null;
  output: BatchOutputSelection | null;
  run: BatchRunSnapshot | null;
  engineBuild?: EngineBuildInfo;
}

export interface BatchStartRequest {
  sourceSelectionId: string;
  outputSelectionId: string;
  operation: string;
  formats: string[];
  continueOnError: boolean;
  maxFiles: number;
}

export interface BatchSummaryRecord {
  total: number;
  accepted: number;
  partial: number;
  failed: number;
  cancelled: number;
  unprocessed: number;
}

export interface BatchArtifactRecord {
  format: string;
  relativePath: string;
  sizeBytes: number;
}

export type BatchRowCategory = 'succeeded' | 'partial' | 'failed' | 'cancelled' | 'unprocessed';

export interface BatchFileRowRecord {
  file: string;
  category: BatchRowCategory;
  attemptedFormats: string[];
  successfulArtifacts: BatchArtifactRecord[];
  diagnosticCodes: string[];
}

export interface BatchResultResponse {
  requestId: string;
  engineStatus: BatchEngineStatus;
  operation: string;
  summary: BatchSummaryRecord;
  rows: BatchFileRowRecord[];
  manifestState: ManifestState;
  manifestPath: string | null;
  reason: string | null;
  engineVersion?: string;
  cadRuntimeVersion?: string;
}

export interface BatchRevealResponse {
  acknowledged: boolean;
  target: string;
}

export interface CommandError {
  code: string;
  message: string;
  field?: string;
}
