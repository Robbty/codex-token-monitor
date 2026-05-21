mod locate;
mod proc;
mod protocol;
mod render;
mod state;
mod tail;

use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{Sender, channel};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Result, anyhow};
use clap::Parser;

use crate::locate::Selector;
use crate::protocol::{EventMsg, RolloutItem};
use crate::render::Format;
use crate::state::TokenState;

/// Stdout token/context monitor for the currently active Codex session.
#[derive(Parser, Debug)]
#[command(name = "codex-tokens", version)]
struct Cli {
    /// Bind to a specific session by thread/session UUID.
    #[arg(long)]
    thread: Option<String>,

    /// Bind to the session whose recorded cwd matches PATH (default: $PWD).
    #[arg(long, value_name = "PATH", num_args = 0..=1, default_missing_value = ".")]
    cwd: Option<PathBuf>,

    /// Override CODEX_HOME (default: $CODEX_HOME or ~/.codex).
    #[arg(long, value_name = "DIR")]
    codex_home: Option<PathBuf>,

    /// Follow the rollout file and emit a fresh snapshot on every token update.
    #[arg(long, short = 'f')]
    follow: bool,

    /// Output as a single-line JSON object instead of KEY=VALUE.
    #[arg(long)]
    json: bool,

    /// Print the resolved rollout file path and exit.
    #[arg(long)]
    locate: bool,

    /// Wait until a matching rollout file appears instead of erroring out
    /// immediately. Useful when starting the monitor before Codex.
    #[arg(long)]
    wait: bool,

    /// Maximum seconds to wait when --wait is set (default: no limit).
    #[arg(long, value_name = "SECS", requires = "wait")]
    wait_timeout: Option<u64>,

    /// Multi-session mode: with --cwd, follow ALL active matching rollouts
    /// instead of just the newest one. Each session's output is prefixed
    /// with a "=== session <uuid> ===" header.
    #[arg(long, requires = "cwd")]
    all: bool,

    /// Maximum age in minutes a rollout's mtime can have to count as "active".
    /// Only applied in --all mode (default: 5 minutes).
    #[arg(long, value_name = "MINUTES", default_value_t = 5, requires = "all")]
    max_age: u64,

    /// With --all --follow: periodically re-scan the sessions directory for
    /// new rollouts and start watching them too (poll every 5 seconds).
    #[arg(long, requires_all = ["all", "follow"])]
    watch_new: bool,

    /// Only include rollouts whose file is currently held open by some
    /// process (= Codex session is still alive). Linux/WSL2 only; on other
    /// platforms or without /proc access this flag has no effect.
    #[arg(long)]
    require_open: bool,
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    let codex_home = match cli.codex_home.clone() {
        Some(p) => p,
        None => locate::codex_home()?,
    };

    let selector = build_selector(&cli)?;

    if cli.all {
        return run_multi_session(&cli, &selector, &codex_home);
    }

    run_single_session(&cli, &selector, &codex_home)
}

fn build_selector(cli: &Cli) -> Result<Selector> {
    if let Some(id) = cli.thread.clone() {
        Ok(Selector::ThreadId(id))
    } else if let Some(cwd) = cli.cwd.clone() {
        let abs = if cwd.as_os_str() == "." {
            std::env::current_dir()?
        } else {
            cwd
        };
        Ok(Selector::Cwd(abs))
    } else {
        Ok(Selector::MostRecent)
    }
}

// ─── Single-session path (unchanged behavior) ────────────────────────────────

fn run_single_session(cli: &Cli, selector: &Selector, codex_home: &Path) -> Result<()> {
    let rollout_path = resolve_with_optional_wait(
        selector,
        codex_home,
        cli.wait,
        cli.wait_timeout,
        cli.require_open,
    )?;

    if cli.locate {
        println!("{}", rollout_path.display());
        return Ok(());
    }

    let format = if cli.json { Format::Json } else { Format::Kv };
    let mut state = TokenState::default();
    let mut tail = tail::Tail::open(&rollout_path, cli.follow)?;
    let mut printed_initial = false;

    loop {
        let item = match tail.next_item()? {
            Some(item) => item,
            None => break,
        };

        let is_token_event = matches!(
            &item,
            RolloutItem::EventMsg {
                payload: EventMsg::TokenCount(_)
            }
        );

        apply(&mut state, item);

        if tail.follow {
            if !printed_initial && tail.passed_initial()? {
                state.session_active = Some(proc::is_held_open(&rollout_path));
                print_snapshot(&state, &format, /*multi*/ false)?;
                printed_initial = true;
            } else if printed_initial && is_token_event {
                state.session_active = Some(proc::is_held_open(&rollout_path));
                print_snapshot(&state, &format, /*multi*/ false)?;
            }
        }
    }

    if !tail.follow {
        state.session_active = Some(proc::is_held_open(&rollout_path));
        print_snapshot(&state, &format, /*multi*/ false)?;
    }
    Ok(())
}

