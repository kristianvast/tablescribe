# tablescribe web UI design system

Scope: `web/index.html`, `web/style.css`, `web/app.js`. Every colour, size, space, radius and
duration in those files traces to a token below (`:root` in `style.css` mirrors this table).

## 0. Research Log (greenfield)

- Embedded refs: shortlisted `linear.app` (dark layered panels, hairline borders, one accent), `superhuman` (keyboard-first dark), `elevenlabs` (dark cinematic audio) -> picked Layer A `soft-skill` + Layer B `linear.app` because the brief is an app shell with surface ambition ("impressive, clean", "mission control") and Linear's luminance-stacked dark material is the closest documented system; `layout-skill` stacked for scroll ownership.
- User-supplied direction acts as the visual contract: dark mission control, near-black blue-gray canvas, layered glass panels, one accent + one colour per speaker, transcript >= 20 px, tabular numerals, 150-200 ms motion, reduced-motion respected, system font stack, fully offline.
- ui-ux-db: one `--domain color` lookup for a dark dashboard palette (sanity check of the ramp below).
- Lazyweb: skipped by judgment - the brief already fixes the palette, density and type; recorded here so the skip is explicit.
- Imagen drafts: skipped - no image-generation tool is available in this session.
- Deviations from `soft-skill`: system fonts are required by the brief (offline, no font files), so its banned-font rule does not apply; contrast is raised above its "softer" default because the page is read from across a table.

## 1. Atmosphere & Identity

A mission-control console at night. The canvas is a deep blue-black that reads as absence, not
paint; panels emerge from it as faint lifts of luminance behind hairline borders, and the only
glass is on chrome that floats over scrolling content (top bar, bottom tab bar, floating pills,
toasts). One cool violet accent marks the primary action and focus; every speaker owns a colour
that appears only on their dot, their name and their timeline marks. Numbers are tabular and
monospaced so timers tick without jitter. The signature is the live transcript: large, calm,
grouped by speaker, with a new line arriving as a short fade-up - the one thing a visitor
remembers is watching the room's words land on the wall in real time.

## 2. Color

Dark-only. The product lives on a shared screen in a meeting room; there is no light theme
(recorded as accepted debt in Section 8).

