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

## Plan-Anzeige (kontoseitige Rate-Limits)

Unter der Topbar erscheint ein **Plan-Widget**, das deine
ChatGPT-Plan-Ressourcen einblendet — die rollierenden Zeitfenster, die
sich unabhängig von der einzelnen Session füllen und leeren. Die Daten
sind dieselben, die du auf
<https://chatgpt.com/codex/cloud/settings/analytics> siehst, aber **vor
Ort** statt im Browser-Tab und ohne dass du dich umorientieren musst.

**Standardmäßig deaktiviert** — die Funktion liest das in
`~/.codex/auth.json` hinterlegte OAuth-Token und schickt es als
`Bearer`-Header an einen ChatGPT-Backend-Endpoint. Wer das nicht
möchte, lässt das Widget einfach aus; der Rest des Displays
funktioniert unverändert.

### Datenfluss

```text
┌──────────────┐         ┌────────────────┐         ┌─────────────────────┐
│   Browser    │ /plan   │ display/       │ spawn   │  codex-tokens plan  │
│   (app.js)   │ ──────► │ server.py      │ ──────► │  (Rust, ureq +      │
│   poll 60 s  │ ◄────── │  60 s Cache    │ ◄────── │   rustls)           │
└──────────────┘   JSON  └────────────────┘  stdout └──────────┬──────────┘
                                                              │ HTTPS GET
                                                              ▼
                              ┌────────────────────────────────────────────┐
                              │ chatgpt.com/backend-api/wham/usage         │
                              │ Authorization: Bearer <access_token>       │
                              └─────────────────┬──────────────────────────┘
                                                │ liest aus
                                                ▼
                                       ~/.codex/auth.json
                                       (von Codex selbst angelegt
                                        und aktualisiert)
```

Der HTTPS-Aufruf passiert **nur** in `codex-tokens plan`, gestartet von
der Display-App. Der Server.py-Prozess kennt das Token nicht direkt —
er liest nur den stdout-JSON des Rust-Subcommand und cached ihn 60 s.

### Erste Aktivierung: Consent-Modal

Beim **ersten** Anklicken des Schalters „Plan-Tracking aktivieren"
poppt ein Modal mit dem genauen Datenfluss und der Endpoint-URL.
Erst nach „Verstanden, aktivieren" werden gleichzeitig zwei Werte in
die Config geschrieben:

- `enabled: true` — Widget eingeschaltet
- `consent_acknowledged: true` — Modal beim nächsten Aktivieren überspringen

Beim Klick auf „Abbrechen" (oder Esc / Backdrop-Klick) bleibt der
Schalter aus, es wird nichts gespeichert, keine HTTPS-Anfrage gestellt.
Der Fokus liegt absichtlich auf „Abbrechen" — versehentliches Enter ist
harmlos.

Wenn du den Consent-Status zurücksetzen willst (z.B. um das Modal noch
einmal zu sehen):

```bash
rm ~/.config/codex-token-monitor/config.json
```

### Konfigurations-Datei

```json
{
  "plan_widget": {
    "enabled": false,
    "consent_acknowledged": false,
    "rows": {
      "main":        true,
      "codex_spark": false,
      "code_review": false,
      "credits":     false
    }
  }
}
```

Liegt in `~/.config/codex-token-monitor/config.json` (oder
`$XDG_CONFIG_HOME/codex-token-monitor/config.json`, falls gesetzt).
Vom Server atomic geschrieben (über `.json.tmp` + `rename`), damit ein
gleichzeitiger Lese-Aufruf nie eine halbe Datei sieht.

### Die vier Zeilen

Über das **⚙-Icon** in der Topbar öffnet sich ein Popover mit den
Row-Toggles. Jede aktivierte Zeile rendert eigenständig:

