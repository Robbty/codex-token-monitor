mod locate;
mod protocol;
mod render;
mod state;
mod tail;

use std::io::Write;
use std::path::PathBuf;

use anyhow::Result;
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
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    let codex_home = match cli.codex_home {
        Some(p) => p,
        None => locate::codex_home()?,
    };

    let selector = if let Some(id) = cli.thread.clone() {
        Selector::ThreadId(id)
    } else if let Some(cwd) = cli.cwd.clone() {
        let abs = if cwd.as_os_str() == "." {
            std::env::current_dir()?
        } else {
            cwd
        };
        Selector::Cwd(abs)
    } else {
        Selector::MostRecent
    };

    let rollout_path = resolve_with_optional_wait(
        &selector,
        &codex_home,
        cli.wait,
        cli.wait_timeout,
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
            None => break, // only reached when follow == false
        };

        let is_token_event = matches!(
            &item,
            RolloutItem::EventMsg {
                payload: EventMsg::TokenCount(_)
            }
        );

        apply(&mut state, item);

        // Snapshot policy:
        //   - non-follow: drain to EOF, emit once at the end.
        //   - follow:     emit once after initial drain, then on every TokenCount.
        if tail.follow {
            if !printed_initial && tail.passed_initial()? {
                print_snapshot(&state, &format)?;
                printed_initial = true;
            } else if printed_initial && is_token_event {
                print_snapshot(&state, &format)?;
            }
        }
    }

    if !tail.follow {
        print_snapshot(&state, &format)?;
    }
    Ok(())
}

fn resolve_with_optional_wait(
    selector: &Selector,
    codex_home: &std::path::Path,
    wait: bool,
    timeout_secs: Option<u64>,
) -> Result<PathBuf> {
    use std::time::{Duration, Instant};

    if !wait {
        return locate::resolve(selector, codex_home);
    }

    let deadline = timeout_secs.map(|s| Instant::now() + Duration::from_secs(s));
    let mut announced = false;

    loop {
        match locate::resolve(selector, codex_home) {
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
                std::thread::sleep(Duration::from_millis(500));
            }
        }
    }
}

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

fn print_snapshot(state: &TokenState, format: &Format) -> Result<()> {
    let mut stdout = std::io::stdout().lock();
    stdout.write_all(render::render(state, format).as_bytes())?;
    if matches!(format, Format::Json) {
        stdout.write_all(b"\n")?;
    } else {
        stdout.write_all(b"---\n")?;
    }
    stdout.flush()?;
    Ok(())
}
