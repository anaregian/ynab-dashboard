"use strict";

// Category colours; deliberately avoids the person colours from config.yml
const PALETTE = ["#2ec4b6", "#ffb347", "#a78bfa", "#facc15", "#22d3ee", "#fb923c", "#84cc16",
                 "#e879f9", "#f87171", "#94a3b8", "#14b8a6", "#c084fc"];
const GOOD = "#3ccf91", BAD = "#ff6b6b";

const state = {
  meta: null,
  view: localStorage.getItem("view") || "combined",
  period: localStorage.getItem("period") || "12m",
  charts: {},
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

let fmt, fmtShort;
function setupCurrency(code) {
  fmt = new Intl.NumberFormat(undefined, { style: "currency", currency: code, maximumFractionDigits: 0 });
  fmtShort = new Intl.NumberFormat(undefined, { style: "currency", currency: code, notation: "compact",
                                                maximumFractionDigits: 1 });
}

// ---- periods ---------------------------------------------------------------------------
function shift(month, delta) {
  const [y, m] = month.split("-").map(Number);
  const t = y * 12 + (m - 1) + delta;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
}
function periods() {
  const today = state.meta.today, year = today.slice(0, 4);
  const first = state.meta.first_month || today;
  return {
    "1m": ["This month", today, today],
    "last": ["Last month", shift(today, -1), shift(today, -1)],
    "3m": ["Last 3 months", shift(today, -2), today],
    "6m": ["Last 6 months", shift(today, -5), today],
    "12m": ["Last 12 months", shift(today, -11), today],
    "ytd": ["This year", `${year}-01`, today],
    "lastyear": ["Last year", `${year - 1}-01`, `${year - 1}-12`],
    "all": ["All time", first, today],
  };
}
const monthLabel = (m) => new Date(`${m}-01T00:00`).toLocaleDateString(undefined, { month: "short", year: "2-digit" });

// ---- controls --------------------------------------------------------------------------
function personColor(name) {
  return state.meta.people.find((p) => p.name === name)?.color || PALETTE[0];
}

function renderControls() {
  const views = [{ name: "combined", label: "Together" }, ...state.meta.people.map((p) => ({ name: p.name, label: p.name, color: p.color }))];
  if (!views.some((v) => v.name === state.view)) state.view = "combined";
  $("views").innerHTML = views.map((v) =>
    `<button data-view="${esc(v.name)}" class="${v.name === state.view ? "active" : ""}">` +
    (v.color ? `<span class="dot" style="background:${v.color}"></span>` : "") + `${esc(v.label)}</button>`).join("");
  $("views").querySelectorAll("button").forEach((b) => b.onclick = () => {
    state.view = b.dataset.view;
    localStorage.setItem("view", state.view);
    renderControls();
    load();
  });

  const ps = periods();
  if (!ps[state.period]) state.period = "12m";
  $("period").innerHTML = Object.entries(ps).map(([k, [label]]) =>
    `<option value="${k}" ${k === state.period ? "selected" : ""}>${label}</option>`).join("");
  $("period").onchange = (e) => {
    state.period = e.target.value;
    localStorage.setItem("period", state.period);
    load();
  };
}

function renderStatus() {
  const m = state.meta;
  const times = m.plans.map((p) => p.last_sync).filter(Boolean).sort();
  const last = times.length ? new Date(times[0]).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "never";
  $("status").textContent = m.syncing ? "Syncing with YNAB…" : `Last synced ${last}`;
  $("sync").disabled = m.syncing;

  const problems = Object.entries(m.errors).map(([who, err]) => `<b>${esc(who)}</b>: ${esc(err)}`);
  if (m.mixed_currencies) problems.push("The plans use different currencies; amounts are added up without conversion.");
  $("banner").hidden = !problems.length;
  $("banner").innerHTML = problems.join("<br>");
}

$("sync").onclick = async () => {
  $("sync").disabled = true;
  const r = await (await fetch("/api/sync", { method: "POST" })).json();
  if (!r.started) { $("status").textContent = r.reason; $("sync").disabled = false; return; }
  $("status").textContent = "Syncing with YNAB…";
  waitForSync();
};

async function waitForSync() {
  do {
    await new Promise((r) => setTimeout(r, 2000));
    state.meta = await (await fetch("/api/meta")).json();
  } while (state.meta.syncing);
  renderStatus();
  load();
}

// ---- charts ----------------------------------------------------------------------------
function chart(id, config) {
  state.charts[id]?.destroy();
  state.charts[id] = new Chart($(id), config);
}

function themeDefaults() {
  const css = getComputedStyle(document.documentElement);
  Chart.defaults.color = css.getPropertyValue("--muted").trim();
  Chart.defaults.borderColor = css.getPropertyValue("--border").trim();
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.tooltip.callbacks.label = (ctx) => {
    const v = typeof ctx.parsed === "number" ? ctx.parsed
      : ctx.chart.options.indexAxis === "y" ? ctx.parsed.x : ctx.parsed.y;
    return ` ${ctx.dataset.label || ctx.label}: ${fmt.format(v)}`;
  };
  Chart.defaults.maintainAspectRatio = false;
}
const moneyAxis = { ticks: { callback: (v) => fmtShort.format(v) } };

function renderCharts(d) {
  const labels = d.months.map(monthLabel);
  const combined = d.view === "combined";
  const ink = getComputedStyle(document.documentElement).getPropertyValue("--text").trim();

  // Income vs spending: stacked per person when combined
  const spendSets = combined
    ? d.people.map((p) => ({ type: "bar", label: `${p} spent`, data: d.cashflow.spending_by_person[p],
                             backgroundColor: personColor(p), stack: "spend", borderRadius: 4 }))
    : [{ type: "bar", label: "Spent", data: d.cashflow.spending, backgroundColor: BAD, stack: "spend", borderRadius: 4 }];
  chart("cashflow", {
    data: {
      labels,
      datasets: [
        { type: "line", label: "Saved", data: d.cashflow.net, borderColor: ink + "99", backgroundColor: ink + "99",
          tension: 0.3, pointRadius: 3, borderDash: [4, 4], order: 0 },
        { type: "bar", label: "Income", data: d.cashflow.income, backgroundColor: GOOD, stack: "income", borderRadius: 4 },
        ...spendSets,
        { type: "bar", label: "Invested", data: d.cashflow.invested, backgroundColor: "#a78bfa", stack: "spend",
          borderRadius: 4, hidden: !d.cashflow.invested.some((v) => v) },
      ],
    },
    options: { scales: { y: moneyAxis, x: { stacked: true, grid: { display: false } } },
               interaction: { mode: "index", intersect: false } },
  });

  chart("groups", {
    type: "doughnut",
    data: { labels: d.groups.map((g) => g.name),
            datasets: [{ data: d.groups.map((g) => g.total), backgroundColor: PALETTE, borderWidth: 0 }] },
    options: { cutout: "62%", plugins: { legend: { position: "right" } } },
  });

  // Net worth. Together: each person's area stacked from $0, so the band sizes are true to scale
  // (the top edge is the combined total, shown in the tooltip footer). One person: a zoomed-in line.
  const nw = d.net_worth;
  const debts = { label: "Debts", data: nw.debts, borderColor: BAD, backgroundColor: BAD, borderDash: [4, 4],
                  tension: 0.3, pointRadius: 0, fill: false, stack: "debts",
                  hidden: true };  // a mortgage dwarfs everything else; toggle it from the legend
  const people = combined
    ? d.people.map((p, i) => ({ label: p, data: nw.by_person[p], borderColor: personColor(p),
                                backgroundColor: personColor(p) + "cc", fill: i ? "-1" : "origin",
                                stack: "people", tension: 0.3, pointRadius: 0, borderWidth: 1.5 }))
    : [{ label: "Net worth", data: nw.by_person[d.view], borderColor: personColor(d.view),
         backgroundColor: personColor(d.view) + "33", fill: "start", tension: 0.3, pointRadius: 0, borderWidth: 2.5 }];
  chart("networth", {
    type: "line",
    data: { labels: nw.months.map(monthLabel), datasets: [...people, debts] },
    options: {
      scales: { y: { ...moneyAxis, stacked: combined, beginAtZero: combined }, x: { grid: { display: false } } },
      interaction: { mode: "index", intersect: false },
      plugins: { tooltip: { callbacks: combined ? {
        footer: (items) => `Together: ${fmt.format(sum(items.filter((it) => it.dataset.stack === "people").map((it) => it.parsed.y)))}`,
      } : {} } },
    },
  });

  // This month's plan
  const b = d.budget.categories.slice(0, 15);
  $("budget-month").textContent = `· ${monthLabel(d.budget.month)}`;
  $("plan-stats").innerHTML = d.plan_months.map((p) =>
    `<span class="chip">${esc(p.person)} · Ready to assign <b>${fmt.format(p.ready_to_assign)}</b>` +
    (p.age_of_money != null ? ` · Age of money <b>${p.age_of_money}d</b>` : "") + `</span>`).join("");
  chart("budget", {
    type: "bar",
    data: {
      labels: b.map((c) => c.name),
      datasets: [
        { label: "Assigned", data: b.map((c) => c.assigned), backgroundColor: "#4f8cff88", borderRadius: 3 },
        { label: "Spent", data: b.map((c) => c.spent), borderRadius: 3,
          backgroundColor: b.map((c) => (c.available < 0 ? BAD : "#4f8cff")) },
      ],
    },
    options: { indexAxis: "y", scales: { x: { ticks: { ...moneyAxis.ticks, maxRotation: 0, maxTicksLimit: 5 } },
                                         y: { grid: { display: false } } },
               interaction: { mode: "index", intersect: false } },
  });

  chart("trend", {
    type: "bar",
    data: { labels, datasets: d.category_trend.map((c, i) => ({
      label: c.name, data: c.data, backgroundColor: c.name === "Other" ? "#94a3b888" : PALETTE[i % PALETTE.length],
      stack: "s" })) },
    options: { scales: { y: { ...moneyAxis, stacked: true }, x: { stacked: true, grid: { display: false } } },
               interaction: { mode: "index", intersect: false } },
  });

  $("split-card").hidden = !combined;
  if (combined) {
    const top = d.categories.filter((c) => c.total > 0).slice(0, 12);
    chart("split", {
      type: "bar",
      data: { labels: top.map((c) => c.name),
              datasets: d.people.map((p) => ({ label: p, data: top.map((c) => c.by_person[p]),
                                               backgroundColor: personColor(p), borderRadius: 3 })) },
      options: { indexAxis: "y", scales: { x: { ...moneyAxis, stacked: true }, y: { stacked: true, grid: { display: false } } },
                 interaction: { mode: "index", intersect: false } },
    });
  }
}

// ---- share of income spent -------------------------------------------------------------
const INVESTED = "#a78bfa";
const sum = (xs) => xs.reduce((s, x) => s + x, 0);
const pct = (part, whole) => (whole > 0 ? (part / whole) * 100 : null);

function incomeUse(d) {
  const c = d.cashflow;
  const rows = d.people.map((p) => ({
    name: p, color: personColor(p), income: sum(c.income_by_person[p]),
    spent: sum(c.spending_by_person[p]), invested: sum(c.invested_by_person[p]),
  }));
  if (d.view === "combined") {
    rows.push({ name: "Together", color: getComputedStyle(document.documentElement).getPropertyValue("--text").trim(),
                income: sum(c.income), spent: sum(c.spending), invested: sum(c.invested) });
  }

  const html = rows.map((r) => {
    const spent = pct(r.spent, r.income), inv = pct(r.invested, r.income);
    if (spent === null) {
      return `<div class="use-row"><div class="use-head"><b>${esc(r.name)}</b><span class="muted">no income in this period</span></div></div>`;
    }
    const over = spent + inv > 100;
    const left = Math.max(0, 100 - spent - inv);
    // When spending + investing exceed income, scale the bar so it still fills 100%
    const scale = over ? 100 / (spent + inv) : 1;
    return `<div class="use-row">
      <div class="use-head">
        <span><span class="dot" style="background:${r.color}"></span><b>${esc(r.name)}</b>
          <span class="muted"> · earned ${fmt.format(r.income)}</span></span>
        <span class="pct ${spent > 100 ? "bad" : ""}">${spent.toFixed(0)}%<small> spent</small></span>
      </div>
      <div class="use-bar">
        <span style="width:${spent * scale}%;background:${spent > 100 ? BAD : r.color}"></span>
        <span style="width:${inv * scale}%;background:${INVESTED}"></span>
        <span style="width:${left}%;background:${GOOD}"></span>
      </div>
      <div class="use-legend">
        <span><i style="background:${spent > 100 ? BAD : r.color}"></i>Spent ${fmt.format(r.spent)}</span>
        <span><i style="background:${INVESTED}"></i>Invested ${fmt.format(r.invested)} (${inv.toFixed(0)}%)</span>
        <span><i style="background:${over ? BAD : GOOD}"></i>${over
          ? `From savings ${fmt.format(r.spent + r.invested - r.income)} (${(spent + inv - 100).toFixed(0)}%)`
          : `Left over ${fmt.format(r.income - r.spent - r.invested)} (${left.toFixed(0)}%)`}</span>
      </div>
    </div>`;
  }).join("");

  // Who brings in what vs who pays for what
  let note = "";
  const ppl = rows.filter((r) => r.name !== "Together");
  const totIncome = sum(ppl.map((r) => r.income)), totSpent = sum(ppl.map((r) => r.spent));
  if (d.view === "combined" && totIncome > 0 && totSpent > 0) {
    note = `<div class="share-note">` + ppl.map((r) =>
      `<b>${esc(r.name)}</b> earns ${pct(r.income, totIncome).toFixed(0)}% of our income and covers ${pct(r.spent, totSpent).toFixed(0)}% of our spending`).join("<br>") + `</div>`;
  }
  $("income-use").innerHTML = html + note;

  // Rolling 3-month ratio of sums, so pay dates landing in different months don't make it jumpy
  const rolling = (spend, income) => spend.map((_, i) => {
    const lo = Math.max(0, i - 2);
    const inc = sum(income.slice(lo, i + 1));
    return inc > 0 ? +((sum(spend.slice(lo, i + 1)) / inc) * 100).toFixed(1) : null;
  });
  const lines = d.people.map((p) => ({
    label: p, data: rolling(c.spending_by_person[p], c.income_by_person[p]),
    borderColor: personColor(p), backgroundColor: personColor(p), tension: 0.3, pointRadius: 3, spanGaps: true,
  }));
  if (d.view === "combined") {
    const ink = getComputedStyle(document.documentElement).getPropertyValue("--text").trim();
    lines.push({ label: "Together", data: rolling(c.spending, c.income), borderColor: ink, backgroundColor: ink,
                 borderDash: [5, 4], tension: 0.3, pointRadius: 0, spanGaps: true });
  }
  lines.push({ label: "All of income", data: d.months.map(() => 100), borderColor: BAD + "aa", borderWidth: 1,
               pointRadius: 0, borderDash: [2, 3] });
  chart("spendrate", {
    type: "line",
    data: { labels: d.months.map(monthLabel), datasets: lines },
    options: {
      scales: { y: { beginAtZero: true, ticks: { callback: (v) => `${v}%` } }, x: { grid: { display: false } } },
      interaction: { mode: "index", intersect: false },
      plugins: { tooltip: { callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y ?? "–"}%` } } },
    },
  });
}

// ---- tables & KPIs ---------------------------------------------------------------------
function table(id, headers, rows) {
  if (!rows.length) { $(id).innerHTML = `<tr><td class="empty">Nothing here for this period</td></tr>`; return; }
  $(id).innerHTML = `<thead><tr>${headers.map(([h, cls]) => `<th class="${cls || ""}">${h}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map((r) => `<tr>${r.join("")}</tr>`).join("")}</tbody>`;
}
const td = (v, cls = "") => `<td class="${cls}">${v}</td>`;
const money = (v, cls = "num") => td(fmt.format(v), cls + (v < 0 ? " bad" : ""));
const tag = (p) => `<span class="tag" style="background:${personColor(p)}">${esc(p)}</span>`;

function renderKpis(d) {
  const k = d.kpi;
  const nw = d.net_worth;
  const nwNow = d.accounts.reduce((s, a) => s + a.balance, 0);
  const idx = Math.max(0, nw.months.indexOf(d.months[0]) - 1);
  const nwThen = d.people.reduce((s, p) => s + (nw.by_person[p][idx] ?? 0), 0);
  const nwChange = nwNow - nwThen;
  const cards = [
    ["Net worth", fmt.format(nwNow), `${nwChange >= 0 ? "▲" : "▼"} ${fmt.format(Math.abs(nwChange))} since ${monthLabel(nw.months[idx])}`, nwChange >= 0],
    ["Income", fmt.format(k.income), `${fmt.format(k.avg_monthly_income)} / month`],
    ["Spending", fmt.format(k.spending), `${fmt.format(k.avg_monthly_spending)} / month`],
    ["Saved", fmt.format(k.saved), (k.savings_rate == null ? "no income in period" : `${k.savings_rate}% savings rate`) +
      (k.invested ? ` · ${fmt.format(k.invested)} invested` : ""), k.saved >= 0],
  ];
  $("kpis").innerHTML = cards.map(([label, value, hint, good]) =>
    `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div>` +
    `<div class="hint ${good === undefined ? "" : good ? "good" : "bad"}">${hint}</div></div>`).join("");
}

function renderTables(d) {
  const combined = d.view === "combined";
  const max = Math.max(1, ...d.categories.map((c) => c.total));
  const totalSpend = d.categories.reduce((s, c) => s + Math.max(0, c.total), 0) || 1;
  table("categories",
    [["Category"], ["Group"], ["Total", "num"], ["Per month", "num"], ["Share", "num"], [""],
     ...(combined ? d.people.map((p) => [esc(p), "num"]) : [])],
    d.categories.map((c) => [
      td(esc(c.name)), td(esc(c.group), "muted"), money(c.total), money(c.monthly_avg),
      td(`${(Math.max(0, c.total) / totalSpend * 100).toFixed(1)}%`, "num muted"),
      td(`<div class="bar" style="width:${Math.max(0, c.total) / max * 100}%"></div>`, "bar-cell"),
      ...(combined ? d.people.map((p) => money(c.by_person[p])) : []),
    ]));

  table("payees", [["Payee"], ["Purchases", "num"], ["Total", "num"]],
    d.payees.map((p) => [td(esc(p.name)), td(p.count, "num muted"), money(p.total)]));

  table("largest", [["Date"], ["Payee"], ["Category"], ...(combined ? [["Who"]] : []), ["Amount", "num"]],
    d.largest.map((t) => [
      td(new Date(t.date + "T00:00").toLocaleDateString(), "muted"),
      td(esc(t.payee) + (t.memo ? ` <span class="muted">· ${esc(t.memo)}</span>` : "")),
      td(esc(t.category), "muted"), ...(combined ? [td(tag(t.person))] : []), money(t.amount)]));

  const typeNames = { checking: "Checking", savings: "Savings", cash: "Cash", creditCard: "Credit card",
                      lineOfCredit: "Line of credit", otherAsset: "Asset", otherLiability: "Liability",
                      mortgage: "Mortgage", autoLoan: "Auto loan", studentLoan: "Student loan",
                      personalLoan: "Personal loan", medicalDebt: "Medical debt", otherDebt: "Debt" };
  table("accounts", [["Account"], ...(combined ? [["Who"]] : []), ["Type"], ["Budget"], ["Balance", "num"]],
    d.accounts.map((a) => [td(esc(a.name)), ...(combined ? [td(tag(a.person))] : []),
      td(typeNames[a.type] || esc(a.type), "muted"), td(a.on_budget ? "On budget" : "Tracking", "muted"),
      money(a.balance)]));

  $("footnote").textContent = combined && d.excluded.count
    ? `${d.excluded.count} transactions (${fmt.format(d.excluded.amount)}) between the two of you are left out of the Together view, so nothing is counted twice.`
    : "";
}

// ---- main ------------------------------------------------------------------------------
async function load() {
  const [, start, end] = periods()[state.period];
  const params = new URLSearchParams({ view: state.view, start, end });
  const res = await fetch(`/api/summary?${params}`);
  if (!res.ok) { $("banner").hidden = false; $("banner").textContent = `Error: ${await res.text()}`; return; }
  const d = await res.json();
  renderKpis(d);
  renderCharts(d);
  incomeUse(d);
  renderTables(d);
}

async function init() {
  state.meta = await (await fetch("/api/meta")).json();
  setupCurrency(state.meta.currency);
  themeDefaults();
  renderControls();
  renderStatus();
  if (!state.meta.plans.length) {
    $("main").innerHTML = `<div class="empty">${state.meta.syncing ? "First sync with YNAB in progress…" : "No YNAB data yet. Check the tokens in .env, then press Sync."}</div>`;
    if (state.meta.syncing) { await waitForSyncThenReload(); }
    return;
  }
  await load();
  if (state.meta.syncing) waitForSync();
}

async function waitForSyncThenReload() {
  do {
    await new Promise((r) => setTimeout(r, 3000));
    state.meta = await (await fetch("/api/meta")).json();
  } while (state.meta.syncing);
  location.reload();
}

init();
