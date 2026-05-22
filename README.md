# codex-token-monitor

Stdout-Anzeige für Token- und Kontext-Daten der aktuell laufenden Codex-Session.
Liest direkt die Rollout-JSONL-Datei unter `$CODEX_HOME/sessions/…` — kein
Patchen von Codex nötig, keine Abhängigkeit auf interne Codex-Crates. Nur die
JSON-Felder, die das Tool nutzt, sind als minimaler Serde-Typ nachgebaut;
unbekannte Felder werden ignoriert, damit Codex-Updates nichts brechen.

## Schnellinstallation (ohne Bauen)

Vorgefertigte Binaries gibt es auf der
[Releases-Seite](https://github.com/Robbty/codex-token-monitor/releases).

Statisch verlinkte Variante (empfohlen — läuft auf jedem x86_64-Linux/WSL2
ohne Abhängigkeiten):

```bash
mkdir -p ~/.local/bin
curl -sSL https://github.com/Robbty/codex-token-monitor/releases/latest/download/codex-tokens-x86_64-linux-musl \
  -o ~/.local/bin/codex-tokens
chmod +x ~/.local/bin/codex-tokens
codex-tokens --version
```

Falls `~/.local/bin` nicht im `$PATH` liegt, in `~/.bashrc` ergänzen:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Integrität prüfen (optional, aber empfohlen):

```bash
curl -sSL https://github.com/Robbty/codex-token-monitor/releases/latest/download/SHA256SUMS \
  | grep codex-tokens-x86_64-linux-musl \
  | sha256sum -c -
```

Für ältere Distros bzw. wenn die dynamische Variante reicht, die `-gnu`-Datei
verwenden — Voraussetzung: glibc ≥ 2.35.

## Bauen

```bash
cd /pfad/zu/codex-token-monitor
cargo build --release
# Binary: target/release/codex-tokens
```

Das Binary ist dynamisch gegen die glibc des Build-Systems gelinkt — siehe
[Portabilität](#portabilität-und-statischer-build) für die Variante, die auf
jedem Linux läuft.

### Komplett-Bundle (Binary + Display-App)

Wer beides nutzen will (CLI **und** das Statusfenster), kann das mit
einem Skript in einem Schritt erzeugen:

```bash
./scripts/build.sh                 # baut gnu+musl, stagt musl-Bundle
./scripts/build.sh --tarball       # zusätzlich ein versendbares .tar.gz
./scripts/build.sh --variant gnu   # dynamische Variante im Bundle
./scripts/build.sh --skip-build    # nur neu stagen ohne Cargo-Aufruf
```

Ergebnis liegt unter `target/release/bundle/`:

```text
target/release/bundle/
├── codex-tokens          ← Binary (default: musl, statisch)
├── display/              ← komplette Display-App
├── README.md             ← diese Doku
└── SHA256SUMS            ← Prüfsumme des Binarys
```

Mit `--tarball` zusätzlich
`target/release/codex-token-monitor-v<version>-x86_64-linux-musl.tar.gz`
(~500 KB), das man einfach an andere Rechner verschicken kann.

## Installation

Damit `codex-tokens` aus jedem Terminal aufrufbar ist, das Binary in ein
Verzeichnis im `$PATH` legen. Übliche Optionen:

**Variante 1 — `~/.local/bin` (XDG-Standard, empfohlen):**

```bash
mkdir -p ~/.local/bin
install -m 755 target/release/codex-tokens ~/.local/bin/

# Prüfen, ob ~/.local/bin im PATH steht:
echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.local/bin" \
  && echo "PATH ok" \
  || echo 'export PATH="$HOME/.local/bin:$PATH"  # in ~/.bashrc ergänzen'
```

**Variante 2 — `~/bin` (klassisch):**

```bash
mkdir -p ~/bin
install -m 755 target/release/codex-tokens ~/bin/
# Bei Bedarf in ~/.bashrc ergänzen:
#   export PATH="$HOME/bin:$PATH"
```

**Variante 3 — `/usr/local/bin` (systemweit, benötigt sudo):**

```bash
sudo install -m 755 target/release/codex-tokens /usr/local/bin/
```

**Variante 4 — direkt mit Cargo:**

```bash
cargo install --path /pfad/zu/codex-token-monitor
# Binary landet in ~/.cargo/bin/codex-tokens (in $PATH, wenn Rust regulär installiert wurde)
```

Hinweis: `/bin` ist auf Linux meist ein Symlink auf `/usr/bin` und für System-
Tools reserviert — eigene Binaries gehören dort nicht hin. Nimm stattdessen
Variante 1, 2 oder 3.

Test nach der Installation:

```bash
which codex-tokens
codex-tokens --version
```

## Verwendung

```text
codex-tokens [OPTIONS]

  --thread <UUID>     An eine konkrete Session binden (Thread-/Session-ID).
  --cwd [PATH]        An die Session binden, deren cwd zu PATH passt (Default: $PWD).
  --codex-home DIR    Überschreibt CODEX_HOME (Default: $CODEX_HOME oder ~/.codex).
  -f, --follow        Folgt der Rollout-Datei und gibt bei jedem TokenCount-Event eine neue Momentaufnahme aus.
      --json          Einzeiliges JSON statt KEY=VALUE.
      --locate        Gibt den ermittelten Rollout-Pfad aus und beendet sich.
      --wait          Wartet, bis eine passende Rollout-Datei erscheint, statt sofort abzubrechen.
                      Damit kann der Monitor vor Codex gestartet werden.
      --wait-timeout SECS  Sekunden-Limit für --wait (Default: unbegrenzt).
      --all           Multi-Session: ALLE aktiven Sessions gleichzeitig verfolgen.
                      Mit --cwd auf ein Projektverzeichnis eingegrenzt; ohne --cwd
                      system-weit (jede Codex-Session auf dem Rechner). Jeder Block
                      wird mit "=== session <uuid> ===" markiert.
      --max-age MINUTES    Nur mit --all: Rollouts, die länger nicht beschrieben wurden,
                           gelten als nicht-aktiv (Default: 5).
      --watch-new     Nur mit --all --follow: scannt alle 5 s nach neu aufgetauchten Rollouts
                      und hängt sie live in den Stream.
      --require-open  Nur Rollouts, deren Datei aktuell von einem Prozess offen gehalten wird
                      (= Codex-Session läuft noch). Linux/WSL2; kein Effekt auf anderen Systemen.
  -h, --help / -V, --version
```

Standardauswahl: die zuletzt geschriebene Rollout-Datei (neueste mtime).

## Schnellstart: Live-Anzeige neben einer Codex-Session

Zwei Terminals, beide im selben Projektverzeichnis. Die Reihenfolge ist
egal — dank `--wait` darf der Monitor vor oder nach Codex starten.

**Terminal 1 — Monitor:**

```bash
cd /pfad/zu/deinem-projekt
codex-tokens --cwd --follow --wait
```

**Terminal 2 — Codex:**

```bash
cd /pfad/zu/deinem-projekt
codex
```

In Terminal 1 läuft ab jetzt ein Dauerstream: nach jedem abgeschlossenen
Codex-Turn erscheint ein neuer Snapshot, getrennt durch eine `---`-Zeile.

Beenden: `Strg+C` im Monitor-Terminal.

## Mehrere Codex-Sessions im selben Verzeichnis

Standardmäßig betrachtet `codex-tokens` nur **eine** Session pro Verzeichnis
(die jüngste). Wenn du mehrere Codex-Instanzen parallel im selben
Projektverzeichnis laufen lässt — etwa in verschiedenen tmux-Panes oder
Terminals — kannst du sie alle gemeinsam erfassen:

```bash
codex-tokens --cwd --all --follow
```

Jede Session wird in der Ausgabe durch einen eigenen Block-Header sichtbar
abgegrenzt:

```text
=== session 019e2b32-2d0a-7951-802a-86f2f48d1b5c ===
session_id=019e2b32-2d0a-7951-802a-86f2f48d1b5c
session_cwd=/pfad/zum/projekt
percent_left=95
…
---
=== session 019e1716-aee2-7402-8806-2623d765c67c ===
session_id=019e1716-aee2-7402-8806-2623d765c67c
session_cwd=/pfad/zum/projekt
percent_left=42
…
---
```

Im JSON-Modus (`--json --all`) wird stattdessen NDJSON ausgegeben: eine
JSON-Zeile pro Session und Update, ideal für `jq`-Pipelines.

### Was zählt als "aktive" Session?

Per Default werden Rollouts ignoriert, deren letzte Änderung länger als
**5 Minuten** zurückliegt. Damit fallen alte Sessions desselben
Verzeichnisses automatisch raus. Das Fenster ist konfigurierbar:

```bash
codex-tokens --cwd --all --max-age 30    # 30 min - großzügiger
codex-tokens --cwd --all --max-age 1     # 1 min - nur ganz frische
```

Soll nichts gefiltert werden (z. B. für eine Archiv-Auswertung), kann
ein sehr hoher Wert verwendet werden:

```bash
codex-tokens --cwd --all --max-age 99999
```

### Neue Sessions während des Laufs erkennen

Standardmäßig folgt der Monitor nur den Sessions, die beim Start vorhanden
waren. Mit `--watch-new` scannt er zusätzlich alle 5 Sekunden das
Sessions-Verzeichnis auf neu aufgetauchte Rollouts:

```bash
codex-tokens --cwd --all --follow --watch-new --wait
```

Damit kannst du den Monitor *vor* Codex starten und beliebig viele
Codex-Instanzen nachträglich hinzustarten — sie erscheinen automatisch
im Stream.

### Nur tatsächlich laufende Sessions: `--require-open`

Der `--max-age`-Filter ist eine Heuristik über die Datei-`mtime` — sie kann
lange Denkpausen einer aktiven Session fälschlich als „nicht aktiv" werten.
Eine präzisere Variante prüft per `/proc`, ob ein Prozess die Rollout-Datei
aktuell **schreibend** geöffnet hält:

```bash
codex-tokens --cwd --all --follow --require-open --watch-new --wait
```

Eigenschaften:

- Linux/WSL2-only (greift auf `/proc/<pid>/fdinfo/<n>`-Metadaten zu)
- Sieht nur Schreib-Handles — andere `codex-tokens`-Instanzen, die die Datei
  lesend tailen, werden korrekt ignoriert
- Ausgabe enthält zusätzlich das Feld `session_active=true|false`
- Funktioniert sowohl mit als auch ohne `--max-age` (oft kann man `--max-age`
  weglassen, wenn `--require-open` aktiv ist)

### Filtern in Bash

Nur eine bestimmte Session aus dem Multi-Stream herauspicken:

```bash
SESSION=019e2b32-2d0a-7951-802a-86f2f48d1b5c
codex-tokens --cwd --all --follow \
  | awk -v s="=== session $SESSION ===" '
      $0 == s {p=1}
      p {print}
      p && /^---$/ {p=0}
    '
```

Mit JSON-Output und `jq`:

```bash
codex-tokens --cwd --all --json --follow \
  | jq -c --arg s "$SESSION" 'select(.session_id == $s)'
```

Aggregierte Übersicht aller aktiven Sessions:

```bash
codex-tokens --cwd --all --json \
  | jq -s 'map({id: .session_id, left: .percent_left, used: .tokens_in_context})'
```

## Aufruf innerhalb der Codex-TUI

Manchmal will man den Token-Stand sehen, ohne ein zweites Terminal zu öffnen.
Codex kann das Kommando in der laufenden TUI selbst ausführen.

**Variante A — Direkt ausführen mit `!`-Präfix (empfohlen):**

In der Eingabezeile tippen:

```
!codex-tokens --cwd
```

Das `!` führt das Kommando sofort aus, ohne das Modell zu befragen. Die
Ausgabe landet direkt im Chat-Verlauf, kostet kein Modell-Token und bricht
den laufenden Codex-Gedankengang nicht ab.

**Variante B — Codex selbst aufrufen lassen:**

Wenn das Modell die Zahlen in seine Antwort einbeziehen soll, einfach
fragen:

```
Führe codex-tokens --cwd aus und sag mir, wie viel Kontext noch frei ist.
```

Codex führt das Kommando in einem normalen Tool-Call aus und kommentiert
das Ergebnis (z. B. *„nur noch 8 % frei, vielleicht `/compact` ausführen?"*).
Kostet einen Turn.

### Warnung: `--follow` / `-f` niemals in der TUI verwenden!

`codex-tokens --follow` läuft endlos. Innerhalb der Codex-TUI würde dieses
Kommando **die laufende Eingabe komplett blockieren**, weil Codex auf das
Ende des Shell-Aufrufs wartet, das niemals kommt. Verwende `--follow`
**ausschließlich** in einem separaten Terminal.

In der TUI nur Snapshot-Aufrufe (ohne `-f`):

```text
!codex-tokens --cwd            # ok
!codex-tokens --cwd --json     # ok
!codex-tokens --cwd --follow   # NICHT verwenden!
```

### Falls doch einmal `-f` in der TUI passiert ist

Wenn Codex auf einem festhängenden `codex-tokens --follow` wartet, lässt sich
das wieder lösen:

1. **`Esc` drücken** — Codex bricht das laufende Tool/Kommando ab. Die TUI
   bleibt offen, du kannst normal weitermachen.
2. **Falls `Esc` nichts hilft:** im *anderen* Terminal das Kommando killen:

   ```bash
   pkill -INT codex-tokens
   ```

3. **Letzte Option — Codex komplett beenden:** zweimal `Strg+C` drücken
   (Codex zeigt nach dem ersten Druck den Hinweis „Press again to quit").
   Das schließt die TUI sauber; der nächste `codex`-Aufruf öffnet eine neue
   Session.

## Ausgabeformat (KEY=VALUE, Default)

```text
session_id=019e218d-…
session_cwd=/pfad/zu/deinem-projekt
context_window=258400
percent_left=79
percent_used=21
tokens_in_context=63437        # Aktuelle Belegung des Kontextfensters (last_token_usage.total_tokens)
session_total_tokens=1484129   # Kumulierter Session-Verbrauch
total_input_tokens=…
total_cached_input_tokens=…
total_output_tokens=…
total_reasoning_output_tokens=…
total_tokens=…
total_blended=…
last_input_tokens=…
last_cached_input_tokens=…
last_output_tokens=…
last_reasoning_output_tokens=…
last_total_tokens=…
last_blended=…
rate_limit_id=codex
plan_type=prolite
primary_used_percent=10.0
primary_window_minutes=300
primary_resets_at=1778685248
secondary_used_percent=3.0
secondary_window_minutes=10080
secondary_resets_at=1779202534
---
```

Der Trenner `---` schließt einen Block ab — relevant im `--follow`-Modus,
wo mehrere Snapshots hintereinander geschrieben werden.

## Bash-Beispiele

Direkt-Auswertung per `eval` (nur Variablen ohne unsichere Zeichen):

```bash
eval "$(codex-tokens | grep -E '^(percent_left|tokens_in_context|context_window)=')"
echo "Frei:    ${percent_left}%"
echo "Belegt:  ${tokens_in_context} / ${context_window} Token"
```

Einzelner Wert via `awk`:

```bash
PCT=$(codex-tokens | awk -F= '/^percent_left=/ {print $2}')
echo "Noch $PCT % Kontext frei"
```

JSON-Pipeline mit `jq`:

```bash
codex-tokens --json | jq '{left: .percent_left, used: .tokens_in_context}'
```

Live-Logfile schreiben:

```bash
codex-tokens --follow --json >> ~/codex-tokens.log &
```

Spezifische Session per UUID (aus TUI ablesbar):

```bash
codex-tokens --thread 019e218d-4998-7183-9bb0-e48103c4fe30
```

An die Session im aktuellen Projektverzeichnis koppeln:

```bash
codex-tokens --cwd
```

## Wie wird die richtige Session erkannt?

Drei Modi, in dieser Priorität:

1. `--thread <UUID>` — Dateiname endet auf `-<UUID>.jsonl`. Deterministisch.
2. `--cwd [PATH]` — liest die erste Zeile (`session_meta`) jeder Rollout-Datei
   und nimmt die neueste, deren `cwd` zum angegebenen Pfad passt.
3. Ohne Option: die jüngste Datei nach Modifikationszeit. Bei mehreren
   parallel laufenden Codex-Sessions ggf. ambig — dann lieber `--thread`,
   `--cwd` oder den Multi-Session-Modus
   ([siehe oben](#mehrere-codex-sessions-im-selben-verzeichnis))
   verwenden.

## Display-App (Statusfenster ohne Browser-Chrome)

Unter `display/` liegt ein kleines Statusfenster, das die laufenden
Codex-Sessions eines Projektverzeichnisses in Echtzeit anzeigt — pro
Session eine eigene Zeile mit horizontalem Auslastungs-Balken (Verlauf
grün→gelb→rot), freier Token-Zahl, Idle-Timer und drei Action-Buttons.
Geschlossene Sessions verschwinden automatisch.

Architektur: ein Python-Helfer-Server (`server.py`, stdlib only)
spawnt `codex-tokens --cwd … --all --follow --watch-new --require-open
--json` als Subprozess und streamt die NDJSON-Snapshots per Server-Sent
Events an das Browserfenster (`index.html` + `app.css` + `app.js`).
Der Browser läuft im `--app=`-Modus ohne URL-Leiste und sieht aus wie
eine native App.

### Voraussetzungen

- `python3` (Standard auf Linux/WSL2)
- ein Chromium-basierter Browser (`chromium`, `google-chrome`, `brave-browser`, `microsoft-edge`)
- `codex-tokens` installiert (siehe oben)
- für die Action-Buttons:
  - `xdg-open` (in jedem Linux-Desktop dabei) — für „📁 Verzeichnis öffnen"
  - `wmctrl` (`sudo apt install wmctrl`) — für „⚡ Terminal fokussieren"
  - `xclip` oder `wl-clipboard` — für „↻ Rollover-Prompt kopieren" und Session-ID-Klick

### Starten

**System-weit** — alle Codex-Sessions auf dem Rechner:

```bash
cd /pfad/zu/codex-token-monitor
./display/launcher.sh
```

**Auf ein Projektverzeichnis eingegrenzt:**

```bash
./display/launcher.sh /pfad/zu/deinem-projekt
```

Es öffnet sich ein schmales Fenster (~520×640 px), das alle aktiven
Codex-Sessions auflistet. Im system-weiten Modus zeigt jede Karte den
zugehörigen `cwd` an, sodass du Sessions verschiedener Projekte
unterscheiden kannst. Schließe das Fenster oder drücke `Strg+C` im
Launcher-Terminal, um sauber zu beenden.

Optional ein anderer Port:

```bash
./display/launcher.sh --port 8888
./display/launcher.sh /pfad/zu/deinem-projekt --port 8888
```

### UI im Überblick

```text
┌──────────────────────────────────────────────────────────────────┐
│ ▮▮▮▮▮▮▮▮░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  198 k frei    │ ← horizontaler Bar
│ codex-token-monitor/tmp                 ● ⌚ 0:42                │   (grün→gelb→rot)
│ 019e315c-c8be · 30 k / 258 k · Σ 67 k    [📁] [⚡] [↻]           │
└──────────────────────────────────────────────────────────────────┘
```

- **Balken oben**: Füllung zeigt verbrauchten Kontext, Farbe fließend
  grün → gelb → rot. Hover-Tooltip zeigt vollständige Token-Werte.
- **Statuspunkt grün/grau**: Codex-Prozess aktiv vs. beendet (per
  `/proc`-Check). Beendete Sessions werden ausgeblendet.
- **⌚ Idle-Timer**: Sekunden/Minuten seit dem letzten TokenCount-Event.
- **Session-ID (gekürzt)**: Klick kopiert die vollständige UUID.
- **📁** öffnet das `session_cwd` im Dateimanager (`xdg-open`).
- **⚡** holt das passende Terminal-/IDE-Fenster nach vorne (per `wmctrl -lx`,
  bevorzugt IDE-Klassen wie VS Code / Cursor / JetBrains, schließt
  Dateimanager-Klassen explizit aus).
- **📋** kopiert den absoluten Pfad zur Rollout-Datei
  (`~/.codex/sessions/.../rollout-*.jsonl`) in die Zwischenablage.
- **↻** kopiert einen Handover-Prompt in die Zwischenablage, den du in
  Codex einfügen kannst, um einen sauberen Session-Rollover auszulösen.

In der Kopfzeile öffnet **📖** ein Modal mit dieser Bedienungsanleitung —
gerendert direkt aus `display/README.md` mit `marked.js`.

### Anpassung

- **Custom Handover-Prompt**: in `display/static/app.js` die Funktion
  `rolloverPrompt(snap)` editieren.
- **Farbverlauf des Balkens**: in `display/static/app.css` der Selektor
  `.bar__fill` mit dem `linear-gradient(...)`.
- **Hilfe-Text**: einfach `display/README.md` editieren — wird beim
  nächsten 📖-Klick neu gerendert (oder im Fenster `Strg+Shift+R`).
- **Mehrere Projekte gleichzeitig**: einfach mehrere `launcher.sh`-
  Instanzen mit verschiedenen `--port`-Werten starten.

## Portabilität und statischer Build

Der Standard-Build mit `cargo build --release` erzeugt ein dynamisch gegen
glibc gelinktes Binary. Es läuft auf jedem **x86_64 Linux** mit gleicher
oder neuerer glibc als das Build-System (typisch: alle aktuellen Distros,
auch WSL2 mit Standard-Ubuntu). Auf älteren Systemen (Ubuntu 20.04, CentOS
7), auf Alpine/musl-Distros oder unter ARM funktioniert es so nicht.

### Statisch verlinktes Binary für maximale Portabilität

Da das Tool reines Rust ohne C-Abhängigkeiten ist, lässt sich mit dem
musl-Target ein **vollständig statisches** Binary bauen, das auf praktisch
jedem x86_64-Linux läuft — unabhängig von glibc-Version, Distribution und
auch in minimalen Containern wie `scratch` oder Alpine.

```bash
# Einmalig: musl-Target installieren
rustup target add x86_64-unknown-linux-musl

# Statisches Binary bauen
cargo build --release --target x86_64-unknown-linux-musl

# Ergebnis (komplett portabel, ~1 MB):
# target/x86_64-unknown-linux-musl/release/codex-tokens
```

Auf manchen Distros (Ubuntu, Debian) wird zusätzlich das System-Paket
`musl-tools` benötigt:

```bash
sudo apt install musl-tools     # Debian/Ubuntu
sudo dnf install musl-gcc       # Fedora
```

Test, dass das Resultat tatsächlich statisch ist:

```bash
file target/x86_64-unknown-linux-musl/release/codex-tokens
# → "statically linked"
ldd  target/x86_64-unknown-linux-musl/release/codex-tokens
# → "not a dynamic executable"
```

### Andere Architekturen

| Zielsystem            | Target-Triple                     |
|-----------------------|-----------------------------------|
| x86_64 Linux glibc    | `x86_64-unknown-linux-gnu`        |
| x86_64 Linux musl     | `x86_64-unknown-linux-musl`       |
| ARM64 Linux (RPi 4+)  | `aarch64-unknown-linux-gnu`       |
| ARM64 Linux musl      | `aarch64-unknown-linux-musl`      |

Cross-Kompilation funktioniert mit `rustup target add <triple>` plus den
passenden Linker (z. B. `gcc-aarch64-linux-gnu`). Für die meisten Fälle ist
die musl-Variante x86_64 die richtige Wahl.

### Was sicher **nicht** geht

- Direkt auf **Windows** (außer in WSL2) — Windows-Native bräuchte einen
  Windows-Build (`x86_64-pc-windows-gnu`/`-msvc`).
- Auf **macOS** — bräuchte einen macOS-Build.

Beides ginge prinzipiell, aber Codex selbst läuft typischerweise unter
Linux/WSL2/macOS, daher ist dort dann auch das passende Build-Target
anzuziehen.

## Robustheit gegenüber Codex-Updates

Die Datenstrukturen in `src/protocol.rs` sind ein minimaler Serde-Spiegel der
JSON-Form auf Disk. Codex schreibt diese Form sowohl in Rollout-Dateien als
auch über sein App-Server-Protokoll (TS-/JsonSchema-exportiert) und hält sie
bewusst stabil. Solange die Feldnamen `total_token_usage`, `last_token_usage`,
`model_context_window`, `input_tokens`, `cached_input_tokens`, `output_tokens`,
`reasoning_output_tokens`, `total_tokens`, `rate_limits` bestehen, läuft das
Tool ohne Anpassung weiter — neue/zusätzliche Felder werden ignoriert.