| Zeile | Quelle in der API-Response | Default | Anzeige |
|---|---|---|---|
| **Plan** | `rate_limit.{primary,secondary}_window` | **an** | 5h + 7d Balken + `Plan · <plan_type>` Label |
| **Codex-Spark** | `additional_rate_limits[…].rate_limit` mit `limit_name == "GPT-5.3-Codex-Spark"` | aus | 5h + 7d Balken |
| **Code-Review** | `code_review_rate_limit` | aus | 5h + 7d Balken (nur sichtbar wenn ≠ `null`) |
| **Credits** | `credits` | aus | Text-Zeile (kein Balken — kein sinnvoller Nenner) |

### Reset-Countdown und Tooltip

Pro Balken steht rechts ein Block wie `27% · ⌛ 1 h 11 min`. Lesart:

- **27%** — verbraucht im aktuellen Zeitfenster
- **⌛ 1 h 11 min** — Restzeit bis das Fenster auf 0 % zurückspringt

Das `⌛`-Symbol ist bewusst nicht das `↻` der Session-Karten (das
bedeutet dort *„Kontext wurde verdichtet"*) — sind zwei verschiedene
Konzepte und sollen optisch nicht verwechselbar sein.

Der **Tooltip** auf der Zahlen-Spalte zeigt zusätzlich Fenster-Länge
und exaktes Reset-Datum:

```text
27% verbraucht
Fenster: 5 h
Reset in 1 h 11 min (02.06.2026, 13:34:22)
```

### Credits-Zeile

```text
Credits  Balance: 0  ·  ≈ 0 lokale msg  ·  ≈ 0 cloud msg
```

`approx_*_messages` ist eine Schätz-Spanne („deine X Credits reichen
für ungefähr Y–Z Nachrichten dieses Typs"). Bei `unlimited: true`
steht `∞` statt der Balance, bei `overage_limit_reached: true` wird
die Zeile rot.

### Neue Premium-Features (dynamische Zeilen)

Wenn OpenAI später neue Einträge unter `additional_rate_limits`
ausrollt (z.B. `GPT-6.0-Codex-Premium`), erkennt das Widget diese
automatisch:

1. `limit_name` wird normalisiert: `GPT-X.Y-Foo-Bar` → `foo_bar`
2. Neuer Toggle erscheint im ⚙-Popover, **default aus**
3. Wird beim nächsten POST `/settings` mit persistiert

So überrumpelt dich kein neues OpenAI-Feature mit einer plötzlich
sichtbaren Zeile — du musst es explizit einschalten.

### Antwort-Schema (Auszug)

Die Felder, die das Widget rendert:

```jsonc
{
  "plan_type": "prolite",                    // freier Text vom Backend
  "rate_limit": {
    "primary_window":   { "used_percent": 27, "limit_window_seconds": 18000,  "reset_at": 1780406150 },
    "secondary_window": { "used_percent": 18, "limit_window_seconds": 604800, "reset_at": 1780903122 }
  },
  "additional_rate_limits": [
    {
      "limit_name": "GPT-5.3-Codex-Spark",
      "metered_feature": "codex_bengalfox",
      "rate_limit": { /* gleiche Struktur wie oben */ }
    }
  ],
  "code_review_rate_limit": null,            // oder gleiches Schema wie rate_limit
  "credits": {
    "balance": "0", "has_credits": false, "unlimited": false,
    "overage_limit_reached": false,
    "approx_local_messages": [0, 0],         // [low, high] Schätzspanne
    "approx_cloud_messages": [0, 0]
  }
}
```

Zeitstempel sind Unix-Sekunden (UTC). Unbekannte zusätzliche Felder
werden ignoriert.

### Token-Refresh

Das `access_token` in `~/.codex/auth.json` ist ein JWT mit kurzer
Lebensdauer (typisch **~60 Min**). Codex selbst refresht es bei jeder
aktiven Nutzung — der Display-Server liest die Datei einfach **bei
jedem Plan-Call frisch ein**.

Solange du Codex regelmäßig benutzt, ist der Token frisch. Wenn du
**längere Zeit untätig warst**, kann der Token abgelaufen sein. Symptom
im Widget: rote Zeile *„Token abgelaufen — Codex starten, damit der
Token refresht wird"*. Lösung: ein einziger Befehl in Codex (auch ein
`!ls`) löst einen Refresh aus, der `auth.json` aktualisiert.

Ein automatischer Refresh-Pfad innerhalb des Tools wäre möglich (über
den OAuth-`refresh_token`-Endpoint von OpenAI), ist aber bewusst nicht
implementiert: er würde uns in Auth-State-Konflikte mit gleichzeitig
laufendem Codex bringen.

### Fehlerzustände

| Status-Code (Backend → UI) | Anzeige | Bedeutung |
|---|---|---|
| (keiner, Erstaufruf) | Plan-Daten werden geladen … | Erste Abfrage läuft (max. 10 s Timeout) |
| `disabled` | (Widget bleibt versteckt) | Opt-in ist aus |
| `auth_missing` | Nicht eingeloggt — Codex starten und einloggen. | `~/.codex/auth.json` fehlt oder enthält kein `tokens.access_token` |
| `token_expired` | Token abgelaufen — Codex starten, damit der Token refresht wird. | HTTP 401/403 vom Backend (siehe „Token-Refresh") |
| `network` | Netzwerk-Problem beim Abrufen der Plan-Daten. | chatgpt.com unerreichbar / TLS-Fehler / 5xx |
| `timeout` | Timeout beim Abrufen der Plan-Daten. | HTTPS-Call dauerte > 10 s |
| `binary_missing` | codex-tokens-Binary nicht gefunden. | Server konnte das Binary nicht spawnen (PATH-Problem) |
| `binary_outdated` | codex-tokens ist zu alt für 'plan' — neue Version installieren. | Vorhandenes Binary kennt das `plan`-Subcommand noch nicht |
| `no_output` | codex-tokens lieferte keine Ausgabe. | Subprocess hat leeren stdout produziert (z.B. Crash) |
| `bad_response` | Unverständliche Antwort vom Plan-Endpoint. | Antwort war kein gültiges JSON |
| `fetch_failed` | Verbindung zum Hilfs-Server unterbrochen. | Browser konnte `/plan` nicht erreichen (Server gestoppt?) |

### Aktualisierungsfrequenz

- **Browser pollt** alle 60 s
- **Server cached** 60 s

Die Zahlen ändern sich ohnehin nur, wenn du eine Codex-Anfrage stellst
— häufigeres Pollen würde nur das Rate-Limit der Plan-Abfrage selbst
belasten (`wham/usage` zählt natürlich auch).

Im UI tickt der Reset-Countdown alle 30 s selbständig herunter
(reines Client-Rendering, kein zusätzlicher Backend-Call).

### Privacy-FAQ

**Wer sieht mein Auth-Token?**
- Der lokale Display-Server (`server.py`) liest die Datei nicht selbst —
  er ruft nur `codex-tokens plan` auf. Der Rust-Subcommand liest
  `auth.json`, baut den Authorization-Header und schickt ihn an
  chatgpt.com. Sonst niemand.

**Wird das Token in Logs geschrieben?**
- Nein. Der Server loggt nur den HTTP-Pfad (`GET /plan`), nicht den
  Subprocess-Output. Der Rust-Subcommand gibt das Token nicht aus —
  bei einem `token_expired`-Fehler nur den abstrakten Status-Code.

**Was passiert mit der API-Antwort?**
- 60 s im Server-RAM gecacht, nichts auf Disk geschrieben. Beim
  Server-Stop ist der Cache weg.

**Welche Daten gehen wohin?**
- HTTPS-Header → chatgpt.com (Token, User-Agent `codex-token-monitor/X.Y.Z`)
- Antwort → in deinem Browser, im Display-RAM. Nirgendwo sonst.

**Ist das DSGVO-relevant?**
- Es kommen Account-/E-Mail-Daten in der Antwort vor. Sie bleiben aber
  ausschließlich auf deinem Rechner. Das Tool macht selbst kein
  Tracking, keine Telemetrie und keinen Outbound an Dritte.

**Was, wenn ich das Vertrauen wieder entziehen will?**
- ⚙ → „Plan-Tracking aktivieren" abschalten → Polling stoppt sofort.
  Optional: Config-Datei löschen.

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
