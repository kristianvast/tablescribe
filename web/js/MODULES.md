# Front-end module contract

`web/app.js` loads `web/js/core.js` + `web/js/markdown.js`, then dynamically imports
`./js/live.js`, `./js/side.js`, `./js/after.js` in that order and calls each module's
`mount(core)` inside try/catch (a missing or throwing module logs `console.error` and the
others still mount). After all mounts it calls `core.connect()`, so the first `snapshot`
reaches every subscriber.

Rules for every module:
- `export function mount(core)` (may be async). No other exports are required.
- Import nothing from sibling modules. Talk to the others only through `core.store`.
- Plain ES modules, no frameworks, no network URLs. Use `core.el` / `core.icon` / `core.escapeHtml`;
  anything put into `innerHTML` must come from `core.renderMarkdown` or be escaped.
- Touch only the DOM regions you own (below). Element ids already exist in `index.html`; reuse them.
- Register hotkeys with `core.hotkey` (it ignores typing in inputs and supports several handlers per key).
- CSS: `web/css/<module>.css` is yours (already linked in index.html). `web/style.css` holds the
  shared design tokens and components (see `web/DESIGN.md`) and is read-only for modules.

## The `core` object

| Export | Notes |
|---|---|
| `store.state` | `{conn, state, meeting, transcript[], notes, factchecks[], speakers{}, bookmarks[], agenda{items,current,started_at}, userNotes, asks{ask_id:{question,scope,text,done}}, minutes{markdown,template}\|null, canControl, rtf, latency, elapsed, view}` |
| `store.on(type, fn)` | `fn(payload, state)`; returns unsubscribe. Types: every WS event (`snapshot state segment speakers notes factcheck bookmark user_notes agenda ask_start ask_delta ask_done minutes error`), core events `conn` (string), `control` (bool), `playback` (`{t, playing, meetingId}`), `mtab` (mobile tab), `local` (patch from `store.set`), and `change` (`fn({type, payload}, state)` after any of the above). The store is already updated when handlers run. |
| `store.emit(type, payload)` / `store.set(patch)` | Custom cross-module events / local state patch (emits `local` + `change`). |
| `dispatch(msg)` | Feed a server-shaped message through the reducer (used for testing). |
| `connect()` | WS to `ws(s)://host/ws`, backoff 0.5 s -> 10 s; `conn` = `connecting`/`open`/`reconnecting`. A snapshot replaces state (asks and `view` survive). |
| `api(method, path, body)` | JSON fetch. Non-2xx: toasts the `detail` (403 local-only, 409 wrong state) and throws `ApiError{status, detail}`. Catch it if you need to reset UI. |
| `copy({markdown, html}, {quiet})` | Rich + plain copy (ClipboardItem on secure contexts, `#copy-shadow` execCommand fallback on LAN http). `html` defaults to `renderMarkdown(markdown)`. Toasts "Copied". Returns `Promise<boolean>`. |
| `toast(msg, {error})` | `#toast`, 1.6 s. |
| `fmtTime(sec)` | `mm:ss` or `h:mm:ss`. |
| `speakerColorIndex(id)` | 1..6 cycling (use `var(--spk-N)`), 0 unknown. `speakerName(id, fallback)` -> mapped name or "Speaker N". |
| `el(tag, attrs, ...children)` | `attrs`: `class`, `dataset`, `style` (object or string), `on<event>` fns, booleans, `html` (trusted HTML only). `icon(name)` -> `<svg><use href="#i-name">`. |
| `escapeHtml(s)` | |
| `seekAudio(seconds, meetingId?)` | Drives the single `#audio` element (`/api/meetings/<id>/audio`, `?t=<now>` cache-bust while live). Emits `playback`. Default meeting = current. |
| `hotkey(key, fn)` | `'b'`, `'/'`, `'Escape'`... Escape fires even inside inputs. |
| `isController()` / `isLiveState(state?)` | |
| `renderMarkdown(md)` / `linkifyTimestamps(html)` | Safe Markdown; `[mm:ss]` -> `<button class="ts ts-link" data-t="sec">`. |

## Handled by app.js (do not duplicate)

- `body[data-state]` (`connecting` until the first snapshot, then the meeting state),
  `body[data-control]` (`true`/`false`; CSS hides every `[data-control]` element for viewers),
  `body[data-screen]`, `body[data-mtab]`.
