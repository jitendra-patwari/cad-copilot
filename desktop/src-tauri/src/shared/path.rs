use serde::{Deserialize, Serialize};
use std::fs::File;
use std::os::windows::ffi::OsStringExt;
use std::os::windows::fs::OpenOptionsExt;
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};
use windows_sys::Win32::Storage::FileSystem::{
    GetFileInformationByHandle, GetFinalPathNameByHandleW, BY_HANDLE_FILE_INFORMATION,
    FILE_FLAG_BACKUP_SEMANTICS, FILE_NAME_NORMALIZED, VOLUME_NAME_DOS,
};

use super::error::CommandError;

pub const MAX_MANIFEST_BYTES: u64 = 2_097_152; // 2 MiB
pub const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileSystemIdentity {
    pub volume_serial_number: u32,
    pub file_index: u64,
}

/// Retrieves the NTFS volume serial number and 64-bit file index from an open handle.
/// Rejects reparse points and junctions.
pub fn get_handle_filesystem_identity(
    file: &File,
    _path_for_err: &Path,
) -> Result<FileSystemIdentity, CommandError> {
    let handle = file.as_raw_handle() as windows_sys::Win32::Foundation::HANDLE;
    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    let ok = unsafe { GetFileInformationByHandle(handle, &mut info) };
    if ok == 0 {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            "Failed to query filesystem identity.",
        ));
    }

    if (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Target path is a symlink, junction, or reparse point.",
        ));
    }

    let file_index = ((info.nFileIndexHigh as u64) << 32) | (info.nFileIndexLow as u64);
    Ok(FileSystemIdentity {
        volume_serial_number: info.dwVolumeSerialNumber,
        file_index,
    })
}

/// Retrieves the NTFS volume serial number and 64-bit file index for a path via an open handle.
/// Opens directories with `FILE_FLAG_BACKUP_SEMANTICS` and rejects reparse points/junctions.
pub fn get_path_filesystem_identity(path: &Path) -> Result<FileSystemIdentity, CommandError> {
    if !path.exists() {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            "Path does not exist.",
        ));
    }

    let file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)
        .map_err(|_| {
            CommandError::new(
                "OUTPUT_UNAVAILABLE",
                "Failed to open path for identity check.",
            )
        })?;

    get_handle_filesystem_identity(&file, path)
}

/// Simplifies a canonical Windows path by stripping extended-length `\\?\` or `\\?\UNC\` prefixes
/// for compatibility with external shell tools (such as explorer.exe) and cleaner UI presentation.
pub fn simplify_windows_path(path: &Path) -> PathBuf {
    let s = path.to_string_lossy();
    if let Some(stripped) = s.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{}", stripped))
    } else if let Some(stripped) = s.strip_prefix(r"\\?\") {
        PathBuf::from(stripped)
    } else {
        path.to_path_buf()
    }
}

/// Verifies that an open file handle does not reference a reparse point or hardlink,
/// resides on the expected volume (if provided), and that its final resolved path on disk
/// is contained within `canonical_run`.
/// This protects against TOCTOU junction / hardlink replacement attacks between path checks and reads.
pub fn verify_open_file_handle(
    file: &File,
    canonical_run: &Path,
    expected_volume: Option<u32>,
) -> Result<(), CommandError> {
    let handle = file.as_raw_handle() as windows_sys::Win32::Foundation::HANDLE;

    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    let ok = unsafe { GetFileInformationByHandle(handle, &mut info) };
    if ok == 0 {
        return Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            "Failed to retrieve file information from open handle.",
        ));
    }

    if let Some(vol) = expected_volume {
        if info.dwVolumeSerialNumber != vol {
            return Err(CommandError::new(
                "OUTPUT_PATH_NOT_ALLOWED",
                "Open file handle resides on a different volume than designated run directory.",
            ));
        }
    }

    if (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Open file handle references a reparse point or symlink.",
        ));
    }

    if info.nNumberOfLinks > 1 {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Open file handle has multiple hardlinks; hardlinks are not permitted.",
        ));
    }

    let mut buf = vec![0u16; 1024];
    let mut len = unsafe {
        GetFinalPathNameByHandleW(
            handle,
            buf.as_mut_ptr(),
            buf.len() as u32,
            FILE_NAME_NORMALIZED | VOLUME_NAME_DOS,
        )
    };

    if len as usize > buf.len() {
        buf.resize(len as usize + 1, 0);
        len = unsafe {
            GetFinalPathNameByHandleW(
                handle,
                buf.as_mut_ptr(),
                buf.len() as u32,
                FILE_NAME_NORMALIZED | VOLUME_NAME_DOS,
            )
        };
    }

    if len == 0 {
        return Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            "Failed to resolve final path from open file handle.",
        ));
    }

    let raw_os = std::ffi::OsString::from_wide(&buf[..len as usize]);
    let handle_final_path = PathBuf::from(raw_os);

    let clean_handle = simplify_windows_path(&handle_final_path);
    let clean_run = simplify_windows_path(canonical_run);

    let handle_str = clean_handle.to_string_lossy().replace('/', "\\");
    let run_str = clean_run.to_string_lossy().replace('/', "\\");
    let normalized_run_prefix = if run_str.ends_with('\\') {
        run_str
    } else {
        format!("{}\\", run_str)
    };

    if !handle_str
        .to_ascii_lowercase()
        .starts_with(&normalized_run_prefix.to_ascii_lowercase())
    {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Open file handle resolves outside the designated run directory.",
        ));
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simplify_windows_path_dos_device() {
        let p = Path::new(r"\\?\C:\Users\test\output");
        let simplified = simplify_windows_path(p);
        assert_eq!(simplified, PathBuf::from(r"C:\Users\test\output"));
    }

    #[test]
    fn test_simplify_windows_path_unc() {
        let p = Path::new(r"\\?\UNC\server\share\output");
        let simplified = simplify_windows_path(p);
        assert_eq!(simplified, PathBuf::from(r"\\server\share\output"));
    }

    #[test]
    fn test_simplify_windows_path_normal() {
        let p = Path::new(r"C:\Users\test\output");
        let simplified = simplify_windows_path(p);
        assert_eq!(simplified, PathBuf::from(r"C:\Users\test\output"));
    }
}
