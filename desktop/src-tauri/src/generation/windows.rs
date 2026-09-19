use std::collections::HashMap;
use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::FromRawHandle;
use std::path::Path;
use std::ptr::{null, null_mut};
use std::sync::Mutex;

use windows_sys::Win32::Foundation::{
    CloseHandle, DuplicateHandle, GetLastError, SetHandleInformation, BOOL, DUPLICATE_SAME_ACCESS,
    FALSE, HANDLE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE, TRUE, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::System::Console::{
    AttachConsole, FreeConsole, GenerateConsoleCtrlEvent, GetConsoleProcessList,
    SetConsoleCtrlHandler, CTRL_BREAK_EVENT,
};
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::Threading::{
    CreateProcessW, DeleteProcThreadAttributeList, GetCurrentProcess, GetCurrentProcessId,
    GetExitCodeProcess, InitializeProcThreadAttributeList, TerminateProcess,
    UpdateProcThreadAttribute, WaitForSingleObject, CREATE_NEW_CONSOLE, CREATE_NEW_PROCESS_GROUP,
    CREATE_UNICODE_ENVIRONMENT, EXTENDED_STARTUPINFO_PRESENT, PROCESS_INFORMATION,
    STARTF_USESHOWWINDOW, STARTF_USESTDHANDLES, STARTUPINFOEXW,
};

use super::types::CommandError;

pub const SW_HIDE: u16 = 0;
pub const ERROR_ACCESS_DENIED: u32 = 5;

pub struct ChildProcess {
    pub pid: u32,
    pub process_handle: HANDLE,
    pub stdin_write: Option<std::fs::File>,
    pub stdout_read: Option<std::fs::File>,
    pub stderr_read: Option<std::fs::File>,
    pub child_has_private_console: bool,
}

impl Drop for ChildProcess {
    fn drop(&mut self) {
        if !self.process_handle.is_null() && self.process_handle != INVALID_HANDLE_VALUE {
            unsafe {
                CloseHandle(self.process_handle);
            }
            self.process_handle = null_mut();
        }
    }
}

// Ensure ChildProcess can be safely sent across threads
unsafe impl Send for ChildProcess {}

fn to_wide_null(s: &str) -> Vec<u16> {
    OsStr::new(s)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

fn to_wide_null_path(p: &Path) -> Vec<u16> {
    p.as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

/// Formats a Windows command line with proper argument quoting.
pub fn build_command_line(program: &Path, args: &[&str]) -> Vec<u16> {
    let mut line = String::new();
    let prog_str = program.to_string_lossy();
    if prog_str.contains(' ') || prog_str.contains('\t') {
        line.push('"');
        line.push_str(&prog_str);
        line.push('"');
    } else {
        line.push_str(&prog_str);
    }

    for arg in args {
        line.push(' ');
        if arg.is_empty() {
            line.push_str("\"\"");
        } else if arg.contains(' ') || arg.contains('\t') || arg.contains('"') {
            line.push('"');
            for c in arg.chars() {
                if c == '"' {
                    line.push_str("\\\"");
                } else {
                    line.push(c);
                }
            }
            line.push('"');
        } else {
            line.push_str(arg);
        }
    }

    to_wide_null(&line)
}

/// Builds a Windows UTF-16 environment block: sorted key-value pairs separated by nulls, terminated by double null.
pub fn build_environment_block(env: &HashMap<String, String>) -> Vec<u16> {
    let mut sorted_keys: Vec<&String> = env.keys().collect();
    sorted_keys.sort_by_key(|a| a.to_uppercase());

    let mut block: Vec<u16> = Vec::new();
    for key in sorted_keys {
        let val = &env[key];
        let entry = format!("{key}={val}");
        block.extend(OsStr::new(&entry).encode_wide());
        block.push(0);
    }
    block.push(0);
    block
}

pub struct AutoCloseHandle(pub HANDLE);

impl Drop for AutoCloseHandle {
    fn drop(&mut self) {
        if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
            unsafe {
                CloseHandle(self.0);
            }
            self.0 = null_mut();
        }
    }
}

impl AutoCloseHandle {
    pub fn new(h: HANDLE) -> Self {
        Self(h)
    }

    pub fn raw(&self) -> HANDLE {
        self.0
    }

    pub fn take(&mut self) -> HANDLE {
        let h = self.0;
        self.0 = null_mut();
        h
    }
}

/// Duplicates an existing process handle into an independently owned AutoCloseHandle.
#[allow(clippy::not_unsafe_ptr_arg_deref)]
pub fn duplicate_process_handle(source: HANDLE) -> Result<AutoCloseHandle, CommandError> {
    unsafe {
        let current_process = GetCurrentProcess();
        let mut target_handle: HANDLE = null_mut();
        let ok = DuplicateHandle(
            current_process,
            source,
            current_process,
            &mut target_handle,
            0,
            FALSE,
            DUPLICATE_SAME_ACCESS,
        );
        if ok == 0 || target_handle.is_null() || target_handle == INVALID_HANDLE_VALUE {
            let err = GetLastError();
            return Err(CommandError::new(
                "TRANSPORT_ERROR",
                format!("Failed to duplicate process handle (code: {err})."),
            ));
        }
        Ok(AutoCloseHandle(target_handle))
    }
}

/// Checks whether the calling process is currently attached to a Windows console.
pub fn is_host_attached_to_console() -> bool {
    let mut pids = [0u32; 1];
    let count = unsafe { GetConsoleProcessList(pids.as_mut_ptr(), 1) };
    count > 0
}

/// Spawns the engine process with explicitly piped stdin, stdout, and stderr.
/// Chooses between private hidden console (release host) and process group (console-attached host).
pub fn spawn_engine_process(
    program: &Path,
    args: &[&str],
    cwd: &Path,
    env: &HashMap<String, String>,
) -> Result<ChildProcess, CommandError> {
    spawn_engine_process_explicit(program, args, cwd, env, None)
}

pub fn spawn_engine_process_explicit(
    program: &Path,
    args: &[&str],
    cwd: &Path,
    env: &HashMap<String, String>,
    force_private_console: Option<bool>,
) -> Result<ChildProcess, CommandError> {
    let sa = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: null_mut(),
        bInheritHandle: FALSE,
    };

    // Stdin pipe: child reads, parent writes
    let mut stdin_read: HANDLE = null_mut();
    let mut stdin_write: HANDLE = null_mut();
    if unsafe { CreatePipe(&mut stdin_read, &mut stdin_write, &sa, 0) } == 0 {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!("Failed to create stdin pipe (code: {}).", unsafe {
                GetLastError()
            }),
        ));
    }
    let stdin_read = AutoCloseHandle(stdin_read);
    let mut stdin_write = AutoCloseHandle(stdin_write);
    // Explicitly make ONLY child read end inheritable
    if unsafe { SetHandleInformation(stdin_read.0, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) } == 0
    {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!(
                "Failed to set stdin read handle inheritance (code: {}).",
                unsafe { GetLastError() }
            ),
        ));
    }

    // Stdout pipe: parent reads, child writes
    let mut stdout_read: HANDLE = null_mut();
    let mut stdout_write: HANDLE = null_mut();
    if unsafe { CreatePipe(&mut stdout_read, &mut stdout_write, &sa, 0) } == 0 {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!("Failed to create stdout pipe (code: {}).", unsafe {
                GetLastError()
            }),
        ));
    }
    let mut stdout_read = AutoCloseHandle(stdout_read);
    let stdout_write = AutoCloseHandle(stdout_write);
    // Explicitly make ONLY child write end inheritable
    if unsafe { SetHandleInformation(stdout_write.0, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) }
        == 0
    {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!(
                "Failed to set stdout write handle inheritance (code: {}).",
                unsafe { GetLastError() }
            ),
        ));
    }

    // Stderr pipe: parent reads, child writes
    let mut stderr_read: HANDLE = null_mut();
    let mut stderr_write: HANDLE = null_mut();
    if unsafe { CreatePipe(&mut stderr_read, &mut stderr_write, &sa, 0) } == 0 {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!("Failed to create stderr pipe (code: {}).", unsafe {
                GetLastError()
            }),
        ));
    }
    let mut stderr_read = AutoCloseHandle(stderr_read);
    let stderr_write = AutoCloseHandle(stderr_write);
    // Explicitly make ONLY child write end inheritable
    if unsafe { SetHandleInformation(stderr_write.0, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) }
        == 0
    {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!(
                "Failed to set stderr write handle inheritance (code: {}).",
                unsafe { GetLastError() }
            ),
        ));
    }

    let host_has_console = is_host_attached_to_console();
    let (creation_flags, child_has_private_console) = match force_private_console {
        Some(true) => (CREATE_NEW_CONSOLE | CREATE_UNICODE_ENVIRONMENT, true),
        Some(false) => (CREATE_NEW_PROCESS_GROUP | CREATE_UNICODE_ENVIRONMENT, false),
        None => {
            if !host_has_console {
                (CREATE_NEW_CONSOLE | CREATE_UNICODE_ENVIRONMENT, true)
            } else {
                (CREATE_NEW_PROCESS_GROUP | CREATE_UNICODE_ENVIRONMENT, false)
            }
        }
    };

    let mut startup_info_ex: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
    startup_info_ex.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
    startup_info_ex.StartupInfo.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    startup_info_ex.StartupInfo.wShowWindow = SW_HIDE;
    startup_info_ex.StartupInfo.hStdInput = stdin_read.0;
    startup_info_ex.StartupInfo.hStdOutput = stdout_write.0;
    startup_info_ex.StartupInfo.hStdError = stderr_write.0;

    let mut inherit_handles = [stdin_read.0, stdout_write.0, stderr_write.0];
    let mut attr_size: usize = 0;
    unsafe {
        InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut attr_size);
    }
    let mut attr_buf = vec![0u8; attr_size];
    let attr_list = attr_buf.as_mut_ptr() as *mut std::ffi::c_void;

    if unsafe { InitializeProcThreadAttributeList(attr_list as _, 1, 0, &mut attr_size) } == 0 {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!(
                "Failed to initialize proc thread attribute list (code: {}).",
                unsafe { GetLastError() }
            ),
        ));
    }

    struct AttrListCleanup(*mut std::ffi::c_void);
    impl Drop for AttrListCleanup {
        fn drop(&mut self) {
            unsafe {
                DeleteProcThreadAttributeList(self.0 as _);
            }
        }
    }
    let _attr_guard = AttrListCleanup(attr_list);

    const PROC_THREAD_ATTRIBUTE_HANDLE_LIST: usize = 0x00020002;
    if unsafe {
        UpdateProcThreadAttribute(
            attr_list as _,
            0,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            inherit_handles.as_mut_ptr() as _,
            std::mem::size_of_val(&inherit_handles),
            null_mut(),
            null_mut(),
        )
    } == 0
    {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!(
                "Failed to update handle list attribute (code: {}).",
                unsafe { GetLastError() }
            ),
        ));
    }

    startup_info_ex.lpAttributeList = attr_list as _;

    let mut cmd_line_wide = build_command_line(program, args);
    let env_block = build_environment_block(env);
    let cwd_wide = to_wide_null_path(cwd);

    let mut proc_info: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };

    let success = unsafe {
        CreateProcessW(
            null(),
            cmd_line_wide.as_mut_ptr(),
            null(),
            null(),
            TRUE,
            creation_flags | EXTENDED_STARTUPINFO_PRESENT,
            env_block.as_ptr() as *const _,
            cwd_wide.as_ptr(),
            &startup_info_ex.StartupInfo,
            &mut proc_info,
        )
    };

    if success == 0 {
        let err_code = unsafe { GetLastError() };
        return Err(CommandError::new(
            "ENGINE_UNAVAILABLE",
            format!("Failed to spawn engine process (code: {err_code})."),
        ));
    }

    // Close child ends in parent process
    drop(stdin_read);
    drop(stdout_write);
    drop(stderr_write);
    if !proc_info.hThread.is_null() {
        unsafe { CloseHandle(proc_info.hThread) };
    }

    let stdin_file = unsafe { std::fs::File::from_raw_handle(stdin_write.take() as _) };
    let stdout_file = unsafe { std::fs::File::from_raw_handle(stdout_read.take() as _) };
    let stderr_file = unsafe { std::fs::File::from_raw_handle(stderr_read.take() as _) };

    Ok(ChildProcess {
        pid: proc_info.dwProcessId,
        process_handle: proc_info.hProcess,
        stdin_write: Some(stdin_file),
        stdout_read: Some(stdout_file),
        stderr_read: Some(stderr_file),
        child_has_private_console,
    })
}

