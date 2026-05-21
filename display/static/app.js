// codex-token-display — DOM logic and SSE client.

(() => {
  const sessions = new Map(); // session_id → snapshot
  const lastUpdate = new Map(); // session_id → epoch_ms of last received snapshot
  const sessionsEl = document.getElementById("sessions");
  const emptyEl = document.getElementById("empty");
  const countEl = document.getElementById("count");
  const cwdEl = document.getElementById("cwd");
  const connEl = document.getElementById("conn");
  const rowTpl = document.getElementById("row-template");

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
      const sid = rowEl.dataset.sid;
      const snap = sessions.get(sid);
      if (!snap?.session_cwd) return;
      postJson("/open-dir", { path: snap.session_cwd })
        .then(() => toast("Verzeichnis geöffnet"))
        .catch(() => toast("Konnte Verzeichnis nicht öffnen", true));
    });
    rowEl.querySelector(".btn--focus").addEventListener("click", () => {
      const sid = rowEl.dataset.sid;
      const snap = sessions.get(sid);
      if (!snap?.session_cwd) return;
      postJson("/focus-terminal", { cwd: snap.session_cwd })
        .then((r) => toast(r.warning ? r.warning : "Terminal fokussiert"))
        .catch(() => toast("Konnte Fenster nicht fokussieren", true));
    });
    rowEl.querySelector(".btn--path").addEventListener("click", () => {
      const sid = rowEl.dataset.sid;
      const snap = sessions.get(sid);
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
      const prompt = rolloverPrompt(snap);
      postJson("/copy", { text: prompt })
        .then(() => toast("Rollover-Prompt kopiert (in Codex einfügen)"))
        .catch(() => toast("Kopieren fehlgeschlagen", true));
    });
  }

  function shortId(sid) {
    return sid.split("-").slice(0, 2).join("-");
  }

  function rolloverPrompt(snap) {
    return `Bitte fasse den aktuellen Stand kompakt in HANDOVER.md zusammen:

1. Was wurde in der bisherigen Session erledigt? (Stichpunkte, max. 10)
2. Welche Tests/Builds laufen aktuell grün/rot?
3. Was sind die offenen TODOs aus dem aktiven Plan?
4. Kontextspezifische Snippets: aktuelle Branch, letzter Commit-Hash,
   geänderte Dateien.

Halte HANDOVER.md unter 200 Zeilen. Beende dann den aktuellen Turn,
damit eine neue Session mit HANDOVER.md als Kontext starten kann.

(Session ${snap?.session_id ?? "—"} · ${snap?.percent_used ?? "?"}% Kontext verbraucht)`;
  }

  function renderRow(rowEl, snap) {
    const pctUsed = snap.percent_used ?? 0;
    const pctLeft = snap.percent_left ?? 100;
    const ctx = snap.context_window ?? 0;
    const used = snap.tokens_in_context ?? 0;
    const free = Math.max(0, ctx - used);

    const fill = rowEl.querySelector(".bar__fill");
    // Reveal the leftmost pctUsed% of the fixed gradient; the rest stays
    // hidden, so the colour at each x-pixel always matches its position on
    // the bar (green on the left, red at the very right).
    fill.style.clipPath = `inset(0 ${(100 - pctUsed).toFixed(2)}% 0 0)`;

    const bar = rowEl.querySelector(".bar");
    bar.title =
      `${free.toLocaleString("de-DE")} Token frei (${pctLeft}%)\n` +
      `${used.toLocaleString("de-DE")} Token verbraucht (${pctUsed}%)\n` +
      `${ctx.toLocaleString("de-DE")} Token Kontextfenster gesamt`;

    rowEl.querySelector(".free").textContent = fmtTokens(free);
    rowEl.querySelector(".pctfree").textContent = pctLeft;

    const cwdEl = rowEl.querySelector(".cwd");
    cwdEl.textContent = snap.session_cwd ?? "(unbekannt)";
    cwdEl.title = snap.session_cwd ?? "";

    const statusEl = rowEl.querySelector(".status");
    statusEl.classList.remove("status--active", "status--closed", "status--unknown");
    if (snap.session_active === true) {
      statusEl.classList.add("status--active");
      statusEl.title = "Codex-Prozess läuft (Datei wird aktiv geschrieben)";
    } else if (snap.session_active === false) {
      statusEl.classList.add("status--closed");
      statusEl.title = "Codex-Session beendet — letzter Stand eingefroren";
    } else {
      statusEl.classList.add("status--unknown");
      statusEl.title = "Status unbekannt";
    }

    const idleEl = rowEl.querySelector(".idle");
    const idleMs = Date.now() - (lastUpdate.get(snap.session_id) ?? Date.now());
    idleEl.textContent = `⌚ ${fmtIdle(Math.floor(idleMs / 1000))}`;

    const sidEl = rowEl.querySelector(".sid");
    sidEl.textContent = shortId(snap.session_id ?? "");
    sidEl.title = `${snap.session_id}\n(klicken zum Kopieren)`;

    rowEl.querySelector(".usage").textContent =
      `${fmtTokens(used)} / ${fmtTokens(ctx)}`;
    rowEl.querySelector(".cumulative").textContent =
      `Σ ${fmtTokens(snap.session_total_tokens)}`;
  }

  function applySnapshot(snap) {
    if (!snap.session_id) return;
    sessions.set(snap.session_id, snap);
    lastUpdate.set(snap.session_id, Date.now());
    renderUI();
  }

  function renderUI() {
    // Drop sessions that are closed.
    for (const [sid, snap] of sessions) {
      if (snap.session_active === false) {
        const row = sessionsEl.querySelector(`[data-sid="${sid}"]`);
        if (row) row.remove();
        sessions.delete(sid);
        lastUpdate.delete(sid);
      }
    }

    // Sort by percent_used desc (most-urgent first).
    const sorted = [...sessions.values()].sort(
      (a, b) => (b.percent_used ?? 0) - (a.percent_used ?? 0)
    );

    for (const snap of sorted) {
      const row = ensureRow(snap.session_id);
      renderRow(row, snap);
      sessionsEl.appendChild(row); // re-order
    }

    countEl.textContent = `${sessions.size} aktiv`;
    emptyEl.classList.toggle("hidden", sessions.size > 0);
    // Topbar scope label is set by initScope() at startup and only overridden
    // here in scoped (single-project) mode where the cwd is fixed.
    if (scope !== null && sessions.size > 0) {
      cwdEl.textContent = scope;
      cwdEl.title = scope;
    }
  }

  // Server tells us whether we're scoped to a single project or system-wide.
  let scope = null;
  async function initScope() {
    try {
      const r = await fetch("/scope");
      const data = await r.json();
      scope = data.scope ?? null;
      if (scope === null) {
        cwdEl.textContent = "Alle Projekte";
        cwdEl.title = "PC-weite Übersicht aller laufenden Codex-Sessions";
      } else {
        cwdEl.textContent = scope;
        cwdEl.title = scope;
      }
    } catch {
      cwdEl.textContent = "?";
    }
  }

  // Refresh idle timers every second.
  setInterval(() => {
    for (const sid of sessions.keys()) {
      const row = sessionsEl.querySelector(`[data-sid="${sid}"]`);
      if (!row) continue;
      const idleMs = Date.now() - (lastUpdate.get(sid) ?? Date.now());
      row.querySelector(".idle").textContent = `⌚ ${fmtIdle(Math.floor(idleMs / 1000))}`;
    }
  }, 1000);

  // -- SSE connection --

  function connect() {
    const es = new EventSource("/events");
    es.onopen = () => {
      connEl.classList.remove("conn--disconnected");
      connEl.classList.add("conn--connected");
      connEl.title = "Mit dem Hilfs-Server verbunden";
    };
    es.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data);
        if (event.type === "snapshot") applySnapshot(event.data);
      } catch {
        /* ignore */
      }
    };
    es.onerror = () => {
      connEl.classList.remove("conn--connected");
      connEl.classList.add("conn--disconnected");
      connEl.title = "Keine Verbindung — Server gestoppt?";
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

  // -- help: open the README in a separate, draggable OS window --
  //
  // Chromium's --app mode propagates the borderless app-window style to
  // popups, so window.open() with popup=yes gives us a standalone window
  // without URL bar — drag-anywhere, resizable, can sit next to the main
  // window. Using a named target means clicking 📖 again just focuses the
  // existing help window instead of opening a second copy.
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

  document.addEventListener("click", (e) => {
    if (e.target.closest("#help-btn")) {
      e.preventDefault();
      openHelp();
    }
  });

  initScope().then(connect);
})();
