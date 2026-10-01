export interface HelpSection {
  readonly id: string;
  readonly title: string;
  readonly description: string;
  readonly items: readonly {
    readonly heading: string;
    readonly text: string;
  }[];
}

export const GEOMETRY_SCOPE: HelpSection = {
  id: 'geometry_scope',
  title: 'Supported Geometry',
  description:
    'Create native, editable 3D CAD models in Siemens Solid Edge (Ordered mode). All modeling executes 100% locally on your machine.',
  items: [
    {
      heading: 'Base Shapes',
      text: 'Rectangular blocks, cylindrical bodies, and custom spur gears.',
    },
    {
      heading: 'Features & Cutouts',
      text: 'Circular through-holes, rectangular cutouts, slot cutouts, and raised rectangular mounting pads.',
    },
    {
      heading: 'Units & Alignment',
      text: 'All dimensions are defined in millimeters (mm). Base bodies are centered on the primary coordinate plane (XY) for easy assembly alignment.',
    },
    {
      heading: 'Export Formats',
      text: 'Every run generates an editable Solid Edge part (.par), neutral exchange file (.step), and 3D printing mesh (.stl), along with a visual preview thumbnail (.jpg) when view capture is available.',
    },
  ],
} as const;

export const PROMPTING_GUIDE: HelpSection = {
  id: 'prompting_guide',
  title: 'Prompting Tips',
  description: 'Best practices for describing 3D parts to generate accurate CAD models with AI.',
  items: [
    {
      heading: 'Start with a Base Shape',
      text: 'Define the main solid body first—such as a rectangular block, cylinder, or spur gear—before requesting secondary features.',
    },
    {
      heading: 'Specify Dimensions in Millimeters',
      text: 'Include explicit measurements (e.g. "60 x 40 x 15 mm block"). All dimensions default to millimeters (mm) and angles to degrees.',
    },
    {
      heading: 'Position & Place Features',
      text: 'Specify where cutouts, holes, or pads belong (e.g. "Add a centered 10 mm through-hole" or "Add a 25 x 15 x 6 mm pad on the top face").',
    },
  ],
} as const;

export const BATCH_ARCHITECTURE: HelpSection = {
  id: 'batch_architecture',
  title: 'Batch Processing',
  description:
    'Convert multiple 3D models and 2D drawings in bulk with automated export safeguards that protect your original CAD files.',
  items: [
    {
      heading: 'Supported Operations & Formats',
      text: 'Export 3D parts and assemblies (.par, .psm, .asm) to STEP (.step), STL (.stl), or Parasolid (.x_t). Publish 2D drawings (.dft) to vector PDF (.pdf) or DXF (.dxf).',
    },
    {
      heading: 'Original File Protection',
      text: 'Batch operations close source files without saving changes, and verify pre- and post-run file checksums to ensure your original CAD files remain untouched.',
    },
    {
      heading: 'Safe Output & No Overwrites',
      text: 'All converted files are saved exclusively to your chosen output folder. If a file with the same name already exists, it is never overwritten, preventing accidental data loss.',
    },
    {
      heading: 'Batch Controls & Error Handling',
      text: 'Process an entire directory or select individual files. The optional "Continue on error" setting allows the remaining files to finish even if a single file fails.',
    },
  ],
} as const;

export const AUTOMATION_ENVIRONMENT: HelpSection = {
  id: 'automation_environment',
  title: 'Privacy',
  description:
    'CAD Copilot operates with zero telemetry, local CAD execution, strict filesystem boundaries, and in-memory key storage.',
  items: [
    {
      heading: '100% Local CAD Engine',
      text: 'All 3D modeling and export processing execute directly in Siemens Solid Edge on your Windows workstation. No cloud CAD services or external servers are used.',
    },
    {
      heading: 'AI Prompt Boundary',
      text: 'In prompt mode, the text entered into the prompt box is sent to Google Gemini to formulate CAD modeling instructions. Local CAD files, drawings, and directory contents are never uploaded to the cloud—all file scanning, inspection, and CAD execution remain strictly on your machine.',
    },
    {
      heading: 'In-Memory API Key',
      text: 'Your Gemini API key is held strictly in volatile application memory for the active session. It is never saved to disk, registry, browser storage, or log files.',
    },
    {
      heading: 'Output Isolation & File Safety',
      text: 'Exported CAD files are written exclusively to your selected output folder. In batch operations, source files are closed without saving and verified with pre- and post-run checksums.',
    },
  ],
} as const;

export const ALL_HELP_SECTIONS: readonly HelpSection[] = [
  GEOMETRY_SCOPE,
  PROMPTING_GUIDE,
  BATCH_ARCHITECTURE,
  AUTOMATION_ENVIRONMENT,
] as const;
