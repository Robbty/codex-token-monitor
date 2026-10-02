#!/usr/bin/env python3
"""
codex-token-display — minimal HTTP/SSE bridge between codex-tokens and a
browser-based status window.

Architecture:
  - spawns `codex-tokens --cwd <dir> --all --follow --watch-new
    --require-open --wait --json` as a child process
  - reads its NDJSON stdout line by line
  - exposes:
        GET /                  → index.html
        GET /static/<file>     → app.js, app.css, …
        GET /events            → Server-Sent Events stream of session snapshots
                                 (+ "ghosts": Codex TUIs without a session)
        GET /plan              → account rate-limit state (opt-in)
        GET /chats             → chats.html (recent-chats window)
        GET /chats-data        → recent chats across all projects (JSON)
        GET /chat-detail       → condensed conversation tail of one session
        GET/POST /settings     → plan-widget, chats and display settings
        POST /open-dir         → opens a directory in the system file manager
        POST /focus-terminal   → focuses the terminal/IDE window of a session
        POST /copy             → copy text to the clipboard
        POST /sticky           → pin a monitor window to all workspaces
        POST /kill             → SIGTERM to the Codex TUI of a session/ghost
        POST /chat-delete      → delete one finished session (`codex delete`)
        POST /chat-favorite    → pin/unpin one chat (★) in the settings
        POST /chat-rename      → set/clear a custom display name for one chat
        POST /handover         → paste the rollover prompt into the session's
                                 terminal (falls back to clipboard copy)
        GET /handover-status   → HANDOVER.md/rollout mtimes for the ↻ watcher
        POST /session-exit     → type /exit into the session's terminal

Session ↔ terminal mapping. Since Codex 0.160 the TUI usually talks to a
shared local app-server daemon (`codex app-server --managed-daemon`), and it
is the DAEMON that writes the rollout files — the TUI process holds nothing
session-specific open. The daemon inherits WINDOWID from whichever terminal
started it, so its environment must never be used to pick a window. The
mapping (see _assign_tuis) therefore goes, most reliable first:
  1. the TUI itself holds the rollout with a write FD (Codex without daemon)
  2. the session's thread name appears in the TUI's terminal title (Codex
     titles its terminal "<thread name> | <project>")
  3. the TUI was started as `codex resume <session id>`
  4. a TUI running at/below the session's cwd (heuristic)
Actions that type into a terminal or kill a process only act on 1–3.

The server uses only the Python standard library; no pip install needed.
"""

import http.server
import json
import os
import queue
import re
import shlex
import shutil
import signal
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 8765
SSE_HEARTBEAT_SEC = 10

# Plan-widget settings live in a small config file alongside the user's
# other XDG state. The widget is opt-in because it reads the auth token
# from ~/.codex/auth.json and makes outbound HTTPS calls.
CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
) / "codex-token-monitor"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_SETTINGS = {
    "plan_widget": {
        "enabled": False,
        # One-shot ack: stays True once the user has seen + accepted the
        # consent modal. We deliberately do NOT reset this when the widget
        # gets toggled off, so the dialog only ever appears once per
        # config-file (deleting the file resurfaces it).
        "consent_acknowledged": False,
        "rows": {
            "main":        True,
            "codex_spark": False,
            "code_review": False,
            "credits":     False,
        },
    },
    # Limits for the recent-chats window (🕘). Both apply combined: first the
    # N newest chats per project directory, then the M newest of those overall.
    # active_first: sort chats of currently running sessions (and, when
    # sorting by directory, their whole directory) above the rest.
    # favorites: pinned session ids (★). Favorites are always listed — they
    # neither count against nor compete for the per_dir/total slots, no matter
    # how old they are. only_favorites: show nothing but favorites.
    # names: user-given display names (sid → name) shown in the topic column
    # instead of the auto title; the auto title moves into the tooltip.
    "chats": {
        "per_dir": 3,
        "total": 10,
        "active_first": False,
        "favorites": [],
        "only_favorites": False,
        "names": {},
    },
    # Window behavior. sticky: show the monitor and its sub-windows (help,
    # recent chats) on all workspaces. Applied via wmctrl when a page loads;
    # set to false to keep windows on the workspace they were opened on.
    "windows": {
        "sticky": True,
    },
    # Main-window card order: usage (most-compacted/fullest first),
    # dir (by project path), start (by session start time, oldest first).
    "display": {
        "sort": "usage",
    },
}

DISPLAY_SORT_MODES = ("usage", "dir", "start")

CHATS_PER_DIR_MAX = 20
CHATS_TOTAL_MAX = 100
CHATS_FAVORITES_MAX = 200  # hard cap so the config file cannot grow unbounded
CHATS_NAMES_MAX = 200      # same rationale for custom chat names
CHATS_NAME_MAX_CHARS = 120

