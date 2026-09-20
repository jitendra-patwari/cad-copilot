use std::collections::{HashMap, HashSet};
use std::fs;
use std::os::windows::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};

use crate::generation::output::{get_path_filesystem_identity, FileSystemIdentity};

use super::types::{BatchSelectSourceResponse, BatchSourceSelection, CommandError};

const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;
pub const MAX_SCAN_ENTRIES: usize = 20_000;
pub const MAX_SUPPORTED_FILES: usize = 500;

#[derive(Debug, Clone)]
pub struct SelectedBatchSource {
    pub selection_id: String,
    pub canonical_root: PathBuf,
    pub root_identity: FileSystemIdentity,
    pub ordered_relative_files: Vec<String>,
    pub file_identities: HashMap<String, FileSystemIdentity>,
    pub par_count: u32,
    pub psm_count: u32,
    pub asm_count: u32,
    pub dft_count: u32,
    pub skipped_count: u32,
}

impl SelectedBatchSource {
    pub fn to_summary(&self) -> BatchSourceSelection {
        let preview_files = self
            .ordered_relative_files
            .iter()
            .take(10)
            .cloned()
            .collect();
        BatchSourceSelection {
            selection_id: self.selection_id.clone(),
            display_root: self.canonical_root.to_string_lossy().to_string(),
            par_count: self.par_count,
            psm_count: self.psm_count,
            asm_count: self.asm_count,
            dft_count: self.dft_count,
            skipped_count: self.skipped_count,
            preview_files,
        }
    }

