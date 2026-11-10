// Shared front-end core: store, WebSocket, REST, clipboard, DOM helpers.
// Plain ES module, no dependencies beyond ./markdown.js. See MODULES.md for the contract.
import { renderMarkdown } from './markdown.js';

// ---------------------------------------------------------------- store

const LIVE_STATES = new Set(['rollcall', 'live', 'stopping']);

function emptyState() {
  return {
    conn: 'connecting',        // 'connecting' | 'open' | 'reconnecting'
    state: 'idle',             // idle | rollcall | live | stopping | ended
    meeting: null,             // Meeting.to_dict() minus the fields lifted below, or null
    transcript: [],            // [Segment] (lifted from meeting.transcript, appended by 'segment')
    notes: null,               // NOTES dict or null
    factchecks: [],            // [factcheck item], de-duplicated by id
    speakers: {},              // {"<id>": "<name>"}
    bookmarks: [],             // [{id, at, label}]
    agenda: { items: [], current: null, started_at: [] },
    userNotes: '',
    asks: {},                  // {ask_id: {question, scope, text, done}}
    minutes: null,             // {markdown, template} or null
    canControl: false,
    rtf: null,
    latency: null,
    elapsed: 0,
    view: null,                // screen override: null follows state; 'ended' while browsing a past meeting
  };
}

const listeners = new Map();

export const store = {
  state: emptyState(),

  /** Subscribe to a WS event type ('snapshot', 'segment', ...), a core event
   *  ('conn', 'playback', 'control'), or 'change'. Returns an unsubscribe fn.
   *  Handlers get (payload, state); 'change' handlers get ({type, payload}, state). */
  on(type, fn) {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(fn);
    return () => listeners.get(type)?.delete(fn);
  },

  /** Emit an event to subscribers of `type`, then a 'change' event. */
  emit(type, payload) {
    for (const fn of [...(listeners.get(type) || [])]) {
      try { fn(payload, store.state); } catch (err) { console.error(`[core] ${type} handler failed`, err); }
    }
    if (type === 'change') return;
    for (const fn of [...(listeners.get('change') || [])]) {
      try { fn({ type, payload }, store.state); } catch (err) { console.error('[core] change handler failed', err); }
    }
  },

  /** Shallow-merge a local patch into state and emit 'change' (type 'local'). */
  set(patch) {
    Object.assign(store.state, patch);
    store.emit('local', patch);
  },
};

function normAgenda(a) {
  if (!a) return { items: [], current: null, started_at: [] };
  if (Array.isArray(a)) return { items: a, current: null, started_at: a.map(() => null) };
  return { items: a.items || [], current: a.current ?? null, started_at: a.started_at || [] };
}

function applySnapshot(msg) {
  const prev = store.state;
  const s = emptyState();
  s.conn = prev.conn;
  s.view = prev.view;
  s.asks = prev.asks;                       // keep chat history across reconnects
  s.state = msg.state || msg.meeting?.state || 'idle';
  s.canControl = !!msg.can_control;
  const m = msg.meeting;
  if (m) {
    s.meeting = m;
    s.transcript = m.transcript || [];
    s.notes = m.notes || null;
    s.factchecks = m.factchecks || [];
    s.speakers = m.speakers || {};
    s.bookmarks = m.bookmarks || [];
    s.agenda = normAgenda(m.agenda);
    s.userNotes = m.user_notes || '';
    s.minutes = m.minutes ? { markdown: m.minutes, template: m.template || null } : null;
  }
  for (const k of ['elapsed', 'rtf', 'latency']) if (msg[k] != null) s[k] = msg[k];
  store.state = s;
}

function ask(id) {
  const a = store.state.asks;
  if (!a[id]) a[id] = { question: '', scope: 'meeting', text: '', done: false };
  return a[id];
}

