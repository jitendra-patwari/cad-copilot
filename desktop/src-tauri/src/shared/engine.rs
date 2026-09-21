use serde::{Deserialize, Serialize};
use std::path::PathBuf;

use super::error::CommandError;

#[derive(Debug, Clone)]
pub struct SourceLayout {
    pub repo_root: PathBuf,
    pub python_exe: PathBuf,
    pub engine_dir: PathBuf,
    pub cargo_manifest: PathBuf,
    pub generate_cmd: PathBuf,
    pub batch_cmd: PathBuf,
}

impl SourceLayout {
    pub fn verify_files(&self) -> bool {
        self.cargo_manifest.is_file()
            && self.python_exe.is_file()
            && self.generate_cmd.is_file()
            && self.batch_cmd.is_file()
            && self.engine_dir.join("pyproject.toml").is_file()
    }
}

/// Resolves the repository root and verified source layout from executable or manifest ancestry.
pub fn resolve_source_layout() -> Result<SourceLayout, CommandError> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Ok(exe) = std::env::current_exe() {
        let mut curr = exe.parent();
        let mut count = 0;
        while let Some(parent) = curr {
            if count > 8 {
                break;
            }
            candidates.push(parent.to_path_buf());
            curr = parent.parent();
            count += 1;
        }
    }

    if let Some(manifest_dir) = option_env!("CARGO_MANIFEST_DIR") {
        let manifest_path = PathBuf::from(manifest_dir);
        let mut curr = Some(manifest_path.as_path());
        let mut count = 0;
        while let Some(parent) = curr {
            if count > 5 {
                break;
            }
            candidates.push(parent.to_path_buf());
            curr = parent.parent();
            count += 1;
        }
    }

    for cand in candidates {
        let cargo_manifest = cand.join("desktop").join("src-tauri").join("Cargo.toml");
        let engine_dir = cand.join("engine");
        let generate_cmd = engine_dir.join("scripts").join("generate.cmd");
        let batch_cmd = engine_dir.join("scripts").join("batch.cmd");
        let python_exe = cand.join(".venv").join("Scripts").join("python.exe");

        let layout = SourceLayout {
            repo_root: cand,
            python_exe,
            engine_dir,
            cargo_manifest,
            generate_cmd,
            batch_cmd,
        };

        if layout.verify_files() {
            verify_engine_compatibility(&layout.engine_dir)?;
            return Ok(layout);
        }
    }

    Err(CommandError::new(
        "ENGINE_UNAVAILABLE",
        "Failed to resolve CAD Copilot repository root and Python virtual environment.",
    ))
}

/// Extracts the declared engine version from pyproject.toml in the engine directory.
pub fn extract_engine_spec_version(engine_dir: &std::path::Path) -> Result<String, CommandError> {
    let pyproject = engine_dir.join("pyproject.toml");
    let content = std::fs::read_to_string(&pyproject).map_err(|_| {
        CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Failed to read engine pyproject.toml specification.",
        )
    })?;

    let mut in_project = false;
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('[') {
            in_project = trimmed == "[project]";
            continue;
        }
        if in_project {
            if let Some(rest) = trimmed.strip_prefix("version") {
                let rest = rest.trim();
                if let Some(val) = rest.strip_prefix('=') {
                    let val = val.trim().trim_matches('"').trim_matches('\'');
                    if !val.is_empty() {
                        return Ok(val.to_string());
                    }
                }
            }
        }
    }

    Err(CommandError::new(
        "ENGINE_UNAVAILABLE",
        "Engine pyproject.toml does not declare a valid version.",
    ))
}

/// Verifies that the engine artifact version matches the expected specification version.
pub fn verify_engine_compatibility(engine_dir: &std::path::Path) -> Result<String, CommandError> {
    let found = extract_engine_spec_version(engine_dir)?;
    if found != ENGINE_SPEC_VERSION {
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Engine specification version does not match supported desktop baseline.",
        ));
    }
    Ok(found)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineBuildInfo {
    pub version: String,
    pub provenance: EngineBuildProvenance,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineBuildProvenance {
    SourceBuild,
    BundledBuild,
}

pub const ENGINE_SPEC_VERSION: &str = "0.1.0";

pub fn current_engine_build_info() -> EngineBuildInfo {
    // In Step 1, supervisors resolve execution through the verified source layout.
    // The version is extracted from the engine's pyproject.toml specification artifact.
    // Truthful bundled provenance is only claimed once Step 2 supplies the packaged executable.
    let version = if let Ok(layout) = resolve_source_layout() {
        extract_engine_spec_version(&layout.engine_dir)
            .unwrap_or_else(|_| ENGINE_SPEC_VERSION.to_string())
    } else {
        ENGINE_SPEC_VERSION.to_string()
    };

    EngineBuildInfo {
        version,
        provenance: EngineBuildProvenance::SourceBuild,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resolve_source_layout() {
        let layout = resolve_source_layout();
        assert!(
            layout.is_ok(),
            "Source layout must resolve in dev checkout: {:?}",
            layout.err()
        );
    }

    #[test]
    fn test_current_engine_build_info() {
        let info = current_engine_build_info();
        assert_eq!(info.version, "0.1.0");
        assert_eq!(info.provenance, EngineBuildProvenance::SourceBuild);
    }

    #[test]
    fn test_extract_engine_spec_version_and_compatibility() {
        let layout = resolve_source_layout().expect("Must resolve source layout");
        let version = extract_engine_spec_version(&layout.engine_dir)
            .expect("Must extract engine version from pyproject.toml");
        assert_eq!(version, ENGINE_SPEC_VERSION);

        let compat = verify_engine_compatibility(&layout.engine_dir);
        assert!(
            compat.is_ok(),
            "Compatibility check must pass for source engine"
        );
    }

    #[test]
    fn test_verify_engine_compatibility_rejects_mismatched_version() {
        let temp_dir = std::env::temp_dir().join(format!(
            "cad_copilot_engine_test_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&temp_dir).expect("Create temp dir");
        let pyproject_path = temp_dir.join("pyproject.toml");
        std::fs::write(
            &pyproject_path,
            "[project]\nname = \"cad-copilot\"\nversion = \"9.9.9\"\n",
        )
        .expect("Write dummy pyproject");

        let res = verify_engine_compatibility(&temp_dir);
        assert!(res.is_err());
        let err = res.unwrap_err();
        assert_eq!(err.code, "ENGINE_UNAVAILABLE");
        assert_eq!(
            err.message,
            "Engine specification version does not match supported desktop baseline."
        );

        let _ = std::fs::remove_dir_all(&temp_dir);
    }
}
