// web/js/after.js - after the meeting: the ended screen (minutes, recording, transcript) and the
// history drawer (past meetings, search, ask across meetings). Owns #screen-ended, #drawer and
// #drawer-scrim; everything else is reached through `core` (see MODULES.md).

const FALLBACK_TEMPLATES = [
  { id: 'standup', name: 'Standup' },
  { id: 'decision-log', name: 'Decision log' },
  { id: 'client', name: 'Client' },
  { id: 'brainstorm', name: 'Brainstorm' },
];
const SEARCH_DEBOUNCE_MS = 220;
const MIN_QUERY_LENGTH = 2;
const SEP = '\u00b7';

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- formatting + data shaping

function toDate(v) {
  if (v === null || v === undefined || v === '') return null;
  const d = typeof v === 'number' ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtDate(v) {
  const d = toDate(v);
  return d ? d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '';
}

function fmtDuration(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return s + ' s';
  const total = Math.round(s / 60);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h ? h + ' h ' + String(m).padStart(2, '0') + ' min' : m + ' min';
}

function durationOf(m) {
  if (m.duration !== null && m.duration !== undefined) return Number(m.duration);
  const a = toDate(m.started ?? m.started_at);
  const b = toDate(m.ended ?? m.ended_at);
  if (a && b) return (b - a) / 1000;
  const last = Array.isArray(m.transcript) && m.transcript[m.transcript.length - 1];
  return last ? Number(last.end) || null : null;
}

/** One shape for both the store's current meeting and GET /api/meetings/<id>. */
function normalizeMeeting(m, id) {
  const minutes = m.minutes && typeof m.minutes === 'object'
    ? { markdown: m.minutes.markdown || '', template: m.minutes.template || m.template || null }
    : (m.minutes ? { markdown: String(m.minutes), template: m.template || null } : null);
  const speakers = m.speakers || {};
  const participants = Array.isArray(m.participants) && m.participants.length
    ? m.participants : [...new Set(Object.values(speakers))];
  return {
    id: m.id || id || null,
    title: m.title || 'Untitled meeting',
    started: m.started ?? m.started_at ?? null,
    duration: durationOf(m),
    participants,
    speakers,
    transcript: Array.isArray(m.transcript) ? m.transcript : [],
    minutes,
    template: minutes?.template || m.template || null,
  };
}

function currentMeeting(s) {
  if (!s.meeting) return null;
  return normalizeMeeting({ ...s.meeting, transcript: s.transcript, speakers: s.speakers, minutes: s.minutes }, s.meeting.id);
}

function nameOf(m, seg) {
  const mapped = m.speakers[String(seg.speaker)];
  if (mapped) return mapped;
  if (seg.name) return seg.name;
  return seg.speaker === null || seg.speaker === undefined ? 'Unknown' : 'Speaker ' + (Number(seg.speaker) + 1);
}

function groupBySpeaker(transcript) {
  const groups = [];
  for (const seg of transcript) {
    const last = groups[groups.length - 1];
    if (last && last.speaker === seg.speaker) last.segs.push(seg);
    else groups.push({ speaker: seg.speaker, segs: [seg] });
  }
  return groups;
}

function transcriptMarkdown(m, fmtTime) {
  const out = ['# ' + m.title + ' - transcript', ''];
  for (const g of groupBySpeaker(m.transcript)) {
    out.push('**' + nameOf(m, g.segs[0]) + '** ' + SEP + ' ' + fmtTime(g.segs[0].start));
    for (const seg of g.segs) out.push('- [' + fmtTime(seg.start) + '] ' + seg.text);
    out.push('');
  }
  return out.join('\n');
}

function safeFilename(s) {
  return String(s ?? '').replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '') || 'meeting';
}

// The server HTML-escapes snippets (Python html.escape: ' arrives as &#x27;); decode before making text nodes.
const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0' };
function decodeEntities(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, k) => {
    if (k[0] !== '#') return ENTITIES[k.toLowerCase()] ?? whole;
    const n = k[1] === 'x' || k[1] === 'X' ? parseInt(k.slice(2), 16) : parseInt(k.slice(1), 10);
    return n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : whole;
  });
}

