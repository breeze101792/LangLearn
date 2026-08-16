// Render top nav links and bind clicks.

const ROUTES = [
  { hash: "#/dictionary", label: "Dictionary" },
  { hash: "#/assist",     label: "Assist" },
  { hash: "#/learn",     label: "Learn" },
  { hash: "#/vocabulary", label: "Vocabulary" },
  { hash: "#/structures", label: "Structures" },
  { hash: "#/phrases",    label: "Phrases" },
  { hash: "#/settings",   label: "Settings" },
];

export function renderNavLinks(activeHash) {
  const host = document.getElementById("nav-links");
  if (!host) return;
  host.innerHTML = "";
  for (const r of ROUTES) {
    const a = document.createElement("a");
    a.href = r.hash;
    a.className = "nav__link" + (r.hash === activeHash ? " nav__link--active" : "");
    a.textContent = r.label;
    a.setAttribute("aria-current", r.hash === activeHash ? "page" : "false");
    host.appendChild(a);
  }
}