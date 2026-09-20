import type { BatchPhase, BatchRunState, BatchSourceSelection } from './types';

export interface FormatTarget {
  readonly id: string;
  readonly ext: string;
  readonly name: string;
  readonly note: string;
}

export interface OperationGroup {
  readonly id: 'export_3d' | 'publish_drawing';
  readonly operation: string;
  readonly description: string;
  readonly formats: readonly FormatTarget[];
}

export const CANONICAL_OPERATIONS: readonly OperationGroup[] = [
  {
    id: 'export_3d',
    operation: '3D CAD Export',
    description: 'Solid model kernel and exchange geometry targets',
    formats: [
      { id: 'step', ext: '.step', name: 'STEP', note: 'Universal CAD exchange' },
      { id: 'stl', ext: '.stl', name: 'STL', note: 'Triangulated facet mesh' },
      { id: 'parasolid', ext: '.x_t', name: 'Parasolid', note: 'Native geometric kernel' },
    ],
  },
  {
    id: 'publish_drawing',
    operation: 'Drawing Publication',
    description: '2D drafting, sheet documentation, and vector publication',
    formats: [
      { id: 'pdf', ext: '.pdf', name: 'Drawing PDF', note: 'Vector sheet publication' },
      { id: 'dxf', ext: '.dxf', name: 'Drawing DXF', note: '2D drafting vector exchange' },
    ],
  },
] as const;

export function isBatchRunActive(state?: BatchRunState | null): boolean {
  if (!state) return false;
  return state === 'starting' || state === 'running' || state === 'cancelling';
}

export function isBatchTerminal(state?: BatchRunState | null): boolean {
  if (!state) return false;
  return state === 'terminal';
}

export function getEligibleCount(
  source: BatchSourceSelection | null,
  operation: 'export_3d' | 'publish_drawing'
): number {
  if (!source) return 0;
  if (operation === 'export_3d') {
    return source.parCount + source.psmCount + source.asmCount;
  }
  if (operation === 'publish_drawing') {
    return source.dftCount;
  }
  return 0;
}

export function formatPhase(phase?: BatchPhase | null): string {
  switch (phase) {
    case 'batch_started':
      return 'Batch Started';
    case 'file_started':
      return 'Processing File';
    case 'format_started':
      return 'Exporting Format';
    case 'format_finished':
      return 'Format Finished';
    case 'file_finished':
      return 'File Finished';
    case 'batch_finished':
      return 'Batch Finished';
    default:
      return 'Idle';
  }
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  const val = bytes / Math.pow(k, i);
  return `${val.toFixed(i === 0 ? 0 : 1)} ${sizes[i]}`;
}