/** Search snippet as DOM nodes: only <mark>...</mark> survives as markup, everything else is text. */
function snippetNodes(snippet) {
  const frag = document.createDocumentFragment();
  let cur = frag;
  for (const part of String(snippet ?? '').split(/(<\/?mark(?:\s[^>]*)?>)/i)) {
    if (!part) continue;
    if (/^<mark(\s|>)/i.test(part)) {
      if (cur === frag) { cur = document.createElement('mark'); frag.append(cur); }
    } else if (/^<\/mark>$/i.test(part)) {
      cur = frag;
    } else {
      cur.append(document.createTextNode(decodeEntities(part)));
    }
  }
  return frag;
}

// ---------------------------------------------------------------- ended screen

export async function mount(core) {
  const { store, el, api, copy, toast, fmtTime, escapeHtml, renderMarkdown, linkifyTimestamps,
    seekAudio, isController, isLiveState, speakerColorIndex } = core;
  const screen = $('screen-ended');
  if (!screen) throw new Error('#screen-ended is missing');

  let past = null;              // normalized meeting while browsing history, else null (follow the store)
  let templates = FALLBACK_TEMPLATES;
  let regenerating = null;      // template id while a regeneration is in flight
  let lastState = store.state.state;
  let transcriptKey = null;
  let lineIndex = [];
  let playingNode = null;

  const shown = () => past || currentMeeting(store.state);
  const templateName = (id) => templates.find((t) => t.id === id)?.name || id || '';
  const span = (cls, text) => el('span', cls ? { class: cls } : null, text);
  const sep = () => el('span', { class: 'sep', 'aria-hidden': 'true' }, SEP);

  function renderEnded() {
    const m = shown();
    if (m?.id) screen.dataset.meetingId = m.id; else delete screen.dataset.meetingId;
    screen.classList.toggle('is-past', !!past);
    const back = $('ended-back');
    back.hidden = !past;
    back.lastElementChild.textContent = isLiveState() ? 'Back to live meeting' : store.state.meeting ? 'Back to current meeting' : 'Back';
    $('ended-new').hidden = isLiveState();   // a meeting is running: starting another would 409
    $('ended-overline').textContent = past ? 'Past meeting' : 'Minutes';
    $('ended-title').textContent = m ? m.title : 'No meeting yet';
    renderMeta(m);
    renderMinutes(m);
    renderTranscript(m);
    renderAudio(m);
  }

  function renderMeta(m) {
    const parts = [];
    if (m) {
      const d = toDate(m.started);
      if (d) parts.push(el('time', { datetime: d.toISOString() }, fmtDate(d)));
      if (m.duration !== null) parts.push(span('mono', fmtDuration(m.duration)));
      if (m.participants.length) parts.push(span(null, m.participants.join(', ')));
    }
    $('ended-meta').replaceChildren(...parts.flatMap((p, i) => (i ? [sep(), p] : [p])));
  }

  function fillTemplateSelect() {
    $('minutes-template').replaceChildren(el('option', { value: '', disabled: true, hidden: true }, 'Choose a template'),
      ...templates.map((t) => el('option', { value: t.id }, t.name || t.id)));
  }

  function renderMinutes(m) {
    const md = m?.minutes?.markdown || '';
    const tpl = m?.template || null;
    const canEdit = isController() && !past && !!m?.id;   // past meetings open read-only (UI-SPEC #5)
    const sel = $('minutes-template');
    if (tpl && !templates.some((t) => t.id === tpl)) {
      templates = [...templates, { id: tpl, name: tpl }];
      fillTemplateSelect();
    }
    // Without minutes the placeholder is selected, so choosing the meeting's own template still fires 'change'.
    sel.value = regenerating || (md && tpl ? tpl : '');
    sel.hidden = !canEdit;
    sel.disabled = !!regenerating;
    const badge = $('minutes-template-badge');
    badge.hidden = !tpl || canEdit;
    badge.textContent = templateName(tpl);
    // The server publishes minutes (or an error) before it enters 'ended', so only 'stopping' is still writing.
    const writing = !!m && !past && !md && store.state.state === 'stopping';
    const status = $('minutes-status');
    status.hidden = !(writing || regenerating);
    status.lastElementChild.textContent = regenerating
      ? (md ? 'Rewriting' : 'Writing') + ' minutes as ' + templateName(regenerating) + '...' : 'Writing minutes...';
    const view = $('minutes');
    if (md) view.innerHTML = linkifyTimestamps(renderMarkdown(md));
    else if (writing || regenerating) view.replaceChildren();
    else {
      const text = !m ? 'Minutes appear here when a meeting ends.'
        : 'No minutes were written for this meeting.' + (canEdit ? ' Choose a template above to write them.' : '');
      view.replaceChildren(el('p', { class: 'empty' }, text));
    }
    for (const id of ['minutes-copy-rich', 'minutes-copy-md', 'minutes-download']) $(id).disabled = !md;
  }

  function renderTranscript(m) {
    const segs = m ? m.transcript : [];
    const count = $('ended-transcript-count');
    count.hidden = !segs.length;
    count.textContent = segs.length + (segs.length === 1 ? ' line' : ' lines');
    $('transcript-ended-empty').hidden = segs.length > 0;
    const key = (m?.id || '') + ':' + segs.length + ':' + (segs.length ? segs[segs.length - 1].id : '');
    if (key === transcriptKey) return;
    transcriptKey = key;
    lineIndex = [];
    playingNode = null;
    const frag = document.createDocumentFragment();
    for (const g of groupBySpeaker(segs)) {
      const first = g.segs[0];
      const group = el('section', { class: 'tgroup', style: '--spk: var(--spk-' + speakerColorIndex(g.speaker) + ')' },
        el('div', { class: 'tgroup-head' },
          el('span', { class: 'spk-dot', 'aria-hidden': 'true' }),
          span('spk-name', nameOf(m, first)),
          span('tgroup-time', fmtTime(first.start))));
      for (const seg of g.segs) {
        const line = el('div', { class: 'line', role: 'button', tabindex: '0', dataset: { t: seg.start }, title: 'Play from ' + fmtTime(seg.start) },
          span('line-time', fmtTime(seg.start)), span('line-text', seg.text));
        lineIndex.push({ start: Number(seg.start) || 0, node: line });
        group.append(line);
      }
      frag.append(group);
    }
    $('transcript-ended').replaceChildren(frag);
  }

  // Point the native player at the shown recording so its controls work before any line is clicked.
  // Playback itself always goes through core.seekAudio; a still-growing live file is left to core.
  function renderAudio(m) {
    const audio = $('audio');
    if (!audio || !m?.id) return;
    if (!past && isLiveState() && m.id === store.state.meeting?.id) return;
    const want = '/api/meetings/' + encodeURIComponent(m.id) + '/audio';
    if ((audio.getAttribute('src') || '') === want) return;   // a cache-busted live URL is swapped: the file is complete now
    audio.pause();
    audio.src = want;
    $('audio-pos').hidden = true;
    setPlaying(null);
  }

  function setPlaying(node) {
    if (playingNode === node) return;
    playingNode?.classList.remove('is-playing');
    playingNode = node;
    node?.classList.add('is-playing');
  }

  function lineAt(t) {
    let hit = null;
    for (const L of lineIndex) { if (L.start <= t + 0.05) hit = L; else break; }
    return hit;
  }

  function revealLine(node) {
    // Center within the part of the box that is on screen: the box itself can reach below the fold.
    const box = $('transcript-ended');
    const b = box.getBoundingClientRect();
    const s = screen.getBoundingClientRect();
    const top = Math.max(b.top, s.top);
    const bottom = Math.min(b.bottom, s.bottom);
    const r = node.getBoundingClientRect();
    if (r.top < top || r.bottom > bottom) box.scrollTop += r.top - top - (bottom - top - r.height) / 2;
  }

  // Which recording is really loaded: core's meetingId lags behind after renderAudio switched the src.
  function loadedMeetingId() {
    const hit = /\/api\/meetings\/([^/?#]+)\/audio/.exec($('audio')?.getAttribute('src') || '');
    return hit ? decodeURIComponent(hit[1]) : null;
  }

  store.on('playback', ({ t, playing, meetingId }) => {
    const m = shown();
    const pos = $('audio-pos');
    if (!m || (loadedMeetingId() ?? meetingId) !== m.id) { pos.hidden = true; setPlaying(null); return; }
    pos.hidden = false;
    pos.textContent = fmtTime(t);
    const hit = lineAt(t);
    const changed = (hit ? hit.node : null) !== playingNode;
    setPlaying(hit ? hit.node : null);
    if (playing && changed && hit) revealLine(hit.node);
  });

  const tbox = $('transcript-ended');
  const playLine = (line) => seekAudio(Number(line.dataset.t), shown()?.id);
  tbox.addEventListener('click', (ev) => { const line = ev.target.closest('.line'); if (line) playLine(line); });
  tbox.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const line = ev.target.closest('.line');
    if (line) { ev.preventDefault(); playLine(line); }
  });

  /** raw = GET /api/meetings/<id> dict (read-only past meeting), or null for the current ended meeting. */
  function showMeeting(raw, id, seekTo) {
    past = raw ? normalizeMeeting(raw, id) : null;
    renderEnded();
    store.set({ view: past ? 'ended' : null });
    const t = Number(seekTo);
    if (seekTo !== null && seekTo !== undefined && Number.isFinite(t)) {
      seekAudio(t, shown()?.id);
      const hit = lineAt(t);
      if (hit) {
        setPlaying(hit.node);
        // Two columns: only the transcript box scrolls, so the title and Back stay in view. One column: scroll the page.
        const box = $('transcript-ended');
        if (box.scrollHeight > box.clientHeight) { screen.scrollTop = 0; revealLine(hit.node); }
        else hit.node.scrollIntoView({ block: 'center' });
      }
    } else {
      screen.scrollTop = 0;
    }
  }

  function leavePast() {
    if (!past) return;
    past = null;
    renderEnded();
    store.set({ view: null });
  }

  async function regenerate(template) {
    const m = shown();
    if (!isController() || past || !m?.id || !template) return;
    regenerating = template;
    renderMinutes(m);
    try {
      const res = await api('POST', '/api/meetings/' + encodeURIComponent(m.id) + '/minutes', { template });
      if (res?.markdown && store.state.meeting?.id === m.id) {
        store.set({ minutes: { markdown: res.markdown, template: res.template || template } });
      }
    } catch (err) {
      if (!(err instanceof core.ApiError)) console.error('[after] minutes regeneration failed', err);
    } finally {
      regenerating = null;
      renderEnded();
    }
  }

  $('minutes-template').addEventListener('change', (ev) => regenerate(ev.target.value));
  $('minutes-copy-rich').addEventListener('click', () => {
    const md = shown()?.minutes?.markdown;
    if (md) copy({ markdown: md });
  });
  $('minutes-copy-md').addEventListener('click', () => {
    const md = shown()?.minutes?.markdown;
    if (md) copy({ markdown: md, html: '<pre>' + escapeHtml(md) + '</pre>' });
  });
  $('minutes-download').addEventListener('click', () => {
    const m = shown();
    const md = m?.minutes?.markdown;
    if (!md) return;
    const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown;charset=utf-8' }));
    const a = el('a', { href: url, download: safeFilename(m.id || m.title) + '-minutes.md', hidden: true });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  screen.querySelector('[data-copy="transcript"]')?.addEventListener('click', () => {
    const m = shown();
    if (m?.transcript.length) copy({ markdown: transcriptMarkdown(m, fmtTime) });
    else toast('No transcript to copy');
  });
  $('ended-back').addEventListener('click', leavePast);
  $('ended-new').addEventListener('click', () => {
    if (!isController() || isLiveState()) return;
    past = null;
    renderEnded();
    store.set({ view: store.state.state === 'ended' ? 'setup' : null });   // idle already shows setup
  });

  function syncView() {
    const s = store.state;
    if (s.state === lastState) return false;
    const startedNow = isLiveState(s.state) && !isLiveState(lastState);
    lastState = s.state;
    if (s.view === 'setup' && s.state !== 'ended') store.set({ view: null });
    else if (s.view === 'ended' && startedNow) { past = null; store.set({ view: null }); }
    return true;
  }
  store.on('snapshot', () => { syncView(); renderEnded(); });
  store.on('state', () => { if (syncView()) renderEnded(); });
  store.on('minutes', () => { if (!past) renderEnded(); });
  store.on('control', () => renderMinutes(shown()));

  async function loadTemplates() {
    try {
      const list = await api('GET', '/api/templates');
      if (Array.isArray(list) && list.length) templates = list.map((t) => (typeof t === 'string' ? { id: t, name: t } : t));
    } catch (err) {
      if (!(err instanceof core.ApiError)) console.error('[after] templates failed', err);
    }
    fillTemplateSelect();
    renderMinutes(shown());
  }

  fillTemplateSelect();
  renderEnded();
  mountDrawer({ core, shown, showMeeting });
  mountAsk({ core });
  loadTemplates();
}

// ---------------------------------------------------------------- history drawer

function mountDrawer({ core, shown, showMeeting }) {
  const { store, el, api, fmtTime, hotkey } = core;
  const drawer = $('drawer');
  const scrim = $('drawer-scrim');
  const closeBtn = $('drawer-close');
  const meetingsPanel = $('meetings-panel');
  const searchPanel = $('search-panel');
  const input = $('search-input');
  const meetingsEmptyText = $('meetings-empty').textContent;
  const searchEmptyText = $('search-empty').textContent;
  let lastFocus = null;
  let meetingsSeq = 0;
  let searchSeq = 0;
  let searchTimer = null;
  let lastQuery = '';
  let openSeq = 0;

  const isOpen = () => drawer.classList.contains('is-open');
  const sep = () => el('span', { class: 'sep', 'aria-hidden': 'true' }, SEP);
  const metaRow = (cls, parts) => el('span', { class: cls }, ...parts.filter(Boolean).flatMap((p, i) => (i ? [sep(), p] : [p])));
  const logUnexpected = (what) => (err) => { if (!(err instanceof core.ApiError)) console.error('[after] ' + what + ' failed', err); };

  function open() {
    if (!isOpen()) {
      lastFocus = document.activeElement;
      drawer.classList.add('is-open');
      scrim.classList.add('is-open');
      loadMeetings();
    }
    closeBtn.focus({ preventScroll: true });
  }

  function close() {
    if (!isOpen()) return;
    drawer.classList.remove('is-open');
    scrim.classList.remove('is-open');
    const back = lastFocus;
    lastFocus = null;
    if (back && document.contains(back) && typeof back.focus === 'function' && !back.closest('[hidden]')) back.focus({ preventScroll: true });
  }

  drawer.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Tab') return;
    const nodes = [...drawer.querySelectorAll('button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])')]
      .filter((n) => !n.disabled && !n.closest('[hidden]'));
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  });
  closeBtn.addEventListener('click', close);
  scrim.addEventListener('click', close);
  hotkey('Escape', () => { if (isOpen()) close(); });
  // UI-SPEC #8 "/ focus Ask": while the drawer is open its Ask is the one on screen (runs after side.js's handler).
  hotkey('/', () => { if (isOpen()) $('ask-all-input').focus(); });
  store.on('history:open', () => open());
  $('ended-history').addEventListener('click', open);

  function meetingRow(m) {
    const duration = durationOf(m);   // the disk fallback of /api/meetings sends duration: null
    const current = document.body.dataset.screen === 'ended' && shown()?.id === m.id;
    const meta = metaRow('meeting-meta', [
      current ? el('span', { class: 'badge' }, 'Showing') : null,
      fmtDate(m.started),
      duration !== null ? fmtDuration(duration) : '',
      (m.participants || []).join(', '),
    ]);
    return el('li', null, el('button', {
      class: 'meeting-row', type: 'button', 'aria-current': current ? 'true' : null, onclick: () => openPast(m.id, null),
    }, el('span', { class: 'meeting-title' }, m.title || m.id), meta));
  }

  async function loadMeetings() {
    const list = $('meetings-list');
    const empty = $('meetings-empty');
    const count = $('meetings-count');
    const seq = ++meetingsSeq;
    if (!list.childElementCount) {
      list.replaceChildren(...[0, 1, 2].map(() => el('li', { class: 'skel', 'aria-hidden': 'true' })));
      empty.hidden = true;
    }
    let items = null;
    try { items = await api('GET', '/api/meetings'); } catch (err) { logUnexpected('meetings list')(err); }
    if (seq !== meetingsSeq) return;
    if (!Array.isArray(items)) items = Array.isArray(items?.meetings) ? items.meetings : null;
    list.replaceChildren(...(items || []).map(meetingRow));
    count.hidden = !items?.length;
    count.textContent = String(items?.length || 0);
    empty.textContent = items ? meetingsEmptyText : 'Could not load past meetings.';
    empty.hidden = !!items?.length;
  }

  function hitRow(h) {
    const start = h.start ?? null;
    const meta = metaRow('hit-meta', [
      el('span', { class: 'hit-title' }, h.title || h.meeting_id),
      fmtDate(h.started),
      start !== null ? el('span', { class: 'hit-time' }, fmtTime(start)) : null,
    ]);
    const snippet = el('span', { class: 'hit-snippet' });
    snippet.append(snippetNodes(h.snippet));
    return el('li', null, el('button', { class: 'hit', type: 'button', onclick: () => openPast(h.meeting_id, start) }, meta, snippet));
  }

  async function runSearch(raw) {
    const q = raw.trim();
    const hits = $('search-hits');
    const count = $('search-count');
    const empty = $('search-empty');
    if (q.length < MIN_QUERY_LENGTH) {
      searchSeq++;
      lastQuery = '';
      searchPanel.hidden = true;
      meetingsPanel.hidden = false;
      hits.replaceChildren();
      return;
    }
    if (q === lastQuery) return;
    lastQuery = q;
    const seq = ++searchSeq;
    searchPanel.hidden = false;
    meetingsPanel.hidden = true;
    count.hidden = false;
    count.textContent = '...';
    let items = null;
    try { items = await api('GET', '/api/search?q=' + encodeURIComponent(q)); } catch (err) { logUnexpected('search')(err); }
    if (seq !== searchSeq) return;
    if (!Array.isArray(items)) items = Array.isArray(items?.results) ? items.results : (items ? [] : null);
    hits.replaceChildren(...(items || []).map(hitRow));
    count.hidden = !items;
    count.textContent = items ? String(items.length) : '';
    empty.textContent = items ? searchEmptyText : 'Search failed. Is the server running?';
    empty.hidden = !!items?.length;
  }

  input.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(input.value), SEARCH_DEBOUNCE_MS);
  });
  input.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    clearTimeout(searchTimer);
    runSearch(input.value);
  });

  async function openPast(id, seekTo) {
    if (!id) return;
    const seq = ++openSeq;
    if (id === store.state.meeting?.id && store.state.state === 'ended') { close(); showMeeting(null, id, seekTo); return; }
    let raw = null;
    try { raw = await api('GET', '/api/meetings/' + encodeURIComponent(id)); } catch (err) { logUnexpected('load meeting')(err); return; }
    if (seq !== openSeq || !raw || typeof raw !== 'object') return;
    const m = !raw.transcript && raw.meeting && typeof raw.meeting === 'object' ? raw.meeting : raw;
    close();
    showMeeting(m, id, seekTo);
  }
}