fn resolve_with_optional_wait(
    selector: &Selector,
    codex_home: &Path,
    wait: bool,
    timeout_secs: Option<u64>,
    require_open: bool,
) -> Result<PathBuf> {
    if !wait {
        return locate::resolve_filtered(selector, codex_home, require_open);
    }

    let deadline = timeout_secs.map(|s| Instant::now() + Duration::from_secs(s));
    let mut announced = false;

    loop {
        match locate::resolve_filtered(selector, codex_home, require_open) {
            Ok(path) => return Ok(path),
            Err(err) => {
                if let Some(d) = deadline
                    && Instant::now() >= d
                {
                    return Err(err.context("--wait timeout reached"));
                }
                if !announced {
                    eprintln!("codex-tokens: waiting for matching rollout file...");
                    announced = true;
                }
                thread::sleep(Duration::from_millis(500));
            }
        }
    }
}

// ─── Multi-session path ──────────────────────────────────────────────────────

/// Discovery / rescan interval for --watch-new.
const DISCOVERY_INTERVAL: Duration = Duration::from_secs(5);

/// Internal message sent from a tail thread to the output coordinator.
enum TailMsg {
    /// Parsed item from the rollout file.
    Item(RolloutItem),
    /// Tail has drained all content present at open time (-> emit initial snapshot).
    InitialDrained,
    /// Tail thread is exiting (file vanished, EOF in non-follow, or fatal error).
    Closed(Option<String>),
}

/// Map key: stable identifier for the source rollout file (its path).
type TailKey = PathBuf;

fn run_multi_session(cli: &Cli, selector: &Selector, codex_home: &Path) -> Result<()> {
    let max_age = Duration::from_secs(cli.max_age.saturating_mul(60));

    // Initial discovery (with optional --wait loop).
    let paths = resolve_all_with_optional_wait(
        selector,
        codex_home,
        max_age,
        cli.wait,
        cli.wait_timeout,
        cli.require_open,
    )?;

    if cli.locate {
        for p in &paths {
            println!("{}", p.display());
        }
        return Ok(());
    }

    let format = if cli.json { Format::Json } else { Format::Kv };
    let (tx, rx) = channel::<(TailKey, TailMsg)>();

    // Track which paths are already being followed (used by discovery thread).
    let mut tracked: HashSet<TailKey> = HashSet::new();

    for path in paths {
        spawn_tail(path.clone(), cli.follow, tx.clone());
        tracked.insert(path);
    }

    // Optional discovery thread for --watch-new.
    if cli.watch_new {
        spawn_discovery(
            selector.clone(),
            codex_home.to_path_buf(),
            max_age,
            cli.require_open,
            tracked.clone(),
            tx.clone(),
        );
    }

    // Drop the original sender so the channel closes when all tails finish
    // in non-follow mode.
    drop(tx);

    // Per-session state.
    let mut states: HashMap<TailKey, TokenState> = HashMap::new();
    let mut active: HashMap<TailKey, bool> = HashMap::new();
    let mut printed_initial: HashSet<TailKey> = HashSet::new();

    while let Ok((key, msg)) = rx.recv() {
        match msg {
            TailMsg::Item(item) => {
                let entry = states.entry(key.clone()).or_default();
                active.entry(key.clone()).or_insert(true);
                let is_token_event = matches!(
                    &item,
                    RolloutItem::EventMsg {
                        payload: EventMsg::TokenCount(_)
                    }
                );
                apply(entry, item);

                // In follow mode emit a snapshot for this session only after the
                // initial drain, on every TokenCount event. In non-follow mode
                // we wait for Closed and emit once at the end.
                if cli.follow && printed_initial.contains(&key) && is_token_event {
                    entry.session_active = Some(proc::is_held_open(&key));
                    print_snapshot(entry, &format, /*multi*/ true)?;
                }
            }
            TailMsg::InitialDrained => {
                if cli.follow && !printed_initial.contains(&key)
                    && let Some(state) = states.get_mut(&key)
                {
                    state.session_active = Some(proc::is_held_open(&key));
                    print_snapshot(state, &format, /*multi*/ true)?;
                    printed_initial.insert(key.clone());
                }
            }
            TailMsg::Closed(err) => {
                if let Some(msg) = err {
                    eprintln!(
                        "codex-tokens: tail for {} stopped: {}",
                        key.display(),
                        msg
                    );
                }
                active.insert(key.clone(), false);
                // In non-follow mode the snapshot is emitted now (we've drained the file).
                if !cli.follow && let Some(state) = states.get_mut(&key) {
                    state.session_active = Some(proc::is_held_open(&key));
                    print_snapshot(state, &format, /*multi*/ true)?;
                }
            }
        }
    }

    Ok(())
}

