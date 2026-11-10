// live.js: setup, roll call, top bar, agenda, roster and the live transcript (regions per MODULES.md).

let core;
let store;
const $ = (id) => document.getElementById(id);
const STATE_LABEL = { idle: 'Idle', rollcall: 'Roll call', live: 'Live', stopping: 'Stopping', ended: 'Ended' };
const FALLBACK_TEMPLATES = ['standup', 'decision-log', 'client', 'brainstorm'];
const SETUP_MEMORY_KEY = 'mc.setup';

// ---------------------------------------------------------------- helpers

function hms(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].map((n) => String(n).padStart(2, '0')).join(':');
}

function busy(btn, on) {
  if (!btn) return;
  const label = btn.querySelector('span') || btn;
  if (!btn.dataset.label) btn.dataset.label = label.textContent;
  label.textContent = on ? 'Working...' : btn.dataset.label;
  btn.disabled = on;
}

function spkStyle(id) { return '--spk: var(--spk-' + core.speakerColorIndex(id) + ')'; }
function isNamed(id) { return !!store.state.speakers[String(id)]; }

function speakerIds() {
  const ids = [];
  for (const seg of store.state.transcript) if (seg.speaker != null && !ids.includes(seg.speaker)) ids.push(seg.speaker);
  for (const key of Object.keys(store.state.speakers)) { const id = Number(key); if (Number.isInteger(id) && !ids.includes(id)) ids.push(id); }
  return ids;
}

// Keyed reconciliation keeps existing rows (their hover, focus and entry animation) and only adds new ones.
function reconcile(container, keys, create, update) {
  const have = new Map([...container.children].map((node) => [node.dataset.key, node]));
  let cursor = null;
  for (const key of keys) {
    const k = String(key);
    let node = have.get(k);
    if (node) have.delete(k); else { node = create(key); node.dataset.key = k; }
    update(node, key);
    const expected = cursor ? cursor.nextSibling : container.firstChild;
    if (node !== expected) container.insertBefore(node, expected);
    cursor = node;
  }
  for (const node of have.values()) node.remove();
}

function lineMarkdown(seg) {
  return '**' + core.speakerName(seg.speaker, seg.name) + '** [' + core.fmtTime(seg.start) + ']: ' + seg.text;
}

function transcriptMarkdown(segs = store.state.transcript) {
  return segs.map((seg) => '- ' + lineMarkdown(seg)).join('\n');
}

function copyMarkdown(markdown) {
  if (!markdown.trim()) { core.toast('Nothing to copy yet'); return; }
  core.copy({ markdown });
}

// ---------------------------------------------------------------- setup screen

const chips = { participants: [], hotwords: [] };

function initChips(name) {
  const box = $('chips-' + name);
  const field = box.querySelector('.chips-field');
  const render = () => {
    for (const node of box.querySelectorAll('.chip')) node.remove();
    box.prepend(...chips[name].map((text, i) => core.el('span', { class: 'chip' },
      core.el('span', null, text),
      core.el('button', { class: 'chip-x', type: 'button', 'aria-label': 'Remove ' + text, onclick: () => { chips[name].splice(i, 1); render(); } }, core.icon('x')))));
  };
  const commit = () => {
    const parts = field.value.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
    for (const p of parts) if (!chips[name].includes(p)) chips[name].push(p);
    field.value = '';
    if (parts.length) render();
  };
  field.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ',') { ev.preventDefault(); commit(); }
    else if (ev.key === 'Backspace' && !field.value && chips[name].length) { chips[name].pop(); render(); }
  });
  field.addEventListener('blur', commit);
  field.addEventListener('paste', (ev) => { if (/[,\n]/.test(ev.clipboardData?.getData('text') || '')) setTimeout(commit); });
  box.addEventListener('click', (ev) => { if (ev.target === box) field.focus(); });
  return { commit, set(list) { chips[name] = [...list]; render(); } };
}

function parseAgenda(text) {
  return text.split('\n').map((line) => line.replace(/^\s*(?:\d+[.)]|[-*])\s*/, '').trim()).filter(Boolean);
}

