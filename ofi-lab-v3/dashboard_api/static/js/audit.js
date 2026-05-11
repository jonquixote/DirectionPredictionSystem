/* Audit page — safe DOM only */

function appendCell(row, text) {
  const td = document.createElement("td");
  td.textContent = text == null ? "" : String(text);
  row.appendChild(td);
}

async function load() {
  const params = new URLSearchParams(
    new FormData(document.getElementById("audit-filter")));
  const r = await fetch("/api/audit?" + params);
  const {entries} = await r.json();
  const tbody = document.querySelector("#audit-table tbody");
  while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
  for (const e of entries) {
    const row = document.createElement("tr");
    appendCell(row, new Date(e.ts_ms).toISOString());
    appendCell(row, e.model_name);
    appendCell(row, e.action);
    appendCell(row, e.actor);
    appendCell(row, e.reason || "");
    appendCell(row, `${e.before_state} → ${e.after_state}`);
    tbody.appendChild(row);
  }
}

document.getElementById("audit-filter").addEventListener("submit", e => {
  e.preventDefault();
  load();
});
load();
