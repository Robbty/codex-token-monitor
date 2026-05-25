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

### Desktop-Icon (zum Anklicken)

Ein Skript legt einen Menü-Eintrag und ein anklickbares Desktop-Symbol an:

```bash
./display/install-desktop.sh                 # system-weit
./display/install-desktop.sh --cwd /pfad     # auf ein Projekt eingegrenzt
./display/install-desktop.sh --no-desktop    # nur Anwendungsmenü, kein Icon
```

Es erzeugt `codex-token-monitor.desktop` in `~/.local/share/applications/`
(Anwendungsmenü) und auf dem Desktop, mit dem mitgelieferten `icon.png`.
Pfade werden dynamisch ermittelt — bei verschobenem Repo das Skript
einfach erneut ausführen.

Beim ersten Doppelklick verlangen XFCE/GNOME ggf. eine Bestätigung
(Rechtsklick → „Diese Datei ausführbar machen" / „Allow Launching").
Das Skript markiert das Icon bereits als vertrauenswürdig
(`gio … metadata::trusted true`), was die Nachfrage in den meisten
Fällen erübrigt.

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
| **Σ 67 k** | Kumulierter Token-Verbrauch über die gesamte Session, inklusive aller Compact-Vorgänge |
| **↻ 2×** | (Badge, nur sichtbar wenn > 0) Wie oft Codex den Kontext bereits zusammengefasst hat — entweder automatisch (Limit erreicht) oder über `/compact` in der TUI. Die Σ-Summe enthält auch die Token, die für diese Compaction-Turns verbraucht wurden. |

Als Faustregel bei ~80 % Verbrauch (= 20 % frei) den ↻-Button drücken,
Handover in Codex einfügen, neue Session starten. Wenn Codex stattdessen
selbst auto-compact, wandert der ↻-Zähler eins hoch.

## Worker-Schalter

Die **Codex-Desktop-App** spawnt beim Start eine Reihe Background-Helper
(Originator `Codex Desktop`). Diese verbrauchen Token, melden aber **kein
Kontextfenster** zurück. Standardmäßig sind sie ausgeblendet; das
Zählerlabel zeigt sie als `X aktiv (+Y Worker)`.

Schiebe den **Worker-Schalter** in der Kopfzeile um, um sie einzublenden:

- Worker-Karten erscheinen unter den echten Sessions
- Ihr Balken füllt sich basierend auf einem **geschätzten** Kontextfenster:
  zuerst wird der Wert einer parallel laufenden echten Session übernommen
  (gleiches Codex, gleiches Modell → gleiche Größe); falls keine echte
  Session läuft, ist der Default `258400` (Codex-/GPT-5-Standard).
- Hover-Tooltip auf der Bar markiert die Schätzung klar:
  „Kontextfenster nicht gemeldet, angenommen 258.400"
- Der `cwd` der Worker-Zeile ist gestrichelt unterstrichen — kleines
  visuelles Zeichen, dass die Werte einer Annahme unterliegen
- Sortierung der Worker: nach kumuliertem Verbrauch absteigend

Der Schalter ist nützlich für die **Kostenübersicht**: alle Worker-Tokens
zählen auf dasselbe Rate-Limit / Budget wie echte Chats. Falls du
hauptsächlich ein Modell mit anderer Kontextgröße verwendest, kannst du
den Fallback-Default in `display/static/app.js` (Konstante
`ASSUMED_CONTEXT_WINDOW`) anpassen.

## Verbindungs-Indikator (oben rechts)

Der kleine farbige Punkt rechts in der Kopfzeile:

- **Grün** — verbunden mit dem lokalen Helfer-Server
- **Rot** — keine Verbindung (Server gestoppt? Hard-Reload mit `Strg+Shift+R`)

## System-weit vs. eingegrenzt

In der Kopfzeile steht entweder

- **„Alle Projekte"** — system-weiter Modus, alle Codex-Sessions auf dem Rechner werden gelistet
- **Der absolute Pfad** — eingegrenzter Modus, nur eine Session, die in genau diesem Verzeichnis läuft

### Sortierung der Karten

Von oben nach unten:

1. **Echte Sessions** vor Worker-Sessions
2. Innerhalb der echten Sessions: zuerst nach **`compact_count` absteigend**
   (jede Kontext-Komprimierung kostet einen kompletten Turn → höchster
   bisheriger „Aufwand" steht oben)
3. Bei gleichem `compact_count`: nach **`percent_used` absteigend** (der
   gerade vollste Balken oben)
4. Worker am Ende, sortiert nach kumuliertem Verbrauch absteigend

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
