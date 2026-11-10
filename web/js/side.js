// web/js/side.js - right column: Actions, Decisions, People notes, Fact-checks, My notes, Ask,
// plus the top-bar "Copy all". Owns #col-right and #tb-copyall (see MODULES.md).
// Plain ES module; talks to the other modules only through core.store.

let core = null;

const TABS = ['notes', 'facts', 'mine', 'ask'];
const VERDICT_ICON = { supported: 'check', disputed: 'xcirc', unclear: 'help' };
const MOBILE = '(max-width: 900px)';
const SAVE_DEBOUNCE_MS = 600;

let activeTab = 'notes';
const openPeople = new Map();   // person name -> <details> open state, kept across re-renders
const localAsks = new Set();    // ask_ids submitted from this pane (shown even when scope = 'all')
const chatNodes = new Map();    // ask_id -> {q, a, md, scope, question, text, done}

// ---------------------------------------------------------------- helpers

const $ = (id) => document.getElementById(id);
const norm = (name) => String(name ?? '').trim().toLowerCase();
const sameName = (a, b) => norm(a) !== '' && norm(a) === norm(b);
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const has = (v) => v !== null && v !== undefined;
const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u.trim()) ? u.trim() : null);
const isMobile = () => window.matchMedia(MOBILE).matches;

function setCount(id, n) { const b = $(id); if (b) { b.textContent = String(n); b.hidden = !n; } }
function setEmpty(id, show) { const p = $(id); if (p) p.hidden = !show; }

function textOf(x) {
  if (typeof x === 'string') return x;
  if (!has(x)) return '';
  return typeof x === 'object' ? String(x.text ?? x.task ?? JSON.stringify(x)) : String(x);
}

const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; } };

/** Speaker id whose mapped name is `name`, else null. */
function speakerIdForName(name) {
  const want = norm(name);
  if (!want) return null;
  for (const [id, n] of Object.entries(core.store.state.speakers)) if (norm(n) === want) return id;
  return null;
}

/** Inline style that sets --spk to the person's speaker colour (slate when unknown). */
const spkStyle = (name) => `--spk: var(--spk-${core.speakerColorIndex(speakerIdForName(name))})`;
const spkDot = () => core.el('i', { class: 'spk-dot', 'aria-hidden': 'true' });

function ownerChip(name) {
  return core.el('span', { class: 'chip chip-owner', style: spkStyle(name), title: name }, spkDot(), core.el('span', null, name));
}

/** [mm:ss] button; app.js routes clicks on button.ts[data-t] to seekAudio. */
function tsButton(sec) {
  const t = Math.max(0, Number(sec) || 0);
  return core.el('button', { type: 'button', class: 'ts ts-link', dataset: { t: String(t) }, title: 'Play from here' }, '[' + core.fmtTime(t) + ']');
}

function copyMarkdown(markdown) {
  if (!markdown || !markdown.trim()) { core.toast('Nothing to copy'); return; }
  core.copy({ markdown });
}

function copyBtn(label, getMarkdown, { text = false } = {}) {
  const onclick = (ev) => { ev.preventDefault(); ev.stopPropagation(); copyMarkdown(getMarkdown()); };
  const cls = text ? 'btn btn-subtle btn-sm' : 'btn btn-subtle btn-icon btn-sm';
  return core.el('button', { type: 'button', class: cls, 'aria-label': label, title: label, onclick },
    core.icon('copy'), text ? core.el('span', null, label) : null);
}

/** On mobile the bottom tab bar (app.js) decides which column is visible; click its button. */
function goMobile(tab) {
  if (isMobile()) document.querySelector(`.tabbar-btn[data-mtab="${tab}"]`)?.click();
}

// ---------------------------------------------------------------- tabs (#rtabs, body[data-rtab])

function showTab(name) {
  if (!TABS.includes(name)) return;
  activeTab = name;
  document.body.dataset.rtab = name;
  for (const tab of document.querySelectorAll('#rtabs .tab[data-rtab]')) {
    const on = tab.dataset.rtab === name;
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
  }
  for (const t of TABS) { const pane = $('pane-' + t); if (pane) pane.hidden = t !== name; }
  if (name === 'facts') $('facts-count')?.classList.remove('is-fresh');
}

