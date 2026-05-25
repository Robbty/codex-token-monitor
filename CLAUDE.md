# CLAUDE.md — Arbeitsanleitung für dieses Projekt

Diese Datei wird beim Start von Claude Code in diesem Verzeichnis automatisch
geladen. Sie fasst zusammen, was du brauchst, um hier produktiv weiterzuarbeiten.

## Was ist das?

**codex-token-monitor** — ein Tool, das die Token-/Kontext-Auslastung
laufender **Codex-CLI**-Sessions sichtbar macht. Zwei Komponenten:

1. **`codex-tokens`** (Rust-CLI, `src/`) — liest die Rollout-JSONL-Dateien
   unter `~/.codex/sessions/...`, die die Codex-CLI sowieso schreibt, und
   gibt Token-/Kontext-Stand auf stdout aus (KEY=VALUE oder JSON).
2. **Display-App** (`display/`) — Python-stdlib-Server + Chromium-`--app`-
   Fenster, das die Sessions als Karten mit Auslastungs-Balken visualisiert.

Das Tool ruft Codex **nie** selbst auf — es liest nur dessen Datei-Output.
Damit ist es unabhängig davon, wie/wo Codex installiert ist.

GitHub: <https://github.com/Robbty/codex-token-monitor>

## Repo-Struktur

```
src/
  main.rs        CLI (clap), Single- + Multi-Session-Orchestrierung (Threads + mpsc)
  locate.rs      Session-Auswahl: Selector { ThreadId, Cwd, MostRecent, All }, mtime-Filter
  proc.rs        /proc-basierte Aktiv-Erkennung (nur Schreib-Handles zählen)
  tail.rs        JSONL-Tailing (poll-basiert, kein notify)
  protocol.rs    schmale serde-Mirrors der Codex-Rollout-JSONL (#[serde(other)]-Catch-All)
  state.rs       TokenState-Aggregation (compact_count, session_active, …)
  render.rs      KV- und JSON-Ausgabe
display/
  launcher.sh          startet server.py + Chromium-App-Fenster
  install-desktop.sh   erzeugt .desktop-Launcher + Desktop-Icon
  server.py            HTTP/SSE-Bridge, spawnt codex-tokens, /open-dir /focus-terminal /copy /readme /help
  index.html
  help.html            eigenständiges Hilfe-Fenster (rendert README via marked.js)
  static/{app.css,app.js,marked.min.js}
  icon.svg / icon.png  App-Icon
  README.md            UI-Bedienung (wird im 📖-Fenster gerendert)
scripts/build.sh       baut gnu+musl + stagt target/release/bundle/ (+ optional Tarball)
README.md              Projekt-README (GitHub-Visitenkarte, CLI-Doku)
```

Datenquelle (read-only): `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`.

## Build / Test / Release

```bash
# CLI bauen
cargo build --release                                   # gnu, target/release/codex-tokens
cargo build --release --target x86_64-unknown-linux-musl # statisch

# Komplett-Bundle + Tarball (das bevorzugte Distributions-Artefakt)
./scripts/build.sh --tarball

# Lokal installieren
install -m 755 target/x86_64-unknown-linux-musl/release/codex-tokens ~/.local/bin/codex-tokens

# Syntax-Checks (es gibt KEINE cargo #[test]s im Projekt — nur manuelle Verifikation)
node --check display/static/app.js
python3 -c "import ast; ast.parse(open('display/server.py').read())"

# Display lokal testen (ohne Browser, nur Server-Endpunkte)
python3 display/server.py --port 18999 --codex-tokens-bin target/release/codex-tokens &
curl -s http://127.0.0.1:18999/readme | head -1
```

Verifikation des CLI gegen echte Daten: `codex-tokens --all --json | jq` oder
gegen eine bestimmte Session `codex-tokens --thread <uuid>`.

## Konventionen (WICHTIG — hier sind schon Fehler passiert)

- **`target/` ist gitignored** und bleibt es. Niemals Binaries committen.
  Binaries werden ausschließlich als GitHub-Release-Assets verteilt.
- **Nie `git add -A` / `git add .`** — schon zweimal sind dadurch
  `__pycache__/` und eine `README.pdf` ins Repo gerutscht. Immer
  spezifische Pfade adden.
- **Release-Builds mit Pfad-Remap**, sonst leakt `/home/peter` in die
  Binary (Panic-Pfade). `scripts/build.sh` macht das automatisch via
  `RUSTFLAGS="--remap-path-prefix $HOME/.cargo=/cargo …"`. Bei manuellem
  `cargo build` für einen Release dran denken.
- **Commit-Stil:** deutschsprachige Messages, ausführlicher Body. Author
  in der bisherigen History: `Robbty <robbty01@gmail.com>` (gesetzt per
  `git -c user.name=… -c user.email=…`, nicht global). Folge diesem Stil.
- **Display-Assets** liegen unter `display/static/`. `index.html`/`help.html`
  laden `/static/...`.
- **Versionsbump** in `Cargo.toml` bei jedem Release; Tag `vX.Y.Z`;
  `gh release create` mit musl-Binary + gnu-Binary + Tarball + SHA256SUMS.

## Gelernte Klippen (nicht nochmal hineintappen)

1. CSS-Spezifität: `.modal.hidden { display:none }` statt nur `.hidden`,
   wenn `.hidden` vor `.modal { display:flex }` in der Datei steht.
2. `/proc`-Aktiv-Check zählt nur **Schreib**-Handles (`fdinfo`-flags
   `& 0o3 != 0`), sonst meldet das lesende Tool sich selbst als „aktiv".
3. `wmctrl -a` nimmt das erste Match — Dateimanager klauen den Fokus.
   `_handle_focus_terminal` nutzt `wmctrl -lx` + WM_CLASS-Scoring.
4. Codex-Desktop spawnt „Worker"-Sessions ohne `model_context_window`.
   Display blendet sie per Toggle ein/aus; ohne gemeldetes Limit wird ein
   Fallback geschätzt (`effectiveContextWindow` in app.js).
5. ImageMagick rendert SVG-Gradienten (hsl, userSpaceOnUse) unzuverlässig —
   `icon.svg` nutzt daher Volltonfarben.
6. Karten-Sortierung primär nach `compact_count`, sekundär `percent_used` —
   sonst springt eine Session nach Auto-Compact unerwartet weg.

## Aktueller Stand & offene Punkte

Siehe `HANDOFF.md` im selben Verzeichnis für den letzten Stand der Arbeit
und die noch offene Release-Entscheidung.
