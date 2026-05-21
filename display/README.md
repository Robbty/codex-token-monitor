# Codex Token Monitor — Bedienung

Dieses Tool hat zwei Komponenten:

1. **`codex-tokens`** — ein CLI, das aus den Rollout-Dateien einer laufenden
   Codex-Session live den Token- und Kontext-Stand ausliest und auf stdout
   ausgibt. Geeignet für Bash-/jq-Pipelines, Statuszeilen, Skripte.
2. **Display-App** (`./display/launcher.sh`) — ein schlankes Statusfenster,
   das `codex-tokens` als Subprozess fährt und die Sessions als Karten
   visualisiert. Geeignet zum „über die Schulter schauen", während Codex
   arbeitet.

Diese Anleitung beschreibt beide Komponenten.

---

## Display-App starten

```bash
# System-weit: alle laufenden Codex-Sessions auf dem Rechner
./display/launcher.sh

# Eingegrenzt: nur Sessions in einem Projektverzeichnis
./display/launcher.sh /pfad/zu/deinem-projekt

# Anderer Port (Default: 8765)
./display/launcher.sh --port 8888
```

Beenden: Fenster schließen oder im Launcher-Terminal `Strg+C`.

## Die Karte im Detail

```text
╭──────────────────────────────────────────────────────────╮
│ ▮▮▮▮░░░░░░░░░░░░░░░░░░░░  30 k verbraucht · 198 k frei  │  ← Balken oben
│ codex-token-monitor/tmp         ● ⌚ 0:42                │  ← cwd · Status · Idle
│ 019e315c-c8be · 30k / 258k · Σ 67k  [📁] [⚡] [📋] [↻]  │  ← ID · Verbrauch · Buttons
╰──────────────────────────────────────────────────────────╯
```

### Balken (oben)

- **Füllgrad** entspricht dem prozentualen Kontextverbrauch.
- **Farbe** ist *positional*: der linkeste Pixel bleibt immer grün, neue Pixel
  beim Wachsen nehmen die Farbe ihrer X-Position (grün → gelbgrün → gelb →
  orange → rot).
- **Hover-Tooltip** zeigt alle Werte aus: Token verbraucht, frei, Kontextfenster
  gesamt, Prozentangaben.

### Statuspunkt (●)

- **Grün** — Codex-Prozess läuft, Datei wird aktiv geschrieben.
- **Grau** — Codex-Session beendet, letzter Stand eingefroren (Karte
  verschwindet innerhalb von 3 Sekunden).
- **Gelb** — Status unbekannt (kein Linux/WSL2, oder `/proc` nicht lesbar).

### Idle-Timer (⌚)

Sekunden, Minuten oder Stunden seit der letzten Aktivität in dieser Session.
Verlangsamt sich, je länger Codex keinen neuen Turn macht.

### Session-ID (klickbar)

Die gekürzte UUID. **Klick kopiert die vollständige Session-ID** in die
Zwischenablage — praktisch für `codex-tokens --thread <UUID>`.

### Action-Buttons

| Button | Aktion |
|---|---|
| **📁** | Öffnet `session_cwd` im Dateimanager (`xdg-open`) |
| **⚡** | Holt das passende IDE-/Terminal-Fenster nach vorne (`wmctrl`) — bevorzugt VS Code, Cursor, JetBrains usw.; ignoriert Dateimanager |
| **📋** | Kopiert den absoluten Pfad zur Rollout-Datei (`~/.codex/sessions/…/rollout-*.jsonl`) in die Zwischenablage |
| **↻** | Kopiert einen Handover-Prompt in die Zwischenablage — in Codex einfügen für einen sauberen Session-Rollover |

## Bedeutung der Zahlen

| Wert | Bedeutung |
|---|---|
| **verbraucht / frei** | Aktuelle Belegung des Kontextfensters in Token |
| **30 k / 258 k** | Aktuell belegt / Kontextfenster-Größe insgesamt |
| **Σ 67 k** | Kumulierter Token-Verbrauch über die gesamte Session (alle Turns aufsummiert) |