function wireTabs() {
  const tabs = $('rtabs');
  if (!tabs) return;
  tabs.addEventListener('click', (ev) => {
    const b = ev.target.closest('.tab[data-rtab]');
    if (b) showTab(b.dataset.rtab);
  });
  tabs.addEventListener('keydown', (ev) => {
    const order = [...tabs.querySelectorAll('.tab[data-rtab]')].filter((b) => b.offsetParent !== null);
    if (!order.length) return;
    const i = Math.max(0, order.findIndex((b) => b.dataset.rtab === activeTab));
    const step = { ArrowRight: i + 1, ArrowLeft: i - 1 + order.length, Home: 0, End: order.length - 1 }[ev.key];
    if (step === undefined) return;
    ev.preventDefault();
    const next = order[step % order.length];
    showTab(next.dataset.rtab);
    next.focus();
  });
  showTab(activeTab);
}

// ---------------------------------------------------------------- NOTES data

const notes = () => core.store.state.notes || {};
const actionsOf = () => (Array.isArray(notes().actions) ? notes().actions.filter(Boolean) : []);
const decisionsOf = () => (Array.isArray(notes().decisions) ? notes().decisions.map(textOf).filter(Boolean) : []);
function peopleOf() { const p = notes().people; return p && typeof p === 'object' && !Array.isArray(p) ? p : {}; }

/** People in NOTES.people order, plus action owners who have no notes yet. */
function peopleList() {
  const people = peopleOf();
  const actions = actionsOf();
  const names = Object.keys(people);
  for (const a of actions) if (a.owner && !names.some((n) => sameName(n, a.owner))) names.push(String(a.owner));
  return names.map((name) => ({
    name,
    notes: (Array.isArray(people[name]) ? people[name] : []).map(textOf).filter(Boolean),
    actions: actions.filter((a) => sameName(a.owner, name)),
  }));
}

function mdActionLine(a, { owner = true } = {}) {
  const meta = [];
  if (owner && a.owner) meta.push(String(a.owner));
  if (a.due) meta.push('due ' + String(a.due));
  return `- [ ] ${textOf(a.task)}${meta.length ? ' (' + meta.join(', ') + ')' : ''}`;
}
const mdActions = () => actionsOf().map((a) => mdActionLine(a)).join('\n');
const mdDecisions = () => decisionsOf().map((d) => '- ' + d).join('\n');
const mdPerson = (p) => `### ${p.name}\n\n` + (p.notes.length ? p.notes.map((n) => '- ' + n).join('\n') : '_No notes yet._');
const mdPersonActions = (p) => `### ${p.name}'s actions\n\n` + (p.actions.length ? p.actions.map((a) => mdActionLine(a, { owner: false })).join('\n') : '_No actions._');
const mdPeople = () => peopleList().map(mdPerson).join('\n\n');

// ---------------------------------------------------------------- Actions, Decisions, People notes

function actionRow(a) {
  const meta = [];
  if (a.owner) meta.push(ownerChip(String(a.owner)));
  if (a.due) meta.push(core.el('span', { class: 'action-due' }, core.icon('clock'), core.el('span', null, String(a.due))));
  return core.el('li', { class: 'action' },
    core.el('span', { class: 'action-box', 'aria-hidden': 'true' }),
    core.el('div', { class: 'action-main' },
      core.el('div', { class: 'action-task' }, textOf(a.task)),
      meta.length ? core.el('div', { class: 'action-meta' }, ...meta) : null));
}

