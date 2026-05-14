//! Locate the rollout JSONL file that belongs to the currently active session.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use anyhow::{Context, Result, anyhow};

use crate::protocol::RolloutItem;

pub enum Selector {
    ThreadId(String),
    Cwd(PathBuf),
    MostRecent,
}

pub fn codex_home() -> Result<PathBuf> {
    if let Ok(p) = std::env::var("CODEX_HOME") {
        return Ok(PathBuf::from(p));
    }
    let home = dirs::home_dir().ok_or_else(|| anyhow!("could not determine home directory"))?;
    Ok(home.join(".codex"))
}

pub fn resolve(sel: &Selector, codex_home: &Path) -> Result<PathBuf> {
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
                {
                    return Ok(path);
                }
            }
            Err(anyhow!(
                "no rollout whose session cwd matches {}",
                target.display()
            ))
        }
        Selector::MostRecent => Ok(files.into_iter().next().unwrap().0),
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