Die Schwelle zum Rollover hängt vom Modell ab — als Faustregel bei ~80 % Verbrauch
Handover-Prompt auslösen, damit noch Platz für die Zusammenfassung bleibt.

## Verbindungs-Indikator (oben rechts)

Der kleine farbige Punkt rechts in der Kopfzeile:

- **Grün** — verbunden mit dem lokalen Helfer-Server
- **Rot** — keine Verbindung (Server gestoppt? Hard-Reload mit `Strg+Shift+R`)

## System-weit vs. eingegrenzt

In der Kopfzeile steht entweder

- **„Alle Projekte"** — system-weiter Modus, alle Codex-Sessions auf dem Rechner werden gelistet
- **Der absolute Pfad** — eingegrenzter Modus, nur eine Session, die in genau diesem Verzeichnis läuft

Sortierung: Sessions mit höchstem Verbrauch stehen oben.

## Anpassung

Die folgenden Stellen sind bewusst leicht editierbar:

- **Handover-Prompt** (Text für ↻-Button) — `display/static/app.js`,
  Funktion `rolloverPrompt(snap)`
- **Farbverlauf** des Balkens — `display/static/app.css`, Selektor `.bar__fill`,
  `linear-gradient(...)`
- **Idle-Schwelle / Polling-Intervall** — `display/server.py`,
  `_sweeper_thread` (Default: alle 3 s `/proc`-Check)

## Voraussetzungen

- `python3` (Standard auf jedem Linux/WSL2)
- ein Chromium-basierter Browser für das `--app`-Fenster
- `codex-tokens` (im PATH oder via `CODEX_TOKENS_BIN`-Variable)
- für die Buttons:
  - `xdg-open` (in jedem Linux-Desktop dabei)
  - `wmctrl` (`sudo apt install wmctrl`)
  - `xclip` oder `wl-clipboard` (`sudo apt install xclip`)

---

## codex-tokens im Terminal (CLI)

Die Display-App ist ein Frontend; die eigentliche Datenquelle ist
`codex-tokens`. Im Terminal lässt es sich direkt benutzen — gut für
Skripte, tmux-Statusleisten, Cronjobs oder schnelles Nachschauen ohne
Fenster zu öffnen.

### Grundaufruf

```bash
# Schnellblick auf die jüngste Session
codex-tokens

# Versionsinfo
codex-tokens --version

# Pfad zur Rollout-Datei, ohne die Session zu lesen
codex-tokens --locate
```

### Welche Session?

Drei Modi, in absteigender Priorität:

| Schalter | Bedeutung |
|---|---|
| `--thread <UUID>` | Konkrete Session per UUID (deterministisch) |
| `--cwd [PATH]` | Session im Verzeichnis PATH; ohne Argument: `$PWD` |
| *(ohne)* | Die zuletzt geschriebene Rollout-Datei systemweit |

Beispiel:

```bash
codex-tokens --cwd ~/projekte/meinprojekt
codex-tokens --thread 019e315c-c8be-7bc2-813a-6732f4305a9e
```

### Multi-Session: `--all`

Mehrere parallele Codex-Sessions gleichzeitig erfassen. Mit `--cwd`
eingegrenzt, ohne `--cwd` system-weit:

```bash
# System-weite Übersicht aller Codex-Sessions
codex-tokens --all

# Nur Sessions in einem Projekt
codex-tokens --cwd ~/projekte/meinprojekt --all
```

Jeder Block wird mit `=== session <uuid> ===` markiert.

### Filter

| Schalter | Wirkung |
|---|---|
| `--max-age MINUTES` | Default 5: Rollouts älter als N min gelten als nicht aktiv |
| `--require-open` | Nur Sessions, deren Rollout aktuell von einem Prozess **schreibend** offen gehalten wird. Linux/WSL2. Präziser als `--max-age` |

Für eine wirklich genaue „läuft noch?"-Anzeige beide kombinieren:

```bash
codex-tokens --all --require-open --max-age 999999
```

### Live-Beobachtung: `--follow` und `--watch-new`

```bash
# Token-Updates live mitlesen (Stream)
codex-tokens --follow

# Multi-Session live, neue Codex-Instanzen werden alle 5 s erkannt
codex-tokens --all --follow --watch-new
```

