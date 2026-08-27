/* Greenlight Studio — front end.
 *
 * No build step and no framework, on purpose: the interesting engineering is
 * behind /api, and a page that is one file of plain JS cannot break its own
 * deployment the week of a deadline.
 *
 * Everything shown here comes from the API response. Nothing on this page is a
 * placeholder number - if the API did not return it, it is not drawn.
 */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  mode: "film",
  locale: "en",
  strings: {},
  samples: [],
  analysis: null,
  whatif: null,
  excluded: new Set(),   // 委員が「比較対象ではない」と外した作品
  busy: false,
};

/* ------------------------------------------------------------------ utils */

const usd = (n) => {
  if (n == null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${Math.round(n / 1e3)}K`;
  return `$${Math.round(n)}`;
};

const pct = (n) => (n == null ? "—" : `${Number(n).toFixed(1)}%`);
const num = (n, d = 2) => (n == null ? "—" : Number(n).toFixed(d));
const t = (key, fallback) => state.strings[key] || fallback || key;

const digits = (value) => {
  const n = parseInt(String(value).replace(/[^\d]/g, ""), 10);
  return Number.isFinite(n) ? n : 0;
};

const commas = (n) => n.toLocaleString("en-US");

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

/* ------------------------------------------------------------------ i18n */

async function loadStrings(locale) {
  const res = await fetch(`/api/strings?locale=${encodeURIComponent(locale)}`);
  const body = await res.json();
  state.locale = body.locale;
  state.strings = body.strings;
  applyStrings();
}

function applyStrings() {
  $$("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n, node.textContent);
  });
  $$("[data-i18n-placeholder]").forEach((node) => {
    node.placeholder = t(node.dataset.i18nPlaceholder, node.placeholder);
  });
  document.documentElement.lang = state.locale;
  fillMonths();
  if (state.analysis) render(state.analysis);
}

function fillMonths() {
  const select = $("#release-month");
  const current = select.value;
  const names = state.locale === "ja"
    ? ["指定なし", "1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"]
    : ["Unspecified", "January", "February", "March", "April", "May", "June",
       "July", "August", "September", "October", "November", "December"];
  select.innerHTML = "";
  names.forEach((name, i) => select.append(el("option", { value: String(i) }, name)));
  select.value = current || "0";
}

/* ------------------------------------------------------------------ setup */

async function boot() {
  await loadStrings(navigator.language?.startsWith("ja") ? "ja" : "en");

  const health = await fetch("/api/health").then((r) => r.json()).catch(() => null);
  if (health?.catalogue?.film) {
    const f = health.catalogue.film;
    const s = health.catalogue.series || {};
    $("#catalogue-line").textContent =
      ` ${commas(f.rows)} films (${f.first_year}–${f.last_year}), ` +
      `${commas(s.rows || 0)} series in ${s.languages || 0} languages.`;
  }

  state.samples = await fetch("/api/samples").then((r) => r.json())
    .then((b) => b.samples).catch(() => []);
  renderSamples();

  $("#mode-toggle").addEventListener("click", (e) => {
    const button = e.target.closest("button");
    if (!button) return;
    setMode(button.dataset.mode);
  });

  $("#locale-toggle").addEventListener("click", async (e) => {
    const button = e.target.closest("button");
    if (!button) return;
    $$("#locale-toggle button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === button)));
    await loadStrings(button.dataset.locale);
  });

  ["#budget", "#per-episode", "#episodes"].forEach((sel) => {
    const input = $(sel);
    input.addEventListener("blur", () => { input.value = commas(digits(input.value)); });
  });

  $("#run").addEventListener("click", analyse);
}

function setMode(mode) {
  state.mode = mode;
  $$("#mode-toggle button").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  $("#film-fields").classList.toggle("hidden", mode !== "film");
  $("#series-fields").classList.toggle("hidden", mode === "film");
  renderSamples();
}

function renderSamples() {
  const host = $("#samples");
  host.innerHTML = "";
  state.samples
    .filter((s) => s.mode === state.mode)
    .forEach((s) => {
      host.append(el("button", { type: "button", onclick: "" }, s.title));
      host.lastChild.addEventListener("click", () => {
        $("#material").value = s.material;
        if (s.budget_usd) $("#budget").value = commas(s.budget_usd);
        if (s.per_episode_budget_usd) $("#per-episode").value = commas(s.per_episode_budget_usd);
        if (s.episodes) $("#episodes").value = String(s.episodes);
        if (s.release_month != null) $("#release-month").value = String(s.release_month);
      });
    });
}

/* ------------------------------------------------------------------ run */

function proposalFromForm() {
  return {
    mode: state.mode,
    budget_usd: digits($("#budget").value) || 25000000,
    per_episode_budget_usd: digits($("#per-episode").value) || 3000000,
    episodes: digits($("#episodes").value) || 8,
    release_month: parseInt($("#release-month").value, 10) || 0,
  };
}

async function analyse() {
  if (state.busy) return;
  const material = $("#material").value.trim();
  const note = $("#input-note");
  if (material.length < 20) {
    note.hidden = false;
    note.className = "notice error";
    note.textContent = state.locale === "ja"
      ? "素材が短すぎます。ログライン一行でも構いませんので入力してください。"
      : "That is too short. A single logline is enough, but there has to be one.";
    return;
  }
  note.hidden = true;

  state.busy = true;
  const button = $("#run");
  button.disabled = true;
  button.innerHTML = "";
  button.append(el("span", { class: "spinner" }), t("action.analyzing", "Working…"));

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ material, locale: state.locale, ...proposalFromForm() }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    state.analysis = body;
    state.excluded.clear();
    render(body);
    buildLevers();
    $("#results").hidden = false;
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    note.hidden = false;
    note.className = "notice error";
    note.textContent = `${t("error.generic", "Something went wrong.")} ${err.message}`;
  } finally {
    state.busy = false;
    button.disabled = false;
    button.textContent = t("action.analyze", "Run the committee");
  }
}

/* ------------------------------------------------------------------ render */

function render(a) {
  const score = a.score.value;
  const verdict = a.score.verdict;

  const dial = $("#dial");
  dial.style.setProperty("--pct", score);
  dial.style.setProperty("--dial", {
    GREENLIT: "var(--green)", CONDITIONAL: "var(--amber)",
    RESHAPE: "var(--gold)", PASS: "var(--rust)",
  }[verdict] || "var(--navy)");
  $("#score").textContent = score;

  const label = $("#verdict");
  label.textContent = t(`verdict.${verdict}`, verdict);
  label.className = `verdict-label verdict-${verdict}`;
  $("#verdict-title").textContent = a.brief?.normalised_title || "";

  $("#run-meta").innerHTML = "";
  $("#run-meta").append(
    metaItem(t("meta.clickhouse", "ClickHouse"), `${num(a.clickhouse_ms, 0)} ms`),
    metaItem(t("meta.elapsed", "Elapsed"), `${num(a.elapsed_seconds, 1)} s`),
    metaItem(t("meta.cost", "Model cost"), `$${num(a.cost?.estimated_cost_usd, 4)}`),
    metaItem("Tools", String(a.guardrails?.tool_calls ?? 0)),
  );

  const warn = $("#warnings");
  const messages = [...(a.warnings || []), ...(a.guardrails?.tripped || [])];
  warn.hidden = messages.length === 0;
  warn.className = "notice";
  warn.textContent = messages.join(" ");

  renderBreakdown(a);
  drawChart(a);
  renderProjection(a);
  renderComps(a);
  renderTrace(a);
  $("#memo").textContent = a.memo || "";
  renderAssumptions(a);
}

function renderBreakdown(a) {
  const host = $("#breakdown");
  host.innerHTML = "";
  const components = a.score?.components || {};
  const entries = Object.entries(components).filter(([k]) => !k.startsWith("_"));
  if (!entries.length) return;

  const box = el("div", { class: "breakdown" },
    el("div", { class: "breakdown-title" }, t("panel.breakdown", "What the score is made of")));

  entries.forEach(([key, value]) => {
    const percent = Math.max(0, Math.min(100, Number(value) * 100));
    box.append(el("div", { class: "breakdown-row" },
      el("div", { class: "k" }, t(`comp.${key}`, key.replace(/_/g, " "))),
      el("div", { class: "breakdown-bar" }, el("span", { style: `width:${percent}%` })),
      el("div", { class: "v" }, percent.toFixed(0))));
  });

  if (components._basis) box.append(el("div", { class: "breakdown-basis" }, components._basis));
  host.append(box);
}

function metaItem(key, value) {
  return el("span", {}, `${key} `, el("b", {}, value));
}

function drawChart(a) {
  const host = $("#chart");
  host.innerHTML = "";
  if (a.mode !== "film") return;
  const p = a.projection;
  const bars = [
    { label: t("field.bear", "Bear"), value: p.bear_gross_usd, fill: "#a8503c" },
    { label: t("field.base", "Base"), value: p.base_gross_usd, fill: "#1b4965" },
    { label: t("field.bull", "Bull"), value: p.bull_gross_usd, fill: "#2f7a5a" },
  ];
  const breakEven = p.break_even_gross_usd;
  const max = Math.max(breakEven, ...bars.map((b) => b.value)) * 1.12;
  const W = 640, H = 168, left = 66, top = 12, barH = 30, gap = 16;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", "100%");
  svg.setAttribute("role", "img");

  const px = (v) => left + (v / max) * (W - left - 90);

  bars.forEach((bar, i) => {
    const y = top + i * (barH + gap);
    svg.append(
      svgEl("text", { x: left - 8, y: y + barH / 2 + 4, "text-anchor": "end",
        "font-size": 12, fill: "#2f3e46", opacity: 0.7 }, bar.label),
      svgEl("rect", { x: left, y, width: Math.max(2, px(bar.value) - left), height: barH,
        rx: 4, fill: bar.fill, opacity: 0.85 }),
      svgEl("text", { x: px(bar.value) + 8, y: y + barH / 2 + 4, "font-size": 12,
        fill: "#2f3e46", "font-weight": 600 }, usd(bar.value)),
    );
  });

  const x = px(breakEven);
  svg.append(
    svgEl("line", { x1: x, y1: 4, x2: x, y2: top + 3 * (barH + gap) - gap + 6,
      stroke: "#e0a96d", "stroke-width": 2, "stroke-dasharray": "5 4" }),
    svgEl("text", { x: x, y: H - 8, "text-anchor": "middle", "font-size": 11,
      fill: "#b07d2a" }, `${t("field.break_even", "Break-even")} ${usd(breakEven)}`),
  );
  host.append(svg);
}

function svgEl(tag, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}

function renderProjection(a) {
  const host = $("#projection-stats");
  host.innerHTML = "";
  const p = a.projection;
  const stats = a.mode === "film"
    ? [
        [t("field.probability", "Reaches break-even"), pct(p.probability_break_even_pct)],
        ["ROI", `${num(p.bear_roi)} / ${num(p.base_roi)} / ${num(p.bull_roi)}`],
        [t("field.break_even", "Break-even gross"), usd(p.break_even_gross_usd)],
        [t("field.sample", "Sample"), commas(p.comp_sample_size || 0)],
      ]
    : [
        [t("field.renewal", "Returns for season 2"), pct(p.renewal_probability_pct)],
        [t("field.cancellation", "Ends after one season"), pct(p.cancellation_risk_pct)],
        [t("field.expected_seasons", "Expected seasons"), num(p.expected_seasons)],
        [t("field.season_cost", "Season cost"), usd(p.season_cost_usd)],
      ];
  stats.forEach(([k, v]) =>
    host.append(el("div", { class: "stat" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v))));
}

function renderComps(a) {
  const rows = a.evidence?.comparable_titles || [];
  $("#comps-count").textContent = rows.length ? `(${rows.length})` : "";
  const table = $("#comps");
  table.innerHTML = "";
  if (!rows.length) {
    table.append(el("tbody", {}, el("tr", {}, el("td", {},
      state.locale === "ja" ? "類似作が見つかりませんでした。" : "No comparable titles were found."))));
    return;
  }
  const film = a.mode === "film";
  const useLabel = state.locale === "ja" ? "採用" : "Use";
  const head = film
    ? [useLabel, "Title", "Year", "Budget", "Gross", "ROI", "Score", "Distance"]
    : [useLabel, state.locale === "ja" ? "作品" : "Title", state.locale === "ja" ? "開始" : "From",
       state.locale === "ja" ? "季" : "Seasons", state.locale === "ja" ? "話数" : "Episodes",
       state.locale === "ja" ? "更新" : "Returned", state.locale === "ja" ? "言語" : "Language", "Distance"];
  table.append(el("thead", {}, el("tr", {}, head.map((h, i) =>
    el("th", { class: i > 2 ? "num" : "" }, h)))));

  const body = el("tbody");
  rows.forEach((c) => {
    const title = state.locale === "ja" && c.title_ja ? c.title_ja : c.title;
    const cells = film
      ? [title, c.release_year, usd(c.budget_usd), usd(c.revenue_usd), num(c.roi_multiple),
         c.has_audience_score ? num(c.audience_score, 0) : "—", num(c.tone_distance, 3)]
      : [title, c.release_year, c.number_of_seasons, c.number_of_episodes || "—",
         c.returned_after_s1 ? "✓" : "—", c.original_language || "—", num(c.tone_distance, 3)];

    // The objection a committee actually makes is "that one is not comparable".
    // A tool that cannot take it is asking to be believed rather than used.
    const excluded = state.excluded.has(c.wikidata_id);
    const box = el("input", { type: "checkbox", class: "use-comp" });
    box.checked = !excluded;
    box.disabled = !c.wikidata_id;
    box.setAttribute("aria-label", `${title}`);
    box.addEventListener("change", () => {
      if (box.checked) state.excluded.delete(c.wikidata_id);
      else state.excluded.add(c.wikidata_id);
      runWhatIf();
    });

    const tr = el("tr", { class: excluded ? "comp-excluded" : "" },
      el("td", {}, box),
      cells.map((v, i) => el("td", { class: i > 1 ? "num" : "" }, v == null ? "—" : v)));
    body.append(tr);
  });
  table.append(body);
}

function renderTrace(a) {
  const host = $("#trace");
  host.innerHTML = "";
  (a.trace || []).forEach((step) => {
    const args = Object.entries(step.arguments || {})
      .filter(([, v]) => v !== "" && v !== 0 && v != null &&
        !(Array.isArray(v) && !v.length) && String(v).length < 60)
      .map(([k, v]) => `${k}=${v}`)
      .join("  ");
    const row = el("div", { class: "trace-row" },
      el("div", {},
        el("span", { class: `trace-tool${step.error ? " trace-error" : ""}` }, step.tool),
        args ? el("span", { class: "trace-args" }, `  ${args}`) : null,
        step.error ? el("div", { class: "trace-args trace-error" }, step.error) : null,
      ),
      el("div", { class: "trace-ms" },
        step.row_count ? `${step.row_count} rows · ` : "",
        `${num(step.elapsed_ms, 0)} ms`),
    );
    host.append(row);
    if (step.sql) {
      host.append(el("div", {},
        el("div", { class: "trace-args", style: "font-size:11px;margin-top:4px" },
          t("meta.generated_sql", "SQL the analyst wrote")),
        el("pre", { class: "sql" }, step.sql)));
    }
  });
}

function renderAssumptions(a) {
  const host = $("#assumptions");
  host.innerHTML = "";
  (a.projection?.assumptions || []).forEach((item) => {
    host.append(el("div", { class: "assumption" },
      el("b", {}, item.key),
      item.value != null && String(item.value).length < 60
        ? el("span", { class: "trace-args" }, `  ${item.value}`) : null,
      el("p", {}, item.note)));
  });
}

/* ------------------------------------------------------------------ what-if */

function buildLevers() {
  const host = $("#whatif-levers");
  host.innerHTML = "";
  const a = state.analysis;
  if (!a) return;

  const levers = a.mode === "film"
    ? [{ id: "budget_usd", label: t("input.budget", "Production budget"),
         min: Math.max(500000, Math.round(a.proposal.budget_usd / 6)),
         max: Math.round(a.proposal.budget_usd * 6),
         step: 500000, value: a.proposal.budget_usd, format: usd }]
    : [{ id: "per_episode_budget_usd", label: t("input.per_episode", "Budget per episode"),
         min: 100000, max: Math.max(20000000, a.proposal.per_episode_budget_usd * 4),
         step: 100000, value: a.proposal.per_episode_budget_usd, format: usd },
       { id: "episodes", label: t("input.episodes", "Episodes"),
         min: 1, max: 40, step: 1, value: a.proposal.episodes, format: String }];

  levers.forEach((lever) => {
    const readout = el("b", {}, lever.format(lever.value));
    const input = el("input", { type: "range", min: lever.min, max: lever.max,
      step: lever.step, value: lever.value, "data-id": lever.id });
    input.addEventListener("input", () => { readout.textContent = lever.format(digits(input.value)); });
    input.addEventListener("change", runWhatIf);
    host.append(el("div", { class: "lever" },
      el("div", { class: "lever-head" }, el("span", {}, lever.label), readout), input));
  });
  $("#whatif-card").hidden = false;
  $("#whatif-meta").innerHTML = "";
}

let whatIfTimer = null;

function runWhatIf() {
  clearTimeout(whatIfTimer);
  whatIfTimer = setTimeout(async () => {
    const a = state.analysis;
    if (!a) return;
    const payload = { request_id: a.request_id, mode: a.mode, ...a.proposal,
                      excluded_ids: Array.from(state.excluded) };
    $$("#whatif-levers input[type=range]").forEach((input) => {
      payload[input.dataset.id] = digits(input.value);
    });
    try {
      const res = await fetch("/api/whatif", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      state.whatif = body;
      applyWhatIf(body);
    } catch (err) {
      $("#whatif-meta").textContent = err.message;
    }
  }, 110);
}

function applyWhatIf(w) {
  const base = state.analysis;
  const merged = { ...base, projection: w.projection, score: w.score, proposal: w.proposal };
  const delta = w.score.value - base.score.value;

  render(merged);   // memo and trace stay as they were; the numbers move
  $("#memo").textContent = base.memo || "";

  const meta = $("#whatif-meta");
  meta.innerHTML = "";
  if (w.comparables_excluded) {
    const note = $("#warnings");
    note.hidden = false;
    note.className = "notice";
    note.textContent = t("comps.excluded_note",
      "Excluding evidence changes the verdict. That is the point, and the reason it is shown.");
  }
  meta.append(
    metaItem(t("meta.clickhouse", "ClickHouse"), `${num(w.clickhouse_ms, 0)} ms`),
    metaItem("Model calls", String(w.model_calls)),
    metaItem(t("panel.comps", "Comparables"),
      w.comparables_excluded
        ? `${w.comparables_used} (−${w.comparables_excluded})`
        : String(w.comparables_used ?? "")),
    w.band_sample_size ? metaItem(t("field.budget_band", "Band"), commas(w.band_sample_size)) : null,
    w.budget_band_usd
      ? metaItem(t("field.budget_band", "Budget band"),
          `${usd(w.budget_band_usd[0])}–${usd(w.budget_band_usd[1])}`) : null,
    el("span", {}, "Δ ", el("b", { class: `delta ${delta >= 0 ? "up" : "down"}` },
      `${delta >= 0 ? "+" : ""}${delta}`)),
  );
}

boot();
