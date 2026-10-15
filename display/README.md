# Codex Token Display — Bedienung

Diese Anleitung beschreibt das **Statusfenster**, das du gerade vor dir
hast: pro laufender Codex-Session eine Karte mit Auslastungs-Balken,
Token-Werten, Modell, Arbeitsfläche, Idle-Timer und Action-Buttons — dazu
das Fenster **🕘 Letzte Chats** und Karten für **Geister-TUIs**.

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

Der Launcher findet `codex-tokens` automatisch im `$PATH` oder unter
`../target/release/`. Über `CODEX_TOKENS_BIN=/pfad/zu/codex-tokens` lässt sich
ein konkretes Binary erzwingen, über `CODEX_BIN=/pfad/zu/codex` das
`codex`-Programm, das 🗑 in „Letzte Chats" benutzt.

### Desktop-Icon (zum Anklicken)

Ein Skript legt einen Menü-Eintrag und ein anklickbares Desktop-Symbol an:

```bash
./display/install-desktop.sh                 # system-weit
./display/install-desktop.sh --cwd /pfad     # auf ein Projekt eingegrenzt
./display/install-desktop.sh --no-desktop    # nur Anwendungsmenü, kein Icon
```

Es erzeugt `codex-token-monitor.desktop` in `~/.local/share/applications/`
(Anwendungsmenü) und auf dem Desktop, mit dem mitgelieferten `icon.png`.
Pfade werden dynamisch ermittelt und `codex-tokens` sowie `codex` fest in die
`Exec`-Zeile geschrieben (`CODEX_TOKENS_BIN`, `CODEX_BIN`), damit der Start
auch aus einer minimalen Desktop-Session-`PATH` klappt (etwa wenn Codex über
nvm installiert ist) — bei verschobenem Repo oder neu installiertem Codex das
Skript einfach erneut ausführen.

Beim ersten Doppelklick verlangen XFCE/GNOME ggf. eine Bestätigung
(Rechtsklick → „Diese Datei ausführbar machen" / „Allow Launching").
Das Skript markiert das Icon bereits als vertrauenswürdig
(`gio … metadata::trusted true`), was die Nachfrage in den meisten
Fällen erübrigt.

## Die Karte im Detail

```text
╭───────────────────────────────────────────────────────────────────╮
│ ▮▮▮▮░░░░░░░░░░░░░░░░░░░░  198 k / 78% frei                       │  ← Balken oben
│ Allgemeines · ~/projekte/xy  (GPT-6 Astra · high)  ● ⌚ 0:42      │  ← Arbeitsfläche · cwd · Modell · Status · Idle
│ 019e315c-c8be · 30k / 258k · Σ 67k   [📁] [⚡] [📋] [↻] [🛑]      │  ← ID · Verbrauch · Buttons
╰───────────────────────────────────────────────────────────────────╯
```

### Arbeitsfläche · Pfad

