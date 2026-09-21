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

    if let Ok(cwd) = std::env::current_dir() {
        let mut curr = Some(cwd.as_path());
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EngineWorkflow {
    Generate,
    Batch,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EngineLaunch {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub is_packaged: bool,
}

/// Pure resolver for packaged engine executable beneath a resource directory.
pub fn resolve_packaged_engine(
    resource_root: &std::path::Path,
    workflow: EngineWorkflow,
) -> Result<EngineLaunch, CommandError> {
    if !resource_root.is_dir() {
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Specified resource root is not an accessible directory.",
        ));
    }

    let canonical_root = match resource_root.canonicalize() {
        Ok(c) => c,
        Err(_) => {
            return Err(CommandError::new(
                "ENGINE_UNAVAILABLE",
                "Failed to canonicalize resource root.",
            ));
        }
    };

    let engine_dir = canonical_root.join("engine");
    if !engine_dir.is_dir() {
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Packaged engine directory was not found at the expected resource path.",
        ));
    }

    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;
    use std::os::windows::fs::MetadataExt;

    let dir_meta = match std::fs::symlink_metadata(&engine_dir) {
        Ok(m) => m,
        Err(_) => {
            return Err(CommandError::new(
                "ENGINE_UNAVAILABLE",
                "Failed to read engine directory metadata.",
            ));
        }
    };

    if dir_meta.is_symlink() || (dir_meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Packaged engine directory cannot be a symlink or reparse point.",
        ));
    }

    let canonical_engine = match engine_dir.canonicalize() {
        Ok(c) => c,
        Err(_) => {
            return Err(CommandError::new(
                "ENGINE_UNAVAILABLE",
                "Failed to canonicalize engine directory.",
            ));
        }
    };

    if !canonical_engine.starts_with(&canonical_root) {
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Packaged engine directory escapes resource root.",
        ));
    }

    let exe_path = canonical_engine.join("cad-copilot-engine.exe");
    if !exe_path.exists() {
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Packaged engine executable was not found at the expected resource path.",
        ));
    }

    let sym_meta = match std::fs::symlink_metadata(&exe_path) {
        Ok(m) => m,
        Err(_) => {
            return Err(CommandError::new(
                "ENGINE_UNAVAILABLE",
                "Failed to read engine executable metadata.",
            ));
        }
    };

    if sym_meta.is_symlink() || (sym_meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Packaged engine executable cannot be a symlink or reparse point.",
        ));
    }

    if !exe_path.is_file() {
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Packaged engine executable is not a regular file.",
        ));
    }

    let canonical_exe = match exe_path.canonicalize() {
        Ok(c) => c,
        Err(_) => {
            return Err(CommandError::new(
                "ENGINE_UNAVAILABLE",
                "Failed to canonicalize engine executable path.",
            ));
        }
    };

    if !canonical_exe.starts_with(&canonical_engine) {
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            "Packaged engine path escapes resource directory.",
        ));
    }

    let args = match workflow {
        EngineWorkflow::Generate => vec!["generate".to_string()],
        EngineWorkflow::Batch => vec!["batch".to_string()],
    };

    let cwd = exe_path.parent().unwrap_or(resource_root).to_path_buf();

    Ok(EngineLaunch {
        program: exe_path,
        args,
        cwd,
        is_packaged: true,
    })
}

/// Resolves the engine launch configuration for the requested workflow.
/// In source mode (default), returns python.exe and module invocation.
/// In packaged mode (under packaged-engine feature), returns packaged binary.
pub fn resolve_engine_launch(
    resource_dir: Option<&std::path::Path>,
    workflow: EngineWorkflow,
) -> Result<EngineLaunch, CommandError> {
    #[cfg(feature = "packaged-engine")]
    {
        if let Some(res) = resource_dir {
            resolve_packaged_engine(res, workflow)
        } else {
            Err(CommandError::new(
                "ENGINE_UNAVAILABLE",
                "Resource directory unavailable for packaged engine launch.",
            ))
        }
    }
    #[cfg(not(feature = "packaged-engine"))]
    {
        let _ = resource_dir;
        let layout = resolve_source_layout()?;
        match workflow {
            EngineWorkflow::Generate => Ok(EngineLaunch {
                program: layout.python_exe,
                args: vec!["-m".to_string(), "ipc".to_string()],
                cwd: layout.repo_root,
                is_packaged: false,
            }),
            EngineWorkflow::Batch => Ok(EngineLaunch {
                program: layout.python_exe,
                args: vec!["-m".to_string(), "ipc.batch_stdio".to_string()],
                cwd: layout.engine_dir,
                is_packaged: false,
            }),
        }
    }
}

