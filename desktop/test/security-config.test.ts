import { describe, it, expect } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';

describe('Tauri security configuration baseline', () => {
  const tauriConfPath = path.resolve(__dirname, '../src-tauri/tauri.conf.json');
  const capabilityPath = path.resolve(__dirname, '../src-tauri/capabilities/default.json');
  const cargoTomlPath = path.resolve(__dirname, '../src-tauri/Cargo.toml');
  const mainRsPath = path.resolve(__dirname, '../src-tauri/src/main.rs');
  const viteConfigPath = path.resolve(__dirname, '../vite.config.ts');

  const tauriConf = JSON.parse(fs.readFileSync(tauriConfPath, 'utf-8'));
  const capability = JSON.parse(fs.readFileSync(capabilityPath, 'utf-8'));
  const cargoToml = fs.readFileSync(cargoTomlPath, 'utf-8');
  const mainRs = fs.readFileSync(mainRsPath, 'utf-8');
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
    // Ensure only local/ipc origins are permitted
    expect(csp).toBe(
      "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self' ipc: http://ipc.localhost"
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
      "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self' ipc: http://ipc.localhost http://localhost:1420 ws://localhost:1420"
    );
  });

  it('enforces least-privilege capability with strictly empty permissions', () => {
    expect(capability.permissions).toEqual([]);
  });

  it('uses distinct Cargo binary and library target names to prevent Cargo #8519 collision', () => {
    expect(cargoToml).toContain('name = "cad-copilot-desktop"');
    expect(cargoToml).toContain('name = "cad_copilot_desktop_lib"');
  });

  it('includes Windows GUI subsystem attribute on main.rs for release builds', () => {
    expect(mainRs).toContain('#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]');
  });

  it('declares approved reverse-DNS identifier and no optional plugins', () => {
    expect(tauriConf.identifier).toBe('io.github.jitendra-patwari.cad-copilot');
    expect(tauriConf.plugins).toBeUndefined();
  });

  it('configures exactly one main window with label matching capabilities', () => {
    expect(tauriConf.app?.windows).toHaveLength(1);
    expect(tauriConf.app?.windows[0]?.label).toBe('main');
    expect(capability.windows).toEqual(['main']);
  });
});
