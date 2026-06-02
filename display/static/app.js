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
  const workerToggle = document.getElementById("show-workers");

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

    // 3. Remove DOM rows that are no longer visible.
    const visibleIds = new Set(visible.map((s) => s.session_id));
    sessionsEl.querySelectorAll(".session").forEach((row) => {
      if (!visibleIds.has(row.dataset.sid)) row.remove();
    });

    // 4. Sort order, top to bottom:
    //    - real sessions first, then workers
    //    - among real sessions: most-compacted first (each compact costs a
    //      whole turn-worth of tokens, so this is a 'cost so far' proxy);
    //      tiebreaker is current bar fill (percent_used desc)
    //    - among workers: highest cumulative consumption first
    visible.sort((a, b) => {
      const aw = isWorker(a), bw = isWorker(b);
      if (aw !== bw) return aw ? 1 : -1;
      if (!aw) {
        const cc = (b.compact_count ?? 0) - (a.compact_count ?? 0);
        if (cc !== 0) return cc;
        return (b.percent_used ?? 0) - (a.percent_used ?? 0);
      }
      return (b.session_total_tokens ?? 0) - (a.session_total_tokens ?? 0);
    });

    for (const snap of visible) {
      const row = ensureRow(snap.session_id);
      renderRow(row, snap);
      sessionsEl.appendChild(row);
    }

    // 5. Count label: distinguish visible vs hidden workers.
    const total = sessions.size;
    const workerCount = [...sessions.values()].filter(isWorker).length;
    const realCount = total - workerCount;
    if (showWorkers) {
      countEl.textContent =
        workerCount > 0
          ? `${realCount} aktiv · ${workerCount} Worker`
          : `${realCount} aktiv`;
    } else {
      countEl.textContent =
        workerCount > 0
          ? `${realCount} aktiv (+${workerCount} Worker)`
          : `${realCount} aktiv`;
    }
    emptyEl.classList.toggle("hidden", visible.length > 0);
    if (scope !== null && visible.length > 0) {
      cwdEl.textContent = scope;
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

  let planSettings = null;
  let planData = null;
  let planError = null;
  let planFetchTimer = null;

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
    for (const k of ["main", "codex_spark", "code_review", "credits"]) {
      const el = document.getElementById(`ps-row-${k}`);
      if (el) el.checked = !!pw.rows[k];
    }
    // Render any extra rows discovered at runtime (unknown additional_rate_limits).
    PS_ROWS_DYNAMIC.innerHTML = "";
    for (const [k, v] of Object.entries(pw.rows)) {
      if (["main", "codex_spark", "code_review", "credits"].includes(k)) continue;
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
    fetchPlan();
    planFetchTimer = setInterval(fetchPlan, PLAN_POLL_MS);
  }
  function stopPlanPolling() {
    if (planFetchTimer) clearInterval(planFetchTimer);
    planFetchTimer = null;
    planData = null;
    planError = null;
    PLAN_WIDGET_EL.classList.add("hidden");
    PLAN_WIDGET_EL.innerHTML = "";
  }

  async function fetchPlan() {
    try {
      const r = await fetch("/plan");
      const j = await r.json();
      if (r.status === 403 || j.code === "disabled") {
        planData = null;
        planError = j;
      } else if (j.error || j.code) {
        // Rust error envelope (auth_missing | token_expired | network | …).
        planData = null;
        planError = j;
      } else {
        planData = j;
        planError = null;
        // Auto-register any unknown additional_rate_limit names with default off.
        ensureKnownAdditionals(j.additional_rate_limits || []);
      }
    } catch (e) {
      planData = null;
      planError = { error: String(e), code: "fetch_failed" };
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

    if (planError) {
      PLAN_WIDGET_EL.appendChild(planErrorEl(planError));
      return;
    }
    if (!planData) {
      // First fetch hasn't returned yet.
      const el = document.createElement("div");
      el.className = "plan__error";
      el.textContent = "Plan-Daten werden geladen …";
      PLAN_WIDGET_EL.appendChild(el);
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

  for (const k of ["main", "codex_spark", "code_review", "credits"]) {
    document.getElementById(`ps-row-${k}`).addEventListener("change", (e) => {
      patchPlanSettings({ rows: { [k]: e.target.checked } }).then(renderPlanWidget);
    });
  }

  // Tick reset countdowns each second without re-fetching from the server.
  setInterval(() => {
    if (!planData || !planSettings?.plan_widget.enabled) return;
    PLAN_WIDGET_EL.querySelectorAll(".plan__bar").forEach((bar) => {
      // The countdown is regenerated on every full render; cheap to redo.
    });
    renderPlanWidget();
  }, 30_000);

  loadPlanSettings();
})();