async function fillSelect(select, path, toOption, fallback) {
  let list = null;
  try { list = await core.api('GET', path); } catch { list = null; }
  if (!Array.isArray(list)) list = [];
  select.replaceChildren(...fallback(list).map(toOption));
}

function rememberSetup(template) {
  try { localStorage.setItem(SETUP_MEMORY_KEY, JSON.stringify({ participants: chips.participants, hotwords: chips.hotwords, template })); } catch { /* storage unavailable */ }
}

function recallSetup() {
  try { return JSON.parse(localStorage.getItem(SETUP_MEMORY_KEY) || '{}') || {}; } catch { return {}; }
}

function renderSetup() {
  const ctrl = core.isController();
  $('setup-card').hidden = !ctrl;
  $('setup-viewer').hidden = ctrl;
}

async function initSetup() {
  const chipsUI = { participants: initChips('participants'), hotwords: initChips('hotwords') };
  const memory = recallSetup();
  if (Array.isArray(memory.participants)) chipsUI.participants.set(memory.participants.filter((s) => typeof s === 'string'));
  if (Array.isArray(memory.hotwords)) chipsUI.hotwords.set(memory.hotwords.filter((s) => typeof s === 'string'));
  const form = $('setup-form');
  const error = $('setup-error');
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    error.hidden = true;
    const title = $('f-title').value.trim();
    if (!title) { error.textContent = 'Give the meeting a title.'; error.hidden = false; $('f-title').focus(); return; }
    chipsUI.participants.commit();
    chipsUI.hotwords.commit();
    const body = {
      title, participants: chips.participants, hotwords: chips.hotwords,
      agenda: parseAgenda($('f-agenda').value), template: $('f-template').value,
    };
    const device = $('f-device').value;
    if (device !== '') body.device = Number(device);
    rememberSetup(body.template);
    busy($('setup-start'), true);
    try { await core.api('POST', '/api/meeting/start', body); }
    catch (err) { error.textContent = err?.detail || 'Could not start the meeting.'; error.hidden = false; }
    finally { busy($('setup-start'), false); }
  });
  for (const id of ['setup-history', 'viewer-history', 'tb-history']) $(id).addEventListener('click', () => store.emit('history:open'));
  const prettify = (id) => id.split('-').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  await Promise.all([
    fillSelect($('f-template'), '/api/templates', (t) => core.el('option', { value: t.id }, t.name || prettify(t.id)),
      (list) => (list.length ? list : FALLBACK_TEMPLATES.map((id) => ({ id, name: prettify(id) })))),
    fillSelect($('f-device'), '/api/devices', (d) => core.el('option', { value: d.index == null ? '' : String(d.index) }, d.name),
      (list) => [{ index: null, name: 'System default input' }, ...list]),
  ]);
  if (memory.template && [...$('f-template').options].some((o) => o.value === memory.template)) $('f-template').value = memory.template;
}

// ---------------------------------------------------------------- roll call

function renderRollcall() {
  const ids = speakerIds();
  const ctrl = core.isController();
  $('roll-empty').hidden = ids.length > 0;
  reconcile($('roll-list'), ids, (id) => core.el('li', { class: 'roll-item', style: spkStyle(id) },
    core.el('span', { class: 'spk-dot', 'aria-hidden': 'true' }),
    core.el('span', { class: 'roll-item-name', onclick: (ev) => startInlineRename(ev.currentTarget.closest('.roll-item'), id) }),
    core.el('span', { class: 'roll-item-id' }, '#' + (id + 1)),
    core.el('button', { class: 'btn btn-subtle btn-icon btn-sm roll-rename-btn', type: 'button', 'aria-label': 'Rename speaker ' + (id + 1), title: 'Rename',
      onclick: (ev) => startInlineRename(ev.currentTarget.closest('.roll-item'), id) }, core.icon('pencil'))),
  (node, id) => {
    const label = node.querySelector('.roll-item-name');
    if (label) {
      label.textContent = core.speakerName(id);
      label.classList.toggle('is-unnamed', !isNamed(id));
    }
    node.querySelector('.roll-rename-btn').hidden = !ctrl;
  });
  $('roll-feed').replaceChildren(...store.state.transcript.slice(-3).map((seg) => core.el('div', { class: 'roll-feed-line', style: spkStyle(seg.speaker) },
    core.el('span', { class: 'spk-name' }, core.speakerName(seg.speaker, seg.name)),
    core.el('span', { class: 'roll-feed-text' }, seg.text))));
}