// ---------------------------------------------------------------- ask across meetings

function mountAsk({ core }) {
  const { store, el, api, copy, toast, escapeHtml, renderMarkdown, linkifyTimestamps } = core;
  const chat = $('chat-all');
  const empty = $('chat-all-empty');
  const form = $('ask-all-form');
  const input = $('ask-all-input');
  const send = form.querySelector('button[type="submit"]');
  const mine = new Set();
  const failed = new Map();   // ask_id -> message of a WS 'error' that carried this ask_id
  const isAll = (id, a) => a.scope === 'all' || mine.has(id);
  const entries = () => Object.entries(store.state.asks).filter(([id, a]) => isAll(id, a));
  const nearBottom = () => chat.scrollHeight - chat.scrollTop - chat.clientHeight < 48;
  const finished = (id, a) => a.done || failed.has(id);
  function answerHtml(id, a) {
    if (a.text) return linkifyTimestamps(renderMarkdown(a.text));
    if (finished(id, a)) return '<p class="empty">' + escapeHtml(failed.get(id) || 'No answer.') + '</p>';
    return '<p></p>';
  }
  const answerNode = (id, a) => el('div', { class: 'msg msg-a' + (finished(id, a) ? '' : ' is-streaming'), dataset: { ask: id } },
    el('div', { class: 'md', html: answerHtml(id, a) }));
  const findAnswer = (id) => chat.querySelector('.msg-a[data-ask="' + CSS.escape(id) + '"]');

  function render() {
    const list = entries();
    empty.hidden = list.length > 0;
    const stick = nearBottom();
    chat.replaceChildren(...list.flatMap(([id, a]) => [el('div', { class: 'msg msg-q' }, a.question || '...'), answerNode(id, a)]));
    if (stick) chat.scrollTop = chat.scrollHeight;
  }

  function update(id) {
    const a = store.state.asks[id];
    if (!a || !isAll(id, a)) return;
    const n = findAnswer(id);
    if (!n) { render(); return; }
    const stick = nearBottom();
    n.classList.toggle('is-streaming', !finished(id, a));
    n.firstElementChild.innerHTML = answerHtml(id, a);
    if (stick) chat.scrollTop = chat.scrollHeight;
  }

  store.on('snapshot', render);
  store.on('ask_start', render);
  store.on('ask_delta', ({ ask_id }) => update(ask_id));
  store.on('ask_done', ({ ask_id }) => update(ask_id));
  store.on('error', ({ ask_id, message }) => {
    if (!ask_id) return;
    failed.set(ask_id, message || 'Ask failed.');
    update(ask_id);
  });

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const question = input.value.trim();
    if (!question) { input.focus(); return; }
    input.value = '';
    send.disabled = true;
    try {
      const res = await api('POST', '/api/ask', { question, scope: 'all' });
      const id = res?.ask_id;
      if (id) {
        mine.add(id);
        if (!store.state.asks[id]?.question) core.dispatch({ type: 'ask_start', ask_id: id, question, scope: 'all' });
        else render();
      }
    } catch (err) {
      if (!(err instanceof core.ApiError)) console.error('[after] ask failed', err);
      input.value = question;
    } finally {
      send.disabled = false;
      input.focus({ preventScroll: true });
    }
  });

  $('drawer').querySelector('[data-copy="ask-all"]').addEventListener('click', () => {
    const list = entries();
    if (!list.length) { toast('Nothing to copy yet'); return; }
    const md = list.map(([, a]) => '**Q:** ' + a.question + '\n\n' + (a.text.trim() || '_(no answer yet)_')).join('\n\n---\n\n') + '\n';
    copy({ markdown: md });
  });

  render();
}