pub type ConsoleCtrlHandlerFn = unsafe extern "system" fn(u32) -> BOOL;

static REGISTERED_HOST_HANDLERS: Mutex<Vec<ConsoleCtrlHandlerFn>> = Mutex::new(Vec::new());

/// Registers a console control handler for the host process and tracks it so it is
/// reinstalled if console detach/reattach cycles occur during private child signaling.
pub fn register_host_ctrl_handler(handler: ConsoleCtrlHandlerFn) -> Result<(), CommandError> {
    let mut guard = REGISTERED_HOST_HANDLERS.lock().unwrap();
    let res = unsafe { SetConsoleCtrlHandler(Some(handler), TRUE) };
    if res == 0 {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!(
                "Failed to register console control handler (code: {}).",
                unsafe { GetLastError() }
            ),
        ));
    }
    if !guard.iter().any(|&h| h as usize == handler as usize) {
        guard.push(handler);
    }
    Ok(())
}

/// Unregisters a console control handler for the host process and removes it from tracking.
pub fn unregister_host_ctrl_handler(handler: ConsoleCtrlHandlerFn) -> Result<(), CommandError> {
    let mut guard = REGISTERED_HOST_HANDLERS.lock().unwrap();
    let res = unsafe { SetConsoleCtrlHandler(Some(handler), FALSE) };
    guard.retain(|&h| h as usize != handler as usize);
    if res == 0 {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!(
                "Failed to unregister console control handler (code: {}).",
                unsafe { GetLastError() }
            ),
        ));
    }
    Ok(())
}