# Codex session ids are UUIDs (v7). Strict validation: client-supplied ids
# are only ever resolved to files via a glob under ~/.codex/sessions.
_SID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Cache for the upstream /plan response. The numbers only change when the
# user actually makes a Codex request; refreshing more often than once a
# minute just burns the rate-limit of our own call.
PLAN_CACHE_TTL_SEC = 60
_plan_cache_lock = threading.Lock()
_plan_cache: dict | None = None
_plan_cache_ts: float = 0.0
_plan_cache_exit: int = 0  # last subprocess exit code


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _clamp_int(value, lo: int, hi: int, default: int) -> int:
    """Coerce value to an int within [lo, hi]; fall back to default."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _load_settings() -> dict:
    """Read settings from disk, merge over defaults, tolerate corruption."""
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    if not isinstance(data, dict):
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    # Deep-merge user data over defaults so a new setting added in a later
    # version surfaces with its default value instead of "missing".
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    pw_in = (data.get("plan_widget") or {})
    pw_out = merged["plan_widget"]
    if isinstance(pw_in.get("enabled"), bool):
        pw_out["enabled"] = pw_in["enabled"]
    if isinstance(pw_in.get("consent_acknowledged"), bool):
        pw_out["consent_acknowledged"] = pw_in["consent_acknowledged"]
    rows_in = pw_in.get("rows") or {}
    if isinstance(rows_in, dict):
        for k, v in rows_in.items():
            if isinstance(v, bool):
                pw_out["rows"][k] = v
    ch_in = data.get("chats") or {}
    ch_out = merged["chats"]
    if isinstance(ch_in, dict):
        ch_out["per_dir"] = _clamp_int(
            ch_in.get("per_dir"), 1, CHATS_PER_DIR_MAX, ch_out["per_dir"])
        ch_out["total"] = _clamp_int(
            ch_in.get("total"), 1, CHATS_TOTAL_MAX, ch_out["total"])
        if isinstance(ch_in.get("active_first"), bool):
            ch_out["active_first"] = ch_in["active_first"]
        if isinstance(ch_in.get("only_favorites"), bool):
            ch_out["only_favorites"] = ch_in["only_favorites"]
        fav_in = ch_in.get("favorites")
        if isinstance(fav_in, list):
            ch_out["favorites"] = [
                sid for sid in fav_in
                if isinstance(sid, str) and _SID_RE.fullmatch(sid)
            ][:CHATS_FAVORITES_MAX]
        names_in = ch_in.get("names")
        if isinstance(names_in, dict):
            ch_out["names"] = {
                sid: name.strip()[:CHATS_NAME_MAX_CHARS]
                for sid, name in list(names_in.items())[:CHATS_NAMES_MAX]
                if isinstance(sid, str) and _SID_RE.fullmatch(sid)
                and isinstance(name, str) and name.strip()
            }
    win_in = data.get("windows") or {}
    if isinstance(win_in, dict) and isinstance(win_in.get("sticky"), bool):
        merged["windows"]["sticky"] = win_in["sticky"]
    disp_in = data.get("display") or {}
    if isinstance(disp_in, dict) and disp_in.get("sort") in DISPLAY_SORT_MODES:
        merged["display"]["sort"] = disp_in["sort"]
    return merged


def _save_settings(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(CONFIG_PATH)  # atomic on POSIX


# --- shared state ---------------------------------------------------------

# Scope label: None for system-wide, otherwise the absolute path the user
# pinned the display to. Exposed via /scope so the UI can adjust its header.
_scope: str | None = None

# Latest snapshot per session_id. The SSE handler sends the full map on
# connect and individual updates afterwards.
_state_lock = threading.Lock()
_sessions: dict[str, dict] = {}

# Per-subscriber queues. Each SSE connection pulls from its own queue.
_subscribers_lock = threading.Lock()
_subscribers: list[queue.Queue] = []

# Ghost codex TUIs — interactive Codex processes that no live session can be
# mapped to (typically: started, but no first prompt sent yet). Recomputed
# by the sweeper and broadcast as a whole whenever the list changes.
_ghosts_lock = threading.Lock()
_ghosts: list[dict] = []

# Cache: session_id -> absolute rollout path. The lookup walks
# ~/.codex/sessions, so we only do it once per session.
_path_cache: dict[str, str] = {}

# Workspace (X11 desktop) name per session, resolved by the sweeper via the
# same window matcher that ⚡ uses; _enrich_snapshot copies it into snapshots
# arriving from the reader thread so the card keeps its prefix between sweeps.
_workspace_by_sid: dict[str, str | None] = {}

# Set in main(); the /plan handler needs to spawn the same binary used by
# the session-tail subprocess.
_codex_tokens_bin: str = "codex-tokens"
# Optional explicit `codex` CLI (for `codex delete`); see _codex_cli().
_codex_bin: str | None = None


def _get_plan_cached(bin_path: str) -> tuple[dict, int]:
    """Return (data, exit_code). `data` is either the upstream JSON or the
    Rust error envelope `{"error","code"}`. Cached for PLAN_CACHE_TTL_SEC.
    Exit code is forwarded so the UI can distinguish states.
    """
    global _plan_cache, _plan_cache_ts, _plan_cache_exit
    now = time.time()
    with _plan_cache_lock:
        if _plan_cache is not None and (now - _plan_cache_ts) < PLAN_CACHE_TTL_SEC:
            return _plan_cache, _plan_cache_exit
    # Spawn outside the lock so concurrent requests don't serialize the call.
    try:
        result = subprocess.run(
            [bin_path, "plan"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        stdout = result.stdout.strip()
        stderr = (result.stderr or "").strip()
        if not stdout:
            # The plan subcommand always emits JSON. Empty stdout means the
            # binary is too old (no `plan` subcommand yet) or crashed before
            # printing. Surface stderr so the UI can show the real cause.
            data = {
                "error": stderr[:240] or "binary returned no output",
                "code": "binary_outdated" if "unexpected argument" in stderr else "no_output",
            }
            exit_code = result.returncode or 4
        else:
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                data = {
                    "error": f"unparseable response: {stdout[:200]}",
                    "code": "bad_response",
                }
            exit_code = result.returncode
    except FileNotFoundError:
        data = {"error": f"binary not found: {bin_path}", "code": "binary_missing"}
        exit_code = 4
    except subprocess.TimeoutExpired:
        data = {"error": "plan fetch timed out", "code": "timeout"}
        exit_code = 4
    with _plan_cache_lock:
        _plan_cache = data
        _plan_cache_ts = time.time()
        _plan_cache_exit = exit_code
    return data, exit_code


def _tilde(path: str | None) -> str | None:
    """Home-Verzeichnis im Pfad durch `~` ersetzen (Anzeige-Kurzform)."""
    if not path:
        return path
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


def _rollout_path_for(sid: str) -> str | None:
    """Absolute rollout path of a session (cached; None if not found)."""
    cached = _path_cache.get(sid)
    if cached is None:
        path = _find_rollout_for_sid(sid)
        if path is not None:
            cached = os.path.realpath(path)
            _path_cache[sid] = cached
    return cached


def _enrich_snapshot(snap: dict) -> dict:
    """Add rollout_path (absolute), the ~-shortened cwd, the file mtime and
    the cached workspace name (resolved by the sweeper) to the snapshot."""
    sid = snap.get("session_id")
    if not sid:
        return snap
    short = _tilde(snap.get("session_cwd"))
    if short and short != snap.get("session_cwd"):
        snap["session_cwd_short"] = short
    workspace = _workspace_by_sid.get(sid)
    if workspace is not None:
        snap["workspace"] = workspace
    cached = _rollout_path_for(sid)
    if cached:
        snap["rollout_path"] = cached
        # Real file mtime = when this session was last written. The client
        # uses it to show the true data age and to flag stale cards, rather
        # than timing from when the snapshot happened to arrive.
        try:
            snap["last_modified"] = os.path.getmtime(cached)
        except OSError:
            pass
    return snap


def _broadcast(event: dict) -> None:
    with _subscribers_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


# --- codex-tokens reader thread -----------------------------------------

def _spawn_tokens(cwd_to_watch: str | None, codex_tokens_bin: str) -> subprocess.Popen:
    cmd = [codex_tokens_bin]
    if cwd_to_watch:
        cmd += ["--cwd", cwd_to_watch]
    cmd += [
        "--all",
        "--follow",
        "--watch-new",
        "--require-open",
        "--wait",
        "--max-age", "999999",
        "--json",
    ]
    sys.stderr.write(f"[server] spawn: {' '.join(shlex.quote(c) for c in cmd)}\n")
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def _reader_thread(cwd_to_watch: str | None, codex_tokens_bin: str) -> None:
    """Run codex-tokens, parse NDJSON, update state, broadcast diffs."""
    while True:
        try:
            proc = _spawn_tokens(cwd_to_watch, codex_tokens_bin)
        except FileNotFoundError:
            sys.stderr.write(
                f"[server] codex-tokens not found at {codex_tokens_bin}. "
                "Install it or pass --codex-tokens-bin.\n"
            )
            time.sleep(5)
            continue

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = snap.get("session_id")
            if not sid:
                continue
            snap = _enrich_snapshot(snap)
            with _state_lock:
                _sessions[sid] = snap
            _broadcast({"type": "snapshot", "data": snap})

        # Process exited — wait and restart.
        sys.stderr.write("[server] codex-tokens exited; restarting in 2 s\n")
        try:
            err = proc.stderr.read() if proc.stderr else ""
            if err:
                sys.stderr.write(f"[server] stderr: {err[:500]}\n")
        except Exception:
            pass
        time.sleep(2)


# --- Codex processes ------------------------------------------------------

# Heuristic for "this process IS a codex TUI". Matches the native codex
# binary inside the npm package (`…/codex-linux-x64/…/bin/codex`); the node
# wrapper (`node …/bin/codex.js`) is a different process and never matches.
# Standalone installs are recognized by the executable's basename instead.
_CODEX_NATIVE_RE = re.compile(r"/codex-linux-(?:x64|arm64)/.+/bin/codex(\s|$)")

# Subcommands that run the native binary WITHOUT an interactive session
# (`codex app-server` is the shared daemon and backs the VS Code extension,
# `codex exec` is headless, …). Such processes never are a "ghost TUI" and
# must not be killable via the 🛑 button. Interactive forms: bare
# `codex [PROMPT]`, `resume`, `fork`.
_CODEX_NON_TUI_SUBCOMMANDS = frozenset({
    "exec", "e", "review", "login", "logout", "mcp", "mcp-server", "plugin",
    "app-server", "remote-control", "completion", "update", "doctor",
    "sandbox", "debug", "apply", "a", "queue", "archive", "delete",
    "migrate-rollouts", "unarchive", "cloud", "exec-server", "features",
    "help", "agents", "proto", "responses-api-proxy", "stdio-to-uds",
})


def _read_cmdline(pid: int | str) -> list[str] | None:
    """argv of a process, None if unreadable (gone / no permission)."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [a.decode("utf-8", errors="replace") for a in raw.split(b"\x00") if a]


def _is_codex_tui(argv: list[str] | None, exe: str | None = None) -> bool:
    """True for the native codex binary running an interactive TUI."""
    if not argv:
        return False
    native = bool(_CODEX_NATIVE_RE.search(argv[0] + " ")) or (
        exe is not None and os.path.basename(exe) == "codex"
    )
    if not native:
        return False
    first_positional = next((a for a in argv[1:] if not a.startswith("-")), None)
    return first_positional not in _CODEX_NON_TUI_SUBCOMMANDS


def _pid_is_codex_tui(pid: int) -> bool:
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        exe = None
    return _is_codex_tui(_read_cmdline(pid), exe)


