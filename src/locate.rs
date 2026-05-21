//! Locate the rollout JSONL file that belongs to the currently active session.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use anyhow::{Context, Result, anyhow};

use crate::protocol::RolloutItem;

#[derive(Clone)]
pub enum Selector {
    ThreadId(String),
    Cwd(PathBuf),
    MostRecent,
    /// System-wide: every rollout, no cwd filter.
    All,
}

pub fn codex_home() -> Result<PathBuf> {
    if let Ok(p) = std::env::var("CODEX_HOME") {
        return Ok(PathBuf::from(p));
    }
    let home = dirs::home_dir().ok_or_else(|| anyhow!("could not determine home directory"))?;
    Ok(home.join(".codex"))
}

pub fn resolve_filtered(
    sel: &Selector,
    codex_home: &Path,
    require_open: bool,
) -> Result<PathBuf> {
    let sessions = codex_home.join("sessions");
    if !sessions.is_dir() {
        return Err(anyhow!(
            "sessions directory not found: {}",
            sessions.display()
        ));
    }
    let files = list_rollouts(&sessions)?;
    if files.is_empty() {
        return Err(anyhow!("no rollout files under {}", sessions.display()));
    }

    match sel {
        Selector::ThreadId(id) => {
            let needle = format!("-{id}.jsonl");
            files
                .into_iter()
                .find(|(p, _)| {
                    p.file_name()
                        .and_then(|n| n.to_str())
                        .is_some_and(|n| n.ends_with(&needle))
                        && (!require_open || crate::proc::is_held_open(p))
                })
                .map(|(p, _)| p)
                .ok_or_else(|| anyhow!("no rollout for thread id {id}"))
        }
        Selector::Cwd(target) => {
            let target = target.canonicalize().unwrap_or_else(|_| target.clone());
            // Walk newest-first; first match wins.
            for (path, _) in files {
                if let Ok(Some(meta_cwd)) = read_session_cwd(&path)
                    && Path::new(&meta_cwd) == target
                    && (!require_open || crate::proc::is_held_open(&path))
                {
                    return Ok(path);
                }
            }
            Err(anyhow!(
                "no rollout whose session cwd matches {}",
                target.display()
            ))
        }
        Selector::MostRecent | Selector::All => {
            for (path, _) in files {
                if !require_open || crate::proc::is_held_open(&path) {
                    return Ok(path);
                }
            }
            Err(anyhow!("no open rollout files"))
        }
    }
}

/// Returns ALL rollout files matching the selector that are newer than `max_age`.
/// Used by multi-session mode; returns paths sorted newest first by mtime.
///
/// If `require_open` is true, an additional filter only keeps paths whose
/// rollout file is currently held open by some process (= the Codex session
/// is still alive). This makes the result independent of the mtime heuristic.
///
/// For `Selector::ThreadId` and `Selector::MostRecent`, this still returns at
/// most one path (those selectors are inherently single-target).
pub fn resolve_all(
    sel: &Selector,
    codex_home: &Path,
    max_age: Duration,
    require_open: bool,
) -> Result<Vec<PathBuf>> {
    let sessions = codex_home.join("sessions");
    if !sessions.is_dir() {
        return Err(anyhow!(
            "sessions directory not found: {}",
            sessions.display()
        ));
    }
    let all_files = list_rollouts(&sessions)?;
    let cutoff = SystemTime::now()
        .checked_sub(max_age)
        .unwrap_or(SystemTime::UNIX_EPOCH);
    let fresh: Vec<_> = all_files
        .into_iter()
        .filter(|(_, mtime)| *mtime >= cutoff)
        .collect();

    match sel {
        Selector::ThreadId(_) | Selector::MostRecent => {
            // Reuse single-session resolve so behavior stays consistent.
            // (max_age is intentionally not applied here — these selectors
            //  ask for a specific session, not "active sessions".)
            match resolve_filtered(sel, codex_home, require_open) {
                Ok(p) => Ok(vec![p]),
                Err(e) => Err(e),
            }
        }
        Selector::Cwd(target) => {
            if fresh.is_empty() {
                return Err(anyhow!(
                    "no active rollout files under {} (within max-age window)",
                    sessions.display()
                ));
            }
            let target = target.canonicalize().unwrap_or_else(|_| target.clone());
            let mut matches: Vec<PathBuf> = Vec::new();
            for (path, _) in fresh {
                if let Ok(Some(meta_cwd)) = read_session_cwd(&path)
                    && Path::new(&meta_cwd) == target
                    && (!require_open || crate::proc::is_held_open(&path))
                {
                    matches.push(path);
                }
            }
            if matches.is_empty() {
                Err(anyhow!(
                    "no active rollout whose session cwd matches {}",
                    target.display()
                ))
            } else {
                Ok(matches)
            }
        }
        Selector::All => {
            // System-wide: every rollout that passes max_age and require_open.
            // No cwd lookup needed, so this is much cheaper than the Cwd branch.
            let matches: Vec<PathBuf> = fresh
                .into_iter()
                .filter(|(p, _)| !require_open || crate::proc::is_held_open(p))
                .map(|(p, _)| p)
                .collect();
            if matches.is_empty() {
                Err(anyhow!(
                    "no active rollout files under {} (within max-age window)",
                    sessions.display()
                ))
            } else {
                Ok(matches)
            }
        }
    }
}

/// Returns rollout files sorted newest first by mtime.
fn list_rollouts(sessions_dir: &Path) -> Result<Vec<(PathBuf, SystemTime)>> {
    let mut out: Vec<(PathBuf, SystemTime)> = Vec::new();
    walk(sessions_dir, &mut out)?;
    out.sort_by(|a, b| b.1.cmp(&a.1));
    Ok(out)
}

fn walk(dir: &Path, out: &mut Vec<(PathBuf, SystemTime)>) -> Result<()> {
    for entry in std::fs::read_dir(dir).with_context(|| format!("read_dir {}", dir.display()))? {
        let entry = entry?;
        let path = entry.path();
        let file_type = entry.file_type()?;
        if file_type.is_dir() {
            walk(&path, out)?;
        } else if file_type.is_file()
            && path
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("rollout-") && n.ends_with(".jsonl"))
        {
            let mtime = entry.metadata()?.modified()?;
            out.push((path, mtime));
        }
    }
    Ok(())
}

fn read_session_cwd(path: &Path) -> Result<Option<String>> {
    let f = File::open(path)?;
    let mut reader = BufReader::new(f);
    let mut line = String::new();
    if reader.read_line(&mut line)? == 0 {
        return Ok(None);
    }
    let item: RolloutItem = serde_json::from_str(line.trim_end())?;
    match item {
        RolloutItem::SessionMeta { payload } => Ok(payload.cwd),
        _ => Ok(None),
    }
}
