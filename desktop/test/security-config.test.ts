import { describe, it, expect } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';

describe('Tauri security configuration baseline', () => {
  const tauriConfPath = path.resolve(__dirname, '../src-tauri/tauri.conf.json');
  const capabilityPath = path.resolve(__dirname, '../src-tauri/capabilities/default.json');
  const cargoTomlPath = path.resolve(__dirname, '../src-tauri/Cargo.toml');
  const mainRsPath = path.resolve(__dirname, '../src-tauri/src/main.rs');
  const libRsPath = path.resolve(__dirname, '../src-tauri/src/lib.rs');
  const viteConfigPath = path.resolve(__dirname, '../vite.config.ts');

  const tauriConf = JSON.parse(fs.readFileSync(tauriConfPath, 'utf-8'));
  const capability = JSON.parse(fs.readFileSync(capabilityPath, 'utf-8'));
  const cargoToml = fs.readFileSync(cargoTomlPath, 'utf-8');
  const mainRs = fs.readFileSync(mainRsPath, 'utf-8');
  const libRs = fs.readFileSync(libRsPath, 'utf-8');
  const viteConfig = fs.readFileSync(viteConfigPath, 'utf-8');

  it('has bundle.active disabled for source-run foundation', () => {
    expect(tauriConf.bundle?.active).toBe(false);
  });

  it('configures devUrl to port 1420 matching Vite strict port in vite.config.ts', () => {
    expect(viteConfig).toMatch(/port:\s*1420/);
    expect(viteConfig).toMatch(/strictPort:\s*true/);
    expect(tauriConf.build?.devUrl).toBe('http://localhost:1420');
    expect(tauriConf.build?.beforeDevCommand).toBe('pnpm dev');
  });

  it('enforces strict production CSP without wildcard, remote origins, unsafe-inline, unsafe-eval, or data:', () => {
    const csp = tauriConf.app?.security?.csp;
    expect(csp).toBeDefined();
    expect(csp).not.toContain('*');
    expect(csp).not.toContain('https:');
    expect(csp).not.toContain('ws:');
    expect(csp).not.toContain('wss:');
    expect(csp).not.toContain('unsafe-eval');
    expect(csp).not.toContain('unsafe-inline');
    expect(csp).not.toContain('data:');
    // Ensure only local/ipc origins and blob: images are permitted
    expect(csp).toBe(
      "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self' ipc: http://ipc.localhost"
    );
  });

  it('enforces narrow devCsp with only local origin, style unsafe-inline for HMR, and WebSocket on port 1420', () => {
    const devCsp = tauriConf.app?.security?.devCsp;
    expect(devCsp).toBeDefined();
    expect(devCsp).not.toContain('*');
    expect(devCsp).not.toContain('https:');
    expect(devCsp).not.toContain('unsafe-eval');
    expect(devCsp).not.toContain("script-src 'self' 'unsafe-inline'");
    expect(devCsp).not.toContain('data:');
    expect(devCsp).toContain('http://localhost:1420');
    expect(devCsp).toContain('ws://localhost:1420');
    expect(devCsp).toBe(
      "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self' ipc: http://ipc.localhost http://localhost:1420 ws://localhost:1420"
    );
  });

  it('enforces least-privilege capability with only approved core event, generation, and batch permissions', () => {
    const expectedPermissions = [
      'core:event:allow-listen',
      'core:event:allow-unlisten',
      'allow-generation-snapshot',
      'allow-generation-select-output',
      'allow-generation-set-key',
      'allow-generation-start',
      'allow-generation-cancel',
      'allow-generation-result',
      'allow-generation-preview',
      'allow-generation-reveal',
      'allow-generation-resolve-close',
      'allow-batch-snapshot',
      'allow-batch-select-source',
      'allow-batch-select-output',
      'allow-batch-start',
      'allow-batch-cancel',
      'allow-batch-result',
      'allow-batch-reveal',
      'allow-batch-resolve-close',
    ];
    expect(capability.permissions).toEqual(expectedPermissions);
  });

  it('uses distinct Cargo binary and library target names to prevent Cargo #8519 collision', () => {
    expect(cargoToml).toContain('name = "cad-copilot-desktop"');
    expect(cargoToml).toContain('name = "cad_copilot_desktop_lib"');
  });

  it('includes Windows GUI subsystem attribute on main.rs for release builds', () => {
    expect(mainRs).toContain('#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]');
  });

  it('declares approved reverse-DNS identifier and no optional plugins in tauri.conf.json', () => {
    expect(tauriConf.identifier).toBe('io.github.jitendra-patwari.cad-copilot');
    expect(tauriConf.plugins).toBeUndefined();
  });

  it('excludes optional plugin dependencies from Cargo.toml', () => {
    expect(cargoToml).not.toMatch(/tauri-plugin/i);
  });

  it('registers only the exact approved Generation and Batch commands and no optional plugins', () => {
    expect(libRs).not.toMatch(/\.plugin\s*\(/);
    expect(mainRs).not.toMatch(/\.plugin\s*\(/);
    expect(mainRs).not.toMatch(/invoke_handler/);

    const approvedCommands = [
      'generation_snapshot',
      'generation_select_output',
      'generation_set_key',
      'generation_start',
      'generation_cancel',
      'generation_result',
      'generation_preview',
      'generation_reveal',
      'generation_resolve_close',
      'batch_snapshot',
      'batch_select_source',
      'batch_select_output',
      'batch_start',
      'batch_cancel',
      'batch_result',
      'batch_reveal',
      'batch_resolve_close',
    ];

    for (const cmd of approvedCommands) {
      expect(libRs).toContain(cmd);
    }
  });

  it('prohibits asset protocol and broad capability scopes', () => {
    expect(tauriConf.app?.security?.assetProtocol).toBeUndefined();
    expect(capability.remote).toBeUndefined();
  });

  it('configures exactly one main window with label matching capabilities', () => {
    expect(tauriConf.app?.windows).toHaveLength(1);
    expect(tauriConf.app?.windows[0]?.label).toBe('main');
    expect(capability.windows).toEqual(['main']);
  });
});

describe('Tauri packaged release configuration overlay & RFC 7396 merge semantics', () => {
  const tauriConfPath = path.resolve(__dirname, '../src-tauri/tauri.conf.json');
  const tauriPackagedConfPath = path.resolve(__dirname, '../src-tauri/tauri.packaged.conf.json');
  const licensePath = path.resolve(__dirname, '../../LICENSE');

  const tauriConf = JSON.parse(fs.readFileSync(tauriConfPath, 'utf-8'));
  const packagedOverlay = JSON.parse(fs.readFileSync(tauriPackagedConfPath, 'utf-8'));

  // RFC 7396 JSON Merge Patch implementation
  function applyMergePatch(target: unknown, patch: unknown): unknown {
    if (patch === null || typeof patch !== 'object' || Array.isArray(patch)) {
      return patch;
    }
    if (target === null || typeof target !== 'object' || Array.isArray(target)) {
      target = {};
    }
    const result: Record<string, unknown> = { ...(target as Record<string, unknown>) };
    for (const [key, value] of Object.entries(patch as Record<string, unknown>)) {
      if (value === null) {
        delete result[key];
      } else {
        result[key] = applyMergePatch(result[key], value);
      }
    }
    return result;
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const effectiveConf = applyMergePatch(tauriConf, packagedOverlay) as Record<string, any>;

  it('declares valid packaged overlay with explicit NSIS target and currentUser install mode', () => {
    expect(packagedOverlay.bundle?.active).toBe(true);
    expect(packagedOverlay.bundle?.targets).toEqual(['nsis']);
    expect(packagedOverlay.bundle?.windows?.nsis?.installMode).toBe('currentUser');
    expect(packagedOverlay.bundle?.windows?.nsis?.installerIcon).toBe('icons/icon.ico');
    expect(packagedOverlay.bundle?.windows?.nsis?.uninstallerIcon).toBe('icons/icon.ico');
  });

  it('points bundle-level licenseFile to the verified root LICENSE file', () => {
    expect(packagedOverlay.bundle?.licenseFile).toBe('../../LICENSE');
    const resolvedLicense = path.resolve(
      __dirname,
      '../src-tauri',
      packagedOverlay.bundle.licenseFile
    );
    expect(resolvedLicense).toBe(licensePath);
    expect(fs.existsSync(resolvedLicense)).toBe(true);
    expect(fs.readFileSync(resolvedLicense, 'utf-8')).toContain('MIT License');
  });

  it('configures exactly one reviewed resource mapping object to engine/', () => {
    const resources = packagedOverlay.bundle?.resources;
    expect(resources).toBeDefined();
    expect(typeof resources).toBe('object');
    expect(Array.isArray(resources)).toBe(false);
    expect(Object.keys(resources)).toEqual(['../../engine/dist/cad-copilot-engine/']);
    expect(resources['../../engine/dist/cad-copilot-engine/']).toBe('engine/');
  });

  it('RFC 7396 effective configuration retains base icon, window, and security posture', () => {
    // Retained base properties
    expect(effectiveConf.bundle?.icon).toEqual(['icons/icon.ico']);
    expect(effectiveConf.identifier).toBe('io.github.jitendra-patwari.cad-copilot');
    expect(effectiveConf.plugins).toBeUndefined();

    // Merged packaged properties
    expect(effectiveConf.bundle?.active).toBe(true);
    expect(effectiveConf.bundle?.targets).toEqual(['nsis']);
    expect(effectiveConf.bundle?.windows?.nsis?.installMode).toBe('currentUser');
    expect(effectiveConf.bundle?.windows?.nsis?.installerIcon).toBe('icons/icon.ico');
    expect(effectiveConf.bundle?.windows?.nsis?.uninstallerIcon).toBe('icons/icon.ico');
    expect(effectiveConf.bundle?.licenseFile).toBe('../../LICENSE');
    expect(effectiveConf.bundle?.resources).toEqual({
      '../../engine/dist/cad-copilot-engine/': 'engine/',
    });

    // Single window retained
    expect(effectiveConf.app?.windows).toHaveLength(1);
    expect(effectiveConf.app?.windows[0]?.label).toBe('main');

    // Strict CSP preserved
    expect(effectiveConf.app?.security?.csp).toBe(
      "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self' ipc: http://ipc.localhost"
    );
    expect(effectiveConf.app?.security?.devCsp).toBe(
      "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self' ipc: http://ipc.localhost http://localhost:1420 ws://localhost:1420"
    );
  });
});