function startInlineRename(item, id) {
  if (!core.isController() || !item || item.querySelector('.roll-rename')) return;
  const label = item.querySelector('.roll-item-name');
  const input = core.el('input', { class: 'input roll-rename', type: 'text', value: store.state.speakers[String(id)] || '', placeholder: 'Name', 'aria-label': 'Name for speaker ' + (id + 1) });
  let closed = false;
  const finish = async (save) => {
    if (closed) return;
    closed = true;
    const name = input.value.trim();
    if (save && name) {
      input.disabled = true;
      try { await core.api('POST', '/api/speakers', { speaker: id, name }); } catch { /* api toasted */ }
    }
    input.replaceWith(label);
    renderRollcall();
    item.querySelector('.roll-rename-btn')?.focus({ preventScroll: true });
  };
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
    else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); finish(false); }
  });
  input.addEventListener('blur', () => finish(false));
  label.replaceWith(input);
  input.focus();
  input.select();
}

function initRollcall() {
  $('roll-done').addEventListener('click', async () => {
    if (!core.isController()) return;
    busy($('roll-done'), true);
    try { await core.api('POST', '/api/meeting/rollcall/done'); } catch { /* api toasted */ }
    finally { busy($('roll-done'), false); }
  });
}

// ---------------------------------------------------------------- top bar

// Elapsed ticks locally between the 1 Hz heartbeats and freezes while the socket is down.
const clock = { base: 0, at: 0 };

function syncClock() { clock.base = Number(store.state.elapsed) || 0; clock.at = performance.now(); }

function liveElapsed() {
  if (!core.isLiveState() || store.state.conn !== 'open') return clock.base;
  return clock.base + (performance.now() - clock.at) / 1000;
}

function renderTopbar() {
  const s = store.state;
  const live = core.isLiveState();
  $('tb-title').textContent = s.meeting?.title || 'Tablescribe';
  const pill = $('tb-state');
  pill.dataset.state = s.state;
  pill.textContent = STATE_LABEL[s.state] || s.state;
  $('tb-viewing').hidden = !!s.canControl;
  $('tb-rec').hidden = !live;
  $('tb-bookmark').hidden = s.state !== 'live';
  $('tb-stop').hidden = s.state !== 'live';
  const readout = $('tb-readout');
  const parts = [];
  if (s.rtf != null) parts.push('RTF ' + Number(s.rtf).toFixed(2));
  if (s.latency != null) parts.push(Number(s.latency).toFixed(1) + ' s lag');
  readout.replaceChildren(...parts.flatMap((p, i) => (i ? [core.el('span', { class: 'sep' }, '\u00b7'), p] : [p])));
  readout.hidden = !live || !parts.length;
  tick();
}

function tick() {
  $('tb-elapsed').textContent = hms(liveElapsed());
  tickAgenda();
}

async function addBookmark() {
  const btn = $('tb-bookmark');
  if (!core.isController() || store.state.state !== 'live' || btn.disabled) return;
  btn.disabled = true;
  try {
    const b = await core.api('POST', '/api/bookmark', {});
    core.toast('Bookmarked at ' + core.fmtTime(b?.at ?? liveElapsed()));
    btn.classList.add('is-flash');
    setTimeout(() => btn.classList.remove('is-flash'), 600);
  } catch { /* api toasted */ }
  finally { btn.disabled = false; }
}

function initTopbar() {
  $('tb-bookmark').addEventListener('click', addBookmark);
  core.hotkey('b', addBookmark);
  $('tb-stop').addEventListener('click', () => { if (core.isController()) openDialog('dialog-stop', 'dialog-stop-cancel'); });
  setInterval(tick, 500);
}

// ---------------------------------------------------------------- dialogs

let dialogOpener = null;