pub fn current_engine_build_info() -> EngineBuildInfo {
    #[cfg(feature = "packaged-engine")]
    {
        EngineBuildInfo {
            version: ENGINE_SPEC_VERSION.to_string(),
            provenance: EngineBuildProvenance::BundledBuild,
        }
    }
    #[cfg(not(feature = "packaged-engine"))]
    {
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
        #[cfg(feature = "packaged-engine")]
        assert_eq!(info.provenance, EngineBuildProvenance::BundledBuild);
        #[cfg(not(feature = "packaged-engine"))]
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

    #[test]
    fn test_resolve_packaged_engine_valid_structure() {
        let temp_dir = std::env::temp_dir().join(format!(
            "cad_pkg_test_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let engine_dir = temp_dir.join("engine");
        std::fs::create_dir_all(&engine_dir).expect("Create engine dir");
        let exe_path = engine_dir.join("cad-copilot-engine.exe");
        std::fs::write(&exe_path, b"MZ_MOCK_EXE").expect("Write mock exe");

        let gen_launch = resolve_packaged_engine(&temp_dir, EngineWorkflow::Generate)
            .expect("Must resolve generate launch");
        assert!(gen_launch.is_packaged);
        assert_eq!(gen_launch.args, vec!["generate".to_string()]);
        assert_eq!(gen_launch.program, exe_path.canonicalize().unwrap());

        let batch_launch = resolve_packaged_engine(&temp_dir, EngineWorkflow::Batch)
            .expect("Must resolve batch launch");
        assert!(batch_launch.is_packaged);
        assert_eq!(batch_launch.args, vec!["batch".to_string()]);

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_resolve_packaged_engine_missing_exe_fails() {
        let temp_dir = std::env::temp_dir().join(format!(
            "cad_pkg_missing_test_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&temp_dir).expect("Create temp dir");

        let res = resolve_packaged_engine(&temp_dir, EngineWorkflow::Generate);
        assert!(res.is_err());
        let err = res.unwrap_err();
        assert_eq!(err.code, "ENGINE_UNAVAILABLE");

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_resolve_packaged_engine_non_directory_fails() {
        let temp_file = std::env::temp_dir().join(format!(
            "cad_pkg_file_test_{}.tmp",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::write(&temp_file, b"NOT_A_DIR").expect("Write file");

        let res = resolve_packaged_engine(&temp_file, EngineWorkflow::Generate);
        assert!(res.is_err());
        let err = res.unwrap_err();
        assert_eq!(err.code, "ENGINE_UNAVAILABLE");

        let _ = std::fs::remove_file(&temp_file);
    }

    #[cfg(not(feature = "packaged-engine"))]
    #[test]
    fn test_resolve_engine_launch_source_mode() {
        let gen_launch = resolve_engine_launch(None, EngineWorkflow::Generate)
            .expect("Must resolve source generate launch");
        assert!(!gen_launch.is_packaged);
        assert_eq!(gen_launch.args, vec!["-m".to_string(), "ipc".to_string()]);

        let batch_launch = resolve_engine_launch(None, EngineWorkflow::Batch)
            .expect("Must resolve source batch launch");
        assert!(!batch_launch.is_packaged);
        assert_eq!(
            batch_launch.args,
            vec!["-m".to_string(), "ipc.batch_stdio".to_string()]
        );
    }

    #[cfg(feature = "packaged-engine")]
    #[test]
    fn test_resolve_engine_launch_packaged_mode_without_resource_dir_fails() {
        let res = resolve_engine_launch(None, EngineWorkflow::Generate);
        assert!(res.is_err());
        assert_eq!(res.unwrap_err().code, "ENGINE_UNAVAILABLE");
    }

    #[cfg(feature = "packaged-engine")]
    #[test]
    fn test_resolve_engine_launch_packaged_mode_with_resource_dir() {
        let temp_dir = std::env::temp_dir().join(format!(
            "cad_pkg_launch_test_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let engine_dir = temp_dir.join("engine");
        std::fs::create_dir_all(&engine_dir).expect("Create engine dir");
        let exe_path = engine_dir.join("cad-copilot-engine.exe");
        std::fs::write(&exe_path, b"MZ_MOCK_EXE").expect("Write mock exe");

        let gen_launch = resolve_engine_launch(Some(&temp_dir), EngineWorkflow::Generate)
            .expect("Must resolve packaged generate launch");
        assert!(gen_launch.is_packaged);
        assert_eq!(gen_launch.args, vec!["generate".to_string()]);

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_resolve_packaged_engine_spaces_and_unicode_paths() {
        let temp_dir = std::env::temp_dir().join(format!(
            "cad copilot büro with spaces {}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let engine_dir = temp_dir.join("engine");
        std::fs::create_dir_all(&engine_dir).expect("Create engine dir");
        let exe_path = engine_dir.join("cad-copilot-engine.exe");
        std::fs::write(&exe_path, b"MZ_MOCK_EXE").expect("Write mock exe");

        let gen_launch = resolve_packaged_engine(&temp_dir, EngineWorkflow::Generate)
            .expect("Must resolve packaged launch with Unicode and spaces");
        assert!(gen_launch.is_packaged);
        assert_eq!(gen_launch.args, vec!["generate".to_string()]);
        assert_eq!(gen_launch.program, exe_path.canonicalize().unwrap());

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_resolve_packaged_engine_missing_engine_dir_fails() {
        let temp_dir = std::env::temp_dir().join(format!(
            "cad_pkg_no_eng_dir_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&temp_dir).expect("Create temp dir");

        let res = resolve_packaged_engine(&temp_dir, EngineWorkflow::Generate);
        assert!(res.is_err());
        assert_eq!(res.unwrap_err().code, "ENGINE_UNAVAILABLE");

        let _ = std::fs::remove_dir_all(&temp_dir);
    }
}
