"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const state = { people: [], colors: {}, categories: [], currency: "USD" };
let fmt;

const tag = (p) => `<span class="tag" style="background:${state.colors[p] || "#4f8cff"}">${esc(p)}</span>`;

async function load() {
  const data = await (await fetch("/api/categories")).json();
  state.people = data.people;
  state.categories = data.categories;
  render();
}

function render() {
  const q = $("search").value.trim().toLowerCase();
  const who = $("person").value;
  const onlyUnmatched = $("unmatched").checked;
  const showHidden = $("hidden").checked;

  // Who feeds each combined category
  const members = {};
  for (const c of state.categories) (members[c.key] ||= []).push(c);

  const names = [...new Set(state.categories.map((c) => c.shared))].sort((a, b) => a.localeCompare(b));
  $("names").innerHTML = names.map((n) => `<option value="${esc(n)}">`).join("");

  const rows = state.categories.filter((c) => {
    if (who && c.person !== who) return false;
    if (!showHidden && c.hidden && !c.spend_12m && !c.mapped) return false;
    if (onlyUnmatched && new Set(members[c.key].map((m) => m.person)).size > 1) return false;
    if (q && ![c.name, c.shared, c.ynab_group].some((s) => (s || "").toLowerCase().includes(q))) return false;
    return true;
  });

  $("count").textContent = `${rows.length} of ${state.categories.length}`;
  $("table").innerHTML = `<thead><tr><th>Who</th><th>YNAB group</th><th>Category in YNAB</th>
      <th class="num">Spent, 12 mo</th><th>Shows as</th><th>Combined with</th></tr></thead><tbody>` +
    rows.map((c) => {
      const others = members[c.key].filter((m) => m !== c);
      return `<tr data-plan="${esc(c.plan_id)}" data-id="${esc(c.id)}">
        <td>${tag(c.person)}</td>
        <td class="muted">${esc(c.ynab_group)}</td>
        <td>${esc(c.name)}${c.hidden ? ' <span class="muted">(hidden)</span>' : ""}</td>
        <td class="num ${c.spend_12m < 0 ? "good" : ""}">${c.spend_12m ? fmt.format(c.spend_12m) : '<span class="muted">–</span>'}</td>
        <td class="map-cell">
          <input class="map ${c.mapped ? "is-mapped" : ""}" list="names" value="${esc(c.mapped || "")}"
                 placeholder="${esc(c.name)}" aria-label="Shows as">
        </td>
        <td>${others.length ? others.map((m) => `${tag(m.person)} ${esc(m.name)}`).join("<br>") : '<span class="muted">–</span>'}</td>
      </tr>`;
    }).join("") + "</tbody>";

  document.querySelectorAll("input.map").forEach((input) => {
    input.addEventListener("change", () => save(input));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });
  });
}

async function save(input) {
  const tr = input.closest("tr");
  const res = await fetch(`/api/categories/${tr.dataset.plan}/${tr.dataset.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ shared: input.value.trim() || null }),
  });
  flash(res.ok ? "Saved" : `Couldn't save: ${await res.text()}`, !res.ok);
  // Re-rendering replaces the inputs: keep the scroll position and the focused box (e.g. after Tab)
  const scroll = document.querySelector(".table-wrap").scrollTop;
  const focused = document.activeElement?.closest?.("tr");
  await load();
  document.querySelector(".table-wrap").scrollTop = scroll;
  if (focused) {
    document.querySelector(`tr[data-plan="${focused.dataset.plan}"][data-id="${focused.dataset.id}"] input.map`)?.focus();
  }
}

let flashTimer;
function flash(text, bad) {
  $("status").textContent = text;
  $("status").className = `status ${bad ? "bad" : "good"}`;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { $("status").textContent = ""; }, 2500);
}

async function init() {
  const meta = await (await fetch("/api/meta")).json();
  fmt = new Intl.NumberFormat(undefined, { style: "currency", currency: meta.currency, maximumFractionDigits: 0 });
  for (const p of meta.people) state.colors[p.name] = p.color;
  $("person").innerHTML = `<option value="">Everyone</option>` +
    meta.people.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("");
  ["search", "person", "unmatched", "hidden"].forEach((id) => $(id).addEventListener("input", render));
  await load();
}

init();
