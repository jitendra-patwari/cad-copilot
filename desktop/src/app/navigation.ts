export type ViewMode = 'generate' | 'batch';

export interface NavItem {
  readonly id: ViewMode;
  readonly label: string;
  readonly description: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: 'generate',
    label: 'Generate',
    description: 'Single CAD part parametric generation',
  },
  {
    id: 'batch',
    label: 'Batch',
    description: 'Sequential batch operations and format exports',
  },
] as const;
