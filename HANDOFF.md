# HANDOFF — Stand der Arbeit

Momentaufnahme für den Übergang in dieses Verzeichnis. Dauerhafte
Projektanleitung steht in `CLAUDE.md`.

## Kontext

Das gesamte Projekt wurde in einer langen Claude-Code-Session aufgebaut,
die im Codex-Quellcode-Verzeichnis (`/home/peter/projekte/codex/codex-main`)
lief. Ab jetzt soll direkt aus `/home/peter/projekte/codex-token-monitor/`
weitergearbeitet werden (auch erreichbar über den Symlink
`/home/peter/Schreibtisch/projekte/codex-token-monitor`).

## Aktueller Stand (Git)

- Branch `main`, sauberer Working Tree, synchron mit
  `origin/main` (<https://github.com/Robbty/codex-token-monitor>).
- Letzter Commit: `4d11724` — Desktop-Icon + Installer.
- Letztes getaggtes Release: **v0.5.0**.
- Zwei Commits liegen auf `main`, sind aber **noch nicht** in einem
  getaggten Release / Tarball:
  - `b497c22` — Karten-Sortierung primär nach `compact_count`
  - `4d11724` — Desktop-Icon + `install-desktop.sh`

## Funktionsumfang (fertig & getestet)

- CLI `codex-tokens` (v0.5.0): Single- + Multi-Session (`--all`,
  system-weit oder per `--cwd`), `--follow`, `--watch-new`, `--wait`,
  `--require-open` (/proc-Aktiv-Check), `--max-age`, KV- und JSON-Ausgabe.
  Felder u.a. `percent_left`, `tokens_in_context`, `session_total_tokens`,
  `session_active`, `compact_count`.
- Display-App: Karten mit 4-Farb-Balken (grün→gelb→rot→violett,
  positional), Status-Punkt, Idle-Timer, Buttons (📁 Dir, ⚡ Terminal-
  Fokus, 📋 Rollout-Pfad kopieren, ↻ Handover-Prompt), ↻-Compact-Badge,
  Worker-Toggle, Hilfe-Fenster (📖) mit gerenderter README, Desktop-Icon.
- Verteilung: GitHub-Releases mit musl-/gnu-Binary, Tarball, SHA256SUMS;
  `scripts/build.sh --tarball` baut alles.

## Offene Entscheidung

**Soll v0.5.1 getaggt werden?** Die zwei o.g. Commits (Sortier-Fix +
Desktop-Icon) sind nur auf `main`, nicht im v0.5.0-Tarball. Wer per
`git clone` arbeitet, hat alles; wer den **Tarball** lädt, bekommt das
Display ohne Icon/Installer und ohne den Sortier-Fix. Die Rust-Binaries
wären in v0.5.1 byte-identisch zu v0.5.0 (keine Code-Änderung) — es ginge
rein darum, den Tarball-Verteilweg aktuell zu halten.

→ Wenn ja: `Cargo.toml` auf 0.5.1, `./scripts/build.sh --tarball`,
   Commit, Tag `v0.5.1`, `gh release create` mit den 4 Assets.

## Mögliche nächste Schritte (Ideen, nicht beauftragt)

- v0.5.1 ziehen (siehe oben).
- Pin-/Anker-Funktion im Display (Karte oben fixieren), damit eine
  beobachtete Session beim Re-Sort nicht wegspringt.
- Schwellen-Benachrichtigung (OS-Notification bei z.B. < 15 % frei).
- Aggregierte Kosten-/Verbrauchs-Summe über alle Sessions in der Topbar.

## Verwandtes Projekt

Unter `/home/peter/Schreibtisch/projekte/claude-token-monitor/HANDOFF.md`
liegt ein Auftrag, dasselbe Tool für **Claude Code** zu bauen (analoge
Architektur, andere Datenquelle `~/.claude/projects/<encoded-cwd>/*.jsonl`).
Noch nicht begonnen.
