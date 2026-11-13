"""Exercise the actual embed.js focus bridge with a small deterministic DOM.

These checks do not launch or automate a browser. Geometry/visual verification
remains separate; the stub intentionally covers only bridge and event behavior.
"""
from pathlib import Path
import json
import shutil
import subprocess
import unittest


DIRECTORY = Path(__file__).resolve().parent
NODE = shutil.which("node")

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = [], messages = [], documentHandlers = new Map(), animationFrames = [];
let document;
class Element {
  constructor(tag = 'div', id = '', className = '') {
    this.tagName = tag.toUpperCase(); this.id = id; this.className = className;
    this.children = []; this.parentElement = null; this.hidden = false;
    this.style = {width: ''}; this.dataset = {}; this.attributes = new Map();
    this.handlers = new Map(); this.focusCalls = 0;
    this.classList = {
      contains: value => this.className.split(/\s+/).includes(value),
      add: value => this.classList.toggle(value, true),
      toggle: (value, enabled) => {
        const values = new Set(this.className.split(/\s+/).filter(Boolean));
        const present = enabled === undefined ? !values.has(value) : enabled;
        present ? values.add(value) : values.delete(value);
        this.className = [...values].join(' '); return present;
      }
    };
    elements.push(this);
  }
  get firstChild() { return this.children[0] || null; }
  append(node) {
    if (node.parentElement) node.parentElement.children = node.parentElement.children.filter(item => item !== node);
    node.parentElement = this; this.children.push(node);
  }
  prepend(node) { this.append(node); this.children.unshift(this.children.pop()); }
  set innerHTML(value) { this.markup = value; this.children = []; this.append(new Element('svg')); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  matches(selector) {
    return selector.split(',').some(raw => {
      const value = raw.trim();
      if (value === 'dialog[open]') return this.tagName === 'DIALOG' && Boolean(this.open);
      if (value.startsWith('#')) return this.id === value.slice(1);
      if (value.startsWith('.')) return this.classList.contains(value.slice(1));
      return this.tagName.toLowerCase() === value;
    });
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  querySelector(selector) {
    for (const node of this.children) { if (node.matches(selector)) return node; const found = node.querySelector(selector); if (found) return found; }
    return null;
  }
  addEventListener(type, handler) {
    if (!this.handlers.has(type)) this.handlers.set(type, []);
    this.handlers.get(type).push(handler);
  }
  dispatch(type, event) { for (const handler of this.handlers.get(type) || []) handler(event); }
  getBoundingClientRect() {
    const width = parseFloat(this.style.width) || (this.id === 'workspace-dock' ? 288 : 900);
    return {x: 100, y: 100, left: 100, top: 100, right: 100 + width, bottom: 600, width, height: 500};
  }
  getClientRects() { return this.hidden ? [] : [this.getBoundingClientRect()]; }
  focus() {
    if (document.activeElement !== this) document.activeElement?.onBlur?.();
    document.activeElement = this; this.focusCalls++;
  }
}
const html = new Element('html'), body = new Element('body'); html.append(body);
const main = new Element('main', '', 'market-workspace'), strip = new Element('section', '', 'market-strip');
const actions = new Element('div', '', 'market-strip-actions'), planButton = new Element('button', 'quick-plan-toggle');
body.append(main); main.append(strip); strip.append(actions); actions.append(planButton);
const dock = new Element('aside', 'workspace-dock', 'workspace-dock'); dock.style.width = '288px'; body.append(dock);
const dockLabel = new Element('span', 'layers-toggle-label'), dockToggle = new Element('button', 'objects-toggle');
dockLabel.textContent = '收起图层'; body.append(dockLabel); body.append(dockToggle);
document = {
  documentElement: html, body, activeElement: body,
  createElement: tag => new Element(tag),
  querySelector: selector => elements.find(node => node.matches(selector)) || null,
  querySelectorAll: () => [],
  getElementById: id => elements.find(node => node.id === id) || null,
  addEventListener(type, handler) {
    if (!documentHandlers.has(type)) documentHandlers.set(type, []);
    documentHandlers.get(type).push(handler);
  }
};
const parent = {postMessage: data => messages.push(data)};
const window = {parent, addEventListener() {}};
const sandbox = {
  document, window, location: {origin: 'http://127.0.0.1:8865', search: '?pane=pane-b&active=false&slot=bottom&count=2'},
  URLSearchParams, innerWidth: 1500, innerHeight: 900,
  performance: {now: () => 1000}, setTimeout: () => 1,
  requestAnimationFrame: callback => (animationFrames.push(callback), animationFrames.length),
  MutationObserver: class {observe() {}}, ResizeObserver: class {observe() {}},
};
vm.runInNewContext(fs.readFileSync(EMBED_PATH, 'utf8'), sandbox, {filename: 'embed.js'});
const bridge = window.FullWorkspace, button = elements.find(node => node.classList.contains('dual-maximize'));
let priority = false, appliedUI = null;
const runtime = {
  snapshot: () => ({symbol: 'CZCE.MA701', ready: true, ui: {dockOpen: !dock.hidden, dockWidth: dock.hidden ? null : dock.getBoundingClientRect().width}}),
  becameActive() {}, becamePassive() {},
  applyUI(ui) {
    appliedUI = ui;
    if (typeof ui.dockOpen === 'boolean') dock.hidden = !ui.dockOpen;
    if (Number.isFinite(ui.dockWidth)) dock.style.width = ui.dockWidth + 'px';
  },
  escapeHasPriority: () => priority,
  canChangeFocus: () => !priority,
  canHideSidebar: () => !priority,
  prepareFocusSwitch: () => !priority
};
bridge.register(runtime);
function mode(maximized, active = true) { bridge.setMode({active, slot: 'bottom', count: 2, maximized}); }
function event(target, extra = {}) {
  return {target, button: 0, repeat: false, isComposing: false, prevented: false, stopped: false,
    preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; },
    stopImmediatePropagation() { this.stopped = true; }, ...extra};
}
function dispatch(type, value) {
  for (const handler of documentHandlers.get(type) || []) { handler(value); if (value.stopped) break; }
}
"""


@unittest.skipUnless(NODE, "Node.js is required to run the actual embed script")
class EmbedFocusTests(unittest.TestCase):
    def run_js(self, scenario):
        script = "const EMBED_PATH = " + json.dumps(str(DIRECTORY / "static/workspace-dual-embed.js")) + ";\n" + HARNESS + "\n" + scenario
        result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cases_fill_the_page_and_stay_full_page_when_repositioned(self):
        self.run_js(r"""
const cases = new Element('dialog', 'cases-dialog'); cases.open = true; body.append(cases);
const localDialog = new Element('dialog', 'local-dialog'); localDialog.open = true; body.append(localDialog);
document.querySelectorAll = () => [cases, localDialog];
for (const maximized of [false, true, false]) {
  mode(maximized);
  while (animationFrames.length) animationFrames.shift()();
  assert.equal(bridge.snapshot().pageDialogOpen, true);
  assert.equal(cases.style.left, '0px'); assert.equal(cases.style.top, '0px');
  assert.equal(cases.style.width, '1500px'); assert.equal(cases.style.height, '900px');
  assert.equal(cases.style.maxWidth, '1500px'); assert.equal(cases.style.maxHeight, '900px');
  assert.equal(localDialog.style.maxWidth, '888px'); assert.equal(localDialog.style.maxHeight, '488px');
}
cases.open = false;
assert.equal(bridge.snapshot().pageDialogOpen, false);
""")

    def test_case_escape_uses_native_close_without_changing_workspace_or_draft(self):
        self.run_js(r"""
const cases = new Element('dialog', 'cases-dialog'); cases.open = true; body.append(cases);
let cancelledDrafts = 0;
document.addEventListener('keydown', () => cancelledDrafts++);
for (const maximized of [false, true]) {
  mode(maximized);
  const escape = event(cases, {key: 'Escape'}); dispatch('keydown', escape);
  assert.equal(escape.stopped, true);
  assert.equal(escape.prevented, false, 'Native dialog cancel must be allowed');
  assert.equal(cancelledDrafts, 0);
  assert.equal(bridge.isMaximized(), maximized);
  assert.equal(messages.length, 0);
}
""")

    def test_mode_without_sidebar_update_does_not_overwrite_dock(self):
        self.run_js(r"""
mode(true);
assert.equal(dock.hidden, false);
assert.equal(dock.style.width, '288px');
assert.equal(bridge.isMaximized(), true);
assert.equal(bridge.snapshot().ui.dockOpen, true);
assert.equal(bridge.snapshot().ui.dockWidth, 288);
dock.style.width = '410px';
mode(false);
assert.equal(dock.hidden, false);
assert.equal(dock.style.width, '410px');
assert.equal(dockLabel.textContent, '收起图层');
assert.equal(bridge.isMaximized(), false);
assert.equal(button.focusCalls, 0, 'Mode changes must not move keyboard focus');
""")

    def test_parent_sidebar_state_updates_controls_without_an_event_loop(self):
        self.run_js(r"""
bridge.setMode({active: true, slot: 'bottom', count: 2, maximized: true, sidebars: {leftOpen: false, rightOpen: false}});
assert.equal(html.classList.contains('dual-left-collapsed'), true);
assert.equal(dock.hidden, true);
assert.equal(dock.style.width, '288px');
assert.equal(dockLabel.textContent, '展开图层');
assert.equal(dockToggle.getAttribute('aria-expanded'), 'false');
assert.equal(messages.length, 0, 'Applying parent state must not request another dock toggle');
assert.equal(bridge.snapshot().ui.dockOpen, false);
assert.equal(bridge.applyFullscreenSidebars({leftOpen: true, rightOpen: true}), true);
assert.equal(html.classList.contains('dual-left-collapsed'), false);
assert.equal(dock.hidden, false);
assert.equal(dockLabel.textContent, '收起图层');
assert.equal(dockToggle.getAttribute('aria-expanded'), 'true');
assert.equal(appliedUI, null, 'Sidebar application must not call the persistent runtime UI setter');
bridge.requestDockVisible(false);
assert.deepEqual(messages.map(item => [item.type, item.open]), [['full-workspace:dock-toggle', false]]);
assert.equal(dock.hidden, false, 'The original button requests state; only parent applies it');
bridge.applyFullscreenSidebars({leftOpen: false, rightOpen: false});
mode(false);
assert.equal(html.classList.contains('dual-left-collapsed'), false);
assert.equal(dock.hidden, true, 'Fullscreen exit does not restore an old dock snapshot');
bridge.applyUI({dockOpen: true, dockWidth: 350});
assert.equal(dock.hidden, false);
assert.equal(dock.style.width, '350px');
assert.equal(bridge.applyFullscreenSidebars({leftOpen: false, rightOpen: false}), false);
assert.equal(dock.hidden, false);
""")

    def test_fullscreen_geometry_reserves_edges_only_in_active_frame(self):
        self.run_js(r"""
mode(false);
const normal = bridge.geometry().slots;
mode(true);
const fullscreen = bridge.geometry().slots;
assert.equal(fullscreen.left, normal.left + 18);
assert.equal(fullscreen.right, normal.right - 18);
assert.equal(fullscreen.width, normal.width - 36);
assert.equal(fullscreen.top, normal.top);
assert.equal(fullscreen.height, normal.height);
assert.equal(html.dataset.dualCount, '2');
mode(true, false);
const passive = bridge.geometry().slots;
assert.equal(passive.left, normal.left);
assert.equal(passive.width, normal.width, 'The passive iframe must not reserve another pair of edge strips');
""")

    def test_sidebar_preflight_preserves_unfinished_field(self):
        self.run_js(r"""
mode(false);
const editor = new Element('input'); editor.value = '123.'; dock.append(editor);
document.activeElement = editor;
let blurSaves = 0, received;
editor.onBlur = () => blurSaves++;
runtime.canHideSidebar = (side, options) => { received = [side, options.notify]; return false; };
const allowed = bridge.canHideSidebar('right', {notify: true});
assert.equal(allowed, false);
assert.deepEqual(received, ['right', true]);
assert.equal(dock.hidden, false);
assert.equal(editor.value, '123.');
assert.equal(document.activeElement, editor);
assert.equal(blurSaves, 0);
assert.equal(messages.length, 0);
""")

    def test_fullscreen_ui_remains_live_without_a_dock_snapshot(self):
        self.run_js(r"""
dock.hidden = true; dock.style.width = '333px';
mode(true);
assert.equal(dock.hidden, true);
assert.equal(dock.style.width, '333px');
dock.hidden = false; dock.style.width = '420px';
mode(true); mode(true);
assert.equal(dock.hidden, false);
assert.equal(bridge.snapshot().ui.dockOpen, true);
assert.equal(bridge.snapshot().ui.dockWidth, 420);
bridge.applyUI({group: 'all', dockOpen: true, dockWidth: 600});
assert.equal(appliedUI.dockOpen, true);
assert.equal(appliedUI.dockWidth, 600);
assert.equal(dock.style.width, '600px');
mode(false);
assert.equal(dock.hidden, false);
assert.equal(dock.style.width, '600px');
""")

    def test_focus_restore_preserves_text_and_composition_focus(self):
        self.run_js(r"""
let blurSaves = 0;
const editor = new Element('textarea'); editor.onBlur = () => blurSaves++;
document.activeElement = editor; priority = true;
mode(true);
assert.equal(bridge.focusMaximizeButton(), false);
mode(false);
assert.equal(document.activeElement, editor);
assert.equal(blurSaves, 0);
assert.equal(button.focusCalls, 0);
priority = false;
mode(true); mode(false);
assert.equal(document.activeElement, editor, 'Even with no inner operation, mode changes alone do not focus a button');
assert.equal(blurSaves, 0);
assert.equal(bridge.focusMaximizeButton(), true);
assert.equal(document.activeElement, button);
""")

    def test_workspace_fullscreen_keeps_a_focused_dock_editor_visible(self):
        self.run_js(r"""
mode(false);
for (const tag of ['input', 'textarea', 'select', 'div']) {
  let blurSaves = 0;
  const editor = new Element(tag); editor.isContentEditable = tag === 'div';
  editor.onBlur = () => blurSaves++;
  dock.append(editor); document.activeElement = editor; priority = true;
  dock.hidden = false; dock.style.width = '288px';
  mode(true);
  assert.equal(dock.hidden, false, tag + ': fullscreen must leave the focused field visible');
  assert.equal(document.activeElement, editor);
  assert.equal(blurSaves, 0);
  dock.style.width = '420px'; mode(true);
  assert.equal(dock.hidden, false);
  mode(false);
  assert.equal(dock.hidden, false);
  assert.equal(dock.style.width, '420px');
  assert.equal(document.activeElement, editor);
  assert.equal(blurSaves, 0);
}
""")

    def test_inactive_button_requests_maximize_without_activation(self):
        self.run_js(r"""
assert.equal(bridge.isActive(), false);
const down = event(button); dispatch('pointerdown', down);
assert.equal(down.prevented, true, 'Pointer focus must not blur an existing text editor');
button.dispatch('click', event(button));
assert.deepEqual(messages.map(item => item.type), ['full-workspace:maximize']);
assert.equal(actions.firstChild, button);
assert.equal(button.getAttribute('aria-pressed'), 'false');
assert.equal(button.getAttribute('aria-label'), '工作区全屏');
messages.length = 0;
dispatch('keydown', event(button, {key: 'Enter'}));
assert.deepEqual(messages.map(item => item.type), ['full-workspace:maximize']);
messages.length = 0;
mode(true, false);
assert.equal(bridge.isActive(), false);
assert.equal(button.getAttribute('aria-label'), '退出全屏（Esc）');
button.dispatch('click', event(button));
assert.deepEqual(messages.map(item => item.type), ['full-workspace:restore']);
assert.equal(bridge.isActive(), false);
messages.length = 0;
dispatch('keydown', event(button, {key: 'Escape'}));
assert.deepEqual(messages.map(item => item.type), ['full-workspace:restore']);
assert.equal(bridge.isActive(), false);
""")

    def test_global_fullscreen_keeps_both_pane_controls_and_survives_close(self):
        self.run_js(r"""
const close = elements.find(node => node.classList.contains('dual-close'));
mode(true, false);
assert.equal(html.dataset.dualCount, '2');
assert.equal(html.dataset.dualSlot, 'bottom');
assert.equal(close.hidden, false);
assert.equal(html.classList.contains('dual-maximized'), true);
assert.equal(bridge.isActive(), false);
const down = event(close); dispatch('pointerdown', down);
close.dispatch('click', event(close));
assert.deepEqual(messages.map(item => item.type), ['full-workspace:close']);
assert.equal(bridge.isMaximized(), true);
bridge.setMode({active: true, slot: 'top', count: 1, maximized: true});
assert.equal(bridge.isMaximized(), true);
assert.equal(close.hidden, true);
assert.equal(html.dataset.dualCount, '1');
assert.equal(button.getAttribute('aria-pressed'), 'true');
""")

    def test_inactive_close_pointer_does_not_activate_or_blur_editor(self):
        self.run_js(r"""
mode(true, false);
const close = elements.find(node => node.classList.contains('dual-close'));
const editor = new Element('textarea');
let blurSaves = 0;
editor.onBlur = () => blurSaves++;
document.activeElement = editor; priority = true;
const down = event(close); dispatch('pointerdown', down);
if (!down.prevented) close.focus(); // Native pointer focus would otherwise blur the editor.
assert.equal(down.prevented, true);
assert.equal(document.activeElement, editor);
assert.equal(blurSaves, 0);
close.dispatch('click', event(close));
assert.deepEqual(messages.map(item => item.type), ['full-workspace:close']);
assert.equal(bridge.isActive(), false);
assert.equal(document.activeElement, editor);
assert.equal(blurSaves, 0);
""")

    def test_escape_prioritizes_inner_interaction_and_button_restores(self):
        self.run_js(r"""
mode(true); priority = true;
const inner = event(body, {key: 'Escape'}); dispatch('keydown', inner);
assert.equal(inner.prevented, false);
assert.equal(messages.length, 0);
priority = false;
const outer = event(body, {key: 'Escape'}); dispatch('keydown', outer);
assert.equal(outer.prevented, true);
assert.deepEqual(messages.map(item => item.type), ['full-workspace:restore']);
messages.length = 0;
button.dispatch('click', event(button));
assert.deepEqual(messages.map(item => item.type), ['full-workspace:restore']);
assert.equal(button.getAttribute('aria-pressed'), 'true');
""")

    def test_escape_closes_popups_before_cancelling_a_drawing(self):
        self.run_js(r"""
mode(true);
const layout = new Element('details', 'layout-picker'); layout.open = true;
const members = new Element('section', 'members-panel'); members.hidden = false;
const membersButton = new Element('button', 'members-button'); membersButton.setAttribute('aria-expanded', 'true');
const mobileMembers = new Element('button', 'mobile-members-toggle'); mobileMembers.setAttribute('aria-expanded', 'true');
const decision = new Element('section', 'decision-detail'); decision.hidden = false;
const decisionButton = new Element('button', 'decision-toggle'); decisionButton.setAttribute('aria-expanded', 'true');
let cancelledDrafts = 0, priorityChecks = 0, drawing = true;
runtime.escapeHasPriority = () => { priorityChecks++; return drawing; };
document.addEventListener('keydown', value => {
  if (value.key === 'Escape' && drawing) { cancelledDrafts++; drawing = false; value.preventDefault(); value.stopImmediatePropagation(); }
});
for (const popup of [layout, members, decision]) {
  const escape = event(body, {key: 'Escape'}); dispatch('keydown', escape);
  assert.equal(popup === layout ? popup.open : !popup.hidden, false);
  assert.equal(escape.prevented, true);
  assert.equal(escape.stopped, true);
  assert.equal(cancelledDrafts, 0);
  assert.equal(priorityChecks, 0);
  assert.equal(messages.length, 0);
}
assert.equal(membersButton.getAttribute('aria-expanded'), 'false');
assert.equal(mobileMembers.getAttribute('aria-expanded'), 'false');
assert.equal(decisionButton.getAttribute('aria-expanded'), 'false');
dispatch('keydown', event(body, {key: 'Escape'}));
assert.equal(cancelledDrafts, 1, 'After popups close, the existing drawing handler keeps priority');
assert.equal(messages.length, 0);
dispatch('keydown', event(body, {key: 'Escape'}));
assert.deepEqual(messages.map(item => item.type), ['full-workspace:restore']);
""")

    def test_dialog_escape_keeps_native_cancel_and_context_menu_uses_original_handler(self):
        self.run_js(r"""
mode(true);
const dialog = new Element('dialog'); dialog.open = true;
let cancelledDrafts = 0, contextHandled = 0, priorityChecks = 0;
const contextMenu = new Element('div', 'object-context-menu'); contextMenu.hidden = false;
runtime.escapeHasPriority = () => { priorityChecks++; return false; };
document.addEventListener('keydown', value => {
  if (value.key === 'Escape' && !contextMenu.hidden) {
    contextHandled++; contextMenu.hidden = true; value.preventDefault(); value.stopImmediatePropagation();
  }
});
document.addEventListener('keydown', () => cancelledDrafts++);
const dialogEscape = event(body, {key: 'Escape'}); dispatch('keydown', dialogEscape);
assert.equal(dialogEscape.stopped, true);
assert.equal(dialogEscape.prevented, false, 'Native dialog cancellation must remain enabled');
assert.equal(contextHandled, 0);
assert.equal(cancelledDrafts, 0);
assert.equal(priorityChecks, 0);
assert.equal(messages.length, 0);
dialog.open = false; // The browser's native cancel/close action is outside this DOM stub.
dispatch('keydown', event(body, {key: 'Escape'}));
assert.equal(contextHandled, 1);
assert.equal(contextMenu.hidden, true);
assert.equal(cancelledDrafts, 0);
assert.equal(priorityChecks, 0);
assert.equal(messages.length, 0);
""")

    def test_repeat_and_composition_escape_do_not_change_popup_or_restore(self):
        self.run_js(r"""
mode(true);
const layout = new Element('details', 'layout-picker'); layout.open = true;
for (const extra of [{repeat: true}, {isComposing: true}, {keyCode: 229}]) {
  const escape = event(body, {key: 'Escape', ...extra}); dispatch('keydown', escape);
  assert.equal(layout.open, true);
  assert.equal(escape.prevented, false);
  assert.equal(escape.stopped, false);
  assert.equal(messages.length, 0);
}
""")


if __name__ == "__main__":
    unittest.main()