def _proc_started_at(pid: int | str) -> float:
    """Process start as epoch seconds: field 22 of /proc/<pid>/stat (clock
    ticks since boot) + btime from /proc/stat. 0.0 if unknown. (The mtime of
    /proc/<pid>/stat is NOT the start time — procfs sets inode times when
    the entry is first looked up.)"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rfind(")") + 1:].split()  # comm may contain spaces
        ticks = int(fields[19])
        btime = next(
            int(line.split()[1])
            for line in Path("/proc/stat").read_text().splitlines()
            if line.startswith("btime ")
        )
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return 0.0


def _proc_window_id(pid: int | str) -> int | None:
    """X11 window ID of the terminal hosting a process. Terminals (xterm/VTE,
    also xfce4-terminal) export WINDOWID to their shells and codex inherits
    it — /proc/<pid>/environ names the exact window, no title heuristics
    needed. None when the terminal doesn't set WINDOWID (e.g. IDE-integrated
    terminals)."""
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    for entry in environ.split(b"\0"):
        if entry.startswith(b"WINDOWID="):
            try:
                return int(entry[len(b"WINDOWID="):])
            except ValueError:
                return None
    return None


def _fd_is_writable(pid: str, fd: str) -> bool:
    """True if the fd was opened for writing (fdinfo flags & O_WRONLY|O_RDWR).
    Read-only handles are ignored on purpose — codex-tokens itself reads the
    rollout files and would otherwise count as a writer."""
    try:
        content = Path(f"/proc/{pid}/fdinfo/{fd}").read_text()
    except OSError:
        return False
    for line in content.splitlines():
        if line.startswith("flags:"):
            try:
                return bool(int(line.split(":", 1)[1].strip(), 8) & 0o3)
            except ValueError:
                return False
    return False


class _CodexProcs:
    """One /proc walk: the interactive Codex TUIs and, per rollout file, the
    process holding it with a write FD (the TUI itself, or — since Codex
    0.160 — the shared app-server daemon)."""

    def __init__(self) -> None:
        # pid → {pid, cwd, window, started_at, argv}
        self.tuis: dict[int, dict] = {}
        # realpath of rollout-*.jsonl → pid holding it for writing
        self.holders: dict[str, int] = {}
        try:
            pids = [p for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return
        for pid in pids:
            try:
                exe = os.readlink(f"/proc/{pid}/exe")
            except OSError:
                exe = None
            argv = _read_cmdline(pid)
            if _is_codex_tui(argv, exe):
                try:
                    cwd = os.readlink(f"/proc/{pid}/cwd")
                except OSError:
                    cwd = ""
                self.tuis[int(pid)] = {
                    "pid": int(pid),
                    "cwd": cwd,
                    "window": _proc_window_id(pid),
                    "started_at": _proc_started_at(pid),
                    "argv": argv or [],
                }
            try:
                fds = os.listdir(f"/proc/{pid}/fd")
            except OSError:
                continue
            for fd in fds:
                try:
                    target = os.readlink(f"/proc/{pid}/fd/{fd}")
                except OSError:
                    continue
                name = os.path.basename(target)
                if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                    continue
                if _fd_is_writable(pid, fd):
                    self.holders[target] = int(pid)

    def holder(self, rollout_path: str | None) -> int | None:
        if not rollout_path:
            return None
        return self.holders.get(os.path.realpath(rollout_path))


def _is_below(cwd: str, root: str) -> bool:
    """True if cwd equals root or lies inside its subtree."""
    return cwd == root or cwd.startswith(root.rstrip("/") + "/")


# How a session was mapped to its TUI (see _assign_tuis). Only these are
# trusted for typing into a terminal or killing a process.
_EXACT_HOW = ("holder", "title", "argv")


def _title_has_name(title: str | None, name: str | None) -> bool:
    """Does a terminal title mention the thread name? Long names may be
    clipped in the title, so a solid prefix counts as well."""
    if not title or not name:
        return False
    return name in title or (len(name) >= 24 and name[:24] in title)


def _assign_tuis(
    procs: _CodexProcs,
    live: list[tuple[str, str | None, str | None]],
    names: dict[str, str],
    titles: dict[int, str] | None,
) -> dict[str, tuple[int, str]]:
    """Map each live session (sid, cwd, rollout path) to the TUI process it
    runs in: sid → (pid, how). Each TUI is claimed at most once; unclaimed
    TUIs are ghosts. Order of trust: holder > title > argv > cwd."""
    assigned: dict[str, tuple[int, str]] = {}
    claimed: set[int] = set()

    def claim(sid: str, pid: int, how: str) -> None:
        assigned[sid] = (pid, how)
        claimed.add(pid)

    # 1. The TUI itself writes the rollout (Codex without the daemon).
    for sid, _cwd, path in live:
        pid = procs.holder(path)
        if pid in procs.tuis:
            claim(sid, pid, "holder")
    # 2. The thread name is in the TUI's terminal title. Thread names are
    #    not unique (two sessions started with the same prompt get the same
    #    name), so this only counts when exactly one TUI matches and no other
    #    live session in an overlapping directory carries the same name.
    if titles:
        for sid, cwd, _path in live:
            name = names.get(sid)
            if sid in assigned or not cwd or not name:
                continue
            twins = [
                o for o, ocwd, _p in live
                if o != sid and names.get(o) == name and ocwd
                and (_is_below(ocwd, cwd) or _is_below(cwd, ocwd))
            ]
            if twins:
                continue
            matches = [
                t for t in procs.tuis.values()
                if t["pid"] not in claimed and _is_below(t["cwd"], cwd)
                and _title_has_name(titles.get(t["window"]), name)
            ]
            if len(matches) == 1:
                claim(sid, matches[0]["pid"], "title")
    # 3. `codex resume <sid>` names the session on the command line.
    for sid, _cwd, _path in live:
        if sid in assigned:
            continue
        for tui in procs.tuis.values():
            if tui["pid"] not in claimed and sid in tui["argv"]:
                claim(sid, tui["pid"], "argv")
                break
    # 4. Heuristic: the oldest unclaimed TUI at/below the session cwd (a
    #    TUI still waiting for its first prompt is usually the younger one).
    for sid, cwd, _path in live:
        if sid in assigned or not cwd:
            continue
        candidates = sorted(
            (t for t in procs.tuis.values()
             if t["pid"] not in claimed and _is_below(t["cwd"], cwd)),
            key=lambda t: t["started_at"],
        )
        if candidates:
            claim(sid, candidates[0]["pid"], "cwd")
    return assigned


# --- thread names (Codex's own chat titles) -------------------------------

_thread_names_lock = threading.Lock()
_thread_names_cache: tuple[float, int, dict[str, str]] | None = None


def _thread_names() -> dict[str, str]:
    """Session id → thread name from ~/.codex/session_index.jsonl (Codex
    appends a line whenever it names or renames a thread; the last one
    wins). Cached by (mtime, size)."""
    global _thread_names_cache
    path = _codex_home() / "session_index.jsonl"
    try:
        st = path.stat()
    except OSError:
        return {}
    with _thread_names_lock:
        hit = _thread_names_cache
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
    names: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                sid, name = rec.get("id"), rec.get("thread_name")
                if isinstance(sid, str) and isinstance(name, str) and name.strip():
                    names[sid] = name.strip()
    except OSError:
        return {}
    with _thread_names_lock:
        _thread_names_cache = (st.st_mtime, st.st_size, names)
    return names


# --- active-status sweeper ------------------------------------------------

def _live_sessions(procs: _CodexProcs) -> list[tuple[str, str | None, str | None]]:
    """(sid, cwd, rollout path) of every tracked session whose rollout is
    currently held open for writing."""
    with _state_lock:
        items = list(_sessions.items())
    live = []
    for sid, snap in items:
        path = snap.get("rollout_path") or _rollout_path_for(sid)
        if procs.holder(path) is not None:
            live.append((sid, snap.get("session_cwd"), path))
    return live


def _window_titles(windows: list[tuple[str, str, str, str]] | None) -> dict[int, str] | None:
    if windows is None:
        return None
    return {_win_id_int(w[0]): w[3] for w in windows}


def _session_window(
    sid: str,
    cwd: str,
    windows: list[tuple[str, str, str, str]],
    procs: _CodexProcs,
    assigned: dict[str, tuple[int, str]],
    names: dict[str, str],
) -> tuple[str, str] | None:
    """(win_id, desktop) of a session's terminal/IDE window, or None."""
    exact: set[int] = set()
    hit = assigned.get(sid)
    if hit is not None:
        window = procs.tuis.get(hit[0], {}).get("window")
        if window is not None:
            exact.add(window)
    foreign = {
        procs.tuis[pid]["window"]
        for other, (pid, _how) in assigned.items()
        if other != sid and procs.tuis.get(pid, {}).get("window") is not None
    }
    return _find_session_window(
        windows, cwd, names.get(sid),
        exact_window_ids=exact, foreign_window_ids=foreign - exact,
    )


def _sweeper_thread() -> None:
    """codex-tokens emits a snapshot per TokenCount event. When a session
    closes, no further events come, so we never see session_active flip to
    false. This thread periodically re-checks every session via one /proc
    walk, refreshes file mtimes and workspace names, and recomputes the
    ghost-TUI list; changes are broadcast."""
    global _ghosts
    while True:
        time.sleep(3)
        with _state_lock:
            items = list(_sessions.items())
        procs = _CodexProcs()
        try:
            windows = _wmctrl_windows()
        except FileNotFoundError:
            windows = None
        desktops = _wmctrl_desktops() if windows else None
        names = _thread_names()

        live = []
        held: dict[str, bool] = {}
        for sid, snap in items:
            path = snap.get("rollout_path") or _rollout_path_for(sid)
            held[sid] = procs.holder(path) is not None
            if held[sid]:
                live.append((sid, snap.get("session_cwd"), path))
        assigned = _assign_tuis(procs, live, names, _window_titles(windows))

        for sid, snap in items:
            now_active = held[sid]
            new_mtime = snap.get("last_modified")
            path = snap.get("rollout_path")
            if path:
                try:
                    new_mtime = os.path.getmtime(path)
                except OSError:
                    pass
            # Ohne wmctrl (oder bei Fehlschlag) den letzten bekannten Wert
            # behalten, statt die Anzeige flackern zu lassen.
            workspace = snap.get("workspace")
            cwd = snap.get("session_cwd")
            if windows is not None and desktops is not None:
                workspace = None
                if cwd:
                    hit = _session_window(sid, cwd, windows, procs, assigned, names)
                    if hit is not None:
                        workspace = desktops.get(hit[1])
            _workspace_by_sid[sid] = workspace
            if (
                snap.get("session_active") != now_active
                or new_mtime != snap.get("last_modified")
                or snap.get("workspace") != workspace
            ):
                snap["session_active"] = now_active
                snap["last_modified"] = new_mtime
                if workspace is None:
                    snap.pop("workspace", None)
                else:
                    snap["workspace"] = workspace
                with _state_lock:
                    _sessions[sid] = snap
                _broadcast({"type": "snapshot", "data": snap})

        claimed = {pid for pid, _how in assigned.values()}
        ghosts = sorted(
            (
                {"pid": t["pid"], "cwd": t["cwd"], "started_at": t["started_at"]}
                for t in procs.tuis.values() if t["pid"] not in claimed
            ),
            key=lambda g: g["pid"],
        )
        with _ghosts_lock:
            changed = ghosts != _ghosts
            _ghosts = ghosts
        if changed:
            _broadcast({"type": "ghosts", "data": ghosts})


def _find_rollout_for_sid(sid: str) -> Path | None:
    needle = f"-{sid}.jsonl"
    sessions_dir = _codex_home() / "sessions"
    if not sessions_dir.is_dir():
        return None
    for path in sessions_dir.rglob(f"*{needle}"):
        return path
    return None


def _current_assignment() -> tuple[_CodexProcs, dict[str, tuple[int, str]], list | None]:
    """Fresh process snapshot + session→TUI mapping for request handlers
    that act on a terminal or process (never trust a 3-s-old sweep for
    that). Returns (procs, assignment, wmctrl windows or None)."""
    procs = _CodexProcs()
    try:
        windows = _wmctrl_windows()
    except FileNotFoundError:
        windows = None
    assigned = _assign_tuis(
        procs, _live_sessions(procs), _thread_names(), _window_titles(windows))
    return procs, assigned, windows


# --- recent chats -----------------------------------------------------------
#
# Data source: the rollout files under ~/.codex/sessions/YYYY/MM/DD/ the CLI
# tails, read-only (plus session_index.jsonl for Codex's own thread names).
# Rollouts can reach tens of MB, so we never load one fully: the cwd sits in
# the session_meta record on the first line, the first prompt within the
# first few hundred KB (head read), the latest prompt and the conversation
# tail at the end (tail read). Per-file metadata is cached by (mtime, size).

CHATS_HEAD_BYTES = 524288   # session_meta + developer instructions are large
CHATS_TAIL_BYTES = 262144
CHATS_META_TAIL_BYTES = 65536
CHAT_DETAIL_MAX_MSGS = 20
CHAT_DETAIL_SNIPPET_CHARS = 400
CHAT_TITLE_CHARS = 120
CHAT_LAST_PROMPT_CHARS = 160

_CWD_RE = re.compile(rb'"cwd":\s*"((?:[^"\\]|\\.)*)"')
# Injected context that arrives as "user" messages in response_item records
# (AGENTS.md, environment, aborted-turn markers, …) — not typed by the user.
_NON_PROMPT_RE = re.compile(r"^(<[a-z_ -]+>|# AGENTS\.md instructions)", re.IGNORECASE)