function personCard(p) {
  const body = core.el('div', { class: 'pn-body' },
    p.notes.length ? core.el('ul', { class: 'pn-list' }, ...p.notes.map((n) => core.el('li', null, n)))
      : core.el('p', { class: 'empty' }, 'No notes yet.'),
    core.el('div', { class: 'pn-actions' },
      copyBtn(`Copy ${p.name}'s notes`, () => mdPerson(p), { text: true }),
      p.actions.length ? copyBtn(`Copy ${p.name}'s actions`, () => mdPersonActions(p), { text: true }) : null));
  return core.el('details', { class: 'pn', open: openPeople.get(p.name) ?? true, ontoggle: (ev) => openPeople.set(p.name, ev.currentTarget.open) },
    core.el('summary', { style: spkStyle(p.name) },
      spkDot(), core.el('span', { class: 'spk-name' }, p.name),
      core.el('span', { class: 'badge', title: `${p.notes.length} notes, ${p.actions.length} actions` }, String(p.notes.length)),
      core.icon('chev', 'ic chev')),
    body);
}

function renderNotesPane() {
  const actions = actionsOf();
  $('actions-list')?.replaceChildren(...actions.map(actionRow));
  setCount('actions-count', actions.length);
  setEmpty('actions-empty', !actions.length);

  const decisions = decisionsOf();
  $('decisions-list')?.replaceChildren(...decisions.map((d) => core.el('li', { class: 'decision' }, core.icon('check'), core.el('span', null, d))));
  setCount('decisions-count', decisions.length);
  setEmpty('decisions-empty', !decisions.length);

  const people = peopleList();
  $('people-notes')?.replaceChildren(...people.map(personCard));
  setEmpty('people-notes-empty', !people.length);
}

// ---------------------------------------------------------------- Fact-checks

function verdictOf(item) { const v = norm(item.verdict); return VERDICT_ICON[v] ? v : 'unclear'; }

function sourcesOf(item) {
  return (Array.isArray(item.sources) ? item.sources : []).map((s) => ({ url: safeUrl(s?.url), title: textOf(s?.title) })).filter((s) => s.url);
}

function factCard(item) {
  const v = verdictOf(item);
  const speaker = textOf(item.speaker);
  const sources = sourcesOf(item);
  return core.el('article', { class: 'fact', dataset: { id: String(item.id ?? '') } },
    core.el('div', { class: 'fact-top' },
      core.el('span', { class: 'verdict', dataset: { v } }, core.icon(VERDICT_ICON[v]), core.el('span', null, v)),
      core.el('span', { class: 'fact-who', style: spkStyle(speaker) },
        speaker ? spkDot() : null, speaker ? core.el('span', { class: 'spk-name' }, speaker) : null,
        has(item.at) ? tsButton(item.at) : null),
      copyBtn('Copy fact-check', () => mdFact(item))),
    core.el('blockquote', { class: 'fact-claim' }, textOf(item.claim)),
    item.summary ? core.el('p', { class: 'fact-summary' }, textOf(item.summary)) : null,
    sources.length ? core.el('div', { class: 'fact-sources' }, ...sources.map((s) =>
      core.el('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer', title: s.url },
        core.icon('ext'), core.el('span', null, s.title || hostOf(s.url))))) : null);
}

function renderFacts() {
  const items = [...core.store.state.factchecks].reverse();
  $('facts-list')?.replaceChildren(...items.map(factCard));
  setCount('facts-count', items.length);
  setEmpty('facts-empty', !items.length);
}

function mdFact(item) {
  const who = [textOf(item.speaker), has(item.at) ? `[${core.fmtTime(item.at)}]` : ''].filter(Boolean).join(' ');
  const lines = [`### ${cap(verdictOf(item))}${who ? ' - ' + who : ''}`, '', `> ${textOf(item.claim)}`];
  if (item.summary) lines.push('', textOf(item.summary));
  const sources = sourcesOf(item);
  if (sources.length) lines.push('', ...sources.map((s) => `- [${s.title || hostOf(s.url)}](${s.url})`));
  return lines.join('\n');
}
const mdFacts = () => core.store.state.factchecks.map(mdFact).join('\n\n');

// ---------------------------------------------------------------- My notes (#my-notes, debounced POST)

let saveTimer = null;
let pendingText = null;
let statusTimer = null;

function setMineStatus(text, cls = '') {
  const b = $('mine-status');
  if (!b) return;
  clearTimeout(statusTimer);
  b.textContent = text; b.className = cls ? 'badge ' + cls : 'badge'; b.hidden = !text;
  if (cls === 'badge-ok') statusTimer = setTimeout(() => { b.hidden = true; }, 2000);
}

