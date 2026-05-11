/* Kill switch banner — polls /api/kill_switch every 5s */

async function pollKill() {
  try {
    const r = await fetch("/api/kill_switch");
    const data = await r.json();
    const banner = document.getElementById("kill-banner");
    if (!banner) return;
    banner.hidden = !data.engaged;
    if (data.engaged && data.state) {
      document.getElementById("kill-reason").textContent = data.state.reason || "";
      document.getElementById("kill-by").textContent = data.state.by || "";
    }
  } catch (e) {
    /* silently ignore fetch errors */
  }
}
setInterval(pollKill, 5000);
pollKill();

document.getElementById("kill-resume")?.addEventListener("click", async () => {
  if (!confirm("Resume trading? You will be asked to type a confirmation phrase.")) return;
  const phrase = prompt('Type "I CONFIRM" to proceed:');
  if (phrase !== "I CONFIRM") return;
  const tokR = await fetch("/api/admin/confirm_intent", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action: "kill_switch_resume", target: "global", by: "ui"})
  });
  const {token} = await tokR.json();
  const r = await fetch("/api/kill_switch/confirm_resume", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({by: "ui", confirmation_token: token})
  });
  if (r.ok) location.reload();
  else {
    const body = await r.json();
    alert(body.detail || "resume failed");
  }
});