| Role | Token | Value | Usage |
|---|---|---|---|
| Canvas | `--bg-canvas` | `#0a0d14` | Page background (deep near-black blue-gray) |
| Canvas glow A | `--glow-a` | `rgba(110,139,255,0.10)` | Fixed radial glow, top-left, atmosphere only |
| Canvas glow B | `--glow-b` | `rgba(79,209,232,0.06)` | Fixed radial glow, bottom-right, atmosphere only |
| Surface 1 (panel) | `--surface-1` | `rgba(255,255,255,0.03)` | Columns, drawers, large panels |
| Surface 2 (card) | `--surface-2` | `rgba(255,255,255,0.045)` | Cards, list rows, inputs at rest |
| Surface 3 (hover) | `--surface-3` | `rgba(255,255,255,0.07)` | Hover wash on rows/buttons |
| Surface 4 (active) | `--surface-4` | `rgba(255,255,255,0.10)` | Pressed/selected wash |
| Elevated | `--surface-elevated` | `#151a24` | Popovers, dialogs, toasts, floating pills (opaque) |
| Glass chrome | `--glass` | `rgba(12,16,24,0.72)` | Top bar, bottom tab bar, drawer header (with `--blur`) |
| Border subtle | `--border-subtle` | `rgba(255,255,255,0.06)` | Default hairline on panels/cards |
| Border default | `--border` | `rgba(255,255,255,0.10)` | Inputs, buttons, dividers that must read |
| Border strong | `--border-strong` | `rgba(255,255,255,0.16)` | Hovered inputs/buttons |
| Inset highlight | `--highlight` | `rgba(255,255,255,0.06)` | 1 px inset top sheen on elevated surfaces |
| Text primary | `--text-1` | `#f2f4f8` | Transcript, headings (13.9:1 on canvas) |
| Text secondary | `--text-2` | `#c3c9d4` | Body, notes (9.6:1) |
| Text tertiary | `--text-3` | `#8c94a3` | Metadata, labels, placeholders (5.1:1) |
| Text quaternary | `--text-4` | `#5f6775` | Disabled text, timestamps at rest (3.1:1, large/mono only) |
| Accent | `--accent` | `#6e8bff` | Links, focus ring, active tab ink, timestamps-as-links (6.3:1) |
| Accent hover | `--accent-hover` | `#88a0ff` | Hovered links |
| Accent fill | `--accent-fill` | `#4f5fe6` | Primary button background (white text 5.1:1) |
| Accent fill hover | `--accent-fill-hover` | `#5b6cf3` | Primary button hover |
| Accent wash | `--accent-wash` | `rgba(110,139,255,0.14)` | Selected/current item wash, focus halo |
| Live red | `--live` | `#ff5c5c` | REC dot, destructive buttons (text 5.6:1) |
| Live red wash | `--live-wash` | `rgba(255,92,92,0.16)` | REC pill background, danger hover |
| Status supported | `--ok` | `#3ddc97` | Fact-check verdict supported (10.3:1) |
| Status disputed | `--bad` | `#ff6b6b` | Fact-check verdict disputed (6.1:1) |
| Status unclear | `--warn` | `#f5b942` | Fact-check verdict unclear (10.0:1) |
| Status wash | `--ok-wash` / `--bad-wash` / `--warn-wash` | status at 0.14 alpha | Verdict badge backgrounds |
| Speaker 1 | `--spk-1` | `#ff9d5c` orange | Speaker dot, name, timeline mark (9.4:1) |
| Speaker 2 | `--spk-2` | `#4fd1e8` cyan | (11.0:1) |
| Speaker 3 | `--spk-3` | `#f073b4` pink | (7.0:1) |
| Speaker 4 | `--spk-4` | `#a3e635` lime | (13.7:1) |
| Speaker 5 | `--spk-5` | `#c084fc` orchid | (7.8:1) |
| Speaker 6 | `--spk-6` | `#5aa9ff` blue | (8.0:1); speakers 7+ cycle 1-6 |
| Speaker unknown | `--spk-0` | `#9aa3b2` slate | Unmapped speaker before roll call |
| Mark (search hit) | `--mark` | `rgba(245,185,66,0.28)` | `<mark>` background in search snippets, text stays `--text-1` |

Rules: accent appears on at most the primary action, focus, and the active tab per screen.
Speaker colours never mark state or status. Verdict colour is always paired with a glyph and a
word. Selected/current state = `--accent-wash` or `--surface-4` plus a glyph/label, never a
coloured side border.

## 3. Typography

Font stack (system, offline):
- Sans `--font-sans`: `-apple-system, "SF Pro Display", "SF Pro Text", system-ui, "Segoe UI", sans-serif`
- Mono `--font-mono`: `ui-monospace, "SF Mono", Menlo, Consolas, monospace`
- `font-variant-numeric: tabular-nums` on every number that changes (timers, timestamps, counts).

| Level | Token | Size | Weight | Line | Tracking | Usage |
|---|---|---|---|---|---|---|
| Display | `--fs-display` | 40px (28px <= 900) | 600 | 1.1 | -0.02em | Roll call instruction |
| H1 | `--fs-h1` | 28px | 600 | 1.2 | -0.015em | Setup heading, ended meeting title, minutes h1 |
| H2 | `--fs-h2` | 20px | 600 | 1.3 | -0.01em | Minutes h2, dialog title |
| Title | `--fs-title` | 17px | 600 | 1.3 | -0.005em | Top bar meeting title, panel heading in drawer |
| Transcript | `--fs-transcript` | 21px (18px <= 900) | 400 | 1.5 | 0 | Live transcript text (>= 20 px desktop) |
| Body | `--fs-body` | 15px | 400 | 1.5 | 0 | Notes, actions, chat, minutes body |
| Small | `--fs-small` | 13px | 400 | 1.45 | 0 | Metadata, snippets, timestamps |
| Caption | `--fs-caption` | 12px | 500 | 1.4 | 0.01em | Badges, chips, kbd |
| Overline | `--fs-overline` | 11px | 600 | 1.3 | 0.08em | Uppercase section labels |
| Mono readout | `--fs-mono` | 13px / 15px | 500 | 1 | 0 | RTF/latency, elapsed timer (15px) |