### Auf eine Session warten: `--wait`

Wenn der Monitor *vor* Codex startet:

```bash
codex-tokens --cwd ~/projekt --wait
codex-tokens --cwd ~/projekt --wait --wait-timeout 60   # Limit
```

### Ausgabeformate

**KEY=VALUE (Default)** — direkt von Bash auswertbar:

```text
session_id=019e315c-c8be-7bc2-813a-6732f4305a9e
session_cwd=/home/peter/projekte/foo
session_active=true
context_window=258400
percent_left=78
percent_used=22
tokens_in_context=56808
session_total_tokens=152334
total_input_tokens=149832
total_cached_input_tokens=128640
total_output_tokens=2502
total_reasoning_output_tokens=1380
last_input_tokens=23200
last_output_tokens=192
last_total_tokens=23392
rate_limit_id=codex
plan_type=prolite
primary_used_percent=12.0
primary_window_minutes=300
primary_resets_at=1778962639
secondary_used_percent=8.0
secondary_window_minutes=10080
secondary_resets_at=1779202534
---
```

Der Block schließt mit `---` ab; im `--all`-Modus startet er zusätzlich
mit `=== session <uuid> ===`.

**JSON** — als NDJSON (eine Zeile pro Session/Update), `jq`-tauglich:

```bash
codex-tokens --json
codex-tokens --all --json | jq '.percent_left'
```

### Bash-Rezepte

Direkt-Auswertung per `eval`:

```bash
eval "$(codex-tokens | grep -E '^(percent_left|tokens_in_context|context_window)=')"
echo "Frei:   ${percent_left}%"
echo "Belegt: ${tokens_in_context} / ${context_window} Token"
```

Schwellen-Alarm:

```bash
PCT=$(codex-tokens --cwd ~/projekt | awk -F= '/^percent_left=/ {print $2}')
if [ "$PCT" -lt 20 ]; then
  notify-send "Codex" "Nur noch ${PCT}% Kontext frei!"
fi
```

Aggregat aller aktiven Sessions:

```bash
codex-tokens --all --require-open --json \
  | jq -s 'map({id: .session_id, cwd: .session_cwd, frei: .percent_left})'
```

Genau eine Session aus dem Live-Stream filtern:

```bash
SID=019e315c-c8be-7bc2-813a-6732f4305a9e
codex-tokens --all --follow \
  | awk -v s="=== session $SID ===" '$0==s{p=1} p{print} p && /^---$/{p=0}'
```

### Aufruf innerhalb der Codex-TUI

In der Codex-TUI-Eingabezeile tippen:

```text
!codex-tokens --cwd
```

Das `!`-Präfix führt das Kommando direkt aus, kostet kein Modell-Token,
und die Ausgabe landet im Chat-Verlauf. **Nicht** `--follow` in der TUI
benutzen — das blockiert dauerhaft. Mit `Esc` ein hängendes Tool-Kommando
abbrechen, im Notfall zweimal `Strg+C` zum Beenden der TUI.

### Alle Schalter im Überblick

```text
codex-tokens [OPTIONS]

  --thread <UUID>       Konkrete Session
  --cwd [PATH]          Session per Arbeitsverzeichnis (Default: $PWD)
  --codex-home DIR      Überschreibt CODEX_HOME (Default: $CODEX_HOME oder ~/.codex)
  -f, --follow          Live-Stream statt einmaliger Snapshot
      --json            JSON statt KEY=VALUE
      --locate          Nur den Rollout-Pfad ausgeben und beenden
      --wait            Warten, falls keine Session da ist
      --wait-timeout SECS  Limit für --wait
      --all             Multi-Session (mit --cwd eingegrenzt, ohne --cwd system-weit)
      --max-age MINUTES Aktivitäts-Filter via mtime (Default 5)
      --watch-new       Neue Sessions live im Stream aufnehmen
      --require-open    Nur Sessions mit lebendem Schreib-Handle (/proc)
  -h, --help / -V, --version
```

---

## Hilfe und Bugs

Issues und Pull Requests: <https://github.com/Robbty/codex-token-monitor>