fn restore_host_handlers() {
    let handlers = {
        let guard = REGISTERED_HOST_HANDLERS.lock().unwrap();
        guard.clone()
    };
    for handler in handlers {
        let _ = unsafe { SetConsoleCtrlHandler(Some(handler), TRUE) };
    }
}

unsafe extern "system" fn ignore_ctrl_break(ctrl_type: u32) -> BOOL {
    if ctrl_type == CTRL_BREAK_EVENT {
        TRUE
    } else {
        FALSE
    }
}

/// Sends a cooperative CTRL_BREAK_EVENT cancellation signal to the child.
pub fn send_cancellation_signal(
    pid: u32,
    child_has_private_console: bool,
) -> Result<(), CommandError> {
    if child_has_private_console {
        // Query console process list with 64-entry buffer to identify console peer (avoiding our own PID)
        let mut pids = [0u32; 64];
        let pids_count = unsafe { GetConsoleProcessList(pids.as_mut_ptr(), pids.len() as u32) };
        let my_pid = unsafe { GetCurrentProcessId() };
        let original_console_peer = if pids_count > 0 {
            let count = (pids_count as usize).min(pids.len());
            pids[..count]
                .iter()
                .copied()
                .find(|&p| p != 0 && p != my_pid)
        } else {
            None
        };
        const ATTACH_PARENT_PROCESS: u32 = 0xFFFF_FFFF;

        let reattach_original_console = || unsafe {
            if let Some(orig_peer) = original_console_peer {
                if AttachConsole(orig_peer) == 0 {
                    let _ = AttachConsole(ATTACH_PARENT_PROCESS);
                }
                restore_host_handlers();
            } else if pids_count > 0 {
                let _ = AttachConsole(ATTACH_PARENT_PROCESS);
                restore_host_handlers();
            }
        };

        unsafe {
            let _ = FreeConsole();

            let attached = AttachConsole(pid);
            if attached == 0 {
                let err = GetLastError();
                reattach_original_console();
                return Err(CommandError::new(
                    "TRANSPORT_ERROR",
                    format!(
                        "Failed to attach to child process console for cancellation (code: {err})."
                    ),
                ));
            }

            // Install temporary ignore handler ONLY for CTRL_BREAK on child console
            let handler_set = SetConsoleCtrlHandler(Some(ignore_ctrl_break), TRUE);
            if handler_set == 0 {
                let err = GetLastError();
                let _ = FreeConsole();
                reattach_original_console();
                return Err(CommandError::new(
                    "TRANSPORT_ERROR",
                    format!("Failed to install temporary console control handler (code: {err})."),
                ));
            }

            let signaled = GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, 0);

            // Allow Windows conhost event dispatch to complete signal delivery
            std::thread::sleep(std::time::Duration::from_millis(50));

            // Clean up temporary handler on child console before disconnecting
            let _ = SetConsoleCtrlHandler(Some(ignore_ctrl_break), FALSE);

            let _ = FreeConsole();

            // Reattach to original console and restore registered host handlers
            reattach_original_console();

            if signaled == 0 {
                return Err(CommandError::new(
                    "TRANSPORT_ERROR",
                    format!(
                        "Failed to send CTRL_BREAK_EVENT to child console (code: {}).",
                        GetLastError()
                    ),
                ));
            }
        }
    } else {
        // Host shares console: signal child's process group directly using child PID
        let res = unsafe { GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid) };
        if res == 0 {
            let err = unsafe { GetLastError() };
            return Err(CommandError::new(
                "TRANSPORT_ERROR",
                format!("Failed to send CTRL_BREAK_EVENT to process group {pid} (code: {err})."),
            ));
        }
    }

    Ok(())
}