_chats_meta_lock = threading.Lock()
_chats_meta_cache: dict[str, tuple[float, int, dict]] = {}
_chats_cwd_cache: dict[str, tuple[float, int, str | None]] = {}


def _read_head_lines(path: Path, max_bytes: int) -> list[str]:
    """First complete lines of a file, reading at most max_bytes."""
    with path.open("rb") as f:
        chunk = f.read(max_bytes)
    complete = len(chunk) < max_bytes
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if not complete and lines:
        lines.pop()  # last line may be cut mid-record
    return lines


def _read_tail_lines(path: Path, max_bytes: int) -> tuple[list[str], bool]:
    """Last complete lines of a file. Returns (lines, truncated) where
    truncated means the read did not start at the beginning of the file."""
    size = path.stat().st_size
    offset = max(0, size - max_bytes)
    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read(max_bytes)
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if offset > 0 and lines:
        lines.pop(0)  # first line may be cut mid-record
    return lines, offset > 0


def _snippet(text: str, limit: int) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + " …", True


def _rollout_sid(path: Path) -> str | None:
    """Session id from a rollout file name (rollout-<ts>-<uuid>.jsonl)."""
    m = _SID_RE.search(path.name[-(36 + 6):])
    return m.group(0) if m else None


def _rollout_cwd(path: Path, st: os.stat_result) -> str | None:
    """cwd from the session_meta record — a regex over the first 8 KB is
    enough (the record is ~20 KB because of embedded instructions, but cwd
    comes early) and much cheaper than parsing it. Cached by (mtime, size)."""
    key = str(path)
    with _chats_meta_lock:
        hit = _chats_cwd_cache.get(key)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
    cwd = None
    try:
        with path.open("rb") as f:
            m = _CWD_RE.search(f.read(8192))
        if m:
            cwd = json.loads(b'"' + m.group(1) + b'"')
    except (OSError, ValueError):
        cwd = None
    with _chats_meta_lock:
        _chats_cwd_cache[key] = (st.st_mtime, st.st_size, cwd)
    return cwd


def _content_texts(content, kinds: tuple[str, ...]) -> list[str]:
    """Text parts of a message content array (types matched case-insensitively)."""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        part["text"] for part in content
        if isinstance(part, dict) and isinstance(part.get("text"), str)
        and str(part.get("type", "")).lower() in kinds
    ]


def _message_of(rec: dict) -> tuple[str, str, bool] | None:
    """(role, text, from_event) of a displayable message record, else None.

    Codex logs every turn twice: as `event_msg` records (user_message /
    agent_message in older versions, item_completed UserMessage /
    AgentMessage since 0.157) and as raw `response_item` messages, which
    additionally carry injected context. Event records are preferred;
    response items serve as fallback for files that have none."""
    rtype = rec.get("type")
    p = rec.get("payload")
    if not isinstance(p, dict):
        return None
    ptype = p.get("type")
    if rtype == "event_msg":
        if ptype == "user_message" and isinstance(p.get("message"), str):
            text = p["message"].strip()
            return ("user", text, True) if text else None
        if ptype == "agent_message" and isinstance(p.get("message"), str):
            text = p["message"].strip()
            return ("assistant", text, True) if text else None
        if ptype == "item_completed":
            item = p.get("item")
            if not isinstance(item, dict):
                return None
            if item.get("type") == "UserMessage":
                text = "\n".join(_content_texts(item.get("content"), ("text",))).strip()
                return ("user", text, True) if text else None
            if item.get("type") == "AgentMessage":
                text = "\n".join(_content_texts(item.get("content"), ("text",))).strip()
                return ("assistant", text, True) if text else None
        return None
    if rtype == "response_item" and ptype == "message":
        role = p.get("role")
        if role == "user":
            parts = [
                t for t in _content_texts(p.get("content"), ("input_text", "text"))
                if t.strip() and not _NON_PROMPT_RE.match(t.strip())
            ]
            text = "\n".join(parts).strip()
            return ("user", text, False) if text else None
        if role == "assistant":
            text = "\n".join(
                _content_texts(p.get("content"), ("output_text", "text"))).strip()
            return ("assistant", text, False) if text else None
    return None


def _messages_from_lines(lines: list[str]) -> list[tuple[str, str, str | None]]:
    """(role, text, timestamp) of all displayable messages in file order,
    deduplicated per the event-first rule of _message_of."""
    events: list[tuple[str, str, str | None]] = []
    items: list[tuple[str, str, str | None]] = []
    for line in lines:
        if '"message' not in line and '"item_completed"' not in line:
            continue  # cheap pre-filter: tool calls, token counts, …
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        msg = _message_of(rec)
        if msg is None:
            continue
        role, text, from_event = msg
        (events if from_event else items).append((role, text, rec.get("timestamp")))
    return events or items


def _extract_chat_meta(path: Path) -> dict:
    """cwd/first prompt/last prompt of one rollout, cached by (mtime, size)."""
    try:
        st = path.stat()
    except OSError:
        return {}
    key = str(path)
    with _chats_meta_lock:
        hit = _chats_meta_cache.get(key)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]

    meta: dict = {
        "cwd": _rollout_cwd(path, st), "first_prompt": None, "last_prompt": None,
    }
    try:
        head = _messages_from_lines(_read_head_lines(path, CHATS_HEAD_BYTES))
        first = next((text for role, text, _ts in head if role == "user"), None)
        if first:
            meta["first_prompt"], _ = _snippet(first, CHAT_TITLE_CHARS)
        tail_lines, _ = _read_tail_lines(path, CHATS_META_TAIL_BYTES)
        last = next(
            (text for role, text, _ts in reversed(_messages_from_lines(tail_lines))
             if role == "user"),
            None,
        )
        if last:
            meta["last_prompt"], _ = _snippet(last, CHAT_LAST_PROMPT_CHARS)
    except OSError:
        return {}

    with _chats_meta_lock:
        _chats_meta_cache[key] = (st.st_mtime, st.st_size, meta)
    return meta


