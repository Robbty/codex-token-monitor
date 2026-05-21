# Codex Token Display — Bedienung

Ein schlankes Statusfenster, das laufende Codex-Sessions in Echtzeit anzeigt.
Pro Session eine Karte mit horizontalem Auslastungs-Balken (grün → gelb → rot),
verbrauchten und freien Token, Idle-Timer und vier Action-Buttons.

## Starten

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

## Hilfe und Bugs

Issues und Pull Requests: <https://github.com/Robbty/codex-token-monitor>