Der Projektpfad wird mit `~` abgekürzt (Tooltip zeigt den vollen Pfad). Davor
steht — gedimmt — der **Name der Arbeitsfläche**, auf der das Terminal der
Session liegt. Ermittelt wird das Fenster wie beim ⚡-Knopf (siehe
[Wie der Monitor Sessions ihrem Terminal zuordnet](#wie-der-monitor-sessions-ihrem-terminal-zuordnet))
und die Arbeitsfläche über `wmctrl -d`. Kein Präfix erscheint, wenn `wmctrl`
fehlt oder kein Fenster zuzuordnen ist (z. B. Codex im IDE-Terminal).

### Modell · Effort

Rechts neben dem Pfad steht ein kleines Badge mit dem **Modell** des letzten
Turns (z. B. „GPT-6 Astra" für `gpt-6-astra`) und dem **Reasoning-Effort**
(`low`/`medium`/`high`/`xhigh`). Der Tooltip nennt die vollständige Modell-ID.
Wechselst du in Codex mit `/model` das Modell, zieht das Badge beim nächsten
Turn nach. Sessions, die noch keinen Turn hatten, zeigen kein Badge.

### Balken (oben)

- **Füllgrad** entspricht dem prozentualen Kontextverbrauch.
- **Farbe** ist *positional* mit vier Stützpunkten: grün → gelb → rot → violett.
  Der linkeste Pixel bleibt immer grün; neue Pixel beim Wachsen nehmen die
  Farbe ihrer X-Position.
- **Hover-Tooltip** zeigt alle Werte: Token verbraucht, frei,
  Kontextfenster gesamt, Prozentangaben.

### Statuspunkt (●)

- **Grün** — die Rollout-Datei der Session ist zum Schreiben geöffnet, die
  Session läuft.
- **Grau** — Codex-Session beendet, letzter Stand eingefroren (Karte
  verschwindet innerhalb von 3 Sekunden).
- **Gelb** — Status unbekannt (kein Linux/WSL2, oder `/proc` nicht lesbar).

Ab Codex 0.160 schreibt meist ein gemeinsamer **Hintergrund-Server**
(`codex app-server --managed-daemon`) die Session-Dateien, nicht das
Codex-Programm im Terminal. Nach `/exit` hält er die Session noch etwa
15–30 Sekunden geladen — so lange bleibt die Karte grün, dann verschwindet
sie. (Codex meldet beim Beenden selbst: „Disconnected from this task. Any
running work continues." — ein noch laufender Turn arbeitet im Hintergrund
weiter.)

### Idle-Timer (⌚) und „wartet"-Markierung

Zeigt, wie lange die Rollout-Datei **nicht mehr beschrieben** wurde (echte
Datei-Änderungszeit, alle 3 s aktualisiert — nicht der Zeitpunkt, zu dem das
Fenster die Daten bekam). Liegt das länger als **10 Minuten** zurück, wird der
⌚-Timer **gelb** und um „· wartet" ergänzt: Die Session lebt, wartet aber auf
deine Eingabe. Der Balken selbst bleibt hell. Das Fenster ist großzügig, weil
ein arbeitender Agent (langer Tool-Lauf, lange Generierung) durchaus Minuten
zwischen Schreibvorgängen brauchen kann.

### Session-ID (klickbar)

Die gekürzte UUID. **Klick kopiert die vollständige Session-ID** in die
Zwischenablage — praktisch für `codex-tokens --thread <UUID>`.

### Action-Buttons

| Button | Aktion |
|---|---|
| **📁** | Öffnet `session_cwd` im Dateimanager — auf der Arbeitsfläche des Projekts: Ein bereits offenes Dateimanager-Fenster mit dem Ordner wird nach vorne geholt (inkl. Arbeitsflächen-Wechsel); sonst wird erst auf die Arbeitsfläche des Session-Terminals gewechselt und dort geöffnet (`wmctrl` + `xdg-open`; ohne `wmctrl` einfach `xdg-open`) |
| **⚡** | Holt das Terminal der Session nach vorne und wechselt dabei auf dessen Arbeitsfläche (`wmctrl`). Welches Fenster das ist, steht unter [Wie der Monitor Sessions ihrem Terminal zuordnet](#wie-der-monitor-sessions-ihrem-terminal-zuordnet). Nur Terminal- und IDE-Fenster kommen infrage, Dateimanager und Browser nie |
| **📋** | Kopiert den absoluten Pfad zur Rollout-Datei (`~/.codex/sessions/…/rollout-*.jsonl`) in die Zwischenablage |
| **↻** | Erzeugt das Handover **direkt in der Session**: Der Prompt wird ins Terminal der Session eingefügt und abgeschickt (`xdotool`). Ein bestehendes `HANDOVER.md` wird dabei nur aktualisiert. Die Karte zeigt danach „⏳ Handover läuft" und fragt, sobald `HANDOVER.md` geschrieben und die Session wieder ruhig ist: „Chat schließen? Ja/✕" — „Ja" schickt `/exit` an dasselbe Terminal. Ohne `xdotool`, bei beendeter Session oder wenn das Terminal nicht **sicher** erkannt ist, wird der Prompt wie früher **nur kopiert** (der Hinweis unten im Fenster nennt den Grund) — dann in Codex mit `Strg+Shift+V` einfügen und abschicken |
| **🛑** | **Beendet** das Codex-Programm der Session — Bestätigungs-Dialog erscheint, dann SIGTERM an den TUI-Prozess. Effekt entspricht dem Schließen des Terminal-Fensters; mit `codex resume` jederzeit fortsetzbar. Default-Fokus auf „Abbrechen" — versehentliches Enter ist harmlos. Nur bei sicher erkanntem Terminal möglich |

Sicherheitsnetz beim ↻-Einfügen: Unmittelbar vor dem Einfüge-Tastendruck prüft
der Monitor, ob die Zwischenablage noch den Handover-Prompt enthält (kopierst
du genau in dieser Sekunde etwas anderes, würde sonst dein Text eingefügt).
Nach dem Abschicken vergleicht er den Text, der in der Session ankam, mit dem
Prompt — weicht er ab, erscheint die Warnung „Achtung: In der Session kam ein
anderer Text an — bitte das Terminal prüfen!".

## Wie der Monitor Sessions ihrem Terminal zuordnet

Für ⚡, 📁, die Arbeitsflächen-Anzeige, ↻ und 🛑 muss der Monitor wissen, in
welchem Terminal eine Session läuft. Ab Codex 0.160 hält das Codex-Programm
im Terminal dafür nichts Eindeutiges mehr offen (die Session-Datei schreibt
der Hintergrund-Server). Der Monitor prüft deshalb der Reihe nach:

1. **Das Codex-Programm schreibt die Datei selbst** (Codex ohne
   Hintergrund-Server) — eindeutig.
2. **Der Chat-Name steht im Fenstertitel.** Codex titelt sein Terminal
   „‹Chat-Name› | ‹Projekt›"; den Chat-Namen kennt der Monitor aus
   `~/.codex/session_index.jsonl`. Zählt nur, wenn genau ein Terminal passt
   und keine zweite laufende Session denselben Namen trägt.
3. **Das Terminal wurde mit `codex resume <Session-ID>` gestartet.**
4. **Notbehelf:** das älteste Codex-Terminal im Projektverzeichnis.

Das passende Fenster findet er dann exakt über die Fenster-ID, die das
Terminal an Codex vererbt (`WINDOWID`). ⚡, 📁 und die Arbeitsflächen-Anzeige
nutzen jede Stufe; **↻ (Einfügen), „Ja" (`/exit`) und 🛑 handeln nur bei den
Stufen 1–3** — in einen nur vermuteten Terminal zu tippen oder ein vermutetes
Programm zu beenden wäre schlimmer als der Rückfall aufs Kopieren.

Typische Fälle, in denen ↻ nur kopiert:

- Die Session ist ganz frisch und hat noch keinen Chat-Namen.
- Du hast den Fenstertitel in Codex mit `/title` so eingestellt, dass der
  Chat-Name nicht mehr darin vorkommt. Abhilfe: in Codex `/title` aufrufen und
  das Element mit dem Chat-Namen wieder einschalten (Beschriftung im Menü
  unsicher — es heißt dort vermutlich „thread" oder „session name").
- Codex läuft im Terminal einer IDE (VS Code u. a.), das keine `WINDOWID`
  setzt.
- Im selben Terminal-Fenster laufen mehrere Codex-Sessions in Reitern und der
  Reiter der Session ist gerade nicht vorne (der Fenstertitel zeigt den
  aktiven Reiter). Abhilfe: den Reiter der Session anklicken, dann ↻.

## Bedeutung der Zahlen

| Wert | Bedeutung |
|---|---|
| **198 k / 78 % frei** | Aktuell freie Token absolut + gleichbedeutende Prozentangabe |
| **30 k / 258 k** | Aktuell belegt / Kontextfenster-Größe insgesamt |
| **Σ 67 k** | Kumulierter Token-Verbrauch über die gesamte Session, inklusive aller Compact-Vorgänge |
| **↻ 2×** | (Badge, nur sichtbar wenn > 0) Wie oft Codex den Kontext bereits zusammengefasst hat — entweder automatisch (Limit erreicht) oder über `/compact` in der TUI. Die Σ-Summe enthält auch die Token, die für diese Compaction-Turns verbraucht wurden. |

Als Faustregel bei ~80 % Verbrauch (= 20 % frei) den ↻-Button drücken — das
Handover entsteht direkt in der Session; nach der Schließen-Nachfrage eine
neue Session starten. Wenn Codex stattdessen selbst auto-compact, wandert der
↻-Zähler eins hoch.

## Lade- und Frische-Zustände

Beim Start (oder nach einem Reconnect) braucht es einen Moment, bis frische
Daten vom Server da sind. Damit kein irreführendes „Keine Sessions" erscheint,
unterscheidet das Display:

- **Verbinde / Lädt** — solange die Verbindung noch nicht steht bzw. kurz
  danach, erscheint ein pulsierender Platzhalter („Verbinde mit dem Server …"
  / „Suche aktive Codex-Sessions …").
- **Live** — frische Daten sind da.
- **Wartet** — Daten älter als 10 min: gelber ⌚-Timer mit „· wartet".

Auch das **Plan-Widget** zeigt beim ersten Laden „Plan-Daten werden geladen …"
und überbrückt vorübergehende Fehler mit ein paar stillen Wiederholungen,
bevor es überhaupt einen Fehler anzeigt.

Die Schwellen stehen oben in `display/static/app.js`: `STALE_AFTER_MS`
(Default 600 000 = 10 min) und `SETTLE_MS` (1 800 ms).

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

## Geister-TUIs

Manchmal hast du ein Codex-Terminal offen, in dem du noch **nichts** eingegeben
hast — die TUI wartet auf den ersten Turn. So eine Session hat noch **kein**
Rollout-File und taucht in der normalen Session-Liste nicht auf. Das Display
erkennt diesen Zustand trotzdem und zeigt für jede solche TUI eine
schmale, gelb umrandete **„Geister-TUI"-Karte** ganz oben:

```text
[ Geister-TUI ]  /home/peter/work/xy  pid 1756458  ⌚ 30 min  📁 ⚡ 🛑
```

- Erkennung läuft alle 3 Sekunden über `/proc`: Jedes interaktive
  Codex-Programm, dem keine laufende Session zugeordnet werden kann (siehe
  [Wie der Monitor Sessions ihrem Terminal zuordnet](#wie-der-monitor-sessions-ihrem-terminal-zuordnet)),
  ist ein Geist. `codex app-server` (der Hintergrund-Server, auch der der
  VS-Code-Erweiterung), `codex exec` und andere Unterbefehle ohne Chat zählen
  nie als Geist und lassen sich auch nicht per 🛑 beenden.
- Buttons: 📁 öffnet das Arbeitsverzeichnis, ⚡ holt das Terminal nach
  vorne (gleiche Heuristik wie bei den Session-Karten), 🛑 beendet die TUI
  per SIGTERM nach Bestätigungs-Dialog.
- Sobald du in der TUI etwas eingibst und Enter drückst, schreibt Codex
  ein Rollout an, die Geister-Karte verschwindet, und eine normale
  Session-Karte erscheint stattdessen (das kann bis zu etwa 8 Sekunden
  dauern).

Das ist besonders praktisch, wenn ein versehentlich gestartetes Codex im
Hintergrund Resources frisst, ohne dass du noch weißt, wo es lief.

## 🕘 Letzte Chats

Der **🕘-Button** in der Kopfzeile öffnet ein eigenes Fenster mit einer Tabelle
der zuletzt aktiven Codex-Chats — über **alle** Projektverzeichnisse hinweg,
nicht nur die gerade laufenden.

**Spalten:**

- **★** — Favorit. Ein Klick auf ☆ heftet den Chat an: Favoriten stehen
  **immer zuoberst** und fallen **nie aus den Limits** heraus — auch ein
  Wochen alter Chat bleibt gelistet, solange der Stern gesetzt ist. Erneuter
  Klick auf ★ löst ihn wieder. Beim Löschen eines Chats verschwindet sein
  Stern mit.
- **Verzeichnis** — der Projektordner (Tooltip: voller Pfad). Ein grüner
  Punkt ● markiert Chats, deren Session gerade läuft.
- **Wann** — letzte Aktivität als relative Zeit („vor 5 min", „gestern", …);
  Tooltip zeigt den exakten Zeitpunkt.
- **Thema** — der Chat-Name, den Codex selbst vergibt (derselbe wie im
  Fenstertitel und in `codex resume`). Fehlt er (ältere Chats), steht dort
  kursiv die erste eigene Nachricht. Über den **✎-Knopf** (erscheint beim
  Überfahren der Zeile) gibst du dem Chat einen **eigenen Namen**: Er ersetzt
  den automatischen Titel (gepunktet unterstrichen), der automatische Titel
  bleibt als Tooltip sichtbar. Enter speichert, Esc bricht ab; ein leeres
  Feld stellt den automatischen Titel wieder her. Der eigene Name gilt nur im
  Monitor, Codex selbst bekommt ihn nicht.
- **🗑** — löscht den Chat nach einer Rückfrage direkt in der Zeile
  („Löschen? Ja/✕"). Der Monitor ruft dafür Codex' eigenes
  `codex delete --force <Session-ID>` auf, das die Rollout-Datei **und**
  Codex' Index-Eintrag entfernt (der Chat taucht danach auch in
  `codex resume` nicht mehr auf). Nur wenn kein `codex` gefunden wird, löscht
  der Monitor die Rollout-Datei direkt. Das ist endgültig. Laufende Sessions
  (●) sind geschützt: Der Knopf ist deaktiviert, und auch der Server lehnt ab.

Ein Klick auf die Spaltenköpfe **Verzeichnis** oder **Wann** sortiert danach
(▲/▼ zeigt die Richtung; erneuter Klick dreht sie um). Default ist
„Verzeichnis"; innerhalb eines Verzeichnisses stehen die neuesten Chats oben.

Der Schalter **„Aktive zuerst"** gibt laufenden Chats (●) Vorrang: Bei
Sortierung nach „Wann" stehen sie als Block oben, bei „Verzeichnis" rücken
ganze Verzeichnisse mit einem laufenden Chat nach oben.

Der Schalter **„Nur ★"** blendet alles außer den Favoriten aus.

**Klick auf eine Zeile** klappt das Ende des Gesprächsverlaufs auf (deine
Nachrichten und die Antworten von Codex), dazu:

- **📁 Verzeichnis öffnen** — wie 📁 auf den Karten.
- **📋 codex resume kopieren** — kopiert
  `cd <Projektordner> && codex resume <Session-ID>`. In einem Terminal
  einfügen (`Strg+Shift+V`) und Enter drücken — der Chat geht dort weiter,
  wo er aufgehört hat.
- **Letzte Zeilen** (Feld im Kopf des Verlaufs, Default 50, höchstens
  1000): so viele Textzeilen vom Ende des Chats werden angezeigt, und zwar
  **vollständig** — das Ende der letzten Antwort (z. B. der Pfad zu einer
  Übergabe-Datei) wird nicht abgeschnitten. Nur die älteste gezeigte
  Nachricht kann vorn gekürzt sein (beginnt mit „…“). Steht oben „ältere
  Teile sind ausgeblendet“, zeigt ein größerer Wert mehr. Der Wert gilt für
  alle Chats und wird gespeichert (`chats.detail_lines`).

**🔍 Suchfeld** (unter den Schaltern): findet heraus, in welchem der
gelisteten Chats ein Begriff vorkam.

- Durchsucht werden **nur die Chats, die gerade in der Liste stehen**, diese
  aber **vollständig**, also auch alte Nachrichten, die der aufgeklappte
  Verlauf nicht mehr zeigt. Gesucht wird in deinen Nachrichten, in den
  Antworten von Codex, im Titel und im eigenen Namen; Werkzeug-Ausgaben und
  der Kontext, den Codex selbst einfügt (z. B. `AGENTS.md`-Anweisungen),
  zählen nicht. Groß- und Kleinschreibung ist egal. Die Suche startet ab
  2 Zeichen von selbst, sobald du kurz mit dem Tippen aufhörst.
- **Suchausdruck** — mehrere Begriffe lassen sich verknüpfen. Die Bedingung
  gilt **je Chat**: Bei `a AND b` müssen beide Begriffe irgendwo im selben
  Chat vorkommen, nicht in derselben Nachricht.

  | Eingabe | findet Chats, in denen … |
  |---|---|
  | `restic` | „restic" vorkommt (auch als Wortteil, z. B. „Restic-Sicherung") |
  | `restic timeshift` | beide Wörter vorkommen (wie `restic AND timeshift`) |
  | `restic OR borg` | mindestens eins der beiden Wörter vorkommt |
  | `steuer AND (binance OR poloniex)` | „steuer" vorkommt und dazu „binance" oder „poloniex" |
  | `restic NOT timeshift` | „restic" vorkommt, „timeshift" aber nirgends |
  | `"persönlicher Ordner"` | genau diese Wortfolge vorkommt |

  Die Verknüpfungen **AND**, **OR** und **NOT** (gleichwertig **UND**,
  **ODER**, **NICHT**) gelten nur in **Großbuchstaben**; klein geschrieben
  („und", „or") sind es normale Suchwörter. AND bindet stärker als OR:
  `a OR b c` heißt `a OR (b AND c)`. Klammern legen die Reihenfolge fest.
  Ist der Ausdruck unvollständig (z. B. offene Klammer, `AND` am Ende), steht
  neben dem Feld in Rot, was fehlt.
- Treffer werden **markiert, nicht gefiltert**. Jede Zeile der Tabelle ist
  ein Chat. Die Zeile eines passenden Chats bekommt einen blauen Balken links
  und das Abzeichen **🔍 n** (Suchbegriffe in n Nachrichten dieses Chats
  gefunden; „🔍 Titel" = nur im Titel; ein nacktes „🔍" = passt nur über NOT,
  es gibt nichts zu zeigen). Die Zeilen aller anderen Chats werden blasser
  und tragen keine gelbe Markierung. Neben dem Suchfeld steht, wie viele der
  gelisteten Chats passen.
- Der Schalter **„Nur Treffer"** neben dem Suchfeld blendet während einer
  Suche alle Chats aus, die nicht passen, statt sie blass anzuzeigen. Ohne
  Suchbegriff wirkt er nicht; dann stehen wie gewohnt alle Chats da. Die
  Angabe „Treffer in X von Y Chats" zählt weiter über alle gelisteten Chats.
  Die Stellung des Schalters wird gespeichert (`chats.only_hits`, Default an).
- Nach der Suche rollt das Fenster zum ersten Treffer. **Enter** springt zum
  nächsten, nach dem letzten wieder zum ersten. Die Suchleiste bleibt beim
  Scrollen oben sichtbar.
- Ein **aufgeklappter Treffer** zeigt über dem Verlauf einen Kasten
  „🔍 Suchbegriffe in n Nachrichten" mit Fundstellen samt Datum und Uhrzeit:
  zu jedem gefundenen Begriff die erste, aufgefüllt auf drei. Die
  Suchbegriffe sind dort und im Verlauf gelb hervorgehoben.
- **Esc** oder das **×** im Suchfeld leert die Suche; alle Markierungen
  verschwinden. Der Suchausdruck wird nicht gespeichert.
- Wer weiter zurück suchen will, stellt vorher die Limits höher
  (**Pro Verzeichnis**, **Gesamt**) oder setzt ★ auf ältere Chats: Alles, was
  gelistet ist, wird durchsucht.

**Limits** — direkt im Fenster einstellbar:

- **Pro Verzeichnis** (Default 3): wie viele der neuesten Chats je Projekt
  berücksichtigt werden — so dominiert kein einzelnes Projekt die Liste.
- **Gesamt** (Default 10): wie viele Chats insgesamt angezeigt werden.

Favoriten zählen nicht gegen diese Plätze. Chats, in denen nie eine Nachricht
abgeschickt wurde, werden übersprungen. Alle Einstellungen dieses Fensters
(Limits, Schalter, Favoriten, eigene Namen) stehen im Abschnitt `chats` der
Konfigurationsdatei `~/.config/codex-token-monitor/config.json`.

Das Fenster liest nur die Dateien unter `~/.codex/sessions/…` und
`~/.codex/session_index.jsonl`, die Codex ohnehin schreibt — verändert wird
nichts, außer beim ausdrücklich bestätigten 🗑-Löschen.

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
  },
  "chats": {
    "per_dir": 3,
    "total": 10,
    "active_first": false,
    "favorites": [],
    "only_favorites": false,
    "names": {}
  },
  "windows": { "sticky": true },
  "display": { "sort": "usage" }
}
```

Die Abschnitte `chats` (🕘 Letzte Chats), `windows` (Fenster auf allen
Arbeitsflächen) und `display` (Karten-Sortierung) setzt das Display selbst,
wenn du die Einstellungen im Fenster änderst; fehlen sie, gelten die oben
gezeigten Standardwerte.

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
| (keiner, Erstaufruf) | Plan-Daten werden geladen … | Erste Abfrage läuft (max. 10 s Timeout, mit stillen Wiederholungen) |
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

Das Dropdown in der Kopfzeile bestimmt die Reihenfolge der Session-Karten
(gespeichert als `display.sort` in der Konfigurationsdatei):

- **Auslastung** (Default) — am häufigsten komprimierte Session zuerst
  (`compact_count`; jede Komprimierung kostet einen ganzen Turn), bei
  Gleichstand der vollere Balken (`percent_used`). So springt eine gerade
  beobachtete Session nach einem Auto-Compact nicht unerwartet weit nach
  unten.
- **Ort** — alphabetisch nach Projektpfad.
- **Startzeit** — nach Beginn der Session, die älteste zuerst (stabile
  Reihenfolge: Karten springen beim Arbeiten nicht umher).

Echte Sessions stehen immer vor Worker-Sessions (nach kumuliertem Verbrauch
sortiert); Geister-TUIs stehen ganz oben.

## Anpassung

- **Custom Handover-Prompt**: in `display/static/app.js` die Funktion
  `rolloverPrompt(snap)` editieren.
- **Farbverlauf des Balkens**: in `display/static/app.css` der Selektor
  `.bar__fill` mit dem `linear-gradient(...)`.
- **Hilfe-Text**: einfach `display/README.md` editieren — wird beim
  nächsten 📖-Klick neu gerendert.
- **Mehrere Projekte gleichzeitig**: einfach mehrere `launcher.sh`-
  Instanzen mit verschiedenen `--port`-Werten starten.
- **Frische-Schwellen**: `STALE_AFTER_MS` und `SETTLE_MS` oben in
  `display/static/app.js`.
- **Fenster auf allen Arbeitsflächen**: Das Monitor-Fenster und seine
  Unterfenster (📖 Doku, 🕘 Letzte Chats) werden beim Öffnen per `wmctrl` auf
  allen Arbeitsflächen sichtbar gemacht („sticky"). Abschalten: in
  `~/.config/codex-token-monitor/config.json` auf oberster Ebene
  `"windows": {"sticky": false}` eintragen (die Datei ist JSON — auf das Komma
  zum vorigen Eintrag achten), dann das Monitor-Fenster schließen und neu
  starten. Ohne `wmctrl` passiert ohnehin nichts.

## Voraussetzungen

- `python3` (Standard auf jedem Linux/WSL2)
- ein Chromium-basierter Browser für das `--app`-Fenster
- `codex-tokens` (im PATH oder via `CODEX_TOKENS_BIN`-Variable)
- für die Buttons:
  - `xdg-open` (in jedem Linux-Desktop dabei)
  - `wmctrl` (`sudo apt install wmctrl`)
  - `xclip` oder `wl-clipboard` (`sudo apt install xclip`)
  - `xdotool` (`sudo apt install xdotool`) — nur für das direkte
    Handover-Einfügen per ↻ und „Ja" (ohne fällt ↻ aufs Kopieren zurück)
  - `codex` — für 🗑 in „Letzte Chats" (`codex delete`)

## Hilfe und Bugs

Issues und Pull Requests: <https://github.com/Robbty/codex-token-monitor>

Vollständige Doku (Install, Build, CLI-Bedienung, Releases, Portabilität):
**[Projekt-README öffnen](/help?doc=main)**