async function flushNotes() {
  clearTimeout(saveTimer);
  saveTimer = null;
  if (pendingText === null) return;
  const text = pendingText;
  pendingText = null;
  setMineStatus('Saving\u2026');
  try {
    await core.api('POST', '/api/user-notes', { text });
    core.store.set({ userNotes: text });
    if (pendingText === null) setMineStatus('Saved', 'badge-ok');
  } catch (err) {
    console.warn('[side] user notes not saved', err);
    setMineStatus('Not saved', 'badge-bad');
  }
}

function onNotesInput(ev) {
  if (!core.isController()) return;
  pendingText = ev.target.value;
  setMineStatus('Unsaved');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushNotes, SAVE_DEBOUNCE_MS);
}

function renderMine() {
  const ta = $('my-notes');
  const view = $('my-notes-view');
  if (!ta || !view) return;
  const text = core.store.state.userNotes || '';
  const ctl = core.isController();
  ta.hidden = !ctl;
  ta.readOnly = !ctl;
  view.hidden = ctl;
  if (ctl) {
    const editing = pendingText !== null || saveTimer !== null;
    if (!editing && ta.value !== text) ta.value = text;
  } else if (text) {
    view.innerHTML = core.renderMarkdown(text);
  } else {
    view.replaceChildren(core.el('p', { class: 'empty' }, 'The host has not written any notes yet.'));
  }
}

function focusMine() { goMobile('notes'); showTab('mine'); if (core.isController()) $('my-notes')?.focus(); }

// ---------------------------------------------------------------- Ask chat (#chat-live)

const isShownAsk = (id, ask) => ask.scope === 'meeting' || localAsks.has(id);
const visibleAsks = () => Object.entries(core.store.state.asks).filter(([id, a]) => isShownAsk(id, a));
const answerHtml = (text) => core.linkifyTimestamps(core.renderMarkdown(text));
const mdAskOne = (a) => `**Q:** ${a.question || ''}\n\n${a.text || '_(no answer yet)_'}`;
const mdAsk = () => visibleAsks().map(([, a]) => mdAskOne(a)).join('\n\n---\n\n');

function chatNode(id) {
  let n = chatNodes.get(id);
  if (n) return n;
  const q = core.el('div', { class: 'msg msg-q' });
  const scope = core.el('div', { class: 'msg-scope' }, 'All meetings');
  const md = core.el('div', { class: 'md' });
  const tools = core.el('div', { class: 'msg-tools' }, copyBtn('Copy answer', () => mdAskOne(core.store.state.asks[id] || {})));
  const a = core.el('div', { class: 'msg msg-a is-streaming' }, scope, md, tools);
  n = { q, a, md, scope, question: null, text: null, done: null };
  chatNodes.set(id, n);
  $('chat-live')?.append(q, a);
  return n;
}

function syncAsk(id, ask) {
  const n = chatNode(id);
  if (n.question !== ask.question) { n.question = ask.question; n.q.textContent = ask.question || '\u2026'; }
  n.scope.hidden = ask.scope !== 'all';
  if (n.text !== ask.text || n.done !== ask.done) {
    n.text = ask.text;
    n.done = ask.done;
    if (ask.text) n.md.innerHTML = answerHtml(ask.text);
    else n.md.replaceChildren(core.el('p', { class: 'msg-wait' }, ask.done ? 'No answer.' : 'Thinking\u2026'));
  }
  n.a.classList.toggle('is-streaming', !ask.done);
}

function renderChat({ stick = false } = {}) {
  const chat = $('chat-live');
  if (!chat) return;
  const nearBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 48;
  for (const [id, n] of chatNodes) {
    if (core.store.state.asks[id]) continue;
    n.q.remove(); n.a.remove(); chatNodes.delete(id);
  }
  const asks = visibleAsks();
  for (const [id, ask] of asks) syncAsk(id, ask);
  setEmpty('chat-live-empty', !asks.length);
  if (stick || nearBottom) chat.scrollTop = chat.scrollHeight;
}

