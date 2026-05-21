//! Detect whether a rollout JSONL file is currently held open by any process.
//!
//! Linux-only: walks `/proc/<pid>/fd/` and compares symlink targets. Codex
//! keeps the rollout file open with a write handle for the duration of a
//! session, so "any process holds this file" is a precise active/closed
//! indicator — far better than the mtime heuristic, which mistakes long idle
//! pauses for closed sessions.
//!
//! Returns `false` on non-Linux or if `/proc` cannot be read (the caller can
//! then fall back to the mtime heuristic). Reads only the entries the current
//! user owns — no elevated privileges needed.

use std::fs;
use std::path::Path;

/// Returns true iff some process on this machine currently holds the rollout
/// file open **for writing**. Read-only handles (e.g. from our own
/// `codex-tokens` process tailing the file) are explicitly ignored.
pub fn is_held_open(rollout: &Path) -> bool {
    let target = match fs::canonicalize(rollout) {
        Ok(p) => p,
        Err(_) => return false,
    };
    let proc_dir = match fs::read_dir("/proc") {
        Ok(d) => d,
        Err(_) => return false,
    };

    for entry in proc_dir.flatten() {
        let name = entry.file_name();
        let name_str = match name.to_str() {
            Some(s) => s,
            None => continue,
        };
        if !name_str.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }

        let pid_path = entry.path();
        let fd_dir = pid_path.join("fd");
        let fds = match fs::read_dir(&fd_dir) {
            Ok(d) => d,
            Err(_) => continue,
        };

        for fd in fds.flatten() {
            let fd_path = fd.path();
            if let Ok(link) = fs::read_link(&fd_path)
                && link == target
            {
                let fd_name = fd.file_name();
                let fdinfo_path = pid_path.join("fdinfo").join(&fd_name);
                if fd_opened_for_write(&fdinfo_path) {
                    return true;
                }
            }
        }
    }

    false
}

/// Parse `/proc/<pid>/fdinfo/<fd>` and check the `flags:` line for write access.
/// O_WRONLY = 0o1, O_RDWR = 0o2 — either makes the lower two bits non-zero.
fn fd_opened_for_write(fdinfo_path: &Path) -> bool {
    let content = match fs::read_to_string(fdinfo_path) {
        Ok(s) => s,
        Err(_) => return false,
    };
    for line in content.lines() {
        if let Some(rest) = line.strip_prefix("flags:") {
            let flags_str = rest.trim();
            // Linux writes flags in octal.
            if let Ok(flags) = u32::from_str_radix(flags_str, 8) {
                return flags & 0o3 != 0;
            }
            return false;
        }
    }
    false
}