function openDialog(id, focusId) {
  const dialog = $(id);
  if (dialog.classList.contains('is-open')) return;
  dialogOpener = document.activeElement;
  dialog.classList.add('is-open');
  $(focusId)?.focus();
}

function closeDialog(id) {
  const dialog = $(id);
  if (!dialog.classList.contains('is-open')) return false;
  dialog.classList.remove('is-open');
  if (dialogOpener?.isConnected) dialogOpener.focus({ preventScroll: true });
  dialogOpener = null;
  return true;
}

function initDialogs() {
  for (const id of ['dialog-stop', 'dialog-rename']) $(id).addEventListener('click', (ev) => { if (ev.target === ev.currentTarget) closeDialog(id); });
  $('dialog-stop-cancel').addEventListener('click', () => closeDialog('dialog-stop'));
  $('dialog-stop-confirm').addEventListener('click', async () => {
    if (!core.isController()) return;
    busy($('dialog-stop-confirm'), true);
    try { await core.api('POST', '/api/meeting/stop'); closeDialog('dialog-stop'); } catch { /* api toasted */ }
    finally { busy($('dialog-stop-confirm'), false); }
  });
  let renameId = null;
  store.on('rename:open', (payload) => {
    const id = Number(payload?.speaker);
    if (!core.isController() || !Number.isInteger(id)) return;
    renameId = id;
    const input = $('rename-input');
    input.value = store.state.speakers[String(id)] || '';
    $('dialog-rename-title').textContent = isNamed(id) ? 'Rename ' + core.speakerName(id) : 'Name speaker ' + (id + 1);
    openDialog('dialog-rename', 'rename-input');
    input.select();
  });
  $('rename-cancel').addEventListener('click', () => closeDialog('dialog-rename'));
  $('rename-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const name = $('rename-input').value.trim();
    if (!name || renameId == null || !core.isController()) return;
    const save = $('rename-form').querySelector('[type=submit]');
    busy(save, true);
    try { await core.api('POST', '/api/speakers', { speaker: renameId, name }); closeDialog('dialog-rename'); } catch { /* api toasted */ }
    finally { busy(save, false); }
  });
  core.hotkey('Escape', () => { closeDialog('dialog-stop') || closeDialog('dialog-rename'); });
}

// ---------------------------------------------------------------- agenda

// started_at is either the meeting clock (seconds) or a wall-clock epoch; values above 1e9 are epochs.
function itemElapsed(i) {
  const { started_at: starts, current } = store.state.agenda;
  const start = starts[i];
  if (start == null) return null;
  const now = start > 1e9 ? Date.now() / 1000 : liveElapsed();
  if (i === current) return now - start;
  const later = starts.filter((t) => t != null && t > start);
  return (later.length ? Math.min(...later) : now) - start;
}

function canEditAgenda() { return core.isController() && store.state.state === 'live'; }

function renderAgenda() {
  const { items, current, started_at: starts } = store.state.agenda;
  const ctrl = canEditAgenda();
  $('agenda-empty').hidden = items.length > 0;
  const count = $('agenda-count');
  count.hidden = !items.length;
  count.textContent = current != null && items[current] != null ? (current + 1) + ' / ' + items.length : String(items.length);
  $('agenda-list').replaceChildren(...items.map((label, i) => {
    const isCurrent = i === current;
    const row = core.el(ctrl ? 'button' : 'div', {
      class: 'agenda-item' + (isCurrent ? ' is-current' : starts[i] != null ? ' is-done' : ''),
      type: ctrl ? 'button' : null,
      'aria-current': isCurrent ? 'step' : null,
      title: ctrl && !isCurrent ? 'Make this the current item' : null,
      onclick: ctrl && !isCurrent ? () => setCurrentItem(i) : null,
    },
    core.el('span', { class: 'agenda-idx' }, isCurrent ? core.icon('play') : String(i + 1)),
    core.el('span', { class: 'agenda-label' }, label),
    core.el('span', { class: 'agenda-time', dataset: { i } }));
    return core.el('li', null, row);
  }));
  tickAgenda();
}