def _list_recent_chats(
    per_dir: int, total: int,
    favorites: set[str] | None = None,
    names: dict[str, str] | None = None,
) -> list[dict]:
    """The newest chats across all projects: per_dir newest per directory
    first, then the total newest of those overall (both limits combined).
    Favorites (★) are always listed first, no matter how old — they neither
    count against nor compete for the per_dir/total slots."""
    sessions_dir = _codex_home() / "sessions"
    if not sessions_dir.is_dir():
        return []
    favorites = favorites or set()
    names = names or {}
    thread_names = _thread_names()

    fav_candidates: list[tuple[float, int, Path, str | None]] = []
    by_dir: dict[str, list[tuple[float, int, Path, str | None]]] = {}
    for path in sessions_dir.rglob("rollout-*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_size == 0:
            continue
        sid = _rollout_sid(path)
        cwd = _rollout_cwd(path, st)
        entry = (st.st_mtime, st.st_size, path, cwd)
        if sid in favorites:
            fav_candidates.append(entry)
        else:
            by_dir.setdefault(cwd or "", []).append(entry)

    candidates: list[tuple[float, int, Path, str | None]] = []
    for files in by_dir.values():
        files.sort(key=lambda t: t[0], reverse=True)
        # A few spares per directory: empty warm-up sessions are skipped
        # below and must not cost the directory its slots.
        candidates.extend(files[:per_dir + 3])
    fav_candidates.sort(key=lambda t: t[0], reverse=True)
    candidates.sort(key=lambda t: t[0], reverse=True)

    with _state_lock:
        active_sids = {
            sid for sid, snap in _sessions.items() if snap.get("session_active")
        }

    now = time.time()
    chats: list[dict] = []
    per_dir_count: dict[str, int] = {}

    def _append(mtime: float, size: int, path: Path, cwd: str | None, favorite: bool) -> bool:
        sid = _rollout_sid(path)
        if not sid:
            return False
        meta = _extract_chat_meta(path)
        if not meta or (not meta.get("first_prompt") and not meta.get("last_prompt")):
            return False  # no prompt ever sent — let older ones move up
        title = thread_names.get(sid)
        title_source = "thread-name" if title else None
        if not title and meta.get("first_prompt"):
            title, title_source = meta["first_prompt"], "first_prompt"
        chats.append({
            "session_id": sid,
            "cwd": cwd,
            "dir_name": Path(cwd).name if cwd else "(unbekannt)",
            "mtime": mtime,
            "age_seconds": max(0, int(now - mtime)),
            "title": title,
            "title_source": title_source,
            "last_prompt": meta.get("last_prompt"),
            "size_bytes": size,
            "active": sid in active_sids,
            "favorite": favorite,
            "custom_name": names.get(sid),
        })
        return True

    for mtime, size, path, cwd in fav_candidates:
        _append(mtime, size, path, cwd, favorite=True)
    listed = 0
    for mtime, size, path, cwd in candidates:
        if listed >= total:
            break
        if per_dir_count.get(cwd or "", 0) >= per_dir:
            continue
        if _append(mtime, size, path, cwd, favorite=False):
            listed += 1
            per_dir_count[cwd or ""] = per_dir_count.get(cwd or "", 0) + 1
    return chats


def _extract_chat_detail(path: Path) -> dict:
    """Condensed conversation tail: the last CHAT_DETAIL_MAX_MSGS displayable
    user/assistant messages, each truncated to CHAT_DETAIL_SNIPPET_CHARS."""
    lines, partial = _read_tail_lines(path, CHATS_TAIL_BYTES)
    messages = []
    for role, text, ts in _messages_from_lines(lines)[-CHAT_DETAIL_MAX_MSGS:]:
        snippet, truncated = _snippet(text, CHAT_DETAIL_SNIPPET_CHARS)
        messages.append({"role": role, "ts": ts, "text": snippet, "truncated": truncated})
    return {"messages": messages, "partial": partial}


def _codex_cli() -> list[str] | None:
    """argv prefix to run the `codex` CLI, or None if it cannot be found.
    npm/nvm installs put a node script (`codex.js`) on the PATH that needs
    `node` — which a desktop session's PATH often lacks, so the node next to
    the script's PATH entry (nvm keeps both in one bin dir) is used."""
    cand = _codex_bin or shutil.which("codex")
    if not cand:
        return None
    real = os.path.realpath(cand)
    if real.endswith(".js"):
        node = shutil.which("node")
        sibling = os.path.join(os.path.dirname(os.path.abspath(cand)), "node")
        if os.access(sibling, os.X_OK):
            node = sibling
        return [node, real] if node else None
    return [cand]


# --- window management (wmctrl) --------------------------------------------
#
# Both the ⚡ (focus terminal) and 📁 (open directory) actions target the
# workspace a project lives on: `wmctrl -ia` activates a window AND switches
# to its desktop, so finding the right window is the whole job.

_IDE_CLASSES = (
    "code", "cursor", "windsurf", "vscodium", "code-oss",
    "jetbrains", "intellij", "pycharm", "webstorm", "phpstorm",
    "goland", "rustrover", "rider", "datagrip", "android-studio",
    "sublime_text", "atom", "zed",
)
_TERM_CLASSES = (
    "terminal", "alacritty", "kitty", "konsole", "xterm",
    "wezterm", "tilix", "guake", "tabby", "rxvt", "urxvt",
    "gnome-terminal", "xfce4-terminal", "qterminal", "foot",
)
_FILE_MANAGER_CLASSES = (
    "nautilus", "dolphin", "thunar", "nemo", "pcmanfm",
    "caja", "spacefm", "krusader",
)


def _wmctrl_windows() -> list[tuple[str, str, str, str]] | None:
    """Parsed `wmctrl -lx` lines as (win_id, desktop, wm_class, title).
    Raises FileNotFoundError if wmctrl is missing; returns None on failure."""
    result = subprocess.run(
        ["wmctrl", "-lx"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    windows = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        win_id, desktop, wm_class, _host, title = parts
        windows.append((win_id, desktop, wm_class, title))
    return windows


def _wmctrl_desktops() -> dict[str, str] | None:
    """Desktop number → workspace name from `wmctrl -d`, None on failure.
    Line format: `N  * DG: WxH  VP: x,y  WA: x,y WxH  NAME` — the name is
    everything after the 9th whitespace-separated field."""
    try:
        proc = subprocess.run(
            ["wmctrl", "-d"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    desktops: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        parts = line.split(None, 9)
        if len(parts) == 10:
            desktops[parts[0]] = parts[9]
    return desktops or None


def _activate_window(win_id: str) -> None:
    """Raise + focus a window; wmctrl also switches to its desktop."""
    subprocess.run(
        ["wmctrl", "-ia", win_id],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )


def _win_id_int(win_id: str) -> int:
    """wmctrl window id ("0x02e00041") as int, -1 if unparseable — WINDOWID
    in a process environment is decimal, wmctrl prints hex."""
    try:
        return int(win_id, 16)
    except ValueError:
        return -1


def _find_session_window(
    windows: list[tuple[str, str, str, str]], cwd: str, thread_name: str | None,
    exact_window_ids: set[int] | None = None,
    foreign_window_ids: set[int] | None = None,
) -> tuple[str, str] | None:
    """Best (win_id, desktop) for a session's IDE/terminal window, or None.

    Exact match first: `exact_window_ids` holds the WINDOWID of the TUI the
    session was mapped to (_assign_tuis). If it is in the window list, that
    IS the session's window; titles never enter into it. Everything below is
    the heuristic fallback for when WINDOWID is unavailable (finished
    session, IDE-integrated terminal); it skips `foreign_window_ids` —
    terminals known to host OTHER sessions.

    Only terminal/IDE-class windows are considered: activating a window also
    switches to ITS workspace, so a weak match on e.g. a browser tab that
    happens to mention the directory name would jump to the wrong workspace.

    Match strength: thread name (Codex titles its terminal "<thread name> |
    <project>", so it identifies THE session's window) > full cwd — both
    absolute and ~-abbreviated, shells title themselves with `~/…` — > bare
    directory basename. Ties are broken by workspace affinity: project
    windows cluster per workspace, so the candidate wins whose desktop also
    holds other windows (file manager, idle terminals, any class) that
    mention the session's cwd in their title."""
    if exact_window_ids:
        exact = [w for w in windows if _win_id_int(w[0]) in exact_window_ids]
        if exact:
            return exact[0][0], exact[0][1]
    home = str(Path.home())
    cwd_tilde = "~" + cwd[len(home):] if cwd.startswith(home + "/") else None
    cwd_basename = Path(cwd).name
    candidates: list[tuple[int, str, str]] = []
    for win_id, desktop, wm_class, title in windows:
        cls_lower = wm_class.lower()
        is_ide = any(ide in cls_lower for ide in _IDE_CLASSES)
        is_term = any(t in cls_lower for t in _TERM_CLASSES)
        if not is_ide and not is_term:
            continue
        if foreign_window_ids and _win_id_int(win_id) in foreign_window_ids:
            continue
        score = 10 if is_ide else 5
        if _title_has_name(title, thread_name):
            score += 20
        elif cwd in title or (cwd_tilde and cwd_tilde in title):
            score += 10
        elif cwd_basename and cwd_basename in title:
            score += 0
        else:
            continue
        candidates.append((score, win_id, desktop))
    if not candidates:
        return None
    best = max(score for score, _, _ in candidates)
    top = [c for c in candidates if c[0] == best]
    if len(top) > 1:
        def workspace_affinity(candidate: tuple[int, str, str]) -> int:
            _, cand_id, cand_desktop = candidate
            return sum(
                1 for w_id, w_desktop, _cls, w_title in windows
                if w_id != cand_id and w_desktop == cand_desktop
                and (cwd in w_title
                     or (cwd_tilde and cwd_tilde in w_title)
                     or cwd_basename in w_title)
            )
        # Stable sort: bei gleicher Affinität bleibt die wmctrl-Reihenfolge.
        top.sort(key=workspace_affinity, reverse=True)
    _, win_id, desktop = top[0]
    return win_id, desktop


def _find_file_manager_window(
    windows: list[tuple[str, str, str, str]], path: str
) -> str | None:
    """A file-manager window already showing `path` (title = the shown
    directory, possibly via a differently-prefixed route, e.g. a symlink)."""
    basename = Path(path).name
    for win_id, _desktop, wm_class, title in windows:
        if not any(fm in wm_class.lower() for fm in _FILE_MANAGER_CLASSES):
            continue
        if path in title or title.rstrip("/").endswith("/" + basename):
            return win_id
    return None


# --- handover injection (xdotool) -------------------------------------------
#
# The ↻ button used to only copy the rollover prompt; now the server can
# deliver it straight into the session's terminal: clipboard → focus the
# exact window → paste keystroke → Return. Injection is deliberately limited
# to a reliable session→TUI mapping (_EXACT_HOW) whose terminal exports
# WINDOWID: typing into a guessed window would be far worse than falling
# back to a plain copy. Requires xdotool; without it the prompt is only
# copied, as before.

HANDOVER_FILENAME = "HANDOVER.md"


def _xdotool(*args: str) -> bool:
    """Run xdotool; False on failure. Raises FileNotFoundError if missing."""
    result = subprocess.run(
        ["xdotool", *args], capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def _active_window_id() -> int:
    """X11 id of the currently focused window, -1 if unknown."""
    result = subprocess.run(
        ["xdotool", "getactivewindow"], capture_output=True, text=True, check=False
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return -1


def _set_selections(text: str) -> bool:
    """Put text into CLIPBOARD and PRIMARY. Both matter: VTE terminals paste
    CLIPBOARD via Ctrl+Shift+V, xterm pastes PRIMARY via Shift+Insert."""
    ok = False
    for sel in ("clipboard", "primary"):
        try:
            p = subprocess.Popen(["xclip", "-selection", sel], stdin=subprocess.PIPE)
            p.communicate(input=text.encode())
            ok = ok or (p.returncode == 0 and sel == "clipboard")
        except FileNotFoundError:
            return False
    return ok


def _clipboard_text() -> str | None:
    """Current CLIPBOARD content, None if unreadable."""
    try:
        result = subprocess.run(
            ["xclip", "-o", "-selection", "clipboard"],
            capture_output=True, timeout=2, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def _exact_session_window(sid: str) -> tuple[tuple[str, str, int] | None, str | None]:
    """((win_id, wm_class, tui pid), None) of the terminal hosting this
    session's TUI, or (None, reason). Only reliable mappings (_EXACT_HOW)
    with a WINDOWID that is in the window list qualify — this window gets
    typed into."""
    try:
        windows = _wmctrl_windows()
    except FileNotFoundError:
        return None, "wmctrl nicht installiert"
    if not windows:
        return None, "Fensterliste nicht lesbar"
    procs = _CodexProcs()
    assigned = _assign_tuis(
        procs, _live_sessions(procs), _thread_names(), _window_titles(windows))
    hit = assigned.get(sid)
    if hit is None:
        return None, "kein Codex-Terminal zu dieser Session gefunden"
    pid, how = hit
    if how not in _EXACT_HOW:
        return None, "Terminal der Session nicht eindeutig erkennbar"
    window = procs.tuis[pid]["window"]
    if window is None:
        return None, "Terminal ohne WINDOWID (z. B. IDE-Terminal)"
    exact = [w for w in windows if _win_id_int(w[0]) == window]
    if len(exact) != 1:
        return None, "Terminal-Fenster nicht gefunden"
    win_id, _desktop, wm_class, _title = exact[0]
    return (win_id, wm_class, pid), None


# Key that submits Codex's composer. Tested against Codex 0.160 in
# xfce4-terminal: a plain Return right after injected input was repeatedly
# ignored (Codex' paste-burst handling swallows a bare CR that follows fast
# input), while the keypad Enter — sent as an escape sequence, not as a raw
# CR byte — submitted reliably, also for a slash command.
SUBMIT_KEY = "KP_Enter"


def _focus_window(win_id: str) -> bool:
    """Activate a window and wait until it really has the keyboard focus."""
    _activate_window(win_id)
    target = _win_id_int(win_id)
    for _ in range(20):  # wmctrl -ia is async; give the WM up to 1 s
        time.sleep(0.05)
        if _active_window_id() == target:
            return True
    return False


def _inject_input(win_id: str, wm_class: str, mode: str, text: str) -> str | None:
    """Deliver input into a terminal window and submit it.
    mode "paste": text is already in the selections, send the paste keystroke
    (bracketed paste keeps multi-line prompts from submitting early).
    mode "command": type a slash command (e.g. "/exit"), close Codex's
    command popup with Escape, then submit.
    Returns an error string, or None on success. Keystrokes go through XTEST
    (focused window), so we verify the focus actually arrived first — if the
    user switched windows meanwhile, we abort rather than type elsewhere."""
    if not _focus_window(win_id):
        return "window did not take focus"
    if mode == "paste":
        # The clipboard is shared with everything else on the desktop: a copy
        # (or a terminal's copy-on-select) between setting it and pasting
        # would inject foreign text. Re-check right before the keystroke —
        # this happened in testing — and refuse rather than paste the wrong
        # thing.
        for _ in range(2):
            if _clipboard_text() == text:
                break
            _set_selections(text)
        else:
            return "Zwischenablage wurde gerade anderweitig belegt"
        # VTE & friends bind Ctrl+Shift+V; xterm has no clipboard binding and
        # pastes PRIMARY via Shift+Insert instead.
        keys = "shift+Insert" if "xterm" in wm_class.lower() else "ctrl+shift+v"
        if not _xdotool("key", "--clearmodifiers", keys):
            return "paste keystroke failed"
        time.sleep(1.2)  # let the TUI ingest the paste before submitting
    else:
        if not _xdotool("type", "--clearmodifiers", "--delay", "40", text):
            return "typing failed"
        time.sleep(0.6)
        # The slash popup is open now; Escape closes it and keeps the text.
        if not _xdotool("key", "--clearmodifiers", "Escape"):
            return "escape keystroke failed"
        time.sleep(0.3)
    if _active_window_id() != _win_id_int(win_id):
        return "focus lost before submitting"
    if not _xdotool("key", "--clearmodifiers", SUBMIT_KEY):
        return "submit keystroke failed"
    return None


def _user_text_since(rollout: str, offset: int) -> str | None:
    """Last user message written to the rollout after byte `offset`."""
    try:
        with open(rollout, "rb") as f:
            f.seek(offset)
            chunk = f.read(CHATS_TAIL_BYTES)
    except OSError:
        return None
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    users = [t for role, t, _ts in _messages_from_lines(lines) if role == "user"]
    return users[-1] if users else None


def _confirm_submitted(
    win_id: str, rollout: str | None, size_before: int, text: str
) -> bool | None:
    """After paste+submit, make sure the prompt was really sent: a new turn
    appends to the rollout within moments. If nothing was written, submit
    once more with a plain Return — only while our window still has the
    focus. Submitting Codex's empty composer is a no-op, so the retry cannot
    do harm when the first one did land after all.

    Returns whether the user message that arrived matches `text` (False =
    something else was submitted — warn!), None if it could not be told
    (no turn recorded yet, e.g. queued behind a running turn)."""
    if not rollout:
        return None

    def grown() -> bool:
        try:
            return os.path.getsize(rollout) > size_before
        except OSError:
            return False

    def wait(pred, seconds: float):
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(0.25)
            value = pred()
            if value:
                return value
        return None

    if not wait(grown, 3.0) and _active_window_id() == _win_id_int(win_id):
        sys.stderr.write("[server] handover: no turn started, submitting again\n")
        _xdotool("key", "--clearmodifiers", "Return")
        wait(grown, 3.0)
    arrived = wait(lambda: _user_text_since(rollout, size_before), 4.0)
    if arrived is None:
        return None
    norm = lambda t: " ".join(t.split())  # noqa: E731 — whitespace-insensitive
    return norm(arrived) == norm(text)


# All monitor windows carry this title prefix (index.html, help.html,
# chats.html). The /sticky endpoint only touches windows matching it, so it
# cannot be used to modify arbitrary windows.
STICKY_TITLE_PREFIX = "Codex Token Monitor"


def _make_windows_sticky(title: str) -> int:
    """Pin every monitor window whose title contains `title` to all
    workspaces. Retries briefly — right after window.open the X window may
    not be mapped yet. Returns the number of windows pinned."""
    for _ in range(6):
        windows = _wmctrl_windows()
        if windows is None:
            return 0
        stuck = 0
        for win_id, _desktop, _cls, wtitle in windows:
            if title in wtitle:
                subprocess.run(
                    ["wmctrl", "-i", "-r", win_id, "-b", "add,sticky"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
                stuck += 1
        if stuck:
            return stuck
        time.sleep(0.3)
    return 0


# --- HTTP handler ---------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "codex-token-display/0.2"

    def log_message(self, fmt: str, *args) -> None:
        # Quieter logs.
        sys.stderr.write(f"[http] {self.address_string()} - {fmt % args}\n")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            self._serve_file(HERE / "index.html", "text/html; charset=utf-8")
        elif url.path.startswith("/static/"):
            fname = url.path[len("/static/"):]
            self._serve_static(fname)
        elif url.path == "/events":
            self._serve_events()
        elif url.path == "/scope":
            self._json_ok(extra={"scope": _scope, "scope_short": _tilde(_scope)})
        elif url.path == "/settings":
            self._serve_settings()
        elif url.path == "/plan":
            self._serve_plan()
        elif url.path == "/readme":
            self._serve_file(HERE / "README.md", "text/markdown; charset=utf-8")
        elif url.path == "/readme-main":
            # Top-level project README (one directory up).
            self._serve_file(
                HERE.parent / "README.md", "text/markdown; charset=utf-8"
            )
        elif url.path == "/help":
            # Single help page; ?doc=main switches to the project README.
            self._serve_file(HERE / "help.html", "text/html; charset=utf-8")
        elif url.path == "/chats":
            self._serve_file(HERE / "chats.html", "text/html; charset=utf-8")
        elif url.path == "/chats-data":
            self._serve_chats_data(url)
        elif url.path == "/chat-detail":
            self._serve_chat_detail(url)
        elif url.path == "/handover-status":
            self._serve_handover_status(url)
        elif url.path == "/icon.png" or url.path == "/favicon.ico":
            # Xubuntu/XFCE picks the favicon up for the window-list entry.
            # The browser also auto-requests /favicon.ico; route both to the
            # same PNG so neither path 404s.
            self._serve_file(HERE / "icon.png", "image/png")
        else:
            self.send_error(404, "not found")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self.send_error(400, "bad json")
            return
        if not isinstance(body, dict):
            self.send_error(400, "bad json")
            return

        if self.path == "/open-readme-dir":
            # Open the directory that contains help.html / README.md
            self._handle_open_dir({"path": str(HERE)})
        elif self.path == "/open-dir":
            self._handle_open_dir(body)
        elif self.path == "/focus-terminal":
            self._handle_focus_terminal(body)
        elif self.path == "/copy":
            self._handle_copy(body)
        elif self.path == "/settings":
            self._handle_update_settings(body)
        elif self.path == "/kill":
            self._handle_kill(body)
        elif self.path == "/sticky":
            self._handle_sticky(body)
        elif self.path == "/chat-delete":
            self._handle_chat_delete(body)
        elif self.path == "/chat-favorite":
            self._handle_chat_favorite(body)
        elif self.path == "/chat-rename":
            self._handle_chat_rename(body)
        elif self.path == "/handover":
            self._handle_handover(body)
        elif self.path == "/session-exit":
            self._handle_session_exit(body)
        else:
            self.send_error(404, "not found")

    # --- helpers ---

    def _serve_file(self, path: Path, ctype: str) -> None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, fname: str) -> None:
        static_root = HERE / "static"
        safe = static_root / fname
        if not safe.resolve().is_relative_to(static_root):
            self.send_error(403)
            return
        ctype = "application/octet-stream"
        if fname.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif fname.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif fname.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        elif fname.endswith(".md"):
            ctype = "text/markdown; charset=utf-8"
        self._serve_file(safe, ctype)

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q: queue.Queue = queue.Queue(maxsize=256)
        with _subscribers_lock:
            _subscribers.append(q)

        try:
            # Initial dump of all known sessions.
            with _state_lock:
                snapshots = list(_sessions.values())
            for snap in snapshots:
                self._sse_send({"type": "snapshot", "data": snap})
            # Initial ghost list — the sweeper only broadcasts changes.
            with _ghosts_lock:
                self._sse_send({"type": "ghosts", "data": list(_ghosts)})

            last_heartbeat = time.time()
            while True:
                try:
                    event = q.get(timeout=SSE_HEARTBEAT_SEC)
                    self._sse_send(event)
                except queue.Empty:
                    pass
                # Heartbeat comment to keep proxies happy.
                if time.time() - last_heartbeat > SSE_HEARTBEAT_SEC:
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    last_heartbeat = time.time()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _subscribers_lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    def _sse_send(self, event: dict) -> None:
        payload = json.dumps(event)
        self.wfile.write(f"data: {payload}\n\n".encode())
        self.wfile.flush()

    def _handle_open_dir(self, body: dict) -> None:
        path = body.get("path", "")
        if not isinstance(path, str) or not path or not Path(path).is_dir():
            self.send_error(400, "missing or invalid 'path'")
            return

        # Land on the project's workspace instead of dropping the file manager
        # onto whatever desktop happens to be active: focus an existing file-
        # manager window for the path if there is one, otherwise switch to the
        # desktop of the session's terminal before opening a new one. Without
        # wmctrl this gracefully degrades to a plain xdg-open.
        windows = None
        try:
            windows = _wmctrl_windows()
        except FileNotFoundError:
            pass
        if windows:
            existing = _find_file_manager_window(windows, path)
            if existing:
                _activate_window(existing)
                self._json_ok(extra={"focused": True})
                return
            hit = self._target_window(body, path, windows)
            if hit and hit[1].isdigit():
                subprocess.run(
                    ["wmctrl", "-s", hit[1]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )

        try:
            subprocess.Popen(
                ["xdg-open", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.send_error(500, "xdg-open not installed")
            return
        self._json_ok()

    def _target_window(
        self, body: dict, cwd: str, windows: list[tuple[str, str, str, str]]
    ) -> tuple[str, str] | None:
        """Window of the session (body.sid) or ghost TUI (body.pid) a card
        action refers to — exact via WINDOWID where possible, else the
        title heuristics of _find_session_window."""
        procs = _CodexProcs()
        pid = body.get("pid")
        if isinstance(pid, int) and pid in procs.tuis:
            window = procs.tuis[pid]["window"]
            exact = {window} if window is not None else set()
            return _find_session_window(windows, cwd, None, exact_window_ids=exact)
        sid = body.get("sid")
        names = _thread_names()
        assigned = _assign_tuis(
            procs, _live_sessions(procs), names, _window_titles(windows))
        if isinstance(sid, str) and _SID_RE.fullmatch(sid):
            return _session_window(sid, cwd, windows, procs, assigned, names)
        return _find_session_window(windows, cwd, None)

    def _handle_focus_terminal(self, body: dict) -> None:
        cwd = body.get("cwd", "")
        if not isinstance(cwd, str) or not cwd:
            self.send_error(400, "missing 'cwd'")
            return
        try:
            windows = _wmctrl_windows()
        except FileNotFoundError:
            self.send_error(500, "wmctrl not installed")
            return
        if windows is None:
            self._json_ok(extra={"warning": "wmctrl -lx failed"})
            return
        hit = self._target_window(body, cwd, windows)
        if hit is None:
            self._json_ok(extra={"warning": "no matching IDE/terminal window found"})
            return
        _activate_window(hit[0])
        self._json_ok()

    def _handle_kill(self, body: dict) -> None:
        """Send SIGTERM to a codex TUI process. Body shape:
            { "session_id": "<uuid>" }   → the TUI the session runs in
            { "pid": <int> }             → use the PID directly (ghost TUIs)
        SIGTERM gives codex a chance to shut down cleanly — same effect as
        closing the terminal window the TUI is running in. Since Codex 0.160
        the rollout is written by the shared app-server daemon, which keeps
        running; only the TUI is ended.
        """
        pid = body.get("pid")
        sid = body.get("session_id")

        if not pid and sid:
            if not isinstance(sid, str) or not _SID_RE.fullmatch(sid):
                self.send_error(400, "invalid 'session_id'")
                return
            _procs, assigned, _windows = _current_assignment()
            hit = assigned.get(sid)
            if hit is None:
                self.send_error(404, "no codex TUI found for that session")
                return
            if hit[1] not in _EXACT_HOW:
                # HTTP status-line must be ASCII/latin-1.
                self.send_error(409, "TUI of that session is ambiguous")
                return
            pid = hit[0]

        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            self.send_error(400, "invalid pid")
            return

        # Sanity check: only kill interactive codex TUIs. Without this, a
        # malicious POST could steer the server into killing arbitrary user
        # processes (or the shared app-server daemon).
        if _read_cmdline(pid_int) is None:
            self.send_error(404, f"pid {pid_int} not found")
            return
        if not _pid_is_codex_tui(pid_int):
            self.send_error(403, f"pid {pid_int} is not a codex TUI process")
            return

        try:
            os.kill(pid_int, signal.SIGTERM)
        except ProcessLookupError:
            self.send_error(404, f"pid {pid_int} already gone")
            return
        except PermissionError:
            self.send_error(403, f"no permission to signal pid {pid_int}")
            return
        sys.stderr.write(f"[server] sent SIGTERM to pid {pid_int}\n")
        self._json_ok(extra={"pid": pid_int})

    def _handle_copy(self, body: dict) -> None:
        text = body.get("text", "")
        if not isinstance(text, str) or not text:
            self.send_error(400, "missing 'text'")
            return
        # Try xclip then wl-copy (Wayland).
        for cmd in (["xclip", "-selection", "clipboard"], ["wl-copy"]):
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                p.communicate(input=text.encode())
                if p.returncode == 0:
                    self._json_ok()
                    return
            except FileNotFoundError:
                continue
        self.send_error(500, "no clipboard tool found (install xclip or wl-clipboard)")

    def _serve_settings(self) -> None:
        self._send_json(_load_settings())

    def _handle_update_settings(self, body: dict) -> None:
        # We accept partial updates so the UI can flip one toggle at a time
        # without round-tripping the entire blob.
        current = _load_settings()
        pw_in = (body.get("plan_widget") or {})
        if isinstance(pw_in, dict):
            if isinstance(pw_in.get("enabled"), bool):
                current["plan_widget"]["enabled"] = pw_in["enabled"]
            if isinstance(pw_in.get("consent_acknowledged"), bool):
                current["plan_widget"]["consent_acknowledged"] = pw_in["consent_acknowledged"]
            rows_in = pw_in.get("rows") or {}
            if isinstance(rows_in, dict):
                for k, v in rows_in.items():
                    if isinstance(v, bool):
                        current["plan_widget"]["rows"][k] = v
        ch_in = body.get("chats") or {}
        if isinstance(ch_in, dict):
            if "per_dir" in ch_in:
                current["chats"]["per_dir"] = _clamp_int(
                    ch_in["per_dir"], 1, CHATS_PER_DIR_MAX, current["chats"]["per_dir"])
            if "total" in ch_in:
                current["chats"]["total"] = _clamp_int(
                    ch_in["total"], 1, CHATS_TOTAL_MAX, current["chats"]["total"])
            if isinstance(ch_in.get("active_first"), bool):
                current["chats"]["active_first"] = ch_in["active_first"]
            if isinstance(ch_in.get("only_favorites"), bool):
                current["chats"]["only_favorites"] = ch_in["only_favorites"]
        win_in = body.get("windows") or {}
        if isinstance(win_in, dict) and isinstance(win_in.get("sticky"), bool):
            current["windows"]["sticky"] = win_in["sticky"]
        disp_in = body.get("display") or {}
        if isinstance(disp_in, dict) and disp_in.get("sort") in DISPLAY_SORT_MODES:
            current["display"]["sort"] = disp_in["sort"]
        try:
            _save_settings(current)
        except OSError as e:
            self.send_error(500, f"could not persist settings: {e}")
            return
        # On opt-in flip, drop the cache so the next /plan call fetches fresh
        # data instead of serving a stale (or empty) cached error envelope.
        global _plan_cache, _plan_cache_ts
        with _plan_cache_lock:
            _plan_cache = None
            _plan_cache_ts = 0.0
        self._json_ok(extra={"settings": current})

    def _serve_plan(self) -> None:
        settings = _load_settings()
        if not settings["plan_widget"]["enabled"]:
            self._send_json(
                {"error": "plan widget not opted in", "code": "disabled"}, status=403)
            return
        data, _exit_code = _get_plan_cached(_codex_tokens_bin)
        self._send_json(data)  # always 200; UI reads `code`

    def _serve_chats_data(self, url) -> None:
        settings = _load_settings()["chats"]
        params = parse_qs(url.query)
        per_dir = _clamp_int(
            (params.get("per_dir") or [None])[0],
            1, CHATS_PER_DIR_MAX, settings["per_dir"])
        total = _clamp_int(
            (params.get("total") or [None])[0],
            1, CHATS_TOTAL_MAX, settings["total"])
        self._send_json({
            "per_dir": per_dir,
            "total": total,
            "chats": _list_recent_chats(
                per_dir, total, set(settings["favorites"]), settings["names"]),
        })

    def _serve_chat_detail(self, url) -> None:
        sid = (parse_qs(url.query).get("sid") or [""])[0]
        # Strict sid validation; the file is then resolved exclusively via the
        # glob under ~/.codex/sessions — never from a client-supplied path.
        if not _SID_RE.fullmatch(sid):
            self.send_error(400, "invalid 'sid'")
            return
        path = _find_rollout_for_sid(sid)
        if path is None:
            self.send_error(404, "session not found")
            return
        meta = _extract_chat_meta(path)
        try:
            detail = _extract_chat_detail(path)
        except OSError:
            self.send_error(500, "could not read rollout file")
            return
        self._send_json({
            "session_id": sid,
            "cwd": meta.get("cwd"),
            "title": _thread_names().get(sid) or meta.get("first_prompt"),
            "messages": detail["messages"],
            "partial": detail["partial"],
        })

    def _handle_chat_delete(self, body: dict) -> None:
        sid = body.get("sid", "")
        # Same strict sid validation as /chat-detail; the file is resolved
        # exclusively via the glob under ~/.codex/sessions — never from a
        # client-supplied path.
        if not isinstance(sid, str) or not _SID_RE.fullmatch(sid):
            self.send_error(400, "invalid 'sid'")
            return
        path = _find_rollout_for_sid(sid)
        if path is None:
            self.send_error(404, "session not found")
            return
        with _state_lock:
            active = bool((_sessions.get(sid) or {}).get("session_active"))
        if active or _CodexProcs().holder(str(path)) is not None:
            self.send_error(409, "session is running")
            return

        # Prefer Codex's own `codex delete`: it also drops the thread from
        # Codex's internal index (state_5.sqlite), which a bare unlink would
        # leave behind as a dead entry in `codex resume`. Only when no codex
        # CLI is found — or it is too old to know `delete` — is the rollout
        # file removed directly.
        how = "unlink"
        cli = _codex_cli()
        if cli is not None:
            try:
                result = subprocess.run(
                    [*cli, "delete", "--force", sid],
                    capture_output=True, text=True, timeout=60,
                    stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                result = None
                sys.stderr.write(f"[server] codex delete failed: {e}\n")
            if result is not None and result.returncode == 0:
                how = "codex"
            elif result is not None and "unrecognized subcommand" not in (result.stderr or ""):
                err = (result.stderr or result.stdout or "").strip().splitlines()
                sys.stderr.write(f"[server] codex delete: {' | '.join(err)[:400]}\n")
                self.send_error(500, "codex delete failed")
                return
        if how == "unlink" or path.exists():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                self.send_error(500, "could not delete rollout file")
                return
        sys.stderr.write(f"[server] deleted session {sid} via {how}\n")

        with _chats_meta_lock:
            _chats_meta_cache.pop(str(path), None)
            _chats_cwd_cache.pop(str(path), None)
        _path_cache.pop(sid, None)
        with _state_lock:
            _sessions.pop(sid, None)
        # A deleted chat cannot stay pinned or named — drop stale entries.
        settings = _load_settings()
        dirty = False
        if sid in settings["chats"]["favorites"]:
            settings["chats"]["favorites"].remove(sid)
            dirty = True
        if sid in settings["chats"]["names"]:
            del settings["chats"]["names"][sid]
            dirty = True
        if dirty:
            try:
                _save_settings(settings)
            except OSError:
                pass  # the file is gone either way; the entry is pruned on next save
        self._json_ok(extra={"deleted": sid, "via": how})

    def _handle_chat_favorite(self, body: dict) -> None:
        sid = body.get("sid", "")
        # Same strict sid validation as /chat-detail; favorites are only ever
        # resolved back to files via the glob under ~/.codex/sessions.
        if not isinstance(sid, str) or not _SID_RE.fullmatch(sid):
            self.send_error(400, "invalid 'sid'")
            return
        want = body.get("favorite")
        if not isinstance(want, bool):
            self.send_error(400, "invalid 'favorite'")
            return
        settings = _load_settings()
        favs: list = settings["chats"]["favorites"]
        if want and sid not in favs:
            if len(favs) >= CHATS_FAVORITES_MAX:
                self.send_error(409, "too many favorites")
                return
            favs.append(sid)
        elif not want and sid in favs:
            favs.remove(sid)
        try:
            _save_settings(settings)
        except OSError as e:
            self.send_error(500, f"could not persist settings: {e}")
            return
        self._json_ok(extra={"sid": sid, "favorite": want})

    def _handle_chat_rename(self, body: dict) -> None:
        sid = body.get("sid", "")
        # Same strict sid validation as /chat-detail; names are keyed by sid
        # only and never resolved to paths, but a garbage key would still
        # bloat the config file forever.
        if not isinstance(sid, str) or not _SID_RE.fullmatch(sid):
            self.send_error(400, "invalid 'sid'")
            return
        name = body.get("name")
        if not isinstance(name, str):
            self.send_error(400, "invalid 'name'")
            return
        name = name.strip()[:CHATS_NAME_MAX_CHARS]
        settings = _load_settings()
        names: dict = settings["chats"]["names"]
        if name:
            if sid not in names and len(names) >= CHATS_NAMES_MAX:
                self.send_error(409, "too many named chats")
                return
            names[sid] = name
        else:
            names.pop(sid, None)  # empty name clears the custom name
        try:
            _save_settings(settings)
        except OSError as e:
            self.send_error(500, f"could not persist settings: {e}")
            return
        self._json_ok(extra={"sid": sid, "name": name or None})

    def _session_snap(self, sid) -> dict | None:
        """Validated snapshot copy for a client-supplied sid, or None (the
        caller then answers 404). cwd is always taken from our own state,
        never from the request body."""
        if not isinstance(sid, str) or not _SID_RE.fullmatch(sid):
            return None
        with _state_lock:
            snap = _sessions.get(sid)
            return dict(snap) if snap else None

    def _handle_handover(self, body: dict) -> None:
        """Deliver the rollover prompt straight into the session's terminal.
        Falls back to a plain clipboard copy (injected=false + reason) when
        xdotool is missing, the session is not running or its terminal
        cannot be identified reliably (see _exact_session_window)."""
        sid = body.get("sid", "")
        text = body.get("text", "")
        snap = self._session_snap(sid)
        if snap is None:
            self.send_error(404, "unknown session")
            return
        if not isinstance(text, str) or not text.strip():
            self.send_error(400, "missing 'text'")
            return
        if not _set_selections(text):
            self.send_error(500, "no clipboard tool found (install xclip)")
            return

        def fallback(reason: str) -> None:
            self._json_ok(extra={"injected": False, "copied": True, "reason": reason})

        if not snap.get("session_active"):
            fallback("Session läuft nicht")
            return
        # xdotool BEFORE resolving/activating the window — otherwise we would
        # steal the focus first and only then notice we cannot type anything.
        if shutil.which("xdotool") is None:
            fallback("xdotool nicht installiert")
            return
        hit, reason = _exact_session_window(sid)
        if hit is None:
            fallback(reason or "kein exaktes Terminal-Fenster gefunden")
            return
        rollout = snap.get("rollout_path")
        try:
            size_before = os.path.getsize(rollout) if rollout else 0
        except OSError:
            size_before = 0
        started = time.time()
        try:
            err = _inject_input(hit[0], hit[1], "paste", text)
        except FileNotFoundError:
            fallback("xdotool nicht installiert")
            return
        if err:
            fallback(err)
            return
        verified = _confirm_submitted(hit[0], rollout, size_before, text)
        if verified is False:
            sys.stderr.write("[server] handover: submitted text differs from the prompt!\n")
        self._json_ok(extra={"injected": True, "started": started, "verified": verified})

    def _serve_handover_status(self, url) -> None:
        """Progress probe for the client after a /handover injection: mtimes
        of HANDOVER.md in the session cwd and of the rollout file, plus the
        server clock (all times from one clock, so the client can compare)."""
        sid = (parse_qs(url.query).get("sid") or [""])[0]
        snap = self._session_snap(sid)
        if snap is None:
            self.send_error(404, "unknown session")
            return
        cwd = snap.get("session_cwd")
        if not cwd:
            self.send_error(404, "session has no cwd")
            return
        handover_mtime = 0.0
        try:
            handover_mtime = os.path.getmtime(os.path.join(cwd, HANDOVER_FILENAME))
        except OSError:
            pass
        session_mtime = snap.get("last_modified") or 0.0
        path = snap.get("rollout_path")
        if path:
            try:
                session_mtime = os.path.getmtime(path)
            except OSError:
                pass
        self._send_json({
            "sid": sid,
            "active": bool(snap.get("session_active")),
            "handover_mtime": handover_mtime,
            "session_mtime": session_mtime,
            "now": time.time(),
        })

    def _handle_session_exit(self, body: dict) -> None:
        """Type /exit into the session's terminal (after the user confirmed on
        the card). Same reliable-window-only policy as /handover — no window,
        no typing."""
        sid = body.get("sid", "")
        snap = self._session_snap(sid)
        if snap is None:
            self.send_error(404, "unknown session")
            return
        if not snap.get("session_active"):
            self.send_error(409, "session is not running")
            return
        if shutil.which("xdotool") is None:
            self.send_error(500, "xdotool not installed")
            return
        hit, _reason = _exact_session_window(sid)
        if hit is None:
            self.send_error(404, "no reliable terminal window for that session")
            return
        try:
            err = _inject_input(hit[0], hit[1], "command", "/exit")
        except FileNotFoundError:
            self.send_error(500, "xdotool not installed")
            return
        if err:
            self.send_error(500, err)
            return
        # Only report success once the TUI is really gone. (Since Codex 0.160
        # /exit just disconnects the TUI; the daemon unloads the thread — and
        # releases the rollout — some 20–30 s later.)
        for _ in range(16):
            time.sleep(0.25)
            if _read_cmdline(hit[2]) is None:
                self._json_ok(extra={"exited": sid})
                return
        self.send_error(500, "codex did not exit")

    def _handle_sticky(self, body: dict) -> None:
        title = body.get("title", "")
        if not isinstance(title, str) or not title.startswith(STICKY_TITLE_PREFIX):
            self.send_error(400, "invalid 'title'")
            return
        if not _load_settings()["windows"]["sticky"]:
            self._json_ok(extra={"skipped": "disabled"})
            return
        try:
            stuck = _make_windows_sticky(title)
        except FileNotFoundError:
            self._json_ok(extra={"warning": "wmctrl not installed"})
            return
        self._json_ok(extra={"stuck": stuck})

    def _json_ok(self, extra: dict | None = None) -> None:
        payload = {"ok": True}
        if extra:
            payload.update(extra)
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="codex-token-display HTTP/SSE bridge")
    parser.add_argument(
        "cwd",
        nargs="?",
        default=None,
        help="Project directory to watch (omit for system-wide view of all Codex sessions)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CODEX_TOKEN_DISPLAY_PORT") or DEFAULT_PORT),
    )
    parser.add_argument(
        "--codex-tokens-bin",
        default=os.environ.get("CODEX_TOKENS_BIN") or "codex-tokens",
    )
    parser.add_argument(
        "--codex-bin",
        default=os.environ.get("CODEX_BIN") or None,
        help="codex CLI used for 🗑 (`codex delete`); default: `codex` from PATH",
    )
    args = parser.parse_args()

    if args.cwd is not None and not Path(args.cwd).is_dir():
        sys.stderr.write(f"[server] cwd does not exist: {args.cwd}\n")
        return 2

    global _scope, _codex_tokens_bin, _codex_bin
    _scope = str(Path(args.cwd).resolve()) if args.cwd else None
    _codex_tokens_bin = args.codex_tokens_bin
    _codex_bin = args.codex_bin

    # Background workers.
    t1 = threading.Thread(
        target=_reader_thread,
        args=(args.cwd, args.codex_tokens_bin),
        daemon=True,
    )
    t1.start()
    t2 = threading.Thread(target=_sweeper_thread, daemon=True)
    t2.start()

    server = ThreadedHTTPServer(("127.0.0.1", args.port), Handler)
    sys.stderr.write(f"[server] listening on http://127.0.0.1:{args.port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[server] shutting down\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