// Mutates store.state for one server message. Unknown types are still emitted.
function reduce(msg) {
  const s = store.state;
  switch (msg.type) {
    case 'snapshot': applySnapshot(msg); break;
    case 'state':
      s.state = msg.state ?? s.state;
      for (const k of ['elapsed', 'rtf', 'latency']) if (msg[k] !== undefined) s[k] = msg[k];
      if (msg.meeting_id && s.meeting && s.meeting.id !== msg.meeting_id) s.meeting = { ...s.meeting, id: msg.meeting_id };
      if (msg.meeting_id && !s.meeting) s.meeting = { id: msg.meeting_id };
      break;
    case 'segment': {
      const seg = msg.segment;
      const i = s.transcript.findIndex((x) => x.id === seg.id);
      if (i >= 0) s.transcript[i] = seg; else s.transcript.push(seg);
      break;
    }
    case 'speakers': s.speakers = { ...s.speakers, ...(msg.map || {}) }; break;
    case 'notes': s.notes = msg.notes; break;
    case 'factcheck': {
      const i = s.factchecks.findIndex((x) => x.id === msg.item.id);
      if (i >= 0) s.factchecks[i] = msg.item; else s.factchecks.push(msg.item);
      break;
    }
    case 'bookmark':
      if (!s.bookmarks.some((b) => b.id === msg.id)) s.bookmarks.push({ id: msg.id, at: msg.at, label: msg.label });
      break;
    case 'user_notes': s.userNotes = msg.text ?? ''; break;
    case 'agenda': s.agenda = normAgenda(msg); break;
    case 'ask_start': Object.assign(ask(msg.ask_id), { question: msg.question, scope: msg.scope || 'meeting' }); break;
    case 'ask_delta': ask(msg.ask_id).text += msg.text || ''; break;
    case 'ask_done': ask(msg.ask_id).done = true; break;
    case 'minutes': s.minutes = { markdown: msg.markdown, template: msg.template }; break;
    case 'error': toast(msg.message || 'Server error', { error: true }); break;
    default: break;
  }
}

/** Feed a server message through the reducer and emit it (also used by tests/modules). */
export function dispatch(msg) {
  if (!msg || typeof msg.type !== 'string') return;
  const wasControl = store.state.canControl;
  reduce(msg);
  store.emit(msg.type, msg);
  if (store.state.canControl !== wasControl) store.emit('control', store.state.canControl);
}

export function isController() { return !!store.state.canControl; }
export function isLiveState(state = store.state.state) { return LIVE_STATES.has(state); }

// ---------------------------------------------------------------- websocket

let ws = null;
let retry = 0;
let retryTimer = null;

function setConn(conn) {
  if (store.state.conn === conn) return;
  store.state.conn = conn;
  store.emit('conn', conn);
}

/** Open (or reopen) the WebSocket. Exponential backoff 0.5 s -> 10 s, reset on open. */
export function connect() {
  clearTimeout(retryTimer);
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => { retry = 0; setConn('open'); };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { console.error('[core] bad WS frame', ev.data); return; }
    dispatch(msg);
  };
  ws.onclose = () => {
    ws = null;
    setConn('reconnecting');
    const delay = Math.min(10000, 500 * 2 ** retry++);
    retryTimer = setTimeout(connect, delay);
  };
  ws.onerror = () => { /* onclose follows and schedules the retry */ };
}

// ---------------------------------------------------------------- REST

export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; this.detail = detail; }
}

/** fetch JSON. Non-2xx: toasts the server's detail (403 local-only, 409 wrong state) and throws ApiError. */
export async function api(method, path, body) {
  const init = { method, headers: { Accept: 'application/json' } };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (err) {
    toast('Network error - is the server running?', { error: true });
    throw new ApiError(0, String(err));
  }
  const type = res.headers.get('content-type') || '';
  const data = type.includes('json') ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const raw = data && typeof data === 'object' ? data.detail : data;
    const detail = typeof raw === 'string' && raw ? raw : (raw ? JSON.stringify(raw) : res.status + ' ' + res.statusText);
    toast(res.status === 403 ? 'Only the host screen can do that (' + detail + ')' : detail, { error: true });
    throw new ApiError(res.status, detail);
  }
  return data;
}

// ---------------------------------------------------------------- toast

let toastTimer = null;
export function toast(msg, { error = false } = {}) {
  const t = document.getElementById('toast');
  if (!t) { console.warn('[toast]', msg); return; }
  t.textContent = msg;
  t.classList.toggle('is-error', error);
  t.classList.add('is-on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('is-on'), 1600);
}

// ---------------------------------------------------------------- clipboard

function shadowCopy(html, text) {
  const shadow = document.getElementById('copy-shadow');
  if (!shadow) return false;
  const active = document.activeElement;
  if (html) shadow.innerHTML = html; else shadow.textContent = text;
  const range = document.createRange();
  range.selectNodeContents(shadow);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (err) { console.error('[core] execCommand copy failed', err); }
  sel.removeAllRanges();
  shadow.textContent = '';
  if (active && typeof active.focus === 'function') active.focus({ preventScroll: true });
  return ok;
}

/** Copy rich text + Markdown. ClipboardItem on secure contexts, hidden contenteditable fallback on LAN http. */
export async function copy({ markdown = '', html } = {}, { quiet = false } = {}) {
  const text = markdown;
  const rich = html ?? (markdown ? renderMarkdown(markdown) : '');
  let ok = false;
  if (window.isSecureContext && navigator.clipboard && window.ClipboardItem) {
    try {
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([rich], { type: 'text/html' }),
        'text/plain': new Blob([text], { type: 'text/plain' }),
      })]);
      ok = true;
    } catch (err) {
      console.warn('[core] ClipboardItem write failed, falling back', err);
    }
  }
  if (!ok) ok = shadowCopy(rich, text);
  if (!quiet) toast(ok ? 'Copied' : 'Copy failed - select and copy manually', { error: !ok });
  return ok;
}