function tickAgenda() {
  for (const node of $('agenda-list').querySelectorAll('.agenda-time')) {
    const t = itemElapsed(Number(node.dataset.i));
    const text = t == null ? '' : core.fmtTime(t);
    if (node.textContent !== text) node.textContent = text;
  }
}

async function setCurrentItem(i) {
  if (!canEditAgenda()) return;
  try { await core.api('POST', '/api/agenda', { current: i }); } catch { /* api toasted */ }
}

function agendaMarkdown() {
  const { items, current } = store.state.agenda;
  return items.map((label, i) => (i + 1) + '. ' + label + (i === current ? ' *(now)*' : '')).join('\n');
}

// ---------------------------------------------------------------- roster

function lineCounts() {
  const counts = new Map();
  for (const seg of store.state.transcript) counts.set(seg.speaker, (counts.get(seg.speaker) || 0) + 1);
  return counts;
}

function renderRoster() {
  const ids = speakerIds();
  const ctrl = core.isController();
  const counts = lineCounts();
  $('roster-empty').hidden = ids.length > 0;
  const count = $('people-count');
  count.hidden = !ids.length;
  count.textContent = String(ids.length);
  const rename = (id) => () => store.emit('rename:open', { speaker: id });
  reconcile($('roster'), ids, (id) => core.el('li', { class: 'person', style: spkStyle(id) },
    core.el('span', { class: 'spk-dot', 'aria-hidden': 'true' }),
    core.el('button', { class: 'person-name', type: 'button', title: 'Rename', onclick: rename(id) }),
    core.el('span', { class: 'person-meta mono', title: 'Lines spoken' }),
    core.el('div', { class: 'person-actions' },
      core.el('button', { class: 'btn btn-subtle btn-icon btn-sm person-rename', type: 'button', 'aria-label': 'Rename', title: 'Rename', onclick: rename(id) }, core.icon('pencil')),
      core.el('button', { class: 'btn btn-subtle btn-icon btn-sm', type: 'button', 'aria-label': 'Copy lines', title: 'Copy what they said', onclick: () => copyPerson(id) }, core.icon('copy')))),
  (node, id) => {
    const name = node.querySelector('.person-name');
    name.textContent = core.speakerName(id);
    name.classList.toggle('is-unnamed', !isNamed(id));
    name.disabled = !ctrl;
    name.title = ctrl ? 'Rename' : '';
    node.querySelector('.person-rename').hidden = !ctrl;
    node.querySelector('.person-meta').textContent = String(counts.get(id) || 0);
  });
}

function copyPerson(id) {
  const segs = store.state.transcript.filter((seg) => seg.speaker === id);
  copyMarkdown(segs.length ? '## ' + core.speakerName(id) + '\n\n' + transcriptMarkdown(segs) : '');
}

function peopleMarkdown() {
  const counts = lineCounts();
  return speakerIds().map((id) => '- ' + core.speakerName(id) + ' (' + (counts.get(id) || 0) + ' lines)').join('\n');
}

function initCopyButtons() {
  const builders = {
    agenda: () => '## Agenda\n\n' + agendaMarkdown(),
    people: () => '## People\n\n' + peopleMarkdown(),
    transcript: () => '## Transcript\n\n' + transcriptMarkdown(),
  };
  $('screen-live').addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-copy]');
    const build = btn && builders[btn.dataset.copy];
    if (!build) return;
    const body = build().split('\n\n').slice(1).join('\n\n');
    copyMarkdown(body.trim() ? build() : '');
  });
}

// ---------------------------------------------------------------- transcript

const tx = { lines: new Map(), group: null, stick: true, programmatic: false, playing: null, lastPlayback: null, lastState: null };

function lineNode(seg) {
  return core.el('div', { class: 'line', dataset: { id: seg.id, start: seg.start }, tabindex: '0' },
    core.el('span', { class: 'line-time' }, core.fmtTime(seg.start)),
    core.el('span', { class: 'line-text' }, seg.text),
    core.el('div', { class: 'line-actions' },
      core.el('button', { class: 'btn btn-subtle btn-icon btn-sm', type: 'button', 'aria-label': 'Copy line', title: 'Copy line', dataset: { act: 'copy' } }, core.icon('copy')),
      core.el('button', { class: 'btn btn-subtle btn-icon btn-sm', type: 'button', 'aria-label': 'Play from here', title: 'Play from here', dataset: { act: 'play' } }, core.icon('play'))));
}

