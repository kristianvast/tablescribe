import * as coreApi from './js/core.js';
import { renderMarkdown, linkifyTimestamps } from './js/markdown.js';

const core = Object.freeze({ ...coreApi, renderMarkdown, linkifyTimestamps });
const { store } = core;
const body = document.body;

const SCREEN_FOR_STATE = { idle: 'setup', rollcall: 'rollcall', live: 'live', stopping: 'live', ended: 'ended' };
const SCREENS = ['setup', 'rollcall', 'live', 'ended'];
let gotSnapshot = false;

function currentScreen() {
  const s = store.state;
  return s.view || SCREEN_FOR_STATE[s.state] || 'setup';
}

function applyChrome() {
  const s = store.state;
  body.dataset.state = gotSnapshot ? s.state : 'connecting';
  body.dataset.control = String(!!s.canControl);
  const screen = gotSnapshot ? currentScreen() : null;
  body.dataset.screen = screen || 'none';
  for (const name of SCREENS) {
    const node = document.getElementById(`screen-${name}`);
    if (node) node.hidden = name !== screen;
  }
  const tabbar = document.getElementById('tabbar');
  if (tabbar) tabbar.hidden = screen !== 'live';
  const conn = document.getElementById('conn');
  if (conn) conn.hidden = s.conn !== 'reconnecting';
}

function setMobileTab(tab) {
  body.dataset.mtab = tab;
  for (const btn of document.querySelectorAll('.tabbar-btn')) {
    const on = btn.dataset.mtab === tab;
    btn.classList.toggle('is-on', on);
    btn.setAttribute('aria-pressed', String(on));
  }
  store.emit('mtab', tab);
}

function wireGlobal() {
  document.getElementById('tabbar')?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.tabbar-btn');
    if (btn) setMobileTab(btn.dataset.mtab);
  });
  document.addEventListener('click', (ev) => {
    const ts = ev.target.closest('button.ts[data-t]');
    if (!ts) return;
    ev.preventDefault();
    const scope = ts.closest('[data-meeting-id]');
    core.seekAudio(Number(ts.dataset.t), scope ? scope.dataset.meetingId : undefined);
  });
}

async function mountModules() {
  for (const name of ['live', 'side', 'after']) {
    try {
      const mod = await import(`./js/${name}.js`);
      if (typeof mod.mount !== 'function') throw new Error(`./js/${name}.js does not export mount(core)`);
      await mod.mount(core);
    } catch (err) {
      console.error(`[app] module ${name} failed to mount`, err);
    }
  }
}

store.on('snapshot', () => { gotSnapshot = true; });
store.on('change', applyChrome);

wireGlobal();
applyChrome();
await mountModules();
core.connect();
