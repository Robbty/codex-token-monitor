// codex-token-display — DOM logic and SSE client.

(() => {
  const sessions = new Map(); // session_id → snapshot
  const lastUpdate = new Map(); // session_id → epoch_ms of last received snapshot
  const sessionsEl = document.getElementById("sessions");
  const emptyEl = document.getElementById("empty");
  const loadingEl = document.getElementById("loading");
  const loadingMsgEl = document.getElementById("loading-msg");
  const countEl = document.getElementById("count");
  const cwdEl = document.getElementById("cwd");
  const connEl = document.getElementById("conn");
  const rowTpl = document.getElementById("row-template");
  const workerToggle = document.getElementById("show-workers");
  const sortSelect = document.getElementById("sort-mode");
  // Ghost codex TUIs (process without a session), see renderGhosts() below.
  let ghosts = [];

  // Card order, persisted as display.sort in the server settings:
  // "usage" (most-compacted/fullest first), "dir" (by project path),
  // "start" (by session start time, oldest first).
  let sortMode = "usage";

  // Connection / freshness state. Until the SSE stream is open and has had a
  // brief moment to deliver the initial session dump, we show a neutral
  // "connecting / loading" placeholder instead of the misleading "no sessions"
  // empty state. Data older than STALE_AFTER_MS is flagged as not-yet-refreshed.
  let sseConnected = false;
  let connectedAt = 0;
  const SETTLE_MS = 1800;        // grace after connect before declaring "empty"
  // No file write for this long → flag the card as "wartet". Generous on
  // purpose: an agent mid-step (long tool run, long generation) can
  // legitimately go minutes between writes, and the ⌚ timer already shows the
  // exact age — so this only flags clearly parked sessions, not busy ones.
  const STALE_AFTER_MS = 600_000; // 10 min

  // Workers are Codex-Desktop background helpers: they emit token-count
  // events without a model_context_window. The dashboard hides them by
  // default (no usable bar) but the toggle in the topbar can show them.
  let showWorkers = false;
  const isWorker = (snap) =>
    !snap.context_window || snap.context_window <= 0;

  // Workers don't report their context window. Borrow the value from any
  // real session if one is around (same Codex / same model → same limit);
  // otherwise fall back to this constant. Adjust if you mostly use a model
  // with a different context size.
  const ASSUMED_CONTEXT_WINDOW = 258400; // GPT-5 / Codex default
  const effectiveContextWindow = (snap) => {
    if (snap.context_window && snap.context_window > 0) return snap.context_window;
    for (const s of sessions.values()) {
      if (s.context_window && s.context_window > 0) return s.context_window;
    }
    return ASSUMED_CONTEXT_WINDOW;
  };

  // -- formatting helpers --

  const fmtTokens = (n) => {
    if (n == null) return "—";
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
    return String(n);
  };

  const fmtIdle = (sec) => {
    if (sec == null) return "";
    if (sec < 60) return `${sec}s`;
    if (sec < 3600) {
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      return s > 0 ? `${m}:${String(s).padStart(2, "0")}` : `${m}m`;
    }
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return `${h}:${String(m).padStart(2, "0")}h`;
  };

  // True data age: prefer the rollout file's mtime (when it was actually
  // written) over the snapshot arrival time, so a reconnect doesn't reset the
  // age of long-idle sessions to zero.
  function dataAgeSec(snap) {
    const baseMs = snap && snap.last_modified
      ? snap.last_modified * 1000
      : (lastUpdate.get(snap?.session_id) ?? Date.now());
    return Math.max(0, Math.floor((Date.now() - baseMs) / 1000));
  }

  function applyAge(rowEl, snap) {
    const ageSec = dataAgeSec(snap);
    const stale = ageSec * 1000 > STALE_AFTER_MS;
    const idleEl = rowEl.querySelector(".idle");
    // All bars stay equally bright; staleness is shown only here on the timer
    // (yellow + an explicit "wartet" tag) so the bar can't be mistaken for a
    // contrast glitch. "wartet" reads as "alive but waiting for input" — it does
    // not contradict the green "running" status dot the way "inaktiv" would.
    idleEl.textContent = stale
      ? `⌚ ${fmtIdle(ageSec)} · wartet`
      : `⌚ ${fmtIdle(ageSec)}`;
    rowEl.classList.toggle("session--stale", stale);
    idleEl.title = stale
      ? `Seit ${fmtIdle(ageSec)} kein neuer Turn — die Session wartet auf Eingabe.`
      : "Seit letzter Aktivität";
  }

  // The bar's colour gradient is defined in CSS (anchored to the full bar
  // width); JS only adjusts the visible-portion clip.

  // -- rendering --

  function ensureRow(sid) {
    let el = sessionsEl.querySelector(`[data-sid="${sid}"]`);
    if (el) return el;
    const frag = rowTpl.content.cloneNode(true);
    el = frag.querySelector(".session");
    el.dataset.sid = sid;
    wireButtons(el);
    sessionsEl.appendChild(el);
    return el;
  }

  function wireButtons(rowEl) {
    rowEl.querySelector(".sid").addEventListener("click", () => {
      const sid = rowEl.dataset.sid;
      postJson("/copy", { text: sid })
        .then(() => toast(`Session-ID kopiert: ${shortId(sid)}…`))
        .catch(() => toast("Kopieren fehlgeschlagen", true));
    });
    rowEl.querySelector(".btn--open").addEventListener("click", () => {
      const snap = sessions.get(rowEl.dataset.sid);
      if (!snap?.session_cwd) return;
      postJson("/open-dir", { path: snap.session_cwd, sid: rowEl.dataset.sid })
        .then((r) => toast(r.focused ? "Dateimanager-Fenster nach vorne geholt" : "Verzeichnis geöffnet"))
        .catch(() => toast("Konnte Verzeichnis nicht öffnen", true));
    });
    rowEl.querySelector(".btn--focus").addEventListener("click", () => {
      const snap = sessions.get(rowEl.dataset.sid);
      if (!snap?.session_cwd) return;
      postJson("/focus-terminal", { cwd: snap.session_cwd, sid: rowEl.dataset.sid })
        .then((r) => toast(r.warning ? r.warning : "Terminal fokussiert"))
        .catch(() => toast("Konnte Fenster nicht fokussieren", true));
    });
    rowEl.querySelector(".btn--path").addEventListener("click", () => {
      const snap = sessions.get(rowEl.dataset.sid);
      const p = snap?.rollout_path;
      if (!p) {
        toast("Pfad noch nicht aufgelöst", true);
        return;
      }
      postJson("/copy", { text: p })
        .then(() => toast(`Pfad kopiert: …/${p.split("/").slice(-2).join("/")}`))
        .catch(() => toast("Kopieren fehlgeschlagen", true));
    });
    rowEl.querySelector(".btn--rollover").addEventListener("click", () => {
      const sid = rowEl.dataset.sid;
      const snap = sessions.get(sid);
      postJson("/handover", { sid, text: rolloverPrompt(snap) })
        .then((r) => {
          if (r.injected && r.verified === false) {
            // In der Session kam ein anderer Text an (z. B. weil die
            // Zwischenablage im selben Moment überschrieben wurde).
            toast("Achtung: In der Session kam ein anderer Text an — bitte das Terminal prüfen!", true);
          } else if (r.injected) {
            toast("Handover-Prompt in die Session eingefügt");
            startHandoverWatch(sid, r.started);
          } else {
            // Terminal nicht sicher erkennbar / kein xdotool / Session tot —
            // der Prompt liegt in der Zwischenablage, wie beim alten Verhalten.
            toast(`Prompt nur kopiert (${r.reason}) — in Codex einfügen`, true);
          }
        })
        .catch(() => toast("Handover fehlgeschlagen", true));
    });
    rowEl.querySelector(".btn--kill").addEventListener("click", () => {
      const sid = rowEl.dataset.sid;
      const snap = sessions.get(sid);
      const cwd = snap?.session_cwd || "(unbekannt)";
      askKillConfirmation({
        kind: "session",
        title: "Codex-Session beenden?",
        detail: `${shortId(sid)} · ${cwd}`,
        payload: { session_id: sid },
      });
    });
  }

  function shortId(sid) {
    return sid.split("-").slice(0, 2).join("-");
  }

  // "gpt-6-astra" → "GPT-6 Astra", "gpt-5.3-codex-spark" → "GPT-5.3 Codex
  // Spark", "gpt-5.5" → "GPT-5.5". Anything else is shown as-is.
  function prettyModel(model) {
    if (!model) return "";
    const m = model.match(/^gpt-(\d+(?:\.\d+)?)(?:-(.+))?$/i);
    if (!m) return model;
    const rest = m[2]
      ? " " + m[2].split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ")
      : "";
    return `GPT-${m[1]}${rest}`;
  }

  function rolloverPrompt(snap) {
    return `Bitte sichere den aktuellen Stand als Handover in HANDOVER.md.
Falls die Datei bereits existiert, aktualisiere sie nur: Struktur beibehalten,
veraltete Angaben ersetzen, Erledigtes zusammenfassen. Sonst lege sie neu an mit:

1. Was wurde in der bisherigen Session erledigt? (Stichpunkte, max. 10)
2. Welche Tests/Builds laufen aktuell grün/rot?
3. Was sind die offenen TODOs aus dem aktiven Plan?
4. Kontextspezifische Snippets: aktuelle Branch, letzter Commit-Hash,
   geänderte Dateien.

Halte HANDOVER.md unter 200 Zeilen. Beende dann den aktuellen Turn,
damit eine neue Session mit HANDOVER.md als Kontext starten kann.

(Session ${snap?.session_id ?? "—"} · ${snap?.percent_used ?? "?"}% Kontext verbraucht)`;
  }

  // -- Handover-Injektion: Fertig-Überwachung + Schließen-Nachfrage --
  //
  // Nach erfolgreicher Injektion (POST /handover) läuft der Prompt IN der
  // Session; wann er fertig ist, weiß nur die Rollout-Datei. Wir pollen
  // /handover-status: fertig = HANDOVER.md wurde nach dem Start geschrieben
  // UND die Rollout-Datei ist seit ein paar Sekunden ruhig (Turn beendet).
  // Dann fragt die Karte einmalig, ob /exit geschickt werden soll — ohne
  // "Ja" passiert nichts. Alle Zeitvergleiche nutzen die Server-Uhr aus der
  // Status-Antwort, nie Date.now() gegen Server-Timestamps.
  const HANDOVER_POLL_MS = 4000;
  const HANDOVER_IDLE_SEC = 8;       // Rollout so lange ruhig → Turn gilt als beendet
  const HANDOVER_TIMEOUT_MS = 600_000; // 10 min ohne Fertig-Meldung → aufgeben

  const handoverWatches = new Map(); // sid → {phase: "waiting"|"confirm", timer}

  function syncHandover(rowEl) {
    const sid = rowEl.dataset.sid;
    const el = rowEl.querySelector(".handover");
    const phase = handoverWatches.get(sid)?.phase ?? "";
    if (el.dataset.phase === phase) return; // Buttons nicht bei jedem Snapshot neu bauen
    el.dataset.phase = phase;
    el.classList.toggle("hidden", !phase);
    el.textContent = "";
    if (phase === "waiting") {
      el.textContent = "⏳ Handover läuft — warte auf HANDOVER.md …";
    } else if (phase === "confirm") {
      el.append("HANDOVER.md aktualisiert — Chat schließen?");
      const yes = document.createElement("button");
      yes.className = "yes";
      yes.textContent = "Ja";
      yes.title = "Schickt /exit an das Terminal der Session";
      yes.addEventListener("click", () => {
        yes.disabled = true;
        postJson("/session-exit", { sid })
          .then(() => { clearHandoverWatch(sid); toast("/exit an die Session geschickt"); })
          .catch(() => {
            yes.disabled = false;
            toast("Konnte Session nicht schließen — bitte manuell /exit eingeben", true);
          });
      });
      const no = document.createElement("button");
      no.textContent = "✕";
      no.title = "Chat offen lassen";
      no.addEventListener("click", () => clearHandoverWatch(sid));
      el.append(yes, no);
    }
  }

  function syncHandoverRow(sid) {
    const rowEl = sessionsEl.querySelector(`[data-sid="${sid}"]`);
    if (rowEl) syncHandover(rowEl);
  }

  function clearHandoverWatch(sid) {
    const w = handoverWatches.get(sid);
    if (w?.timer) clearInterval(w.timer);
    handoverWatches.delete(sid);
    syncHandoverRow(sid);
  }

  function startHandoverWatch(sid, started) {
    clearHandoverWatch(sid);
    const deadline = Date.now() + HANDOVER_TIMEOUT_MS;
    const timer = setInterval(async () => {
      let st;
      try {
        const r = await fetch(`/handover-status?sid=${encodeURIComponent(sid)}`);
        if (!r.ok) throw new Error(String(r.status));
        st = await r.json();
      } catch {
        st = null; // transient (Server-Neustart o. Ä.) — nächster Poll versucht's wieder
      }
      if (st) {
        const written = st.handover_mtime >= started - 1;
        if (!st.active) {
          // Session hat sich selbst beendet — nichts mehr zu schließen.
          clearHandoverWatch(sid);
          toast(written
            ? "Handover geschrieben — Session hat sich bereits beendet"
            : "Session beendet, ohne HANDOVER.md zu schreiben", !written);
          return;
        }
        if (written && st.now - st.session_mtime >= HANDOVER_IDLE_SEC) {
          const w = handoverWatches.get(sid);
          clearInterval(timer);
          if (w) { w.timer = null; w.phase = "confirm"; }
          syncHandoverRow(sid);
          return;
        }
      }
      if (Date.now() > deadline) {
        clearHandoverWatch(sid);
        toast("Handover: keine Fertig-Meldung nach 10 min — bitte in der Session nachsehen", true);
      }
    }, HANDOVER_POLL_MS);
    handoverWatches.set(sid, { phase: "waiting", timer });
    syncHandoverRow(sid);
  }

  function renderRow(rowEl, snap) {
    const worker = isWorker(snap);
    const ctx = effectiveContextWindow(snap);
    // For workers, tokens_in_context is missing too — fall back to the
    // single token_count event's total. session_total_tokens is the same
    // value in their case (only one event ever fired).
    const used = snap.tokens_in_context ?? snap.session_total_tokens ?? 0;
    const free = Math.max(0, ctx - used);
    const pctUsed = ctx > 0 ? Math.min(100, Math.round((used / ctx) * 100)) : 0;
    const pctLeft = 100 - pctUsed;

    rowEl.classList.toggle("session--worker", worker);
    // A worker is no interactive chat in a terminal — neither the rollover
    // prompt nor ending a TUI makes sense there.
    rowEl.querySelector(".btn--rollover").classList.toggle("hidden", worker);
    rowEl.querySelector(".btn--kill").classList.toggle("hidden", worker);
    // Frisch erzeugte Zeilen (Toggle-Wechsel) sollen ihren Handover-Zustand
    // (⏳ / Schließen-Nachfrage) zurückbekommen.
    syncHandover(rowEl);

    const fill = rowEl.querySelector(".bar__fill");
    const bar = rowEl.querySelector(".bar");
    const labelEl = rowEl.querySelector(".bar__label");

    // Both worker and real sessions get a properly-filled bar now.
    fill.style.clipPath = `inset(0 ${(100 - pctUsed).toFixed(2)}% 0 0)`;
    labelEl.innerHTML =
      `<span class="free"></span>&nbsp;/&nbsp;<span class="pctfree"></span>%&nbsp;frei`;
    labelEl.querySelector(".free").textContent = fmtTokens(free);
    labelEl.querySelector(".pctfree").textContent = pctLeft;

    bar.title = worker
      ? `Worker-Session — Kontextfenster nicht gemeldet, angenommen ` +
        `${ctx.toLocaleString("de-DE")}.\n` +
        `${used.toLocaleString("de-DE")} Token belegt (${pctUsed}%)\n` +
        `${free.toLocaleString("de-DE")} Token frei (${pctLeft}%)`
      : `${free.toLocaleString("de-DE")} Token frei (${pctLeft}%)\n` +
        `${used.toLocaleString("de-DE")} Token verbraucht (${pctUsed}%)\n` +
        `${ctx.toLocaleString("de-DE")} Token Kontextfenster gesamt`;

    // "Arbeitsfläche · ~/pfad" — the workspace prefix only when known.
    const cwdElRow = rowEl.querySelector(".cwd");
    const cwdShort = snap.session_cwd_short ?? snap.session_cwd ?? "(unbekannt)";
    cwdElRow.textContent = "";
    if (snap.workspace) {
      const wsEl = document.createElement("span");
      wsEl.className = "workspace";
      wsEl.textContent = `${snap.workspace} · `;
      cwdElRow.append(wsEl);
    }
    cwdElRow.append(cwdShort);
    cwdElRow.title =
      (snap.workspace ? `Arbeitsfläche „${snap.workspace}“\n` : "") +
      (snap.session_cwd ?? "");

    // Modell + Effort rechts neben dem Pfad (nur wenn bekannt).
    const modelEl = rowEl.querySelector(".modelinfo");
    const modelName = prettyModel(snap.model);
    if (modelName) {
      modelEl.textContent = modelName;
      if (snap.effort) {
        const eff = document.createElement("span");
        eff.className = "effort";
        eff.textContent = ` · ${snap.effort}`;
        modelEl.append(eff);
      }
      modelEl.title = snap.effort
        ? `Modell ${snap.model} · Effort ${snap.effort}`
        : `Modell ${snap.model}`;
      modelEl.classList.remove("hidden");
    } else {
      modelEl.textContent = "";
      modelEl.classList.add("hidden");
    }

    const statusEl = rowEl.querySelector(".status");
    statusEl.classList.remove("status--active", "status--closed", "status--unknown");
    if (snap.session_active === true) {
      statusEl.classList.add("status--active");
      statusEl.title = "Codex-Session läuft (Rollout-Datei ist zum Schreiben geöffnet)";
    } else if (snap.session_active === false) {
      statusEl.classList.add("status--closed");
      statusEl.title = "Codex-Session beendet — letzter Stand eingefroren";
    } else {
      statusEl.classList.add("status--unknown");
      statusEl.title = "Status unbekannt";
    }

    applyAge(rowEl, snap);

    const sidEl = rowEl.querySelector(".sid");
    sidEl.textContent = shortId(snap.session_id ?? "");
    sidEl.title = `${snap.session_id}\n(klicken zum Kopieren)`;

    rowEl.querySelector(".usage").textContent =
      `${fmtTokens(used)} / ${fmtTokens(ctx)}`;
    rowEl.querySelector(".cumulative").textContent =
      `Σ ${fmtTokens(snap.session_total_tokens)}`;

    // Compact counter: only visible if the session has been compacted
    // at least once. Shows e.g. "↻ 2×" with a tooltip explaining the
    // mechanism.
    const cc = snap.compact_count ?? 0;
    const compactEl = rowEl.querySelector(".compact");
    if (cc > 0) {
      compactEl.querySelector(".compact-n").textContent = cc;
      const total = snap.session_total_tokens ?? 0;
      compactEl.title =
        `Kontext wurde ${cc}× zusammengefasst ` +
        `(Auto-Compact am Limit oder /compact).\n` +
        `Σ ${total.toLocaleString("de-DE")} Token sind die echte ` +
        `Gesamtsumme des Chats inklusive aller Compaction-Vorgänge.`;
      compactEl.classList.remove("hidden");
    } else {
      compactEl.classList.add("hidden");
    }
  }

  function applySnapshot(snap) {
    if (!snap.session_id) return;
    sessions.set(snap.session_id, snap);
    lastUpdate.set(snap.session_id, Date.now());
    renderUI();
  }

  function renderUI() {
    // 1. Drop closed sessions completely.
    for (const [sid, snap] of sessions) {
      if (snap.session_active === false) {
        const row = sessionsEl.querySelector(`[data-sid="${sid}"]`);
        if (row) row.remove();
        sessions.delete(sid);
        lastUpdate.delete(sid);
      }
    }

    // 2. Determine visible set based on the worker toggle.
    const visible = [...sessions.values()].filter(
      (s) => showWorkers || !isWorker(s)
    );

    // 3. Remove DOM rows that are no longer visible. Ghost cards (no
    //    data-sid) are owned by renderGhosts(), not by us.
    const visibleIds = new Set(visible.map((s) => s.session_id));
    sessionsEl.querySelectorAll(".session:not(.session--ghost)").forEach((row) => {
      if (!visibleIds.has(row.dataset.sid)) row.remove();
    });

    // 4. Sort order: always real sessions first, then workers. Within the
    //    groups the selected mode applies:
    //    - usage (default): most-compacted first (each compact costs a whole
    //      turn of tokens, so it's a 'cost so far' proxy), tiebreaker bar
    //      fill; workers by cumulative consumption.
    //    - dir: by project path; start: by session start (oldest first).
    //    Usage order stays the final tiebreaker for the other modes.
    const cmpUsage = (a, b) => {
      if (!isWorker(a)) {
        const cc = (b.compact_count ?? 0) - (a.compact_count ?? 0);
        if (cc !== 0) return cc;
        return (b.percent_used ?? 0) - (a.percent_used ?? 0);
      }
      return (b.session_total_tokens ?? 0) - (a.session_total_tokens ?? 0);
    };
    visible.sort((a, b) => {
      const aw = isWorker(a), bw = isWorker(b);
      if (aw !== bw) return aw ? 1 : -1;
      if (sortMode === "dir") {
        const d = (a.session_cwd ?? "").localeCompare(b.session_cwd ?? "", "de");
        if (d !== 0) return d;
      } else if (sortMode === "start") {
        // ISO-8601 timestamps compare correctly as strings; unknown start
        // times sink to the bottom of their group.
        const sa = a.started_at ?? "￿";
        const sb = b.started_at ?? "￿";
        if (sa !== sb) return sa < sb ? -1 : 1;
      }
      return cmpUsage(a, b);
    });

    for (const snap of visible) {
      const row = ensureRow(snap.session_id);
      renderRow(row, snap);
      sessionsEl.appendChild(row);
    }

    // 5. Count label: distinguish visible vs hidden workers.
    const workerCount = [...sessions.values()].filter(isWorker).length;
    const realCount = sessions.size - workerCount;
    let label = `${realCount} aktiv`;
    if (workerCount > 0) {
      label += showWorkers ? ` · ${workerCount} Worker` : ` (+${workerCount} Worker)`;
    }
    countEl.textContent = label;

    // Three placeholder states, so the user can tell "still loading" apart
    // from a genuine "no sessions": connecting → loading → empty. A ghost
    // TUI on screen means "not empty" either.
    const nothing = visible.length === 0 && ghosts.length === 0;
    const connecting = !sseConnected;
    const settling = sseConnected && nothing && Date.now() - connectedAt < SETTLE_MS;
    if (connecting || settling) {
      loadingMsgEl.textContent = connecting
        ? "Verbinde mit dem Server …"
        : "Suche aktive Codex-Sessions …";
      loadingEl.classList.remove("hidden");
      loadingEl.classList.add("loading-pulse");
      emptyEl.classList.add("hidden");
    } else {
      loadingEl.classList.add("hidden");
      loadingEl.classList.remove("loading-pulse");
      emptyEl.classList.toggle("hidden", !nothing);
    }

    if (scope !== null && visible.length > 0) {
      cwdEl.textContent = scopeShort ?? scope;
      cwdEl.title = scope;
    }
  }

  // Toggle wiring: flip showWorkers and re-render.
  if (workerToggle) {
    workerToggle.addEventListener("change", () => {
      showWorkers = workerToggle.checked;
      renderUI();
    });
  }
  if (sortSelect) {
    sortSelect.addEventListener("change", () => {
      sortMode = sortSelect.value;
      renderUI();
      fetch("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display: { sort: sortMode } }),
      }).catch(() => {}); // Sortierung funktioniert auch ohne Persistenz
    });
  }

  // Server tells us whether we're scoped to a single project or system-wide.
  let scope = null;
  let scopeShort = null;
  async function initScope() {
    try {
      const r = await fetch("/scope");
      const data = await r.json();
      scope = data.scope ?? null;
      scopeShort = data.scope_short ?? scope;
      if (scope === null) {
        cwdEl.textContent = "Alle Projekte";
        cwdEl.title = "PC-weite Übersicht aller laufenden Codex-Sessions";
      } else {
        cwdEl.textContent = scopeShort;
        cwdEl.title = scope;
      }
    } catch {
      cwdEl.textContent = "?";
    }
  }

  // Refresh age timers (and the stale flag) every second.
  setInterval(() => {
    for (const [sid, snap] of sessions) {
      const row = sessionsEl.querySelector(`[data-sid="${sid}"]`);
      if (!row) continue;
      applyAge(row, snap);
    }
  }, 1000);

  // -- SSE connection --

  function connect() {
    const es = new EventSource("/events");
    es.onopen = () => {
      connEl.classList.remove("conn--disconnected");
      connEl.classList.add("conn--connected");
      connEl.title = "Mit dem Hilfs-Server verbunden";
      sseConnected = true;
      connectedAt = Date.now();
      renderUI();
      // Re-render once the settle window elapses, to flip a still-empty
      // dashboard from "Suche …" to the real "Keine aktiven Sessions".
      setTimeout(renderUI, SETTLE_MS + 100);
    };
    es.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data);
        if (event.type === "snapshot") applySnapshot(event.data);
        else if (event.type === "ghosts") applyGhosts(event.data);
      } catch {
        /* ignore */
      }
    };
    es.onerror = () => {
      connEl.classList.remove("conn--connected");
      connEl.classList.add("conn--disconnected");
      connEl.title = "Keine Verbindung — Server gestoppt?";
      sseConnected = false;
      renderUI();
      // EventSource auto-reconnects.
    };
  }

  // -- POST helper --

  async function postJson(path, payload) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    const txt = await r.text();
    return txt ? JSON.parse(txt) : {};
  }

  // -- toast --

  let toastTimer;
  function toast(msg, isErr) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.style.borderColor = isErr ? "var(--red)" : "var(--border)";
    el.classList.add("toast--visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("toast--visible"), 2500);
  }

  // -- help + recent chats: separate, draggable OS windows --
  //
  // Chromium's --app mode propagates the borderless app-window style to
  // popups, so window.open() with popup=yes gives us a standalone window
  // without URL bar — drag-anywhere, resizable, can sit next to the main
  // window. Using a named target means clicking the button again just
  // focuses the existing window instead of opening a second copy.
  let helpWin = null;
  function openHelp() {
    if (helpWin && !helpWin.closed) {
      helpWin.focus();
      return;
    }
    helpWin = window.open(
      "/help",
      "codex-token-help",
      "popup=yes,width=760,height=820,resizable=yes,scrollbars=yes"
    );
    if (helpWin) helpWin.focus();
  }

  let chatsWin = null;
  function openChats() {
    if (chatsWin && !chatsWin.closed) {
      chatsWin.focus();
      return;
    }
    chatsWin = window.open(
      "/chats",
      "codex-token-chats",
      "popup=yes,width=860,height=720,resizable=yes,scrollbars=yes"
    );
    if (chatsWin) chatsWin.focus();
  }

  document.addEventListener("click", (e) => {
    if (e.target.closest("#help-btn")) {
      e.preventDefault();
      openHelp();
    } else if (e.target.closest("#chats-btn")) {
      e.preventDefault();
      openChats();
    }
  });

  initScope().then(connect);

  // Fenster auf allen Arbeitsflächen anzeigen (per wmctrl sticky; abschaltbar
  // über windows.sticky in ~/.config/codex-token-monitor/config.json)
  postJson("/sticky", { title: document.title }).catch(() => {});

  // ---------------------------------------------------------------------
  // Plan widget (account-level rate limits — opt-in)
  // ---------------------------------------------------------------------
  //
  // Data path: server.py /plan → spawns `codex-tokens plan` → calls
  // chatgpt.com/backend-api/wham/usage with the OAuth token stored under
  // ~/.codex/auth.json. The endpoint returns a primary (5h) and secondary
  // (7d) rolling window for the main plan, optional per-feature windows
  // under `additional_rate_limits[]`, and a credits balance.
  //
  // We poll once a minute when enabled — the values only change when the
  // user actually makes a Codex request, so higher frequency would just
  // burn the user's own rate-limit budget on us.

  const PLAN_POLL_MS = 60_000;
  const PLAN_WIDGET_EL = document.getElementById("plan-widget");
  const PLAN_SETTINGS_EL = document.getElementById("plan-settings");
  const PLAN_SETTINGS_BTN = document.getElementById("plan-settings-btn");
  const PS_ROWS_DYNAMIC = document.getElementById("ps-rows-dynamic");

  // Rows that have a fixed checkbox in index.html. Everything else is a
  // dynamically discovered additional_rate_limits entry.
  const STATIC_ROWS = ["main", "codex_spark", "code_review", "credits"];

  let planSettings = null;
  let planData = null;
  let planError = null;
  let planFetchTimer = null;
  let planRetryTimer = null;
  let planLoaded = false;   // have we ever received good plan data?
  let planAttempts = 0;     // consecutive failed attempts during initial load
  let planErrStreak = 0;    // consecutive errors after data was already shown
  const PLAN_MAX_INITIAL_ATTEMPTS = 3;
  const PLAN_INITIAL_RETRY_MS = 2500;

  // Known additional_rate_limits ↔ setting-key mapping. Falls back to
  // a normalized form derived from limit_name when the upstream serves a
  // new entry we haven't seen before (default toggle: off).
  const KNOWN_ADDITIONAL = {
    "GPT-5.3-Codex-Spark": "codex_spark",
  };

  function normalizeAdditionalKey(limitName) {
    return limitName
      .replace(/^GPT-\d+(\.\d+)?-/, "")  // strip "GPT-5.3-"
      .toLowerCase()
      .replace(/-/g, "_")
      .replace(/[^a-z0-9_]/g, "");
  }

  function additionalKey(limitName) {
    return KNOWN_ADDITIONAL[limitName] || normalizeAdditionalKey(limitName);
  }

  async function loadPlanSettings() {
    try {
      const r = await fetch("/settings");
      planSettings = await r.json();
    } catch {
      planSettings = {
        plan_widget: {
          enabled: false,
          rows: { main: true, codex_spark: false, code_review: false, credits: false },
        },
      };
    }
    // The card-sort preference travels in the same settings file.
    sortMode = planSettings?.display?.sort ?? "usage";
    if (sortSelect) {
      sortSelect.value = sortMode;
      renderUI();
    }
    syncSettingsUI();
    if (planSettings.plan_widget.enabled) {
      startPlanPolling();
    }
  }

  async function patchPlanSettings(patch) {
    const r = await fetch("/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_widget: patch }),
    });
    const j = await r.json();
    if (j.settings) {
      planSettings = j.settings;
      syncSettingsUI();
    }
  }

  function syncSettingsUI() {
    const pw = planSettings?.plan_widget;
    if (!pw) return;
    document.getElementById("ps-enabled").checked = !!pw.enabled;
    for (const k of STATIC_ROWS) {
      const el = document.getElementById(`ps-row-${k}`);
      if (el) el.checked = !!pw.rows[k];
    }
    // Render any extra rows discovered at runtime (unknown additional_rate_limits).
    PS_ROWS_DYNAMIC.innerHTML = "";
    for (const [k, v] of Object.entries(pw.rows)) {
      if (STATIC_ROWS.includes(k)) continue;
      const lbl = document.createElement("label");
      lbl.className = "settings-popover__row";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!v;
      cb.addEventListener("change", () =>
        patchPlanSettings({ rows: { [k]: cb.checked } }).then(renderPlanWidget)
      );
      const span = document.createElement("span");
      span.textContent = k;
      lbl.appendChild(cb);
      lbl.appendChild(span);
      PS_ROWS_DYNAMIC.appendChild(lbl);
    }
  }

  function startPlanPolling() {
    if (planFetchTimer) clearInterval(planFetchTimer);
    planLoaded = false;
    planAttempts = 0;
    planErrStreak = 0;
    planError = null;
    renderPlanWidget();   // show "wird geladen …" immediately, not a blank/error
    fetchPlan();
    planFetchTimer = setInterval(fetchPlan, PLAN_POLL_MS);
  }
  function stopPlanPolling() {
    if (planFetchTimer) clearInterval(planFetchTimer);
    if (planRetryTimer) clearTimeout(planRetryTimer);
    planFetchTimer = null;
    planRetryTimer = null;
    planData = null;
    planError = null;
    planLoaded = false;
    PLAN_WIDGET_EL.classList.add("hidden");
    PLAN_WIDGET_EL.innerHTML = "";
  }

  async function fetchPlan() {
    if (planRetryTimer) { clearTimeout(planRetryTimer); planRetryTimer = null; }

    let result, isError = false;
    try {
      const r = await fetch("/plan");
      const j = await r.json();
      // 403/disabled or a Rust error envelope (auth_missing | token_expired | …).
      isError = r.status === 403 || j.code === "disabled" || !!j.error || !!j.code;
      result = j;
    } catch (e) {
      isError = true;
      result = { error: String(e), code: "fetch_failed" };
    }

    if (!isError) {
      planData = result;
      planError = null;
      planLoaded = true;
      planAttempts = 0;
      planErrStreak = 0;
      // Auto-register any unknown additional_rate_limit names with default off.
      ensureKnownAdditionals(result.additional_rate_limits || []);
    } else if (!planLoaded) {
      // Initial load: a transient error (cold cache, token still settling) must
      // not flash an error. Keep the loading state and retry a few times.
      planAttempts += 1;
      if (planAttempts < PLAN_MAX_INITIAL_ATTEMPTS) {
        planError = null;
        planRetryTimer = setTimeout(fetchPlan, PLAN_INITIAL_RETRY_MS);
      } else {
        planError = result;
        planData = null;
      }
    } else {
      // Already showed data: ride out a single hiccup on the last good values;
      // only surface an error if it persists across consecutive polls.
      planErrStreak += 1;
      if (planErrStreak >= 2) {
        planError = result;
        planData = null;
      }
    }
    renderPlanWidget();
  }

  function ensureKnownAdditionals(list) {
    if (!planSettings) return;
    const rows = planSettings.plan_widget.rows;
    let added = false;
    for (const entry of list) {
      const k = additionalKey(entry.limit_name);
      if (!(k in rows)) {
        rows[k] = false;
        added = true;
      }
    }
    if (added) {
      // Persist so the new toggle shows up next time too.
      patchPlanSettings({ rows: { ...rows } });
    }
  }

  function renderPlanWidget() {
    PLAN_WIDGET_EL.innerHTML = "";
    if (!planSettings?.plan_widget.enabled) {
      PLAN_WIDGET_EL.classList.add("hidden");
      return;
    }
    PLAN_WIDGET_EL.classList.remove("hidden");

    // Prefer showing data (even mid-refresh); only show an error when there is
    // genuinely no data to show; otherwise we are still loading.
    if (!planData) {
      if (planError) {
        PLAN_WIDGET_EL.appendChild(planErrorEl(planError));
      } else {
        const el = document.createElement("div");
        el.className = "plan__loading loading-pulse";
        el.textContent = "Plan-Daten werden geladen …";
        PLAN_WIDGET_EL.appendChild(el);
      }
      return;
    }

    const rows = planSettings.plan_widget.rows;

    if (rows.main && planData.rate_limit) {
      PLAN_WIDGET_EL.appendChild(
        rateLimitRow("Plan", planData.rate_limit, planData.plan_type)
      );
    }
    if (rows.code_review && planData.code_review_rate_limit) {
      PLAN_WIDGET_EL.appendChild(
        rateLimitRow("Code-Review", planData.code_review_rate_limit)
      );
    }
    for (const entry of planData.additional_rate_limits || []) {
      const k = additionalKey(entry.limit_name);
      if (rows[k] && entry.rate_limit) {
        PLAN_WIDGET_EL.appendChild(
          rateLimitRow(entry.limit_name, entry.rate_limit)
        );
      }
    }
    if (rows.credits && planData.credits) {
      PLAN_WIDGET_EL.appendChild(creditsRow(planData.credits));
    }
  }

  function rateLimitRow(label, rl, planType) {
    const row = document.createElement("div");
    row.className = "plan__row";
    if (rl.limit_reached) row.classList.add("plan__row--limit-reached");

    const lbl = document.createElement("div");
    lbl.className = "plan__label";
    if (planType) {
      // Main row: show "Plan · <plan_type>" so the user sees at a glance
      // what tariff the numbers belong to. The plan_type comes raw from the
      // backend (e.g. "prolite", "plus", "pro").
      lbl.textContent = `${label} · ${planType}`;
      lbl.title = `Plan-Typ laut Backend: ${planType}`;
    } else {
      lbl.textContent = label;
    }
    row.appendChild(lbl);

    const bars = document.createElement("div");
    bars.className = "plan__bars";
    if (rl.primary_window)   bars.appendChild(windowBar("5 h", rl.primary_window));
    if (rl.secondary_window) bars.appendChild(windowBar("7 d", rl.secondary_window));
    row.appendChild(bars);
    return row;
  }

  function windowBar(windowLabel, win) {
    const pct = Math.max(0, Math.min(100, win.used_percent ?? 0));
    const wrap = document.createElement("div");
    wrap.className = "plan__bar";

    const w = document.createElement("span");
    w.className = "plan__bar-window";
    w.textContent = windowLabel;
    wrap.appendChild(w);

    const track = document.createElement("div");
    track.className = "plan__bar-track";
    const fill = document.createElement("div");
    fill.className = "plan__bar-fill";
    fill.style.clipPath = `inset(0 ${(100 - pct).toFixed(2)}% 0 0)`;
    track.appendChild(fill);
    wrap.appendChild(track);

    const num = document.createElement("span");
    num.className = "plan__bar-num";
    // ⌛ = Restzeit bis Reset des Fensters. (Bewusst nicht das ↻ der
    // session row, das dort "Kontext wurde verdichtet" bedeutet.)
    num.textContent = `${pct}% · ⌛ ${fmtResetIn(win.reset_at)}`;
    if (win.reset_at) {
      const d = new Date(win.reset_at * 1000);
      num.title =
        `${pct}% verbraucht\n` +
        `Fenster: ${(win.limit_window_seconds/3600).toFixed(0)} h\n` +
        `Reset in ${fmtResetIn(win.reset_at)} (${d.toLocaleString("de-DE")})`;
    }
    wrap.appendChild(num);

    return wrap;
  }

  function fmtResetIn(unixSec) {
    if (!unixSec) return "—";
    const sec = unixSec - Math.floor(Date.now() / 1000);
    if (sec <= 0) return "jetzt";
    if (sec < 3600) return `${Math.floor(sec/60)} min`;
    if (sec < 86400) {
      const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60);
      return m > 0 ? `${h} h ${m} min` : `${h} h`;
    }
    const d = Math.floor(sec/86400), h = Math.floor((sec%86400)/3600);
    return h > 0 ? `${d} d ${h} h` : `${d} d`;
  }

  function creditsRow(credits) {
    const row = document.createElement("div");
    row.className = "plan__row";

    const lbl = document.createElement("div");
    lbl.className = "plan__label";
    lbl.textContent = "Credits";
    row.appendChild(lbl);

    const text = document.createElement("div");
    text.className = "plan__credits";
    const balance = credits.unlimited ? "∞" : credits.balance;
    const localMsg = (credits.approx_local_messages || []).join("–");
    const cloudMsg = (credits.approx_cloud_messages || []).join("–");
    text.textContent =
      `Balance: ${balance}  ·  ≈ ${localMsg || "0"} lokale msg  ·  ≈ ${cloudMsg || "0"} cloud msg`;
    if (credits.overage_limit_reached) {
      text.style.color = "var(--red)";
      text.title = "Overage-Limit erreicht";
    }
    row.appendChild(text);
    return row;
  }

  function planErrorEl(err) {
    const el = document.createElement("div");
    el.className = "plan__error";
    const code = err.code || "unknown";
    const messages = {
      disabled:        "Plan-Tracking ist deaktiviert.",
      auth_missing:    "Nicht eingeloggt — Codex starten und einloggen.",
      token_expired:   "Token abgelaufen — Codex starten, damit der Token refresht wird.",
      network:         "Netzwerk-Problem beim Abrufen der Plan-Daten.",
      timeout:         "Timeout beim Abrufen der Plan-Daten.",
      binary_missing:  "codex-tokens-Binary nicht gefunden.",
      binary_outdated: "codex-tokens ist zu alt für 'plan' — neue Version installieren.",
      no_output:       "codex-tokens lieferte keine Ausgabe.",
      bad_response:    "Unverständliche Antwort vom Plan-Endpoint.",
      fetch_failed:    "Verbindung zum Hilfs-Server unterbrochen.",
    };
    el.textContent = `Plan: ${messages[code] || err.error || code}`;
    el.title = err.error || "";
    return el;
  }

  // --- settings popover wiring ---

  PLAN_SETTINGS_BTN.addEventListener("click", (e) => {
    e.stopPropagation();
    PLAN_SETTINGS_EL.classList.toggle("hidden");
  });
  document.addEventListener("click", (e) => {
    if (PLAN_SETTINGS_EL.classList.contains("hidden")) return;
    if (e.target.closest("#plan-settings") || e.target.closest("#plan-settings-btn")) return;
    PLAN_SETTINGS_EL.classList.add("hidden");
  });

  document.getElementById("ps-enabled").addEventListener("change", (e) => {
    const checked = e.target.checked;
    const consented = planSettings?.plan_widget?.consent_acknowledged === true;
    if (checked && !consented) {
      // First time turning it on — revert the box and surface the consent
      // dialog. We don't persist enabled=true until the user confirms.
      e.target.checked = false;
      showConsentModal();
      return;
    }
    patchPlanSettings({ enabled: checked }).then(() => {
      if (checked) startPlanPolling();
      else stopPlanPolling();
      renderPlanWidget();
    });
  });

  // --- consent modal ---

  const CONSENT_EL = document.getElementById("plan-consent");
  const CONSENT_OK = document.getElementById("plan-consent-ok");
  const CONSENT_CANCEL = document.getElementById("plan-consent-cancel");

  function showConsentModal() {
    CONSENT_EL.classList.remove("hidden");
    // Focus the safe choice ("Abbrechen") so an accidental Enter does no harm.
    setTimeout(() => CONSENT_CANCEL.focus(), 50);
  }
  function hideConsentModal() {
    CONSENT_EL.classList.add("hidden");
  }

  CONSENT_OK.addEventListener("click", () => {
    // Atomic: record consent AND enable the widget in one patch.
    patchPlanSettings({ enabled: true, consent_acknowledged: true }).then(() => {
      document.getElementById("ps-enabled").checked = true;
      hideConsentModal();
      startPlanPolling();
      renderPlanWidget();
    });
  });
  CONSENT_CANCEL.addEventListener("click", hideConsentModal);
  CONSENT_EL.querySelector(".modal__backdrop").addEventListener("click", hideConsentModal);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !CONSENT_EL.classList.contains("hidden")) {
      hideConsentModal();
    }
  });

  for (const k of STATIC_ROWS) {
    document.getElementById(`ps-row-${k}`).addEventListener("change", (e) => {
      patchPlanSettings({ rows: { [k]: e.target.checked } }).then(renderPlanWidget);
    });
  }

  // Tick reset countdowns without re-fetching from the server.
  setInterval(() => {
    if (!planData || !planSettings?.plan_widget.enabled) return;
    renderPlanWidget();
  }, 30_000);

  loadPlanSettings();

  // ---------------------------------------------------------------------
  // Kill: confirmation modal + POST /kill
  // ---------------------------------------------------------------------

  const KILL_MODAL    = document.getElementById("kill-confirm");
  const KILL_DETAIL   = document.getElementById("kill-confirm-detail");
  const KILL_OK       = document.getElementById("kill-confirm-ok");
  const KILL_CANCEL   = document.getElementById("kill-confirm-cancel");
  let killPending = null;  // { kind, payload }

  // Open the kill-confirm modal pre-populated with a detail line. Caller
  // passes the payload that will be sent to POST /kill on confirm.
  function askKillConfirmation({ kind, title, detail, payload }) {
    killPending = { kind, payload };
    document.getElementById("kill-confirm-title").textContent = title;
    KILL_DETAIL.textContent = detail;
    KILL_MODAL.classList.remove("hidden");
    setTimeout(() => KILL_CANCEL.focus(), 50);
  }
  function hideKillModal() {
    KILL_MODAL.classList.add("hidden");
    killPending = null;
  }

  KILL_OK.addEventListener("click", () => {
    if (!killPending) { hideKillModal(); return; }
    const { payload, kind } = killPending;
    postJson("/kill", payload)
      .then((r) => {
        const pidText = r?.pid ? ` (pid ${r.pid})` : "";
        toast(kind === "ghost"
          ? `Geister-TUI beendet${pidText}`
          : `Session beendet${pidText}`);
      })
      .catch((e) => {
        const why = String(e?.message || e) === "409"
          ? "Terminal der Session nicht eindeutig erkennbar"
          : String(e?.message || e);
        toast(`Beenden fehlgeschlagen: ${why}`, true);
      });
    hideKillModal();
  });
  KILL_CANCEL.addEventListener("click", hideKillModal);
  KILL_MODAL.querySelector(".modal__backdrop").addEventListener("click", hideKillModal);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !KILL_MODAL.classList.contains("hidden")) {
      hideKillModal();
    }
  });

  // ---------------------------------------------------------------------
  // Ghost TUIs: slim cards above the regular sessions list
  // ---------------------------------------------------------------------
  //
  // A "ghost" is an interactive codex process no live session could be
  // mapped to — typically a TUI that was started but has not received its
  // first prompt yet. The server recomputes the list every 3 s and sends it
  // whenever it changes; we render a small line per ghost with a kill
  // button so they're easy to clean up.

  const GHOST_TPL = document.getElementById("ghost-template");

  function fmtAge(epochSec) {
    if (!epochSec) return "";
    const sec = Math.max(0, Math.floor(Date.now() / 1000 - epochSec));
    if (sec < 60)    return `${sec}s`;
    if (sec < 3600)  return `${Math.floor(sec/60)} min`;
    if (sec < 86400) return `${Math.floor(sec/3600)} h`;
    return `${Math.floor(sec/86400)} d`;
  }

  function applyGhosts(list) {
    ghosts = Array.isArray(list) ? list : [];
    renderGhosts();
    renderUI(); // empty-state depends on the ghost count too
  }

  function renderGhosts() {
    // Remove existing ghost cards.
    sessionsEl.querySelectorAll(".session--ghost").forEach((el) => el.remove());
    // Render fresh ones at the TOP of the sessions container. Sorted
    // oldest-first, so after the insertBefore loop the newest — the most
    // likely "I just opened this and forgot" candidate — stands on top.
    const sorted = [...ghosts].sort((a, b) => (a.started_at || 0) - (b.started_at || 0));
    for (const g of sorted) {
      const frag = GHOST_TPL.content.cloneNode(true);
      const el = frag.querySelector(".session--ghost");
      el.dataset.pid = String(g.pid);
      el.querySelector(".ghost__cwd").textContent = g.cwd || "(unbekannt)";
      el.querySelector(".ghost__cwd").title = g.cwd || "";
      el.querySelector(".ghost__pid").textContent = `pid ${g.pid}`;
      el.querySelector(".ghost__age").textContent = `⌚ ${fmtAge(g.started_at)}`;
      el.querySelector(".btn--open").addEventListener("click", () => {
        if (!g.cwd) return;
        postJson("/open-dir", { path: g.cwd, pid: g.pid })
          .then((r) => toast(r.focused ? "Dateimanager-Fenster nach vorne geholt" : "Verzeichnis geöffnet"))
          .catch(() => toast("Konnte Verzeichnis nicht öffnen", true));
      });
      el.querySelector(".btn--focus").addEventListener("click", () => {
        if (!g.cwd) return;
        postJson("/focus-terminal", { cwd: g.cwd, pid: g.pid })
          .then((r) => toast(r.warning || "Terminal fokussiert"))
          .catch(() => toast("Konnte Fenster nicht fokussieren", true));
      });
      el.querySelector(".btn--kill").addEventListener("click", () => {
        askKillConfirmation({
          kind: "ghost",
          title: "Geister-TUI beenden?",
          detail: `pid ${g.pid} · ${g.cwd || "(unbekannt)"}`,
          payload: { pid: g.pid },
        });
      });
      sessionsEl.insertBefore(el, sessionsEl.firstChild);
    }
  }

  // Tick the age timer once a second.
  setInterval(() => {
    sessionsEl.querySelectorAll(".session--ghost").forEach((el) => {
      const g = ghosts.find((x) => String(x.pid) === el.dataset.pid);
      if (!g) return;
      el.querySelector(".ghost__age").textContent = `⌚ ${fmtAge(g.started_at)}`;
    });
  }, 1000);
})();