function groupNode(seg) {
  return core.el('div', { class: 'tgroup', dataset: { speaker: seg.speaker }, style: spkStyle(seg.speaker) },
    core.el('div', { class: 'tgroup-head' },
      core.el('span', { class: 'spk-dot', 'aria-hidden': 'true' }),
      core.el('span', { class: 'spk-name' }, core.speakerName(seg.speaker, seg.name)),
      core.el('span', { class: 'tgroup-time' }, core.fmtTime(seg.start))));
}

function bookmarkNode(b) {
  return core.el('div', { class: 'bookmark-mark', dataset: { at: b.at }, role: 'button', tabindex: '0', title: 'Play from this bookmark' },
    core.icon('flag'), core.el('span', null, b.label || 'Bookmark'), core.el('span', { class: 'mono' }, core.fmtTime(b.at)));
}

function appendSegment(root, seg, fresh) {
  const existing = tx.lines.get(seg.id);
  if (existing) {
    existing.querySelector('.line-text').textContent = seg.text;
    existing.querySelector('.line-time').textContent = core.fmtTime(seg.start);
    existing.dataset.start = seg.start;
    return;
  }
  if (!tx.group || Number(tx.group.dataset.speaker) !== seg.speaker) { tx.group = groupNode(seg); root.append(tx.group); }
  const line = lineNode(seg);
  if (fresh) line.classList.add('is-new');
  tx.group.append(line);
  tx.lines.set(seg.id, line);
}

function renderTranscript() {
  const segs = [...store.state.transcript].sort((a, b) => a.start - b.start);
  const marks = [...store.state.bookmarks].sort((a, b) => a.at - b.at);
  tx.lines.clear();
  tx.group = null;
  const frag = document.createDocumentFragment();
  let m = 0;
  const placeMarks = (upto) => {
    while (m < marks.length && (upto == null || marks[m].at <= upto)) { frag.append(bookmarkNode(marks[m++])); tx.group = null; }
  };
  for (const seg of segs) { placeMarks(seg.start); appendSegment(frag, seg, false); }
  placeMarks(null);
  $('transcript').replaceChildren(frag);
  renderTranscriptMeta();
  highlightPlaying(tx.lastPlayback);
  requestAnimationFrame(() => scrollToLive(true));
}

function renderTranscriptMeta() {
  const n = store.state.transcript.length;
  $('transcript-count').hidden = !n;
  $('transcript-count').textContent = String(n);
  $('transcript-empty').hidden = n > 0;
}

function onSegment(seg) {
  appendSegment($('transcript'), seg, true);
  renderTranscriptMeta();
  if (tx.stick) scrollToLive();
}

function onBookmark(b) {
  const lastStart = tx.group?.lastElementChild?.dataset.start;
  if (lastStart != null && b.at < Number(lastStart)) { renderTranscript(); return; }
  $('transcript').append(bookmarkNode(b));
  tx.group = null;
  if (tx.stick) scrollToLive();
}

function renameInPlace() {
  for (const group of $('transcript').querySelectorAll('.tgroup')) {
    group.querySelector('.spk-name').textContent = core.speakerName(Number(group.dataset.speaker));
  }
}

function highlightPlaying(p) {
  tx.lastPlayback = p;
  let target = null;
  if (p?.playing && (p.meetingId == null || p.meetingId === store.state.meeting?.id)) {
    let best = null;
    for (const seg of store.state.transcript) if (seg.start <= p.t + 0.05 && (!best || seg.start > best.start)) best = seg;
    if (best) target = tx.lines.get(best.id) || null;
  }
  if (tx.playing === target) return;
  tx.playing?.classList.remove('is-playing');
  tx.playing = target;
  target?.classList.add('is-playing');
}

