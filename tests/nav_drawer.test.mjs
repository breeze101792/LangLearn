// Unit tests for the pure drawer state machine. Run with:
//   node tests/nav_drawer.test.mjs
// Exits 0 on pass, 1 on first failure.

import { createDrawer } from "../frontend/static/js/components/drawer-state.js";

let failures = 0;
let passed = 0;

function test(name, fn) {
  try {
    fn();
    console.log("ok  -", name);
    passed++;
  } catch (e) {
    console.log("FAIL -", name);
    console.log("       ", e.message);
    failures++;
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}

function fakeEl(initial = {}) {
  const el = {
    hidden: initial.hidden === true,
    classes: new Set(initial.classes || []),
    attrs: { ...(initial.attrs || {}) },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    classList: {
      add: (c) => el.classes.add(c),
      remove: (c) => el.classes.delete(c),
      contains: (c) => el.classes.has(c),
    },
  };
  return el;
}

// Drawer is hidden by default in the real DOM. The state machine assumes
// that, so mirror it here.
function makeDrawer() {
  const drawer = fakeEl({ hidden: true });
  const backdrop = fakeEl({ hidden: true });
  const trigger = fakeEl();
  const body = fakeEl();
  const ctl = createDrawer({ drawer, backdrop, trigger, body });
  return { ctl, drawer, backdrop, trigger, body };
}

test("starts closed", () => {
  const { ctl, drawer, backdrop } = makeDrawer();
  assert(!ctl.isOpen(), "should start closed");
  assert(drawer.hidden === true, "drawer should be hidden");
  assert(backdrop.hidden === true, "backdrop should be hidden");
});

test("open() sets hidden=false and aria attrs", () => {
  const { ctl, drawer, backdrop, trigger, body } = makeDrawer();
  ctl.open();
  assert(ctl.isOpen(), "should be open");
  assert(drawer.hidden === false, "drawer visible");
  assert(backdrop.hidden === false, "backdrop visible");
  assert(drawer.getAttribute("aria-hidden") === "false", "aria-hidden=false");
  assert(trigger.getAttribute("aria-expanded") === "true", "aria-expanded=true");
  assert(body.classes.has("nav-drawer-open"), "body class added");
});

test("open() is idempotent", () => {
  const { ctl } = makeDrawer();
  ctl.open();
  ctl.open();
  assert(ctl.isOpen());
});

test("close() restores hidden + aria + body class", () => {
  const { ctl, drawer, backdrop, trigger, body } = makeDrawer();
  ctl.open();
  ctl.close();
  assert(!ctl.isOpen(), "should be closed");
  assert(drawer.hidden === true, "drawer hidden");
  assert(backdrop.hidden === true, "backdrop hidden");
  assert(drawer.getAttribute("aria-hidden") === "true", "aria-hidden=true");
  assert(trigger.getAttribute("aria-expanded") === "false", "aria-expanded=false");
  assert(!body.classes.has("nav-drawer-open"), "body class removed");
});

test("close() is idempotent", () => {
  const { ctl, drawer } = makeDrawer();
  ctl.close();
  assert(drawer.hidden === true);
});

test("onLinkClick closes an open drawer", () => {
  const { ctl } = makeDrawer();
  ctl.open();
  const link = { classList: { contains: (c) => c === "nav-drawer__link" }, closest: (sel) => sel === ".nav-drawer__link" ? link : null };
  ctl.onLinkClick(link);
  assert(!ctl.isOpen(), "drawer should be closed after link click");
});

test("onLinkClick on closed drawer is a no-op", () => {
  const { ctl, drawer } = makeDrawer();
  const link = { closest: (sel) => sel === ".nav-drawer__link" ? link : null };
  ctl.onLinkClick(link);
  assert(drawer.hidden === true, "still hidden");
  assert(!ctl.isOpen());
});

test("onLinkClick with non-link target does nothing", () => {
  const { ctl } = makeDrawer();
  ctl.open();
  ctl.onLinkClick({ closest: () => null });
  assert(ctl.isOpen(), "drawer stays open on non-link click");
});

test("onLinkClick with null target is safe", () => {
  const { ctl } = makeDrawer();
  ctl.open();
  ctl.onLinkClick(null);
  assert(ctl.isOpen(), "drawer stays open on null click");
});

test("REGRESSION: repeated open/link-click/close cycles all close (the bug)", () => {
  // The original bug: the second link click did not close the drawer
  // because the hashchange auto-close handler was re-bound inside the
  // hashchange dispatch, missing the current event's snapshot.
  // The fix uses a click handler on the links host, so every click
  // closes the drawer regardless of event ordering.
  const { ctl } = makeDrawer();
  const link = { closest: (sel) => sel === ".nav-drawer__link" ? link : null };

  for (let i = 0; i < 5; i++) {
    ctl.open();
    assert(ctl.isOpen(), `cycle ${i}: should be open after open()`);
    ctl.onLinkClick(link);
    assert(!ctl.isOpen(), `cycle ${i}: should be closed after link click`);
  }
});

console.log(`\n${passed} passed, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