    pub fn to_select_response(&self) -> BatchSelectSourceResponse {
        BatchSelectSourceResponse {
            selection_id: self.selection_id.clone(),
            display_root: self.canonical_root.to_string_lossy().to_string(),
            par_count: self.par_count,
            psm_count: self.psm_count,
            asm_count: self.asm_count,
            dft_count: self.dft_count,
            skipped_count: self.skipped_count,
            files: self.ordered_relative_files.clone(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct SelectedBatchOutput {
    pub selection_id: String,
    pub canonical_root: PathBuf,
    pub root_identity: FileSystemIdentity,
}

pub fn generate_selection_id(prefix: &str) -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static COUNTER: AtomicU64 = AtomicU64::new(1);
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let seq = COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("{}-{}-{}", prefix, millis, seq)
}

pub fn is_reparse_point(path: &Path) -> bool {
    if let Ok(meta) = fs::symlink_metadata(path) {
        (meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0
    } else {
        false
    }
}

pub fn has_reparse_component(path: &Path) -> bool {
    for ancestor in path.ancestors() {
        if ancestor.parent().is_some() && is_reparse_point(ancestor) {
            return true;
        }
    }
    false
}

pub fn is_reserved_windows_name(stem: &str) -> bool {
    let s = stem.to_ascii_uppercase();
    matches!(
        s.as_str(),
        "CON"
            | "PRN"
            | "AUX"
            | "NUL"
            | "COM1"
            | "COM2"
            | "COM3"
            | "COM4"
            | "COM5"
            | "COM6"
            | "COM7"
            | "COM8"
            | "COM9"
            | "LPT1"
            | "LPT2"
            | "LPT3"
            | "LPT4"
            | "LPT5"
            | "LPT6"
            | "LPT7"
            | "LPT8"
            | "LPT9"
    )
}

pub fn validate_path_security(path: &Path) -> Result<(), CommandError> {
    let clean_path = crate::generation::output::simplify_windows_path(path);
    let s = clean_path.to_string_lossy();
    if s.starts_with(r"\\") || s.starts_with(r"//") {
        return Err(CommandError::new(
            "INPUT_PATH_NOT_ALLOWED",
            "UNC network shares and extended paths are not permitted.",
        ));
    }
    if s.contains(':') && s.chars().skip(2).any(|c| c == ':') {
        return Err(CommandError::new(
            "INPUT_PATH_NOT_ALLOWED",
            "Alternate data streams are not permitted.",
        ));
    }

    for comp in clean_path.components() {
        match comp {
            Component::ParentDir => {
                return Err(CommandError::new(
                    "INPUT_PATH_NOT_ALLOWED",
                    "Path traversal ('..') is not permitted.",
                ));
            }
            Component::Normal(os_str) => {
                let name = os_str.to_string_lossy();
                if let Some(stem) = Path::new(&*name).file_stem() {
                    if is_reserved_windows_name(&stem.to_string_lossy()) {
                        return Err(CommandError::new(
                            "INPUT_PATH_NOT_ALLOWED",
                            format!(
                                "Path component '{}' contains a reserved Windows device name.",
                                name
                            ),
                        ));
                    }
                }
            }
            _ => {}
        }
    }

    Ok(())
}

pub fn detect_supported_extension(path: &Path) -> Option<&'static str> {
    let ext = path.extension()?.to_str()?.to_ascii_lowercase();
    match ext.as_str() {
        "par" => Some("par"),
        "psm" => Some("psm"),
        "asm" => Some("asm"),
        "dft" => Some("dft"),
        _ => None,
    }
}

pub fn to_canonical_relative_path(path: &Path, root: &Path) -> Result<String, CommandError> {
    let clean_path = crate::generation::output::simplify_windows_path(path);
    let clean_root = crate::generation::output::simplify_windows_path(root);
    let rel = clean_path.strip_prefix(&clean_root).map_err(|_| {
        CommandError::new(
            "INPUT_PATH_NOT_ALLOWED",
            format!(
                "Path '{}' is not under root '{}'.",
                path.display(),
                root.display()
            ),
        )
    })?;

    let mut parts = Vec::new();
    for comp in rel.components() {
        if let Component::Normal(os_str) = comp {
            parts.push(os_str.to_string_lossy().to_string());
        } else {
            return Err(CommandError::new(
                "INPUT_PATH_NOT_ALLOWED",
                "Invalid relative path component.",
            ));
        }
    }

    Ok(parts.join("/"))
}

pub fn process_picked_files(files: Vec<PathBuf>) -> Result<SelectedBatchSource, CommandError> {
    if files.is_empty() {
        return Err(CommandError::new(
            "SELECTION_CANCELLED",
            "No files were selected.",
        ));
    }

    if files.len() > MAX_SUPPORTED_FILES {
        return Err(CommandError::new(
            "TOO_MANY_FILES",
            format!(
                "Selected {} files, which exceeds the maximum limit of {}.",
                files.len(),
                MAX_SUPPORTED_FILES
            ),
        ));
    }

    // Determine the common direct parent
    let first_file = &files[0];
    validate_path_security(first_file)?;

    if has_reparse_component(first_file) {
        return Err(CommandError::new(
            "INPUT_PATH_NOT_ALLOWED",
            "Selected file or an ancestor directory is a symlink, junction, or reparse point.",
        ));
    }

    let parent_dir = first_file.parent().ok_or_else(|| {
        CommandError::new(
            "INPUT_PATH_NOT_ALLOWED",
            "Selected file does not have a valid parent directory.",
        )
    })?;

    let canonical_root = fs::canonicalize(parent_dir).map_err(|e| {
        CommandError::new(
            "INPUT_ROOT_NOT_FOUND",
            format!("Failed to resolve source root directory: {}", e),
        )
    })?;
    let canonical_root = crate::generation::output::simplify_windows_path(&canonical_root);

    validate_path_security(&canonical_root)?;
    if is_reparse_point(&canonical_root) {
        return Err(CommandError::new(
            "INPUT_PATH_NOT_ALLOWED",
            "Source root directory is a symlink, junction, or reparse point.",
        ));
    }

    let root_identity = get_path_filesystem_identity(&canonical_root).map_err(|e| {
        CommandError::new(
            "INPUT_ROOT_NOT_FOUND",
            format!("Failed to query root filesystem identity: {}", e.message),
        )
    })?;

    let mut par_count = 0u32;
    let mut psm_count = 0u32;
    let mut asm_count = 0u32;
    let mut dft_count = 0u32;
    let mut skipped_count = 0u32;

    let mut ordered_relative_files = Vec::new();
    let mut file_identities = HashMap::new();
    let mut seen_casefold = HashSet::new();

    for file_path in &files {
        validate_path_security(file_path)?;

        if has_reparse_component(file_path) {
            return Err(CommandError::new(
                "INPUT_PATH_NOT_ALLOWED",
                format!(
                    "Selected file '{}' or an ancestor directory is a symlink or reparse point.",
                    file_path.display()
                ),
            ));
        }

        let current_parent = file_path
            .parent()
            .ok_or_else(|| CommandError::new("INPUT_PATH_NOT_ALLOWED", "Invalid file parent."))?;

        let current_canonical_parent = fs::canonicalize(current_parent).map_err(|e| {
            CommandError::new(
                "INPUT_PATH_NOT_ALLOWED",
                format!("Parent resolution error: {}", e),
            )
        })?;
        let current_canonical_parent =
            crate::generation::output::simplify_windows_path(&current_canonical_parent);

        // Enforce the one-parent rule
        if current_canonical_parent != canonical_root {
            return Err(CommandError::new(
                "INPUT_PATH_NOT_ALLOWED",
                "All selected files must reside in the exact same parent directory.",
            ));
        }

        if is_reparse_point(file_path) {
            return Err(CommandError::new(
                "INPUT_PATH_NOT_ALLOWED",
                format!(
                    "Selected file '{}' is a symlink or reparse point.",
                    file_path.display()
                ),
            ));
        }

        let ext = match detect_supported_extension(file_path) {
            Some(e) => e,
            None => {
                skipped_count += 1;
                continue;
            }
        };

        let file_name = file_path
            .file_name()
            .ok_or_else(|| CommandError::new("INPUT_PATH_NOT_ALLOWED", "Missing file name."))?
            .to_string_lossy()
            .to_string();

        let casefold_key = file_name.to_lowercase();
        if !seen_casefold.insert(casefold_key) {
            return Err(CommandError::new(
                "DUPLICATE_INPUT_DETECTED",
                format!(
                    "Case-insensitive filename collision detected for '{}'.",
                    file_name
                ),
            ));
        }

        let identity = get_path_filesystem_identity(file_path).map_err(|e| {
            CommandError::new(
                "INPUT_FILE_NOT_FOUND",
                format!(
                    "Failed to query identity for '{}': {}",
                    file_name, e.message
                ),
            )
        })?;

        match ext {
            "par" => par_count += 1,
            "psm" => psm_count += 1,
            "asm" => asm_count += 1,
            "dft" => dft_count += 1,
            _ => {}
        }

        file_identities.insert(file_name.clone(), identity);
        ordered_relative_files.push(file_name);
    }

    if ordered_relative_files.is_empty() {
        return Err(CommandError::new(
            "NO_SUPPORTED_FILES_FOUND",
            "None of the selected files have supported extensions (.par, .psm, .asm, .dft).",
        ));
    }

    ordered_relative_files.sort();

    Ok(SelectedBatchSource {
        selection_id: generate_selection_id("src"),
        canonical_root,
        root_identity,
        ordered_relative_files,
        file_identities,
        par_count,
        psm_count,
        asm_count,
        dft_count,
        skipped_count,
    })
}

pub fn scan_folder_bounded(folder: PathBuf) -> Result<SelectedBatchSource, CommandError> {
    validate_path_security(&folder)?;

    if !folder.exists() || !folder.is_dir() {
        return Err(CommandError::new(
            "INPUT_ROOT_NOT_FOUND",
            format!(
                "Selected folder does not exist or is not a directory: {}",
                folder.display()
            ),
        ));
    }

    if has_reparse_component(&folder) {
        return Err(CommandError::new(
            "INPUT_PATH_NOT_ALLOWED",
            "Selected folder or an ancestor directory is a symlink, junction, or reparse point.",
        ));
    }

    let canonical_root = fs::canonicalize(&folder).map_err(|e| {
        CommandError::new(
            "INPUT_ROOT_NOT_FOUND",
            format!("Failed to resolve folder path: {}", e),
        )
    })?;
    let canonical_root = crate::generation::output::simplify_windows_path(&canonical_root);

    validate_path_security(&canonical_root)?;

    if is_reparse_point(&canonical_root) {
        return Err(CommandError::new(
            "INPUT_PATH_NOT_ALLOWED",
            "Selected folder is a symlink, junction, or reparse point.",
        ));
    }

    let root_identity = get_path_filesystem_identity(&canonical_root).map_err(|e| {
        CommandError::new(
            "INPUT_ROOT_NOT_FOUND",
            format!("Failed to query root filesystem identity: {}", e.message),
        )
    })?;

    let mut par_count = 0u32;
    let mut psm_count = 0u32;
    let mut asm_count = 0u32;
    let mut dft_count = 0u32;
    let mut skipped_count = 0u32;

    let mut ordered_relative_files = Vec::new();
    let mut file_identities = HashMap::new();
    let mut seen_casefold = HashSet::new();

    let mut visited_entries = 0usize;
    let mut dirs_to_visit = vec![canonical_root.clone()];

    while let Some(current_dir) = dirs_to_visit.pop() {
        let entries = match fs::read_dir(&current_dir) {
            Ok(iter) => iter,
            Err(_) => {
                skipped_count += 1;
                continue;
            }
        };

        for entry in entries {
            visited_entries += 1;
            if visited_entries > MAX_SCAN_ENTRIES {
                return Err(CommandError::new(
                    "SELECTION_SCAN_OVERFLOW",
                    format!(
                        "Folder scan exceeded the maximum of {} visited entries. Please select a narrower folder.",
                        MAX_SCAN_ENTRIES
                    ),
                ));
            }

            let entry = match entry {
                Ok(e) => e,
                Err(_) => {
                    skipped_count += 1;
                    continue;
                }
            };

            let path = entry.path();
            if is_reparse_point(&path) {
                skipped_count += 1;
                continue;
            }

            let file_type = match entry.file_type() {
                Ok(ft) => ft,
                Err(_) => {
                    skipped_count += 1;
                    continue;
                }
            };

            if file_type.is_dir() {
                dirs_to_visit.push(path);
            } else if file_type.is_file() {
                let ext = match detect_supported_extension(&path) {
                    Some(e) => e,
                    None => {
                        continue;
                    }
                };

                if validate_path_security(&path).is_err() {
                    skipped_count += 1;
                    continue;
                }

                let rel_path = to_canonical_relative_path(&path, &canonical_root)?;
                let casefold_key = rel_path.to_lowercase();
                if !seen_casefold.insert(casefold_key) {
                    return Err(CommandError::new(
                        "DUPLICATE_INPUT_DETECTED",
                        format!(
                            "Case-insensitive filename collision detected for relative path '{}'.",
                            rel_path
                        ),
                    ));
                }

                let identity = match get_path_filesystem_identity(&path) {
                    Ok(id) => id,
                    Err(_) => {
                        skipped_count += 1;
                        continue;
                    }
                };

                match ext {
                    "par" => par_count += 1,
                    "psm" => psm_count += 1,
                    "asm" => asm_count += 1,
                    "dft" => dft_count += 1,
                    _ => {}
                }

                if (par_count + psm_count + asm_count + dft_count) as usize > MAX_SUPPORTED_FILES {
                    return Err(CommandError::new(
                        "TOO_MANY_FILES",
                        format!(
                            "Found more than {} supported files. Maximum supported batch size is {}.",
                            MAX_SUPPORTED_FILES, MAX_SUPPORTED_FILES
                        ),
                    ));
                }

                file_identities.insert(rel_path.clone(), identity);
                ordered_relative_files.push(rel_path);
            }
        }
    }

    if ordered_relative_files.is_empty() {
        return Err(CommandError::new(
            "NO_SUPPORTED_FILES_FOUND",
            "No supported Solid Edge documents (.par, .psm, .asm, .dft) were found in the selected folder.",
        ));
    }

    ordered_relative_files.sort();

    Ok(SelectedBatchSource {
        selection_id: generate_selection_id("src"),
        canonical_root,
        root_identity,
        ordered_relative_files,
        file_identities,
        par_count,
        psm_count,
        asm_count,
        dft_count,
        skipped_count,
    })
}

pub fn process_picked_output(folder: PathBuf) -> Result<SelectedBatchOutput, CommandError> {
    validate_path_security(&folder)?;

    if !folder.exists() || !folder.is_dir() {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            format!(
                "Selected path does not exist or is not a directory: {}",
                folder.display()
            ),
        ));
    }

    if has_reparse_component(&folder) {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Output directory or an ancestor directory is a symlink, junction, or reparse point.",
        ));
    }

    let canonical_root = fs::canonicalize(&folder).map_err(|e| {
        CommandError::new(
            "OUTPUT_UNAVAILABLE",
            format!("Failed to resolve output directory: {}", e),
        )
    })?;
    let canonical_root = crate::generation::output::simplify_windows_path(&canonical_root);

    validate_path_security(&canonical_root)?;

    if is_reparse_point(&canonical_root) {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Output directory is a symlink, junction, or reparse point.",
        ));
    }

    let root_identity = get_path_filesystem_identity(&canonical_root).map_err(|e| {
        CommandError::new(
            "OUTPUT_UNAVAILABLE",
            format!("Failed to query output filesystem identity: {}", e.message),
        )
    })?;

    Ok(SelectedBatchOutput {
        selection_id: generate_selection_id("out"),
        canonical_root,
        root_identity,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::File;

    #[test]
    fn test_reserved_windows_name_detection() {
        assert!(is_reserved_windows_name("con"));
        assert!(is_reserved_windows_name("CON"));
        assert!(is_reserved_windows_name("prn"));
        assert!(is_reserved_windows_name("aux"));
        assert!(is_reserved_windows_name("nul"));
        assert!(is_reserved_windows_name("com1"));
        assert!(is_reserved_windows_name("COM9"));
        assert!(is_reserved_windows_name("lpt1"));
        assert!(is_reserved_windows_name("LPT9"));

        assert!(!is_reserved_windows_name("part1"));
        assert!(!is_reserved_windows_name("connector"));
        assert!(!is_reserved_windows_name("normal_file"));
    }

    #[test]
    fn test_validate_path_security_rejections() {
        // UNC path
        assert!(validate_path_security(Path::new(r"\\server\share\file.par")).is_err());
        assert!(validate_path_security(Path::new(r"//server/share/file.par")).is_err());

        // Alternate data stream
        assert!(validate_path_security(Path::new(r"C:\test\file.par:stream")).is_err());

        // Traversal
        assert!(validate_path_security(Path::new(r"C:\test\..\file.par")).is_err());

        // Reserved name
        assert!(validate_path_security(Path::new(r"C:\test\CON.par")).is_err());
        assert!(validate_path_security(Path::new(r"C:\test\prn\file.par")).is_err());

        // Valid path
        assert!(validate_path_security(Path::new(r"C:\test\valid_part.par")).is_ok());
    }

    #[test]
    fn test_detect_supported_extension() {
        assert_eq!(
            detect_supported_extension(Path::new("part.par")),
            Some("par")
        );
        assert_eq!(
            detect_supported_extension(Path::new("part.PAR")),
            Some("par")
        );
        assert_eq!(
            detect_supported_extension(Path::new("sheet.psm")),
            Some("psm")
        );
        assert_eq!(
            detect_supported_extension(Path::new("assy.asm")),
            Some("asm")
        );
        assert_eq!(
            detect_supported_extension(Path::new("draft.dft")),
            Some("dft")
        );

        assert_eq!(detect_supported_extension(Path::new("step.stp")), None);
        assert_eq!(detect_supported_extension(Path::new("text.txt")), None);
        assert_eq!(detect_supported_extension(Path::new("no_ext")), None);
    }

    struct TestDir(PathBuf);
    impl TestDir {
        fn new(prefix: &str) -> Self {
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!("cad_batch_sel_{}_{}", prefix, nanos));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn test_process_picked_files_one_parent_rule() {
        let temp_dir = TestDir::new("one_parent");
        let sub1 = temp_dir.path().join("sub1");
        let sub2 = temp_dir.path().join("sub2");
        fs::create_dir(&sub1).unwrap();
        fs::create_dir(&sub2).unwrap();

        let f1 = sub1.join("part1.par");
        let f2 = sub2.join("part2.par");
        File::create(&f1).unwrap();
        File::create(&f2).unwrap();

        let res = process_picked_files(vec![f1, f2]);
        assert!(res.is_err());
        let err = res.unwrap_err();
        assert_eq!(err.code, "INPUT_PATH_NOT_ALLOWED");
    }

    #[test]
    fn test_process_picked_files_duplicate_casefold() {
        let temp_dir = TestDir::new("casefold");
        let f1 = temp_dir.path().join("model.par");
        let f2 = temp_dir.path().join("MODEL.PAR");
        File::create(&f1).unwrap();

        let res = process_picked_files(vec![f1, f2]);
        assert!(res.is_err());
        let err = res.unwrap_err();
        assert_eq!(err.code, "DUPLICATE_INPUT_DETECTED");
    }

    #[test]
    fn test_process_picked_files_success() {
        let temp_dir = TestDir::new("success");
        let f1 = temp_dir.path().join("b_model.par");
        let f2 = temp_dir.path().join("a_model.asm");
        File::create(&f1).unwrap();
        File::create(&f2).unwrap();

        let res = process_picked_files(vec![f1, f2]).unwrap();
        assert_eq!(res.par_count, 1);
        assert_eq!(res.asm_count, 1);
        assert_eq!(
            res.ordered_relative_files,
            vec!["a_model.asm", "b_model.par"]
        );
    }

    #[test]
    fn test_has_reparse_component_normal_path() {
        let temp_dir = TestDir::new("reparse_test");
        let sub = temp_dir.path().join("normal_sub");
        fs::create_dir(&sub).unwrap();
        let f = sub.join("part.par");
        File::create(&f).unwrap();

        assert!(!has_reparse_component(&f));
        assert!(!has_reparse_component(&sub));
    }

    #[test]
    fn test_canonical_root_has_no_extended_prefix() {
        let temp_dir = TestDir::new("prefix_test");
        let f = temp_dir.path().join("part.par");
        File::create(&f).unwrap();

        // 1. process_picked_files
        let picked = process_picked_files(vec![f.clone()]).unwrap();
        let root_str = picked.canonical_root.to_string_lossy();
        assert!(
            !root_str.starts_with(r"\\?\"),
            "process_picked_files root should not start with \\\\?\\"
        );
        let summary = picked.to_summary();
        assert!(
            !summary.display_root.starts_with(r"\\?\"),
            "display_root should not start with \\\\?\\"
        );

        // 2. scan_folder_bounded
        let scanned = scan_folder_bounded(temp_dir.path().to_path_buf()).unwrap();
        let scan_root_str = scanned.canonical_root.to_string_lossy();
        assert!(
            !scan_root_str.starts_with(r"\\?\"),
            "scan_folder_bounded root should not start with \\\\?\\"
        );

        // 3. process_picked_output
        let output = process_picked_output(temp_dir.path().to_path_buf()).unwrap();
        let out_root_str = output.canonical_root.to_string_lossy();
        assert!(
            !out_root_str.starts_with(r"\\?\"),
            "process_picked_output root should not start with \\\\?\\"
        );

        // 4. to_canonical_relative_path with extended prefix
        let extended_path = PathBuf::from(format!(r"\\?\{}", f.to_string_lossy()));
        let rel = to_canonical_relative_path(&extended_path, &picked.canonical_root).unwrap();
        assert_eq!(rel, "part.par");
    }
}