/// Waits for the process to exit up to timeout_ms. Returns Some(exit_code) if exited, None if timed out.
#[allow(clippy::not_unsafe_ptr_arg_deref)]
pub fn wait_process_timeout(handle: HANDLE, timeout_ms: u32) -> Result<Option<u32>, CommandError> {
    let wait_res = unsafe { WaitForSingleObject(handle, timeout_ms) };
    if wait_res == WAIT_OBJECT_0 {
        let mut exit_code: u32 = 0;
        let get_res = unsafe { GetExitCodeProcess(handle, &mut exit_code) };
        if get_res == 0 {
            return Err(CommandError::new(
                "TRANSPORT_ERROR",
                format!("Failed to get process exit code (code: {}).", unsafe {
                    GetLastError()
                }),
            ));
        }
        Ok(Some(exit_code))
    } else if wait_res == WAIT_TIMEOUT {
        Ok(None)
    } else {
        Err(CommandError::new(
            "TRANSPORT_ERROR",
            format!("WaitForSingleObject failed (code: {}).", unsafe {
                GetLastError()
            }),
        ))
    }
}

/// Forcibly terminates the process if graceful cancellation or completion fails.
#[allow(clippy::not_unsafe_ptr_arg_deref)]
pub fn force_terminate_process(handle: HANDLE, exit_code: u32) -> Result<(), CommandError> {
    let res = unsafe { TerminateProcess(handle, exit_code) };
    if res == 0 {
        let err = unsafe { GetLastError() };
        if err != ERROR_ACCESS_DENIED {
            return Err(CommandError::new(
                "TRANSPORT_ERROR",
                format!("Failed to terminate process (code: {err})."),
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_command_line() {
        let prog = Path::new(r"C:\Program Files\Python\python.exe");
        let args = ["-m", "ipc", "--flag", "value with space", ""];
        let wide = build_command_line(prog, &args);
        let s = String::from_utf16_lossy(&wide);
        assert!(s.starts_with(
            r#""C:\Program Files\Python\python.exe" -m ipc --flag "value with space" """#
        ));
    }

    #[test]
    fn test_build_environment_block() {
        let mut env = HashMap::new();
        env.insert("B_VAR".to_string(), "2".to_string());
        env.insert("A_VAR".to_string(), "1".to_string());

        let block = build_environment_block(&env);
        let s = String::from_utf16_lossy(&block);
        assert_eq!(s, "A_VAR=1\0B_VAR=2\0\0");
    }

    #[test]
    fn test_host_ctrl_handler_registration() {
        unsafe extern "system" fn dummy_handler(_: u32) -> BOOL {
            FALSE
        }

        if is_host_attached_to_console() {
            assert!(register_host_ctrl_handler(dummy_handler).is_ok());
            assert!(unregister_host_ctrl_handler(dummy_handler).is_ok());
        }
    }
}
