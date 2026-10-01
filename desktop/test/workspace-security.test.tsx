import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { App } from '../src/app/App';

describe('Root workspace discovery and script behavior', () => {
  const rootDir = path.resolve(__dirname, '../../');
  const rootPackageJsonPath = path.resolve(rootDir, 'package.json');
  const desktopPackageJsonPath = path.resolve(__dirname, '../package.json');
  const enginePackageJsonPath = path.resolve(rootDir, 'engine/package.json');
  const workspaceYamlPath = path.resolve(rootDir, 'pnpm-workspace.yaml');

  const rootPackageJson = JSON.parse(fs.readFileSync(rootPackageJsonPath, 'utf-8'));
  const desktopPackageJson = JSON.parse(fs.readFileSync(desktopPackageJsonPath, 'utf-8'));
  const enginePackageJson = JSON.parse(fs.readFileSync(enginePackageJsonPath, 'utf-8'));
  const workspaceYaml = fs.readFileSync(workspaceYamlPath, 'utf-8');

  it('declares desktop and engine as explicit workspace packages in pnpm-workspace.yaml', () => {
    expect(workspaceYaml).toMatch(/^\s*-\s*['"]?desktop['"]?\s*$/m);
    expect(workspaceYaml).toMatch(/^\s*-\s*['"]?engine['"]?\s*$/m);
  });

  it('provides exact filtered desktop convenience scripts in root package.json', () => {
    const scripts = rootPackageJson.scripts;
    expect(scripts['dev:desktop']).toBe('pnpm --filter @cad-copilot/desktop dev');
    expect(scripts['test:desktop']).toBe('pnpm --filter @cad-copilot/desktop test');
    expect(scripts['lint:desktop']).toBe('pnpm --filter @cad-copilot/desktop lint');
    expect(scripts['typecheck:desktop']).toBe('pnpm --filter @cad-copilot/desktop typecheck');
    expect(scripts['format:desktop']).toBe('pnpm --filter @cad-copilot/desktop format');
    expect(scripts['format:check:desktop']).toBe('pnpm --filter @cad-copilot/desktop format:check');
    expect(scripts['build:desktop']).toBe('pnpm --filter @cad-copilot/desktop build');
  });

  it('composes root test safely from engine offline test and desktop test', () => {
    const rootScripts = rootPackageJson.scripts;
    expect(rootScripts['test']).toBe('pnpm test:engine:offline && pnpm test:desktop');
    expect(rootScripts['test:engine:offline']).toBe(
      'pnpm --filter @cad-copilot/engine test:offline'
    );

    const engineScripts = enginePackageJson.scripts;
    expect(engineScripts['test:offline']).toBe('pytest -m "not com and not live_ai"');
  });

  it('enforces one-shot script commands without watch mode in desktop package.json', () => {
    const scripts = desktopPackageJson.scripts;
    expect(scripts.test).toBe('vitest run');
    expect(scripts.lint).toBe('eslint . --max-warnings=0');
    expect(scripts.typecheck).toBe('tsc -b --noEmit');
    expect(scripts['format:check']).toBe('prettier --check .');
  });

  it('declares exact deterministic desktop packaging scripts in engine and root package.json', () => {
    expect(enginePackageJson.scripts['package:desktop']).toBe(
      '..\\.venv\\Scripts\\python.exe scripts/package_desktop_engine.py'
    );
    expect(rootPackageJson.scripts['package:desktop']).toBe(
      'pnpm --filter @cad-copilot/engine package:desktop && .\\.venv\\Scripts\\python.exe scripts/package_desktop.py'
    );
  });
});

describe('Zero runtime network and zero persistent storage baseline', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;
  let xhrSpy: ReturnType<typeof vi.spyOn>;
  let wsSpy: ReturnType<typeof vi.spyOn>;
  let tauriInvokeSpy: ReturnType<typeof vi.fn>;
  let tauriIpcSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();

    // Blocking network test doubles
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
      throw new Error('Blocked unexpected network fetch in test environment');
    });

    xhrSpy = vi.spyOn(window.XMLHttpRequest.prototype, 'open').mockImplementation(() => {
      throw new Error('Blocked unexpected XMLHttpRequest in test environment');
    });

    wsSpy = vi.spyOn(globalThis, 'WebSocket').mockImplementation(() => {
      throw new Error('Blocked unexpected WebSocket in test environment');
    });

    // Native IPC test doubles
    tauriInvokeSpy = vi.fn((cmd: string, args?: unknown) => {
      if (cmd === 'generation_snapshot') {
        return Promise.resolve({
          revision: 1,
          nativeAvailable: true,
          keyConfigured: false,
          output: null,
          run: null,
        });
      }
      if (cmd === 'batch_snapshot') {
        return Promise.resolve({
          revision: 1,
          nativeAvailable: true,
          source: null,
          output: null,
          run: null,
        });
      }
      if (cmd === 'generation_set_key') {
        const payload = args as { request?: { key?: string | null } } | undefined;
        return Promise.resolve({ configured: Boolean(payload?.request?.key) });
      }
      throw new Error(`Blocked unexpected native Tauri IPC invoke: ${cmd}`);
    });
    tauriIpcSpy = vi.fn(() => {
      throw new Error('Blocked unexpected native Tauri IPC postMessage');
    });

    Object.defineProperty(window, '__TAURI_INTERNALS__', {
      value: { invoke: tauriInvokeSpy },
      configurable: true,
      writable: true,
    });
    Object.defineProperty(window, '__TAURI_IPC__', {
      value: tauriIpcSpy,
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    xhrSpy.mockRestore();
    wsSpy.mockRestore();
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    delete (window as unknown as Record<string, unknown>).__TAURI_IPC__;
    localStorage.clear();
    sessionStorage.clear();
  });

  it('makes zero network requests and no automatic generation calls during render, navigation, collapse/expand, and diagnostics', async () => {
    const { getByRole } = render(<App />);

    // Collapse sidebar
    const collapseBtn = getByRole('button', { name: /collapse sidebar/i });
    fireEvent.click(collapseBtn);

    // Navigate while collapsed
    const batchBtn = getByRole('button', { name: /^batch/i });
    fireEvent.click(batchBtn);

    const generateBtn = getByRole('button', { name: /^generate/i });
    fireEvent.click(generateBtn);

    // Open and close help modal while collapsed
    const helpBtn = getByRole('button', { name: /open help and documentation/i });
    fireEvent.click(helpBtn);

    const closeBtn = getByRole('button', { name: /close help/i });
    fireEvent.click(closeBtn);

    // Expand sidebar back
    const expandBtn = getByRole('button', { name: /expand sidebar/i });
    fireEvent.click(expandBtn);

    // Settle effects before asserting
    await new Promise((resolve) => setTimeout(resolve, 20));

    // Verify zero network calls
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(xhrSpy).not.toHaveBeenCalled();
    expect(wsSpy).not.toHaveBeenCalled();
    expect(tauriIpcSpy).not.toHaveBeenCalled();

    // Verify no automatic generation or batch execution was launched
    const invokedCommands = tauriInvokeSpy.mock.calls.map((c) => c[0]);
    expect(invokedCommands).not.toContain('generation_start');
    expect(invokedCommands).not.toContain('batch_start');
    expect(
      invokedCommands.every((c) => c === 'generation_snapshot' || c === 'batch_snapshot')
    ).toBe(true);
  });

  it('creates zero persistent settings, keys, or browser storage entries across all interactions including key configuration and draft clearing', async () => {
    render(<App />);

    // 1. Switch to Prompt mode and enter a draft API key
    const promptTabBtn = screen.getByRole('button', { name: /prompt to cad/i });
    fireEvent.click(promptTabBtn);

    const keyInput = screen.getByPlaceholderText(/enter gemini api key/i);
    fireEvent.change(keyInput, { target: { value: 'AIzaSyDraftSecretKey123' } });
    expect(keyInput).toHaveValue('AIzaSyDraftSecretKey123');

    // Storage remains strictly empty while drafting
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);

    // 2. Collapse sidebar
    const collapseBtn = screen.getByRole('button', { name: /collapse sidebar/i });
    fireEvent.click(collapseBtn);

    // 3. Switch away to Batch view - this must immediately wipe the draft key in memory
    const batchBtn = screen.getByRole('button', { name: /^batch/i });
    fireEvent.click(batchBtn);

    // Storage remains strictly empty in Batch
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);

    // 4. Interact with Help modal
    const helpBtn = screen.getByRole('button', { name: /open help and documentation/i });
    fireEvent.click(helpBtn);
    const closeBtn = screen.getByRole('button', { name: /close help/i });
    fireEvent.click(closeBtn);

    // 5. Expand sidebar back
    const expandBtn = screen.getByRole('button', { name: /expand sidebar/i });
    fireEvent.click(expandBtn);

    // 6. Navigate back to Generate view and verify draft key was wiped clean
    const generateBtn = screen.getByRole('button', { name: /^generate/i });
    fireEvent.click(generateBtn);
    fireEvent.click(screen.getByRole('button', { name: /prompt to cad/i }));

    const reopenedInput = screen.getByPlaceholderText(/enter gemini api key/i);
    expect(reopenedInput).toHaveValue('');

    // 7. Save a key to native session memory via IPC
    fireEvent.change(reopenedInput, { target: { value: 'AIzaSySavedKey789' } });
    const saveBtn = screen.getByRole('button', { name: /^save$/i });
    fireEvent.click(saveBtn);

    // Await completion of async save handler and verify configured state in UI
    await waitFor(() => {
      expect(screen.getByText('Configured (Session memory)')).toBeInTheDocument();
    });

    expect(tauriInvokeSpy).toHaveBeenCalledWith(
      'generation_set_key',
      { request: { key: 'AIzaSySavedKey789' } },
      undefined
    );

    // Storage remains strictly 0 after saving finishes
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);

    // 8. Open key editor, clear key, and verify null key is transmitted via IPC
    const keyConfigHeader = screen.getByRole('button', {
      name: /google gemini api configuration/i,
    });
    fireEvent.click(keyConfigHeader);

    const clearBtn = await screen.findByRole('button', { name: /^clear$/i });
    fireEvent.click(clearBtn);

    // Wait for unconfigured key badge to reappear
    await waitFor(() => {
      expect(screen.getByText(/key required for prompt/i)).toBeInTheDocument();
    });

    expect(tauriInvokeSpy).toHaveBeenCalledWith(
      'generation_set_key',
      { request: { key: null } },
      undefined
    );

    // Storage remains strictly 0 after clearing
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});