fn resolve_all_with_optional_wait(
    selector: &Selector,
    codex_home: &Path,
    max_age: Duration,
    wait: bool,
    timeout_secs: Option<u64>,
    require_open: bool,
) -> Result<Vec<PathBuf>> {
    if !wait {
        return locate::resolve_all(selector, codex_home, max_age, require_open);
    }

    let deadline = timeout_secs.map(|s| Instant::now() + Duration::from_secs(s));
    let mut announced = false;

    loop {
        match locate::resolve_all(selector, codex_home, max_age, require_open) {
            Ok(paths) if !paths.is_empty() => return Ok(paths),
            Ok(_) | Err(_) => {
                if let Some(d) = deadline
                    && Instant::now() >= d
                {
                    return Err(anyhow!("--wait timeout reached without matching rollout"));
                }
                if !announced {
                    eprintln!("codex-tokens: waiting for matching rollout file...");
                    announced = true;
                }
                thread::sleep(Duration::from_millis(500));
            }
        }
    }
}

fn spawn_tail(path: PathBuf, follow: bool, tx: Sender<(TailKey, TailMsg)>) {
    thread::spawn(move || {
        let key = path.clone();
        let mut tail = match tail::Tail::open(&path, follow) {
            Ok(t) => t,
            Err(e) => {
                let _ = tx.send((key, TailMsg::Closed(Some(e.to_string()))));
                return;
            }
        };

        // Loop reading items.
        let mut signaled_drain = false;
        loop {
            // Detect "initial drain done" boundary: if we're in follow mode and
            // have passed the initial_len, fire the signal once.
            if follow && !signaled_drain {
                if let Ok(true) = tail.passed_initial() {
                    if tx.send((key.clone(), TailMsg::InitialDrained)).is_err() {
                        return;
                    }
                    signaled_drain = true;
                }
            }

            match tail.next_item() {
                Ok(Some(item)) => {
                    if tx.send((key.clone(), TailMsg::Item(item))).is_err() {
                        return;
                    }
                }
                Ok(None) => {
                    // EOF in non-follow mode: send drain signal then Closed.
                    if !follow && !signaled_drain {
                        let _ = tx.send((key.clone(), TailMsg::InitialDrained));
                    }
                    let _ = tx.send((key, TailMsg::Closed(None)));
                    return;
                }
                Err(e) => {
                    let _ = tx.send((key, TailMsg::Closed(Some(e.to_string()))));
                    return;
                }
            }
        }
    });
}

fn spawn_discovery(
    selector: Selector,
    codex_home: PathBuf,
    max_age: Duration,
    require_open: bool,
    initial: HashSet<TailKey>,
    tx: Sender<(TailKey, TailMsg)>,
) {
    thread::spawn(move || {
        let mut tracked = initial;
        loop {
            thread::sleep(DISCOVERY_INTERVAL);
            let paths = match locate::resolve_all(&selector, &codex_home, max_age, require_open) {
                Ok(p) => p,
                Err(_) => continue, // sessions dir might be empty/transient
            };
            for path in paths {
                if !tracked.contains(&path) {
                    tracked.insert(path.clone());
                    spawn_tail(path, /*follow*/ true, tx.clone());
                }
            }
        }
    });
}

// ─── Shared helpers ──────────────────────────────────────────────────────────

fn apply(state: &mut TokenState, item: RolloutItem) {
    match item {
        RolloutItem::SessionMeta { payload } => {
            state.session_id = Some(payload.id);
            state.session_cwd = payload.cwd;
        }
        RolloutItem::EventMsg {
            payload: EventMsg::TokenCount(ev),
        } => {
            state.apply(&ev);
        }
        _ => {}
    }
}

fn print_snapshot(state: &TokenState, format: &Format, multi: bool) -> Result<()> {
    // Build the full block first, then write it with a single write_all so
    // concurrent threads' output cannot interleave at the line level.
    let mut buf = render::render(state, format, multi).into_bytes();
    if matches!(format, Format::Json) {
        buf.push(b'\n');
    } else {
        buf.extend_from_slice(b"---\n");
    }
    let mut stdout = std::io::stdout().lock();
    stdout.write_all(&buf)?;
    stdout.flush()?;
    Ok(())
}
