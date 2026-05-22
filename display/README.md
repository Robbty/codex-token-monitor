# Codex Token Display — Bedienung

Diese Anleitung beschreibt das **Statusfenster**, das du gerade vor dir
hast: pro laufender Codex-Session eine Karte mit Auslastungs-Balken,
Token-Werten, Idle-Timer und vier Action-Buttons.

Für die **CLI-Bedienung von `codex-tokens`** (Pipelines, tmux, Skripte,
Cronjobs) sowie für Build- und Installations-Anleitungen → siehe die
**[Projekt-README öffnen](/help?doc=main)** (auch über den 📚-Button
oben in dieser Toolbar erreichbar; sie öffnet in einem zweiten Fenster).

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
│ ▮▮▮▮░░░░░░░░░░░░░░░░░░░░  198 k / 78% frei              │  ← Balken oben
│ codex-token-monitor/tmp         ● ⌚ 0:42                │  ← cwd · Status · Idle
│ 019e315c-c8be · 30k / 258k · Σ 67k  [📁] [⚡] [📋] [↻]  │  ← ID · Verbrauch · Buttons
╰──────────────────────────────────────────────────────────╯
```

### Balken (oben)

- **Füllgrad** entspricht dem prozentualen Kontextverbrauch.
- **Farbe** ist *positional* mit vier Stützpunkten: grün → gelb → rot → violett.
  Der linkeste Pixel bleibt immer grün; neue Pixel beim Wachsen nehmen die
  Farbe ihrer X-Position.
- **Hover-Tooltip** zeigt alle Werte: Token verbraucht, frei,
  Kontextfenster gesamt, Prozentangaben.

### Statuspunkt (●)

- **Grün** — Codex-Prozess läuft, Datei wird aktiv geschrieben.
- **Grau** — Codex-Session beendet, letzter Stand eingefroren (Karte
  verschwindet innerhalb von 3 Sekunden).
- **Gelb** — Status unbekannt (kein Linux/WSL2, oder `/proc` nicht lesbar).

### Idle-Timer (⌚)

Sekunden, Minuten oder Stunden seit der letzten Aktivität in dieser Session.

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
| **198 k / 78 % frei** | Aktuell freie Token absolut + gleichbedeutende Prozentangabe |
| **30 k / 258 k** | Aktuell belegt / Kontextfenster-Größe insgesamt |
| **Σ 67 k** | Kumulierter Token-Verbrauch über die gesamte Session |

Als Faustregel bei ~80 % Verbrauch (= 20 % frei) den ↻-Button drücken,
Handover in Codex einfügen, neue Session starten.

## Worker-Schalter

Die **Codex-Desktop-App** spawnt beim Start eine Reihe Background-Helper
(Originator `Codex Desktop`). Diese verbrauchen Token, melden aber **kein
Kontextfenster** zurück — eine Prozent-/Frei-Anzeige ist daher nicht möglich.
Standardmäßig sind sie ausgeblendet; das Zählerlabel zeigt sie als
`X aktiv (+Y Worker)`.

Schiebe den **Worker-Schalter** in der Kopfzeile um, um sie einzublenden:

- Worker-Karten erscheinen unter den echten Sessions
- Ihr Balken bleibt leer (kein Kontextfenster)
- Im Balken-Label steht stattdessen `X verbraucht · Worker (kein Kontextfenster)`
- Sortierung der Worker: nach kumuliertem Verbrauch absteigend

Der Schalter ist nützlich für die **Kostenübersicht**: alle Worker-Tokens
zählen auf dasselbe Rate-Limit / Budget wie echte Chats.

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

- **Custom Handover-Prompt**: in `display/static/app.js` die Funktion
  `rolloverPrompt(snap)` editieren.
- **Farbverlauf des Balkens**: in `display/static/app.css` der Selektor
  `.bar__fill` mit dem `linear-gradient(...)`.
- **Hilfe-Text**: einfach `display/README.md` editieren — wird beim
  nächsten 📖-Klick neu gerendert.
- **Mehrere Projekte gleichzeitig**: einfach mehrere `launcher.sh`-
  Instanzen mit verschiedenen `--port`-Werten starten.

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

Vollständige Doku (Install, Build, CLI-Bedienung, Releases, Portabilität):
**[Projekt-README öffnen](/help?doc=main)**
