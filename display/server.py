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
        POST /open-dir         → opens a directory in the system file manager
        POST /focus-terminal   → tries to focus the terminal/IDE window for a cwd

The server uses only the Python standard library; no pip install needed.
"""

import http.server
import json
import os
import queue
import re
import shlex
import signal
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Heuristic for "this process IS a codex TUI". Matches both the JS wrapper
# (`node …/bin/codex …`) and the native codex binary inside the npm package
# (`…/codex-linux-x64/…/bin/codex`). We want the *native* binary because
# it's the one that opens the rollout file; the node wrapper does not.
_CODEX_NATIVE_RE = re.compile(r"/codex-linux-(?:x64|arm64)/.+/bin/codex(\s|$)")

# Subcommands that run the native binary WITHOUT an interactive session
# (`codex app-server` backs the VS Code extension, `codex exec` is headless,
# …). Such processes never are a "ghost TUI" and must not be killable via
# the 🛑 button. Interactive forms: bare `codex [PROMPT]`, `resume`, `fork`.
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


def _is_codex_tui(argv: list[str] | None) -> bool:
    """True for the native codex binary running an interactive TUI."""
    if not argv or not _CODEX_NATIVE_RE.search(" ".join(argv[:1]) + " "):
        return False
    first_positional = next((a for a in argv[1:] if not a.startswith("-")), None)
    return first_positional not in _CODEX_NON_TUI_SUBCOMMANDS


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
}

# Cache for the upstream /plan response. The numbers only change when the
# user actually makes a Codex request; refreshing more often than once a
# minute just burns the rate-limit of our own call.
PLAN_CACHE_TTL_SEC = 60
_plan_cache_lock = threading.Lock()
_plan_cache: dict | None = None
_plan_cache_ts: float = 0.0
_plan_cache_exit: int = 0  # last subprocess exit code


def _load_settings() -> dict:
    """Read settings from disk, merge over defaults, tolerate corruption."""
    try:
        raw = CONFIG_PATH.read_text()
        data = json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    # Deep-merge user data over defaults so a new row toggle added in a
    # later version surfaces with its default value instead of "missing".
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

# Ghost codex TUIs — processes that exist but hold no rollout file open
# (no card in the regular sessions list). Refreshed by _ghost_scanner_thread,
# replaced as a whole on each scan tick. UI gets the full list each time.
_ghosts_lock = threading.Lock()
_ghosts: list[dict] = []

# Cache: session_id -> absolute rollout path. The lookup walks
# ~/.codex/sessions, so we only do it once per session.
_path_cache: dict[str, str] = {}

# Set in main(); the /plan handler needs to spawn the same binary used by
# the session-tail subprocess.
_codex_tokens_bin: str = "codex-tokens"


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


def _enrich_snapshot(snap: dict) -> dict:
    """Add rollout_path (absolute) to the snapshot, with caching."""
    sid = snap.get("session_id")
    if not sid:
        return snap
    cached = _path_cache.get(sid)
    if cached is None:
        path = _find_rollout_for_sid(sid)
        if path is not None:
            try:
                cached = str(path.resolve())
            except OSError:
                cached = str(path)
            _path_cache[sid] = cached
    if cached:
        snap["rollout_path"] = cached
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


# --- active-status sweeper ------------------------------------------------

def _sweeper_thread() -> None:
    """
    codex-tokens emits a snapshot per TokenCount event. When a session closes,
    no further events come, so we never see session_active flip to false.
    This thread periodically re-checks each known session's rollout file via
    /proc, and broadcasts an update if the active flag changed.
    """
    while True:
        time.sleep(3)
        with _state_lock:
            items = list(_sessions.items())
        for sid, snap in items:
            cwd = snap.get("session_cwd")
            # We don't know the rollout path directly from the JSON output.
            # Re-derive it: search ~/.codex/sessions for a file ending in -<sid>.jsonl.
            path = _find_rollout_for_sid(sid)
            if not path:
                continue
            now_active = _is_held_open(path)
            if snap.get("session_active") != now_active:
                snap["session_active"] = now_active
                with _state_lock:
                    _sessions[sid] = snap
                _broadcast({"type": "snapshot", "data": snap})


def _find_rollout_for_sid(sid: str) -> Path | None:
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    needle = f"-{sid}.jsonl"
    sessions_dir = home / "sessions"
    if not sessions_dir.is_dir():
        return None
    for path in sessions_dir.rglob(f"*{needle}"):
        return path
    return None


def _is_held_open(rollout: Path) -> bool:
    """Linux-only: returns True if any process holds rollout with a write FD."""
    return _find_pid_holding(rollout) is not None


def _find_pid_holding(rollout: Path) -> int | None:
    """Walk /proc and return the first PID that has `rollout` open with a
    write FD. Returns None if nothing matches (file closed / non-Linux).
    Same /proc/fdinfo flag-bits parsing as _is_held_open before; we just
    surface the PID instead of a boolean. Read-only handles are ignored
    on purpose — we (codex-tokens itself) read these files and would
    otherwise show up as "active".
    """
    try:
        target = rollout.resolve()
    except OSError:
        return None
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return None
    for pid_dir in proc_root.iterdir():
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / "fd"
        try:
            entries = list(fd_dir.iterdir())
        except (PermissionError, FileNotFoundError):
            continue
        for fd in entries:
            try:
                if fd.resolve() != target:
                    continue
            except OSError:
                continue
            fdinfo = pid_dir / "fdinfo" / fd.name
            try:
                content = fdinfo.read_text()
            except (PermissionError, FileNotFoundError):
                continue
            for line in content.splitlines():
                if line.startswith("flags:"):
                    try:
                        flags = int(line.split(":", 1)[1].strip(), 8)
                    except ValueError:
                        break
                    if flags & 0o3:
                        try:
                            return int(pid_dir.name)
                        except ValueError:
                            return None
                    break
    return None


def _find_codex_processes() -> list[dict]:
    """Enumerate native codex-binary processes via /proc. Returns dicts with
    pid, cwd, started_at (epoch). Includes ALL codex TUIs — even ones that
    haven't started a session yet (no rollout file).
    """
    out: list[dict] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return out
    for pid_dir in proc_root.iterdir():
        if not pid_dir.name.isdigit():
            continue
        # Match against the *cmdline* (NUL-separated argv). The native
        # codex binary lives under codex-linux-x64/...; the node wrapper
        # is a different process and gets filtered out here, as are
        # non-interactive subcommands (app-server, exec, …).
        argv = _read_cmdline(pid_dir.name)
        if not _is_codex_tui(argv):
            continue
        try:
            cwd = str((pid_dir / "cwd").resolve())
        except (OSError, PermissionError):
            cwd = ""
        pid = int(pid_dir.name)
        out.append({
            "pid": pid,
            "cwd": cwd,
            "started_at": _proc_started_at(pid),
            "cmdline": " ".join(argv)[:200],
        })
    return out


def _find_ghost_codex_processes(known_session_pids: set[int]) -> list[dict]:
    """Codex TUIs that hold NO rollout file open with write access — i.e.
    sessions that exist as a process but have no card in the display.
    Two reasons this happens in practice:
      1. TUI was started, but no user turn submitted yet (no JSONL created)
      2. Session was finished but TUI window still open at prompt
    `known_session_pids` is the set of PIDs we already render as active
    cards, so we can avoid double-listing them.
    """
    ghosts = []
    for p in _find_codex_processes():
        if p["pid"] in known_session_pids:
            continue
        # Recheck: does THIS PID hold any rollout-*.jsonl with a write FD?
        if _pid_holds_any_rollout(p["pid"]):
            continue
        ghosts.append({
            "pid": p["pid"],
            "cwd": p["cwd"],
            "started_at": p["started_at"],
        })
    return ghosts


def _pid_holds_any_rollout(pid: int) -> bool:
    """True if the given PID has any rollout-*.jsonl open with a write FD."""
    fd_dir = Path(f"/proc/{pid}/fd")
    fdinfo_dir = Path(f"/proc/{pid}/fdinfo")
    try:
        entries = list(fd_dir.iterdir())
    except (PermissionError, FileNotFoundError):
        return False
    for fd in entries:
        try:
            target = fd.resolve()
        except OSError:
            continue
        if "rollout-" not in target.name or not target.name.endswith(".jsonl"):
            continue
        try:
            content = (fdinfo_dir / fd.name).read_text()
        except (PermissionError, FileNotFoundError):
            continue
        for line in content.splitlines():
            if line.startswith("flags:"):
                try:
                    flags = int(line.split(":", 1)[1].strip(), 8)
                except ValueError:
                    break
                if flags & 0o3:
                    return True
                break
    return False


def _ghost_scanner_thread() -> None:
    """Periodically broadcast the list of ghost codex TUIs to all SSE
    subscribers. Full-replace semantics: each tick sends the COMPLETE
    current list, so the UI can simply reflect what's there without
    diffing.
    """
    global _ghosts
    while True:
        time.sleep(5)
        # Resolve which PIDs already have a rendered session card.
        with _state_lock:
            session_sids = [s for s in _sessions.values() if s.get("session_active")]
        known_pids: set[int] = set()
        for snap in session_sids:
            sid = snap.get("session_id")
            if not sid:
                continue
            path = _find_rollout_for_sid(sid)
            if not path:
                continue
            pid = _find_pid_holding(path)
            if pid:
                known_pids.add(pid)
        ghosts = _find_ghost_codex_processes(known_pids)
        with _ghosts_lock:
            _ghosts = ghosts
        # Always broadcast — cheap and lets the UI keep its clock fresh.
        _broadcast({"type": "ghosts", "data": ghosts})


# --- HTTP handler ---------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "codex-token-display/0.1"

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
            self._json_ok(extra={"scope": _scope})
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
            # Initial ghost list — without it, freshly-opened browsers would
            # show no ghosts until the next 5-second scanner tick.
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
        finally:
            with _subscribers_lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    def _sse_send(self, event: dict) -> None:
        try:
            payload = json.dumps(event)
            self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    def _handle_open_dir(self, body: dict) -> None:
        path = body.get("path", "")
        if not path or not Path(path).is_dir():
            self.send_error(400, "missing or invalid 'path'")
            return
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

    def _handle_focus_terminal(self, body: dict) -> None:
        cwd = body.get("cwd", "")
        if not cwd:
            self.send_error(400, "missing 'cwd'")
            return

        cwd_basename = Path(cwd).name

        # Enumerate all windows with their WM_CLASS via `wmctrl -lx`.
        # Output format per line:
        #   <window_id>  <desktop>  <wm_class>  <hostname>  <title>
        try:
            result = subprocess.run(
                ["wmctrl", "-lx"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            self.send_error(500, "wmctrl not installed")
            return
        if result.returncode != 0:
            self._json_ok(extra={"warning": "wmctrl -lx failed"})
            return

        # Score candidates: IDE/editor > terminal > anything else; file
        # managers are excluded so that an already-open file-manager window
        # for the same cwd does not steal the match from the IDE window.
        ide_classes = (
            "code", "cursor", "windsurf", "vscodium", "code-oss",
            "jetbrains", "intellij", "pycharm", "webstorm", "phpstorm",
            "goland", "rustrover", "rider", "datagrip", "android-studio",
            "sublime_text", "atom", "zed",
        )
        term_classes = (
            "terminal", "alacritty", "kitty", "konsole", "xterm",
            "wezterm", "tilix", "guake", "tabby", "rxvt", "urxvt",
            "gnome-terminal", "xfce4-terminal", "qterminal", "foot",
        )
        file_manager_classes = (
            "nautilus", "dolphin", "thunar", "nemo", "pcmanfm",
            "caja", "spacefm", "krusader",
        )

        candidates: list[tuple[int, str, str]] = []  # (score, window_id, title)
        for line in result.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            win_id, _desktop, wm_class, _host, title = parts
            if cwd not in title and cwd_basename not in title:
                continue
            cls_lower = wm_class.lower()
            if any(fm in cls_lower for fm in file_manager_classes):
                continue  # explicitly skip file managers
            score = 1  # generic title match
            if any(ide in cls_lower for ide in ide_classes):
                score = 10
            elif any(t in cls_lower for t in term_classes):
                score = 5
            candidates.append((score, win_id, title))

        if not candidates:
            self._json_ok(extra={"warning": "no matching IDE/terminal window found"})
            return

        candidates.sort(key=lambda c: c[0], reverse=True)
        _, win_id, _ = candidates[0]
        subprocess.run(
            ["wmctrl", "-ia", win_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        self._json_ok()

    def _handle_kill(self, body: dict) -> None:
        """Send SIGTERM to a codex TUI process. Body shape:
            { "session_id": "<uuid>" }   → look up the PID via the rollout file
            { "pid": <int> }             → use the PID directly (ghost TUIs)
        SIGTERM gives codex a chance to flush its rollout cleanly — same
        effect as closing the terminal window the TUI is running in.
        """
        pid = body.get("pid")
        sid = body.get("session_id")

        if not pid and sid:
            rollout = _find_rollout_for_sid(sid)
            if not rollout:
                # HTTP status-line must be ASCII/latin-1, no ellipsis here.
                self.send_error(404, f"no rollout file for session {sid[:8]}...")
                return
            pid = _find_pid_holding(rollout)
            if not pid:
                self.send_error(404, "no process holds that session open")
                return

        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            self.send_error(400, "invalid pid")
            return

        # Sanity check: only kill interactive codex TUIs. Without this, a
        # malicious POST could steer the server into killing arbitrary user
        # processes (or the app-server behind the VS Code extension).
        argv = _read_cmdline(pid_int)
        if argv is None:
            self.send_error(404, f"pid {pid_int} not found")
            return
        if not _is_codex_tui(argv):
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
        if not text:
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
        body = json.dumps(_load_settings()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_update_settings(self, body: dict) -> None:
        # We accept partial updates so the UI can flip one toggle at a time
        # without round-tripping the entire blob.
        current = _load_settings()
        pw_in = (body.get("plan_widget") or {})
        if isinstance(pw_in.get("enabled"), bool):
            current["plan_widget"]["enabled"] = pw_in["enabled"]
        if isinstance(pw_in.get("consent_acknowledged"), bool):
            current["plan_widget"]["consent_acknowledged"] = pw_in["consent_acknowledged"]
        rows_in = pw_in.get("rows") or {}
        if isinstance(rows_in, dict):
            for k, v in rows_in.items():
                if isinstance(v, bool):
                    current["plan_widget"]["rows"][k] = v
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
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "plan widget not opted in",
                "code": "disabled",
            }).encode())
            return

        data, exit_code = _get_plan_cached(_codex_tokens_bin)
        status = 200 if exit_code == 0 else 200  # always 200; UI reads `code`
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
    args = parser.parse_args()

    if args.cwd is not None and not Path(args.cwd).is_dir():
        sys.stderr.write(f"[server] cwd does not exist: {args.cwd}\n")
        return 2

    global _scope, _codex_tokens_bin
    _scope = str(Path(args.cwd).resolve()) if args.cwd else None
    _codex_tokens_bin = args.codex_tokens_bin

    # Background workers.
    t1 = threading.Thread(
        target=_reader_thread,
        args=(args.cwd, args.codex_tokens_bin),
        daemon=True,
    )
    t1.start()
    t2 = threading.Thread(target=_sweeper_thread, daemon=True)
    t2.start()
    t3 = threading.Thread(target=_ghost_scanner_thread, daemon=True)
    t3.start()

    server = ThreadedHTTPServer(("127.0.0.1", args.port), Handler)
    sys.stderr.write(f"[server] listening on http://127.0.0.1:{args.port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[server] shutting down\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