// ---------------------------------------------------------------- formatting + DOM

export function fmtTime(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? h + ':' + String(m).padStart(2, '0') + ':' + ss : String(m).padStart(2, '0') + ':' + ss;
}

/** 1..6 cycling by speaker id (matches --spk-N), 0 for unknown/unmapped. */
export function speakerColorIndex(id) {
  const n = Number(id);
  if (id === null || id === undefined || id === '' || !Number.isInteger(n) || n < 0) return 0;
  return (n % 6) + 1;
}

/** Display name for a speaker id: mapped name, else "Speaker N". */
export function speakerName(id, fallback) {
  const name = store.state.speakers[String(id)];
  if (name) return name;
  if (fallback) return fallback;
  return id === null || id === undefined ? 'Unknown' : 'Speaker ' + (Number(id) + 1);
}

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export function escapeHtml(s) { return String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]); }

/** el('button', {class:'btn', onclick: fn, dataset:{t: 3}, hidden: true}, 'Text', child). 'html' sets trusted innerHTML. */
export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class' || k === 'className') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, v);
  }
  const add = (c) => {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) c.forEach(add);
    else node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  };
  children.forEach(add);
  return node;
}

/** Inline SVG icon from the sprite in index.html, e.g. icon('copy'). */
export function icon(name, cls = 'ic') {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(ns, 'use');
  use.setAttribute('href', '#i-' + name);
  svg.append(use);
  return svg;
}

// ---------------------------------------------------------------- audio

let audioSrcBase = null;
let audioBound = false;

function bindAudio(audio) {
  if (audioBound) return;
  audioBound = true;
  const report = () => store.emit('playback', {
    t: audio.currentTime, playing: !audio.paused, meetingId: audioSrcBase?.id ?? null,
  });
  for (const ev of ['play', 'pause', 'timeupdate', 'seeked', 'ended']) audio.addEventListener(ev, report);
}

/** Play the single #audio element from `seconds`. While the meeting is live the WAV is still
 *  growing, so the src is cache-busted with ?t=<now> on every seek. Emits 'playback'. */
export function seekAudio(seconds, meetingId = store.state.meeting?.id) {
  const audio = document.getElementById('audio');
  if (!audio || !meetingId) { toast('No recording available yet'); return null; }
  bindAudio(audio);
  const t = Math.max(0, Number(seconds) || 0);
  const live = isLiveState() && meetingId === store.state.meeting?.id;
  const base = '/api/meetings/' + encodeURIComponent(meetingId) + '/audio';
  const needsSrc = live || !audioSrcBase || audioSrcBase.id !== meetingId;
  const start = () => {
    audio.currentTime = t;
    audio.play().catch((err) => console.warn('[core] audio.play blocked', err));
  };
  if (needsSrc) {
    audioSrcBase = { id: meetingId };
    audio.src = live ? base + '?t=' + Date.now() : base;
    audio.addEventListener('loadedmetadata', start, { once: true });
    audio.load();
  } else {
    start();
  }
  store.emit('playback', { t, playing: true, meetingId });
  return audio;
}

// ---------------------------------------------------------------- hotkeys

const hotkeys = new Map();

function isTyping(target) {
  if (!target || !(target instanceof Element)) return false;
  if (target.isContentEditable) return true;
  return /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
}

function onKeydown(ev) {
  if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.isComposing) return;
  const key = ev.key.length === 1 ? ev.key.toLowerCase() : ev.key;
  const fns = hotkeys.get(key);
  if (!fns || !fns.size) return;
  if (key !== 'Escape' && isTyping(ev.target)) return;
  ev.preventDefault();
  for (const fn of [...fns]) {
    try { fn(ev); } catch (err) { console.error('[core] hotkey ' + key + ' failed', err); }
  }
}

/** hotkey('b', fn) / hotkey('/', fn) / hotkey('Escape', fn). Letters are case-insensitive; ignored while
 *  typing in inputs/textarea/select/contenteditable (Escape still fires). Returns an unsubscribe fn. */
export function hotkey(key, fn) {
  if (!hotkeys.size) document.addEventListener('keydown', onKeydown);
  const k = key.length === 1 ? key.toLowerCase() : key;
  if (!hotkeys.has(k)) hotkeys.set(k, new Set());
  hotkeys.get(k).add(fn);
  return () => hotkeys.get(k)?.delete(fn);
}
