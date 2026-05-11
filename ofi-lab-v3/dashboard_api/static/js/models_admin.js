/* Models admin — safe DOM only, no innerHTML with API data */

async function loadModels() {
  const r = await fetch("/api/models/list");
  const {models} = await r.json();
  const grid = document.getElementById("models-grid");
  while (grid.firstChild) grid.removeChild(grid.firstChild);
  for (const m of models) {
    const cardResp = await fetch(`/_partial/model_card?name=${encodeURIComponent(m.name)}`);
    const cardHtml = await cardResp.text();
    const doc = new DOMParser().parseFromString(cardHtml, "text/html");
    const card = doc.body.firstElementChild;
    if (card) grid.appendChild(document.adoptNode(card));
  }
  wireToggles();
}

async function getConfirmationToken(action, target) {
  const r = await fetch("/api/admin/confirm_intent", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action, target, by: "ui"})
  });
  return (await r.json()).token;
}

function openConfirm(title, body, onConfirm) {
  const dlg = document.getElementById("confirm-modal");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-body").textContent = body;
  const phrase = document.getElementById("confirm-phrase");
  const reason = document.getElementById("confirm-reason");
  phrase.value = "";
  reason.value = "";
  document.getElementById("confirm-go").onclick = async () => {
    if (phrase.value !== "I CONFIRM") {
      alert("Type the exact phrase to confirm.");
      return;
    }
    await onConfirm(reason.value);
    dlg.close();
    loadModels();
  };
  dlg.showModal();
}

function wireToggles() {
  document.querySelectorAll(".model-card").forEach(card => {
    const name = card.dataset.name;

    card.querySelector(".toggle-paper")?.addEventListener("change", async e => {
      const action = e.target.checked ? "enable_paper" : "disable_paper";
      await fetch(`/api/models/${encodeURIComponent(name)}/${action}`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({by: "ui"})
      });
    });

    card.querySelector(".toggle-live")?.addEventListener("change", e => {
      const enabled = e.target.checked;
      e.target.checked = !enabled;
      if (!enabled) {
        fetch(`/api/models/${encodeURIComponent(name)}/disable_live`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({by: "ui"})
        }).then(loadModels);
        return;
      }
      openConfirm(
        `Enable live trading for ${name}?`,
        "This will start placing real orders.",
        async (reason) => {
          const token = await getConfirmationToken("enable_live", name);
          const r = await fetch(`/api/models/${encodeURIComponent(name)}/enable_live`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({by: "ui", reason, confirmation_token: token})
          });
          if (!r.ok) {
            const detail = (await r.json()).detail || "request failed";
            alert(detail);
          }
        });
    });

    card.querySelector(".btn-reload")?.addEventListener("click", () => {
      openConfirm(`Reload ${name}?`, "Bumps generation, drains pending queue.",
        async (reason) => {
          const token = await getConfirmationToken("reload", name);
          await fetch(`/api/models/${encodeURIComponent(name)}/reload`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({by: "ui", reason, confirmation_token: token})
          });
        });
    });

    card.querySelector(".btn-rollback")?.addEventListener("click", () => {
      const target = prompt("Target generation?");
      if (!target) return;
      const targetGen = parseInt(target, 10);
      if (Number.isNaN(targetGen)) { alert("Invalid generation"); return; }
      openConfirm(`Roll ${name} back to gen ${targetGen}?`,
        "Restores previous artifact from archive.",
        async (reason) => {
          const token = await getConfirmationToken("rollback", name);
          await fetch(`/api/models/${encodeURIComponent(name)}/rollback`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({by: "ui", reason,
                                  confirmation_token: token,
                                  target_generation: targetGen})
          });
        });
    });
  });
}

loadModels();
