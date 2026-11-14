const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/static/workspace.js', 'utf8');
const shell = fs.readFileSync(__dirname + '/static/workspace-dual-shell.js', 'utf8');

function fixture() {
  const documentEvents = {}, windowEvents = {}, nodes = {};
  const doc = {activeElement: null, addEventListener(type, fn) {(documentEvents[type] ||= []).push(fn);}};
  function node(id, bounds, parent = null) {
    const value = {id, parent, hidden: false, style: {}, attrs: {}, handlers: {},
      contains(target) {return target === this || target?.parent === this;},
      setAttribute(key, val) {this.attrs[key] = val;},
      addEventListener(type, fn) {this.handlers[type] = fn;},
      getClientRects() {return this.hidden ? [] : [this.getBoundingClientRect()];},
      getBoundingClientRect() {const width = parseFloat(this.style.width) || bounds.width;
        const left = parseFloat(this.style.left) || bounds.left, top = parseFloat(this.style.top) || bounds.top;
        return {left, top, right: left + width, bottom: top + bounds.height, width, height: bounds.height};},
      focus() {doc.activeElement = this;},
    }; nodes[id] = value; return value;
  }
  const button = node('members-button', {left: 700, top: 8, width: 120, height: 24});
  const mobile = node('mobile-members-toggle', {left: 700, top: 8, width: 80, height: 24}); mobile.hidden = true;
  const panel = node('members-panel', {left: 400, top: 620, width: 310, height: 250}); panel.hidden = true;
  const field = node('profile-name', {left: 420, top: 700, width: 200, height: 26}, panel);
  panel.querySelector = () => field;
  const context = {document: doc, window: {FullWorkspace: {queueState() {}}, addEventListener(type, fn) {(windowEvents[type] ||= []).push(fn);}},
    innerWidth: 900, innerHeight: 1000, $: id => nodes[id]};
  const start = source.indexOf('  function installMembersPopover(){');
  const end = source.indexOf('  const membersPopover=', start);
  const api = vm.runInNewContext(source.slice(start, end) + '\ninstallMembersPopover()', context);
  const dispatch = (events, type, event = {}) => {for (const fn of events[type] || []) fn(event);};
  return {api, button, mobile, panel, field, doc, context, documentEvents, windowEvents,
    clickButton: () => button.handlers.click(),
    outside: target => dispatch(documentEvents, 'pointerdown', {target}), dispatch};
}

test('profile anchors below the header, not a bottom pane, and keeps original fields', () => {
  const f = fixture(); f.field.value = '未保存昵称'; f.clickButton();
  assert.equal(f.panel.style.top, '38px');
  assert.equal(f.panel.style.left, '510px');
  assert.equal(f.panel.hidden, false);
  assert.equal(f.doc.activeElement, f.field);
  f.outside(f.field); assert.equal(f.api.isOpen(), true);
  f.outside(f.button); assert.equal(f.api.isOpen(), true);
  f.outside({id: 'chart'}); assert.equal(f.api.isOpen(), false);
  assert.equal(f.field.value, '未保存昵称');
  assert.equal(f.doc.activeElement, f.field, 'outside dismissal must not steal the clicked control focus');
  assert.equal(f.button.attrs['aria-expanded'], 'false');
  f.clickButton(); assert.equal(f.field.value, '未保存昵称');
  assert.equal(f.windowEvents.blur, undefined, 'native color picker blur must not dismiss the editor');
});

test('Escape respects composition and closes only the popup, returning trigger focus', () => {
  const f = fixture(); f.clickButton(); let consumed = 0;
  const event = {key: 'Escape', preventDefault() {consumed++;}, stopImmediatePropagation() {consumed++;}};
  f.dispatch(f.documentEvents, 'keydown', {...event, isComposing: true});
  assert.equal(f.api.isOpen(), true); assert.equal(consumed, 0);
  f.dispatch(f.documentEvents, 'keydown', event);
  assert.equal(f.api.isOpen(), false); assert.equal(consumed, 2);
  assert.equal(f.doc.activeElement, f.button);
});

test('resize clamps the popup to a narrow viewport and hidden header closes it', () => {
  const f = fixture(); f.clickButton();
  f.context.innerWidth = 270; f.context.innerHeight = 180;
  f.dispatch(f.windowEvents, 'resize');
  assert.equal(f.panel.style.width, '258px');
  assert.equal(f.panel.style.left, '6px');
  assert.equal(f.panel.style.maxHeight, '136px');
  f.button.hidden = true; f.api.position(); assert.equal(f.api.isOpen(), false);
});

test('passive iframe gives way only in the popup intersection using local coordinates', () => {
  const start = shell.indexOf('  function membersClip('), end = shell.indexOf('  function maximizeWorkspace(', start);
  const clip = vm.runInNewContext(shell.slice(start, end) + '\nmembersClip');
  const record = box => ({frame: {getBoundingClientRect: () => box}});
  const passive = record({left: 200, top: 100, width: 700, height: 400});
  const active = record({left: 20, top: 30, width: 1200, height: 900});
  const popup = {left: 710, top: 38, right: 1020, bottom: 288};
  const result = clip(passive, active, popup);
  assert.match(result, /^polygon\(evenodd,/);
  assert.ok(result.includes('530px 0px, 700px 0px, 700px 218px, 530px 218px'));
  assert.equal(clip(passive, active, null), '');
  assert.equal(clip(passive, active, {...popup, top: 800, bottom: 900}), '');
  assert.equal(clip(passive, active, {...popup, left: NaN}), '');
});

test('an older in-flight save cannot clear a newer profile draft', async () => {
  const values = {'profile-name': {value: '第一版'}, 'profile-color': {value: '#112233'}, 'profile-status': {textContent: ''}};
  let complete; const state = {session: {user: {name: '原名'}}, profileDirty: true};
  const context = {state, window:{}, $: id => values[id], clearTimeout() {}, api: () => new Promise(resolve => {complete = resolve;}), refreshPresence: async () => {},renderMembers(){}};
  const start = source.indexOf('  async function saveProfile(){'), end = source.indexOf('  function markProfileField(', start);
  const save = vm.runInNewContext(source.slice(start, end) + '\nsaveProfile', context);
  const first = save(); values['profile-name'].value = '继续输入';
  complete({user: {name: '第一版', color: '#112233'}}); await first;
  assert.equal(state.profileDirty, true);
  const second = save(); complete({user: {name: '继续输入', color: '#112233'}}); await second;
  assert.equal(state.profileDirty, false); assert.equal(state.profileSaving, false);
});

test('color-only save never sends another pane stale nickname', async () => {
  const values = {'profile-name': {value: '旧昵称'}, 'profile-color': {value: '#445566'}, 'profile-status': {textContent: ''}};
  const state={session:{user:{id:'me',name:'新昵称',color:'#112233'}},profileFields:new Set(['color']),profileDirty:true};
  let sent;
  const context={state,window:{},$:id=>values[id],clearTimeout(){},refreshPresence:async()=>{},renderMembers(){},api:async(path,method,patch)=>{sent=patch;return{user:{id:'me',name:'新昵称',color:patch.color}};}};
  const start=source.indexOf('  async function saveProfile(){'),end=source.indexOf('  function markProfileField(',start);
  await vm.runInNewContext(source.slice(start,end)+'\nsaveProfile()',context);
  assert.equal(sent.name,undefined);assert.equal(sent.color,'#445566');
  assert.equal(state.session.user.name,'新昵称');assert.equal(state.profileDirty,false);
});
