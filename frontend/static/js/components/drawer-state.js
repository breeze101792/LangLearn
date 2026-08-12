// Pure state machine for the mobile drawer. No DOM, no globals.
// Exposed as an ES module so it can be imported by the browser module
// (nav-drawer.js) and by the Node test harness (tests/nav_drawer.test.mjs)
// without duplicating the open/close logic.

export function createDrawer(elements) {
  const { drawer, backdrop, trigger, body } = elements;
  let open = false;

  function openDrawer() {
    if (open) return;
    open = true;
    drawer.hidden = false;
    backdrop.hidden = false;
    drawer.setAttribute("aria-hidden", "false");
    trigger.setAttribute("aria-expanded", "true");
    if (body) body.classList.add("nav-drawer-open");
  }

  function closeDrawer() {
    if (!open) return;
    open = false;
    drawer.hidden = true;
    backdrop.hidden = true;
    drawer.setAttribute("aria-hidden", "true");
    trigger.setAttribute("aria-expanded", "false");
    if (body) body.classList.remove("nav-drawer-open");
  }

  function isOpen() {
    return open;
  }

  // Used by the link auto-close: a click on a link inside the host closes
  // the drawer if it is currently open. Mimics the real delegated click
  // handler in bindLinkAutoClose().
  function onLinkClick(target) {
    const link = target && typeof target.closest === "function"
      ? target.closest(".nav-drawer__link")
      : null;
    if (link && open) closeDrawer();
  }

  return { open: openDrawer, close: closeDrawer, isOpen, onLinkClick };
}