- Screen visibility: `#screen-setup|rollcall|live|ended` shown from `state`
  (idle->setup, rollcall->rollcall, live/stopping->live, ended->ended) unless `store.state.view`
  is set. after.js sets `store.set({view: 'ended'})` to show a past meeting and `{view: null}` to return.
- `#conn` reconnect banner, `#tabbar` visibility and mobile tab switching (`data-mtab`).
- Any click on `button.ts[data-t]` anywhere calls `seekAudio(t, meetingId)`, where `meetingId`
  comes from the nearest ancestor `[data-meeting-id]` (set it on containers showing a past meeting).

## Ownership

### live.js - `web/css/live.css`
Regions: `#screen-setup` (form `#setup-form`, chips `#chips-participants`/`#chips-hotwords`,
`#f-template`, `#f-device`, `#setup-viewer`, `#setup-history`/`#viewer-history` -> emit
`history:open`), `#screen-rollcall` (`#roll-list`, `#roll-empty`, `#roll-feed`, `#roll-done`),
top bar `#topbar` except `#tb-copyall` (`#tb-title`, `#tb-state`, `#tb-viewing`, `#tb-rec`,
`#tb-elapsed`, `#tb-readout`, `#tb-bookmark` + hotkey B, `#tb-history` -> emit `history:open`,
`#tb-stop`), `#col-left` (`#panel-agenda`, `#agenda-list`, `#agenda-count`, `#agenda-empty`,
`#panel-people`, `#roster`, `#people-count`, `#roster-empty`), `#col-center` (`#transcript`,
`#transcript-count`, `#transcript-empty`, `#jump-live` + hotkey J, inline bookmarks, line
actions Copy line / Play from here), `#dialog-stop`, `#dialog-rename`. Copy buttons
`[data-copy=agenda|people|transcript]` inside `#screen-live`.
Reads: `state, meeting, transcript, speakers, bookmarks, agenda, elapsed, rtf, latency, canControl`.
Events: `snapshot state segment speakers bookmark agenda playback control`.
Emits: `history:open`, `rename:open` (`{speaker}`; also handled by itself).

### side.js - `web/css/side.css`
Regions: `#col-right` (`#rtabs` tab switching via `body[data-rtab]`, `#pane-notes`
(`#actions-list`, `#actions-count`, `#actions-empty`, `#decisions-*`, `#people-notes*` with
per-person "Copy <name>'s actions"), `#pane-facts` (`#facts-list`, `#facts-empty`, `#facts-count`),
`#pane-mine` (`#my-notes` debounced POST /api/user-notes, `#my-notes-view` for viewers,
`#mine-status`, hotkey N), `#pane-ask` (`#chat-live`, `#ask-form`, `#ask-input`, scope seg, hotkey /)),
`#tb-copyall` (whole live state as Markdown). Copy buttons
`[data-copy=actions|decisions|people-notes|facts|mine|ask]`.
Reads: `notes, factchecks, userNotes, asks, speakers, canControl, meeting`.
Events: `snapshot notes factcheck user_notes ask_start ask_delta ask_done control`.
Ask chat shows asks whose `scope === 'meeting'`.

### after.js - `web/css/after.css`
Regions: `#screen-ended` (`#ended-back`, `#ended-overline`, `#ended-title`, `#ended-meta`,
`#ended-history`, `#ended-new`, `#minutes-template`, `#minutes-template-badge`,
`#minutes-copy-rich`, `#minutes-copy-md`, `#minutes-download`, `#minutes-status`, `#minutes`,
`#audio-pos`, `#transcript-ended`, `#ended-transcript-count`, `#transcript-ended-empty`,
`[data-copy=transcript]` inside it), the history drawer `#drawer` + `#drawer-scrim`
(`#drawer-close`, Escape, `#search-input`, `#search-panel`, `#search-hits`, `#search-count`,
`#search-empty`, `#meetings-panel`, `#meetings-list`, `#meetings-count`, `#meetings-empty`,
`#chat-all`, `#chat-all-empty`, `#ask-all-form`, `#ask-all-input`, `[data-copy=ask-all]`).
Listens for `history:open` and opens the drawer. Opening a past meeting fetches
`GET /api/meetings/<id>`, renders it in `#screen-ended` with `data-meeting-id` set, sets
`view: 'ended'`, and seeks the hit's timestamp.
Reads: `state, meeting, transcript, speakers, minutes, asks (scope 'all'), canControl, view`.
Events: `snapshot state minutes ask_start ask_delta ask_done playback history:open`.
The `#audio` element lives in `#screen-ended`; every module plays audio only through `seekAudio`.