function onAskEvent(msg) {
  const id = String(msg.ask_id);
  const ask = core.store.state.asks[id];
  if (ask && isShownAsk(id, ask)) renderChat({ stick: msg.type === 'ask_start' });
}

function wireScope() {
  $('ask-form')?.querySelector('.seg')?.addEventListener('click', (ev) => {
    const b = ev.target.closest('.seg-btn[data-scope]');
    if (!b) return;
    for (const x of b.parentElement.querySelectorAll('.seg-btn')) { x.classList.toggle('is-on', x === b); x.setAttribute('aria-pressed', String(x === b)); }
  });
}

async function submitAsk(ev) {
  ev.preventDefault();
  const form = $('ask-form');
  const input = $('ask-input');
  const question = input?.value.trim();
  if (!form || !input || !question) return;
  const scope = form.querySelector('.seg-btn.is-on')?.dataset.scope === 'all' ? 'all' : 'meeting';
  const send = form.querySelector('button[type="submit"]');
  if (send) send.disabled = true;
  try {
    const res = await core.api('POST', '/api/ask', { question, scope });
    if (res && has(res.ask_id)) {
      const id = String(res.ask_id);
      localAsks.add(id);
      core.dispatch({ type: 'ask_start', ask_id: id, question, scope });
    }
    input.value = '';
    renderChat({ stick: true });
  } catch (err) {
    console.warn('[side] ask failed', err);
  } finally {
    if (send) send.disabled = false;
    input.focus();
  }
}

function focusAsk() { goMobile('ask'); showTab('ask'); $('ask-input')?.focus(); }

// ---------------------------------------------------------------- Copy buttons + Copy all

const COPY = { actions: mdActions, decisions: mdDecisions, 'people-notes': mdPeople, facts: mdFacts, mine: () => core.store.state.userNotes || '', ask: mdAsk };

function mdAll() {
  const s = core.store.state;
  return [
    `# ${textOf(s.meeting?.title) || 'Meeting'}`, '', `_Exported ${new Date().toLocaleString()}_`, '',
    '## Actions', '', mdActions() || '_None._', '',
    '## Decisions', '', mdDecisions() || '_None._', '',
    '## People notes', '', mdPeople() || '_None._', '',
    '## Fact-checks', '', mdFacts() || '_None._', '',
    '## My notes', '', s.userNotes || '_None._', '',
  ].join('\n');
}

function wireCopy() {
  $('col-right')?.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-copy]');
    const fn = b && COPY[b.dataset.copy];
    if (!fn) return;
    ev.preventDefault();
    copyMarkdown(fn());
  });
  $('tb-copyall')?.addEventListener('click', () => copyMarkdown(mdAll()));
}

function renderCopyAll() { const b = $('tb-copyall'); if (b) b.hidden = !core.store.state.meeting; }

// ---------------------------------------------------------------- mount

function renderAll() { renderNotesPane(); renderFacts(); renderMine(); renderChat(); renderCopyAll(); }

export function mount(c) {
  core = c;
  const { store } = core;
  wireTabs();
  wireScope();
  wireCopy();
  const ta = $('my-notes');
  ta?.addEventListener('input', onNotesInput);
  ta?.addEventListener('blur', () => { if (saveTimer !== null) flushNotes(); });
  $('ask-form')?.addEventListener('submit', submitAsk);

  store.on('snapshot', renderAll);
  store.on('notes', renderNotesPane);
  store.on('speakers', () => { renderNotesPane(); renderFacts(); });
  store.on('factcheck', () => {
    renderFacts();
    if (activeTab !== 'facts') $('facts-count')?.classList.add('is-fresh');
  });
  store.on('user_notes', renderMine);
  store.on('control', () => { renderMine(); renderCopyAll(); });
  store.on('state', renderCopyAll);
  for (const type of ['ask_start', 'ask_delta', 'ask_done']) store.on(type, onAskEvent);
  store.on('mtab', (tab) => {
    if (tab === 'ask') showTab('ask');
    else if (tab === 'notes' && activeTab === 'ask') showTab('notes');
  });
  core.hotkey('n', focusMine);
  core.hotkey('/', focusAsk);
  renderAll();
}
