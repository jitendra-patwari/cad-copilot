export type DiagnosticStatus = 'neutral' | 'unavailable';

export interface DiagnosticItem {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly status: DiagnosticStatus;
  readonly hint: string;
}

export const FOUNDATION_DIAGNOSTICS: readonly DiagnosticItem[] = [
  {
    id: 'output_dir',
    label: 'Output Directory',
    value: 'Not selected',
    status: 'neutral',
    hint: 'Directory configured per generation or batch run.',
  },
  {
    id: 'solid_edge',
    label: 'Solid Edge Automation',
    value: 'Not checked',
    status: 'neutral',
    hint: 'Local Siemens Solid Edge installation check.',
  },
  {
    id: 'gemini',
    label: 'Gemini AI Integration',
    value: 'Not configured',
    status: 'neutral',
    hint: 'Generative geometric plan proposal integration.',
  },
  {
    id: 'model',
    label: 'AI Model',
    value: 'Not loaded',
    status: 'neutral',
    hint: 'Geometric reasoning and feature proposal model.',
  },
  {
    id: 'engine_version',
    label: 'Python CAD Engine',
    value: 'Not connected',
    status: 'unavailable',
    hint: 'Local Python CAD automation engine.',
  },
] as const;