Rules: two families only. Body never below 13px; 12px only in badges/chips. Weights 400/500/600.

## 4. Spacing & Layout

Base 4px: `--s-1` 4, `--s-2` 8, `--s-3` 12, `--s-4` 16, `--s-5` 20, `--s-6` 24, `--s-8` 32,
`--s-10` 40, `--s-12` 48, `--s-16` 64.

Shell (desktop > 900px): `scroll-body-shell` - grid rows `auto minmax(0,1fr)`; the body is a
grid of three columns `--col-left` 300px / `minmax(0,1fr)` / `--col-right` 380px, each column
its own scroll owner (`min-height: 0; overflow-y: auto`). The transcript column is the only
region with autoscroll logic. Content measure inside the transcript: `--measure` 72ch max.
Mobile (<= 900px): single column, body = active tab pane (Transcript / Notes / Ask), bottom tab
bar `--tabbar-h` 56px + safe-area inset, top bar collapses to title + REC + timer + menu.
Breakpoints: `--bp-mobile` 900px (layout), 1440 and 1920 are tested desktop widths.

## 5. Components

- **panel** - `section.panel` > `header.panel-head` (overline title, count badge, actions cluster with copy button) + `div.panel-body`. Surface 1, border subtle, radius lg. States: empty (muted line + next action), loading (skeleton rows), error (inline line). Layout: stack.
- **button** - `.btn` base (height 36px, radius md, caption/small type, 120ms hover wash). Variants: `.btn-primary` (accent fill, white text), `.btn-ghost` (surface 2 + border), `.btn-subtle` (transparent, wash on hover), `.btn-danger` (live red text, red wash on hover), `.btn-icon` (36x36, inline SVG 16px), `.btn-sm` (28px). States: hover (surface 3 / fill hover), active (`transform: scale(0.98)` + surface 4), focus-visible (2px accent ring, 2px offset), disabled (opacity .45, no pointer), loading (text swaps to "Working..." and disabled).
- **chip** - `.chip` pill: participant chips (removable x), owner chips (speaker dot + name). Radius full, caption type, surface 2 + border subtle.
- **badge** - `.badge` for counts and verdicts; verdict variant pairs glyph + word on the status wash.
- **state pill** - `.pill-state` in the top bar: Roll call / Live / Stopping / Ended; Live shows the pulsing REC dot.
- **field** - `.field` label above `.input` / `.textarea` / `.select` (surface 2, border default, radius md, 40px height, accent focus ring). Placeholder is a format hint, never the only label.
- **tabs** - `.tabs` segmented strip, active tab = surface 4 wash + text-1 (no accent border); count badge inside tab. Keyboard: arrows move, Enter/Space select.
- **transcript group** - `.tgroup` (speaker header: dot, name in speaker colour, mm:ss) > `.line` rows with hover action cluster (Copy line, Play from here), `.line.is-playing` wash, `.bookmark-mark` inline row with flag glyph. New line: `fade-up` 180ms; reduced motion: fade only.
- **jump pill** - floating `.pill-float` over the transcript ("Jump to live", arrow glyph), elevated surface, appears when autoscroll is paused.
- **agenda item** - `.agenda-item` row: index, label, mono timer; `.is-current` = accent wash + play glyph; controller rows are buttons.
- **roster row** - `.person` row: speaker dot, name, hover reveals rename (pencil) and copy actions.
- **action item** - `.action` row: check glyph box (visual), task text, owner chip, due small text.
- **factcheck card** - `.fact` card: verdict badge, claim quote, speaker (speaker colour) + mm:ss, summary, source links (new tab, external glyph).
- **chat** - `.chat` log of `.msg.q` (right-aligned, surface 2) and `.msg.a` (full width, markdown, `[mm:ss]` rendered as `.ts-link` buttons); composer = input + scope segmented control + send button.
- **markdown view** - `.md` renders the safe renderer output: h1-h3, p, strong/em, ul/ol, task list, links, code/pre, blockquote, table, hr.
- **drawer** - `.drawer` right-side sheet (glass header, panel body, 480px desktop / full width mobile), scrim click + Esc close, focus trapped to the drawer's close button on open.
- **dialog** - `.dialog` centered confirm (stop meeting): title, one-sentence message, Cancel (default focus) + destructive confirm.
- **toast** - `.toast` bottom-center elevated pill, 1.6s, fade-up in / fade out.
- **reconnect banner** - `.conn` strip under the top bar: "Reconnecting..." with a small spinner glyph; hidden when connected.
- **bottom tab bar** - `.tabbar` (mobile) glass, three buttons with icon above one-word label, 56px tall, active = text-1 + icon fill.
- **kbd** - `.kbd` inline key cap for hotkey hints.