// Autoscroll sticks to the bottom until the user scrolls up; programmatic smooth scrolls must not unstick it.
function scrollToLive(instant = false) {
  const t = $('transcript');
  tx.stick = true;
  tx.programmatic = true;
  if (instant) t.style.scrollBehavior = 'auto';
  t.scrollTop = t.scrollHeight;
  if (instant) t.style.scrollBehavior = '';
  $('jump-live').hidden = true;
}

function initTranscript() {
  const t = $('transcript');
  t.addEventListener('scroll', () => {
    const nearBottom = t.scrollHeight - t.scrollTop - t.clientHeight < 48;
    if (nearBottom) { tx.stick = true; tx.programmatic = false; }
    else if (!tx.programmatic) tx.stick = false;
    $('jump-live').hidden = tx.stick;
  }, { passive: true });
  for (const type of ['wheel', 'touchstart', 'pointerdown', 'keydown']) t.addEventListener(type, () => { tx.programmatic = false; }, { passive: true });
  t.addEventListener('click', (ev) => {
    const mark = ev.target.closest('.bookmark-mark');
    if (mark) { core.seekAudio(Number(mark.dataset.at)); return; }
    const line = ev.target.closest('.line');
    if (!line) return;
    const seg = store.state.transcript.find((x) => String(x.id) === line.dataset.id);
    if (!seg) return;
    if (ev.target.closest('[data-act]')?.dataset.act === 'copy') copyMarkdown(lineMarkdown(seg));
    else core.seekAudio(seg.start);
  });
  t.addEventListener('keydown', (ev) => {
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches('.line, .bookmark-mark')) { ev.preventDefault(); ev.target.click(); }
  });
  $('jump-live').addEventListener('click', () => scrollToLive());
  core.hotkey('j', () => { if (!$('screen-live').hidden) scrollToLive(); });
  window.addEventListener('resize', () => { if (tx.stick) scrollToLive(true); });
  store.on('mtab', (tab) => { if (tab === 'transcript' && tx.stick) requestAnimationFrame(() => scrollToLive(true)); });
}

// ---------------------------------------------------------------- wiring

// A 'state' event names only the meeting id; the title and the rest arrive by refetching the snapshot once per meeting.
const detailsFetched = new Set();

async function ensureMeetingDetails(s) {
  const id = s.meeting?.id;
  if (!id || s.meeting.title || detailsFetched.has(id)) return;
  detailsFetched.add(id);
  try {
    const snap = await core.api('GET', '/api/state');
    if (snap?.meeting?.id === id) core.dispatch({ ...snap, type: 'snapshot' });
  } catch { detailsFetched.delete(id); }
}

function renderAll() {
  renderSetup();
  renderTopbar();
  renderRollcall();
  renderAgenda();
  renderRoster();
  renderTranscript();
}

function wireStore() {
  store.on('snapshot', () => { tx.lastState = store.state.state; syncClock(); renderAll(); });
  store.on('state', (msg, s) => {
    const was = tx.lastState;
    tx.lastState = s.state;
    syncClock();
    renderTopbar();
    ensureMeetingDetails(s);
    if (was === s.state) return;
    renderAgenda();
    if (s.state === 'live') requestAnimationFrame(() => scrollToLive(true));
  });
  store.on('segment', (msg, s) => { onSegment(msg.segment); renderRoster(); if (s.state === 'rollcall') renderRollcall(); });
  store.on('speakers', () => { renameInPlace(); renderRollcall(); renderRoster(); });
  store.on('bookmark', onBookmark);
  store.on('agenda', renderAgenda);
  store.on('playback', highlightPlaying);
  store.on('control', () => { renderSetup(); renderTopbar(); renderRollcall(); renderAgenda(); renderRoster(); });
  store.on('conn', () => tick());
  store.on('local', (patch) => { if (patch && 'view' in patch && patch.view == null && tx.stick) requestAnimationFrame(() => scrollToLive(true)); });
}

export function mount(c) {
  core = c;
  store = c.store;
  initRollcall();
  initTopbar();
  initDialogs();
  initCopyButtons();
  initTranscript();
  wireStore();
  initSetup().catch((err) => console.error('[live] setup init failed', err));
}