## 6. Motion & Interaction

| Type | Token | Duration | Easing | Usage |
|---|---|---|---|---|
| Micro | `--t-micro` | 120ms | `ease-out` | Hover/active wash, icon fill |
| Standard | `--t-std` | 180ms | `cubic-bezier(0.32,0.72,0,1)` | New line fade-up, tab switch, pill show/hide, toast |
| Panel | `--t-panel` | 240ms | `cubic-bezier(0.32,0.72,0,1)` | Drawer/dialog enter, exit mirrors |
| Pulse | `--t-pulse` | 1600ms | `ease-in-out` loop | REC dot while live only |

Rules: only `transform`, `opacity`, `filter` animate. New transcript lines translateY(6px) -> 0
with opacity. Under `prefers-reduced-motion: reduce`: slides become fades, the REC dot stops
pulsing (solid dot), drawers cross-fade. Press feedback same frame (`:active` scale .98).
Spinner only for the reconnect state. No hover that changes nothing: hover reveals line actions
or lifts a wash on interactive rows only.

## 7. Depth & Surface

Strategy: **tonal-shift + hairline borders** (Linear's luminance stacking). No drop shadows on
in-flow surfaces; elevated overlays (dialog, drawer, toast, floating pill, menus) use
`--shadow-elevated: 0 12px 32px rgba(0,0,0,0.5), 0 1px 0 var(--highlight) inset` and an opaque
elevated fill. Glass (`--glass` + `backdrop-filter: blur(16px) saturate(140%)`) only on fixed
chrome. Atmosphere: fixed, pointer-events-none layer with two radial glows and a 3%-opacity SVG
noise (inline data URI).

Radius: `--r-sm` 6px (inputs, chips, kbd), `--r-md` 10px (buttons, cards), `--r-lg` 14px
(panels, columns), `--r-xl` 20px (dialog, drawer corners), `--r-full` 999px (pills, dots).
Nested corners are concentric: card inside panel = panel radius minus panel padding.

## 8. Accessibility Constraints & Accepted Debt

Constraints: WCAG 2.2 AA - every text token above is >= 4.5:1 on the canvas except `--text-4`
which is restricted to large/mono timestamps; focus-visible ring on every control; every icon
button has `aria-label`; live regions: transcript column `aria-live="polite"` off (too chatty),
toasts `role="status"`; keyboard shortcuts never fire inside inputs; drawer/dialog close on Esc
and return focus; `prefers-reduced-motion` honoured (Section 6); touch targets >= 44px on mobile.

| Item | Location | Why accepted | Owner / Exit |
|---|---|---|---|
| Dark theme only | whole app | Shared-screen room display; user chose "dark mission control"; light theme would need a second palette | ui owner; add if the product ever runs on personal laptops |
| Viewer sees controller's Ask questions | Ask panel | The WS broadcasts `ask_start`/`ask_delta` to every client; on a shared screen this is desired | none |
| Audio seek on a growing WAV may lag at the live edge | transcript click-to-play while live | The file is cache-busted on each seek; the header length is stale until the next flush | audio owner (Recorder flush cadence) |
