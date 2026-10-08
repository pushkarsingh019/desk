/* The desk.
 *
 * This layer is deliberately thin: it renders desk state and turns gestures
 * into layout transitions on the server, which owns the layout model. It holds
 * no rules of its own beyond the ones the eye needs — where the pointer is,
 * what it is over, and how the desk is transformed. Desk geometry — the
 * overview bounds, the fan step, the default and minimum sheet size — comes
 * from the server's `geometry` block rather than being worked out twice.
 *
 * The one invariant worth stating here: an update must never move anything.
 * `sheet.version` events take the swapInPlace path, which touches an element's
 * src and nothing else. They never re-render and never touch the layout.
 */

'use strict';

const HOME = { x: 216, y: 24, scale: 1 };

/* The desk is a real, bounded slab. Sheets cannot be pushed off it and the
   view cannot wander away from it: the edge is the landmark that a featureless
   infinite plane never gave us, and a desk you can lose is not a desk. */
const DESK = { w: 4800, h: 3200 };
/** How much floor may show past an edge before panning stops. */
const FLOOR_MARGIN = 160;
/** Paper does not hang off a real desk. */
const EDGE_INSET = 12;
/** The inbox tray covers this much of the window's left edge. */
const INBOX_W = 176;
/** Above this the grain is only magnified into mush, so it fades out. */
const GRAIN_FADE_SCALE = 2;
/** How long a dropped stream runs cold before the cup starts to alarm. */
const STALE_MS = 20000;

const MIN_SCALE = 0.1;
const MAX_SCALE = 8;
const RING_MS = 600;
const CLICK_SLOP = 4;
const DOUBLE_CLICK_MS = 450;

/** How long to wait before re-opening a stream the browser gave up on. */
const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 5000;

const IMAGE_KINDS = new Set(['svg', 'png']);
const FRAME_KINDS = new Set(['html', 'md', 'pdf']);

// --- elements -------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const viewportEl = $('viewport');
const surface = $('surface');
const desktop = $('desktop');
const grain = $('grain');
const mug = $('mug');
const skinBtn = $('btn-skin');
const inboxItems = $('inbox-items');
const inboxEmpty = $('inbox-empty');
const inboxCount = $('inbox-count');
const trashCount = $('trash-count');
const trashDrop = $('trash-drop');
const trashPanel = $('trash-panel');
const trashItems = $('trash-items');
const trashEmpty = $('trash-empty');
const zoomReadout = $('zoom-readout');
const connection = $('connection');

// --- state ----------------------------------------------------------------

// `geometry` is null only before the first /api/state lands. Nothing renders
// until then, so nothing reads it: the page is served by the same server that
// computes it, with no build step and no deploy step between them, so a desk
// that answers without geometry does not exist.
let state = { sheets: [], trash: [], layout: emptyLayout(), geometry: null };
let sheetsById = new Map();
const nodes = new Map(); // render key -> element
const held = new Set(); // sheet/pile keys the pointer is currently moving
let view = { ...HOME };

/** The sheet whose embedded page currently has the pointer. Never persisted:
 *  activation is a property of this browser tab, not of the desk. */
let activeId = null;

function emptyLayout() {
  return { sheets: {}, piles: {}, next_z: 1, next_pile: 1, viewport: { ...HOME } };
}

function adoptGeometry(geometry) {
  if (geometry) state.geometry = geometry;
}

function indexSheets() {
  sheetsById = new Map(state.sheets.map((s) => [s.id, s]));
}

// --- api ------------------------------------------------------------------

async function apiGet(path) {
  const resp = await fetch(path, { cache: 'no-store' });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

async function apiPost(path, body) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

/** Send one layout transition and adopt the state the server hands back. */
async function layoutOp(op, params) {
  try {
    const { layout, geometry } = await apiPost('/api/layout', { op, ...params });
    state.layout = layout;
    adoptGeometry(geometry);
    render();
  } catch (err) {
    console.error('layout', op, err);
    await refresh();
  }
}

async function refresh() {
  const fresh = await apiGet('/api/state');
  // A different desk than the one on screen: take its own viewport, not the
  // pan the user made on the last one.
  if (state.desk && fresh.desk !== state.desk) viewTouched = false;
  state = fresh;
  indexSheets();
  renderDesks();
  const stored = state.layout.viewport;
  if (stored && !viewTouched) view = clampView({ x: stored.x, y: stored.y, scale: stored.scale });
  applyView();
  render();
  if (fullscreenId !== null && sheetsById.has(fullscreenId)) renderPins(sheetsById.get(fullscreenId));
}

// --- which desk -----------------------------------------------------------

/* Several desks, one of them out. The switcher lists them; the current one is
   the one every /desk lands on, so the page title says which it is. */
const deskSwitch = $('desk-switch');

function renderDesks() {
  const names = state.desks || [state.desk || 'main'];
  deskSwitch.textContent = '';
  for (const name of names) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    deskSwitch.appendChild(option);
  }
  deskSwitch.value = state.desk;
  document.title = state.desk && state.desk !== 'main' ? 'Desk \u2014 ' + state.desk : 'Desk';
  $('btn-remove-desk').disabled = state.sheets.length > 0 || names.length < 2;
}

async function deskOp(op, name) {
  try {
    await apiPost('/api/desks', { op, name });
  } catch (err) {
    console.error('desks', op, err);
    alert(err.message || String(err));
  }
  await refresh();
}

deskSwitch.addEventListener('change', () => deskOp('switch', deskSwitch.value));

$('btn-new-desk').addEventListener('click', () => {
  const name = prompt('Name the new desk');
  if (name && name.trim()) deskOp('create', name.trim());
});

$('btn-remove-desk').addEventListener('click', () => {
  if (state.sheets.length) return;
  if (confirm('Take the desk \u201c' + state.desk + '\u201d away? Its trash goes with it.')) {
    deskOp('remove', state.desk);
  }
});

// --- the slab -------------------------------------------------------------

desktop.style.width = DESK.w + 'px';
desktop.style.height = DESK.h + 'px';

/* Fibre, drawn once into a data URL. Each band is a stripe running along the
   grain whose phase is warped by a slow function of x; integer frequencies in
   both axes mean the plate tiles seamlessly. A photographed walnut texture
   would look better and would also be the first binary asset in a repo whose
   whole premise is static, buildless serving. This costs about 10ms.

   The plate carries alpha only and is used as a MASK, not as an image, so one
   plate serves oak and walnut alike: the skin supplies the colour. */
function grainPlate(size) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(size, size);
  const px = image.data;
  const bands = [
    { along: 9, across: 1, warp: 2.2, a: 0.42 },
    { along: 19, across: 2, warp: 1.6, a: 0.28 },
    { along: 43, across: 1, warp: 1.1, a: 0.18 },
    { along: 83, across: 3, warp: 0.7, a: 0.10 },
  ];
  const TAU = Math.PI * 2;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let v = 0;
      for (const b of bands) {
        v += b.a * Math.sin(TAU * b.along * y / size + b.warp * Math.sin(TAU * b.across * x / size));
      }
      let t = Math.min(1, Math.max(0, v * 0.5 + 0.5));
      t = Math.pow(t, 1.6) + (Math.random() - 0.5) * 0.06;
      const i = (y * size + x) * 4;
      px[i] = 0;
      px[i + 1] = 0;
      px[i + 2] = 0;
      px[i + 3] = Math.min(255, Math.max(0, t * 70));
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas.toDataURL('image/png');
}

document.documentElement.style.setProperty('--grain-plate', 'url(' + grainPlate(512) + ')');

// --- which desk you are sitting at ---------------------------------------

/* Two materials over one structure. A skin sets colours and which props are
   on; it can never move a sheet or change a gesture, because `desk.css` owns
   the geometry and holds no colour of its own. */
const SKINS = ['day', 'night'];

function applySkin(name) {
  const skin = SKINS.includes(name) ? name : 'day';
  document.documentElement.dataset.skin = skin;
  skinBtn.textContent = skin;
  try {
    localStorage.setItem('desk.skin', skin);
  } catch (err) {
    // A private window refuses storage. The skin still applies for this tab.
  }
}

function cycleSkin() {
  applySkin(SKINS[(SKINS.indexOf(document.documentElement.dataset.skin) + 1) % SKINS.length]);
}

// The head script already picked one before first paint; adopt its choice so
// the button's label and the document agree.
applySkin(document.documentElement.dataset.skin);
skinBtn.addEventListener('click', cycleSkin);

/** The region the view may explore: the slab, plus anything a previous,
 *  unbounded desk left outside it. Legacy sheets stay reachable, and — this is
 *  the point — are never moved by anything except the user's own hand. */
function clampRegion() {
  const box = state.geometry && state.geometry.bounds;
  let left = 0;
  let top = 0;
  let right = DESK.w;
  let bottom = DESK.h;
  if (box && box.w > 0 && box.h > 0) {
    left = Math.min(left, box.x);
    top = Math.min(top, box.y);
    right = Math.max(right, box.x + box.w);
    bottom = Math.max(bottom, box.y + box.h);
  }
  return { x: left, y: top, w: right - left, h: bottom - top };
}

/** Where one edge of the desk is allowed to sit, in screen pixels. */
function clampAxis(edge, span, lo, hi) {
  const min = hi - FLOOR_MARGIN - span;
  const max = lo + FLOOR_MARGIN;
  if (min > max) return lo + (hi - lo - span) / 2; // desk smaller than the window
  return Math.min(max, Math.max(min, edge));
}

/** Keep the desk on screen. No rubber-band: a bounce implies the desk moved on
 *  its own, and nothing here moves unless the user moves it. Panning simply
 *  stops, and the band of floor you are looking at says which edge you hit. */
function clampView(next) {
  const r = clampRegion();
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next.scale));
  const left = clampAxis(next.x + r.x * scale, r.w * scale, INBOX_W, window.innerWidth);
  const top = clampAxis(next.y + r.y * scale, r.h * scale, 0, window.innerHeight);
  return { x: left - r.x * scale, y: top - r.y * scale, scale };
}

/** Paper does not hang off a real desk. A sheet left outside the slab by a
 *  previous, unbounded desk is pulled in only when the user drags it. */
function clampSheet(x, y, w, h) {
  const maxX = Math.max(EDGE_INSET, DESK.w - EDGE_INSET - w);
  const maxY = Math.max(EDGE_INSET, DESK.h - EDGE_INSET - h);
  return {
    x: Math.min(maxX, Math.max(EDGE_INSET, x)),
    y: Math.min(maxY, Math.max(EDGE_INSET, y)),
  };
}

/** Light the edge a dragged sheet has come up against. */
function markEdge(at, rawX, rawY) {
  desktop.classList.toggle('edge-w', at.x > rawX);
  desktop.classList.toggle('edge-e', at.x < rawX);
  desktop.classList.toggle('edge-n', at.y > rawY);
  desktop.classList.toggle('edge-s', at.y < rawY);
}

function clearEdges() {
  desktop.classList.remove('edge-w', 'edge-e', 'edge-n', 'edge-s');
}

// --- the desk transform ---------------------------------------------------

let viewTouched = false;
let viewSaveTimer = null;

function applyView() {
  surface.style.transform =
    'translate(' + view.x + 'px, ' + view.y + 'px) scale(' + view.scale + ')';
  zoomReadout.textContent = Math.round(view.scale * 100) + '%';
  // Leaning in past 2x, the wood is only being magnified into mush, and the
  // figure is what you came for. Cross-fading it out caps the cost of the
  // grain plate at exactly the zoom levels where it would be paid.
  document.body.classList.toggle('zoomed-in', view.scale > GRAIN_FADE_SCALE);
}

function setView(next, { persist = true } = {}) {
  view = clampView(next);
  viewTouched = true;
  applyView();
  if (persist) {
    clearTimeout(viewSaveTimer);
    viewSaveTimer = setTimeout(() => {
      apiPost('/api/layout', { op: 'viewport', ...view }).catch(() => {});
    }, 400);
  }
}

function toDesk(clientX, clientY) {
  return { x: (clientX - view.x) / view.scale, y: (clientY - view.y) / view.scale };
}

function zoomAt(clientX, clientY, factor) {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale * factor));
  const k = scale / view.scale;
  setView({
    x: clientX - (clientX - view.x) * k,
    y: clientY - (clientY - view.y) * k,
    scale,
  });
}

function goHome() {
  setView({ ...HOME });
}

/** Fit everything on the desk into the window — the zoomed-out overview. */
function goOverview() {
  const box = state.geometry.bounds || { x: 0, y: 0, w: DESK.w, h: DESK.h };
  if (box.w <= 0 || box.h <= 0) return goHome();
  const pad = 60;
  const left = 176; // the inbox strip covers this much of the window
  const availW = window.innerWidth - left - pad * 2;
  const availH = window.innerHeight - pad * 2 - 60;
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.min(availW / box.w, availH / box.h, 1)));
  setView({
    x: left + pad + (availW - box.w * scale) / 2 - box.x * scale,
    y: pad + (availH - box.h * scale) / 2 - box.y * scale,
    scale,
  });
}

function fanOffset(i, w, h) {
  const step = state.geometry.fan_step;
  return { x: i * w * step.x, y: i * h * step.y };
}

/** A pile fans by its largest member, so the step is even down the stack. */
function pileSize(pile) {
  const members = pile.members.map((m) => state.layout.sheets[m]).filter(Boolean);
  const floor = state.geometry.default_size;
  return {
    w: Math.max(...members.map((s) => s.w), floor.w),
    h: Math.max(...members.map((s) => s.h), floor.h),
  };
}

// --- rendering ------------------------------------------------------------

function contentNode(sheet) {
  if (IMAGE_KINDS.has(sheet.kind)) {
    const img = document.createElement('img');
    img.draggable = false;
    img.alt = sheet.name;
    img.addEventListener('error', () => markBroken(img.parentElement, sheet));
    return img;
  }
  const frame = document.createElement('iframe');
  frame.setAttribute('title', sheet.name);
  if (sheet.kind === 'pdf') {
    // A PDF goes to the browser's own viewer, which refuses to run in a
    // sandboxed frame at all — measured on Chrome 151: `allow-same-origin`,
    // `allow-scripts allow-same-origin`, `allow-same-origin allow-downloads`
    // and a bare `sandbox` all render a broken-document icon, and only the
    // absence of the attribute shows the document. The fence does not depend
    // on it here: the viewer is not a page that runs the file's own script,
    // and the store serves the bytes as `application/pdf`, so there is nothing
    // in a PDF sheet for a sandbox to hold back. Every other kind is fenced.
  } else {
    // No allow-same-origin: the embedded page runs in an opaque origin and so
    // cannot reach the desk's DOM, storage, or API. This is the escape hatch's
    // fence.
    frame.setAttribute('sandbox', 'allow-scripts allow-popups allow-forms');
  }
  return frame;
}

function markBroken(body, sheet) {
  if (!body || body.querySelector('.sheet-broken')) return;
  const note = document.createElement('div');
  note.className = 'sheet-broken';
  note.textContent = sheet.name + ' could not be rendered';
  body.appendChild(note);
}

/** Fill (or refresh) a sheet body. Only ever touches src when it changed. */
function fillBody(body, sheet) {
  body.dataset.sheetContent = sheet.id;
  const wantsFrame = FRAME_KINDS.has(sheet.kind);
  let node = body.querySelector('img, iframe');
  const wrongType = node && (node.tagName === 'IFRAME') !== wantsFrame;
  if (!node || wrongType || body.dataset.kind !== sheet.kind) {
    body.textContent = '';
    node = contentNode(sheet);
    body.appendChild(node);
    const shield = document.createElement('div');
    shield.className = 'sheet-shield';
    body.appendChild(shield);
    body.dataset.kind = sheet.kind;
    body.dataset.url = '';
  }
  if (body.dataset.url !== sheet.content_url) {
    const broken = body.querySelector('.sheet-broken');
    if (broken) broken.remove();
    node.src = sheet.content_url;
    body.dataset.url = sheet.content_url;
  }
}

function buildSheet(sheet) {
  const el = document.createElement('div');
  el.className = 'sheet';
  el.dataset.sheetId = sheet.id;

  const chrome = document.createElement('div');
  chrome.className = 'sheet-chrome';
  const name = document.createElement('span');
  name.className = 'sheet-name';
  const origin = document.createElement('span');
  origin.className = 'sheet-origin';
  const version = document.createElement('span');
  version.className = 'sheet-version';
  // The open-comment count. On the margin, never over the figure: the
  // fullscreen pins are the one accepted exception to that rule.
  const comments = document.createElement('span');
  comments.className = 'sheet-comments';
  comments.hidden = true;
  const grow = document.createElement('button');
  grow.className = 'sheet-grow';
  grow.title = 'Enlarge (or double-click)';
  grow.textContent = '\u2197';
  grow.addEventListener('click', (e) => {
    e.stopPropagation();
    const host = grow.closest('.sheet, .pile');
    if (host && host.dataset.sheetId) openFullscreen(host.dataset.sheetId);
  });

  const bin = document.createElement('button');
  bin.className = 'sheet-trash';
  bin.title = 'Throw away';
  bin.textContent = '×';
  bin.addEventListener('click', (e) => {
    e.stopPropagation();
    // Read the sheet off the element, never off the closure: a pile element is
    // reused across changes of top member, so a captured id goes stale.
    const host = bin.closest('.sheet, .pile');
    if (host && host.dataset.sheetId) trashSheet(host.dataset.sheetId);
  });
  chrome.append(name, origin, version, comments, grow, bin);

  const body = document.createElement('div');
  body.className = 'sheet-body';

  const handle = document.createElement('div');
  handle.className = 'resize-handle';
  handle.dataset.role = 'resize';

  el.append(chrome, body, handle);
  return el;
}

/* Where a sheet came from, spelled the way scp would: a sheet sent from
   another machine is `origin:path`, a sheet published here is its path. */
function whereFrom(sheet) {
  return sheet.origin ? sheet.origin + ':' + sheet.source_path : sheet.source_path;
}

function updateSheetEl(el, sheet, placement, { z, x, y }) {
  el.querySelector('.sheet-name').textContent = sheet.name;
  el.querySelector('.sheet-name').title = whereFrom(sheet);
  const origin = el.querySelector('.sheet-origin');
  origin.textContent = sheet.origin ? 'from ' + sheet.origin : '';
  origin.hidden = !sheet.origin;
  el.querySelector('.sheet-version').textContent = 'v' + sheet.version;
  setCommentBadge(el, sheet);
  fillBody(el.querySelector('.sheet-body'), sheet);
  el.style.zIndex = z;
  el.classList.toggle('active', activeId === sheet.id && el.classList.contains('sheet'));
  // A sheet the pointer is holding owns its own geometry until the gesture
  // ends. Writing it here would snap a drag or a resize back mid-gesture.
  if (!held.has(el.dataset.key)) {
    el.style.width = placement.w + 'px';
    el.style.height = placement.h + 'px';
    el.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
  }
}

function render() {
  const wanted = new Set();
  const layout = state.layout;
  if (activeId !== null && !sheetsById.has(activeId)) activeId = null;

  for (const [id, placement] of Object.entries(layout.sheets)) {
    if (placement.inbox || placement.pile) continue;
    const sheet = sheetsById.get(id);
    if (!sheet) continue;
    const key = 'sheet:' + id;
    wanted.add(key);
    let el = nodes.get(key);
    if (!el) {
      el = buildSheet(sheet);
      el.dataset.key = key;
      nodes.set(key, el);
      surface.appendChild(el);
    }
    updateSheetEl(el, sheet, placement, { z: placement.z, x: placement.x, y: placement.y });
  }

  for (const [pileId, pile] of Object.entries(layout.piles)) {
    const members = pile.members.filter((m) => sheetsById.has(m));
    if (!members.length) continue;
    if (pile.open) {
      const frameKey = 'frame:' + pileId;
      wanted.add(frameKey);
      let frame = nodes.get(frameKey);
      if (!frame) {
        frame = document.createElement('div');
        frame.className = 'pile-open-frame';
        frame.dataset.pileId = pileId;
        // A fanned pile has no `.pile` element left to click a second time, so
        // it carries its own way back — the counterpart of the count badge.
        const collapse = document.createElement('button');
        collapse.className = 'pile-collapse';
        collapse.title = 'Collapse the pile';
        collapse.textContent = 'collapse';
        collapse.addEventListener('click', (e) => {
          e.stopPropagation();
          layoutOp('toggle_pile', { pile_id: frame.dataset.pileId });
        });
        frame.appendChild(collapse);
        nodes.set(frameKey, frame);
        surface.appendChild(frame);
      }
      const { w, h } = pileSize(pile);
      const last = fanOffset(members.length - 1, w, h);
      frame.style.left = pile.x - 12 + 'px';
      frame.style.top = pile.y - 12 + 'px';
      frame.style.width = w + last.x + 24 + 'px';
      frame.style.height = h + last.y + 24 + 'px';
      frame.style.zIndex = pile.z;

      members.forEach((id, i) => {
        const key = 'sheet:' + id;
        wanted.add(key);
        const sheet = sheetsById.get(id);
        let el = nodes.get(key);
        if (!el) {
          el = buildSheet(sheet);
          el.dataset.key = key;
          nodes.set(key, el);
          surface.appendChild(el);
        }
        el.dataset.pileId = pileId;
        const off = fanOffset(i, w, h);
        updateSheetEl(el, sheet, layout.sheets[id], {
          z: pile.z + 1 + i,
          x: pile.x + off.x,
          y: pile.y + off.y,
        });
      });
    } else {
      const key = 'pile:' + pileId;
      wanted.add(key);
      const topId = members[members.length - 1];
      const sheet = sheetsById.get(topId);
      let el = nodes.get(key);
      if (!el) {
        el = buildSheet(sheet);
        el.classList.remove('sheet');
        el.classList.add('pile');
        el.querySelector('.resize-handle').remove();
        // In the chrome's flow, not floating over it: a badge pinned to the
        // corner sits on top of the × and swallows every attempt to use it.
        const badge = document.createElement('span');
        badge.className = 'pile-badge';
        badge.title = 'Sheets in this pile';
        el.querySelector('.sheet-chrome').insertBefore(badge, el.querySelector('.sheet-trash'));
        el.dataset.key = key;
        nodes.set(key, el);
        surface.appendChild(el);
      }
      el.dataset.pileId = pileId;
      el.dataset.topId = topId;
      // The element outlives any one top member, so its identity has to be
      // rewritten every render or the chrome acts on the sheet it used to show.
      el.dataset.sheetId = topId;
      el.querySelector('.pile-badge').textContent = members.length;
      const placement = layout.sheets[topId];
      updateSheetEl(el, sheet, placement, { z: pile.z, x: pile.x, y: pile.y });
    }
  }

  for (const [key, el] of nodes) {
    if (!wanted.has(key)) {
      el.remove();
      nodes.delete(key);
    }
  }

  renderInbox();
  renderTrash();
}

function renderInbox() {
  const ids = Object.entries(state.layout.sheets)
    .filter(([, p]) => p.inbox)
    .map(([id]) => id)
    .filter((id) => sheetsById.has(id));
  inboxCount.textContent = ids.length;
  inboxEmpty.hidden = ids.length > 0;
  inboxItems.textContent = '';
  for (const id of ids) {
    const sheet = sheetsById.get(id);
    const item = document.createElement('div');
    item.className = 'inbox-item';
    item.dataset.inboxId = id;
    let preview;
    if (IMAGE_KINDS.has(sheet.kind)) {
      preview = document.createElement('img');
      preview.className = 'preview';
      preview.src = sheet.content_url;
      preview.alt = sheet.name;
      preview.draggable = false;
      preview.dataset.sheetContent = id;
    } else {
      preview = document.createElement('div');
      preview.className = 'preview preview-generic';
      preview.textContent = '.' + sheet.kind;
    }
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = sheet.name;
    label.title = whereFrom(sheet);
    item.append(preview, label);
    if (sheet.origin) {
      const from = document.createElement('span');
      from.className = 'from';
      from.textContent = 'from ' + sheet.origin;
      item.appendChild(from);
    }
    inboxItems.appendChild(item);
  }
}

function renderTrash() {
  trashCount.textContent = state.trash.length;
  trashEmpty.hidden = state.trash.length > 0;
  trashItems.textContent = '';
  for (const sheet of state.trash) {
    const row = document.createElement('div');
    row.className = 'trash-item';
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = sheet.origin ? sheet.name + ' \u2014 from ' + sheet.origin : sheet.name;
    name.title = whereFrom(sheet);
    const restore = document.createElement('button');
    restore.textContent = 'restore';
    restore.addEventListener('click', () => restoreSheet(sheet.id));
    row.append(name, restore);
    trashItems.appendChild(row);
  }
}

// --- updates in place -----------------------------------------------------

/** A sheet gained a version. Swap its pixels and paint a ring. Nothing else. */
function swapInPlace(sheet) {
  const previous = sheetsById.get(sheet.id);
  if (previous) Object.assign(previous, sheet);
  else {
    state.sheets.push(sheet);
    indexSheets();
  }
  const bodies = document.querySelectorAll('[data-sheet-content="' + sheet.id + '"]');
  for (const body of bodies) {
    if (body.tagName === 'IMG') {
      body.src = sheet.content_url;
      continue;
    }
    fillBody(body, sheet);
    const frame = body.closest('.sheet, .pile');
    if (frame) ring(frame);
    const label = frame && frame.querySelector('.sheet-version');
    if (label) label.textContent = 'v' + sheet.version;
  }
  const waiting = inboxItems.querySelector('[data-sheet-content="' + sheet.id + '"]');
  if (waiting) ring(waiting.closest('.inbox-item'));
  // A collapsed pile shows only its top sheet, so a member updating underneath
  // would otherwise be silent. The ring is what says something in there changed.
  const pileId = pileOf(sheet.id);
  if (pileId) ring(nodes.get('pile:' + pileId));
  if (fullscreenId === sheet.id) {
    fillFullscreen(sheet, { keepView: true });
    // The pins are fractions, so they stay put; a pin made on the version
    // that just went turns hollow.
    renderPins(sheet);
  }
}

const ringTimers = new WeakMap();

function ring(el) {
  if (!el) return;
  clearTimeout(ringTimers.get(el)); // a second update must not cut its own ring short
  el.classList.remove('updated');
  void el.offsetWidth; // restart the animation
  el.classList.add('updated');
  ringTimers.set(el, setTimeout(() => el.classList.remove('updated'), RING_MS + 60));
}

// --- gestures -------------------------------------------------------------

let lastClick = { id: null, at: 0 };

/** Did this click complete a double-click on `id`?
 *
 *  The native dblclick cannot be trusted here — the drag shield retargets it
 *  away from the sheet — so the pair is recognised from the sheet the pointer
 *  actually went down on. Both the desk and the inbox ask this same question.
 */
function completesDoubleClick(id) {
  const now = Date.now();
  if (lastClick.id === id && now - lastClick.at < DOUBLE_CLICK_MS) {
    lastClick = { id: null, at: 0 };
    return true;
  }
  lastClick = { id: id, at: now };
  return false;
}

viewportEl.addEventListener('pointerdown', (e) => {
  if (e.button !== 0 && e.button !== 1) return;
  // A sheet's own controls answer their click. Starting a gesture underneath
  // one swallows it — that is how the pile's × used to fan the pile instead.
  if (e.target.closest('.sheet-trash, .sheet-grow, .pile-collapse')) return;

  const resize = e.target.closest('.resize-handle');
  const sheetEl = e.target.closest('.sheet');
  const pileEl = e.target.closest('.pile');

  if (resize && sheetEl) return beginResize(e, sheetEl);
  if (sheetEl) return beginMove(e, sheetEl, 'sheet');
  if (pileEl) return beginMove(e, pileEl, 'pile');
  deactivate();
  if (e.button === 0 && anyPileOpen()) layoutOp('close_piles', {});
  beginPan(e);
});

function anyPileOpen() {
  return Object.values(state.layout.piles).some((p) => p.open);
}

/** --- activation ---------------------------------------------------------
 *
 *  Every sheet drags from anywhere, which means an embedded page has to be
 *  covered by default or it would eat the gesture. A click — not a drag — on
 *  a covered sheet lifts its cover so the plot inside becomes interactive.
 *  Clicking another sheet, clicking the desk, or Escape puts the cover back.
 *  This lives entirely in the page; the desk's layout never learns about it.
 */
let pendingActivation = null;

/** Activation waits out the double-click window. Lifting the shield on the
 *  first click sends the second one into the iframe, and a framed sheet can
 *  then never be enlarged by double-clicking it. */
function activateSoon(id) {
  cancelActivation();
  pendingActivation = setTimeout(() => {
    pendingActivation = null;
    activate(id);
  }, DOUBLE_CLICK_MS);
}

function cancelActivation() {
  if (pendingActivation === null) return;
  clearTimeout(pendingActivation);
  pendingActivation = null;
}

function activate(id) {
  if (activeId === id) return;
  deactivate();
  activeId = id;
  const el = nodes.get('sheet:' + id);
  if (el) el.classList.add('active');
}

function deactivate() {
  cancelActivation();
  if (activeId === null) return;
  const el = nodes.get('sheet:' + activeId);
  if (el) el.classList.remove('active');
  activeId = null;
}

function isFramed(id) {
  const sheet = sheetsById.get(id);
  return !!sheet && FRAME_KINDS.has(sheet.kind);
}

function beginPan(e) {
  const start = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
  viewportEl.classList.add('panning');
  captureDrag(e, {
    move(ev) {
      setView({ x: start.vx + (ev.clientX - start.x), y: start.vy + (ev.clientY - start.y), scale: view.scale }, { persist: false });
    },
    up() {
      viewportEl.classList.remove('panning');
      setView(view);
    },
  });
}

function beginResize(e, sheetEl) {
  const id = sheetEl.dataset.sheetId;
  const placement = state.layout.sheets[id];
  const start = { x: e.clientX, y: e.clientY, w: placement.w, h: placement.h };
  const key = sheetEl.dataset.key;
  held.add(key);
  document.body.classList.add('dragging');
  const sizeAt = (ev) => {
    const floor = state.geometry.min_size;
    // Growing a sheet stops at the edge of the desk, the same way sliding one
    // does. A sheet a previous, unbounded desk left off the slab has no room
    // to measure against, so it keeps the old, unbounded behaviour.
    const roomW = placement.x < DESK.w ? Math.max(floor, DESK.w - EDGE_INSET - placement.x) : Infinity;
    const roomH = placement.y < DESK.h ? Math.max(floor, DESK.h - EDGE_INSET - placement.y) : Infinity;
    return {
      w: Math.min(roomW, Math.max(floor, start.w + (ev.clientX - start.x) / view.scale)),
      h: Math.min(roomH, Math.max(floor, start.h + (ev.clientY - start.y) / view.scale)),
    };
  };

  captureDrag(e, {
    move(ev) {
      const { w, h } = sizeAt(ev);
      sheetEl.style.width = w + 'px';
      sheetEl.style.height = h + 'px';
    },
    up(ev) {
      held.delete(key);
      document.body.classList.remove('dragging');
      const { w, h } = sizeAt(ev);
      layoutOp('resize', { sheet_id: id, w: Math.round(w), h: Math.round(h) });
    },
  });
}

function beginMove(e, el, type) {
  const key = el.dataset.key;
  const pileId = type === 'pile' ? el.dataset.pileId : null;
  const id = type === 'sheet' ? el.dataset.sheetId : el.dataset.topId;
  const origin =
    type === 'pile'
      ? { x: state.layout.piles[pileId].x, y: state.layout.piles[pileId].y }
      : pileOf(id)
        ? currentFannedPosition(id)
        : { x: state.layout.sheets[id].x, y: state.layout.sheets[id].y };
  const start = { x: e.clientX, y: e.clientY };
  const size = type === 'pile' ? pileSize(state.layout.piles[pileId]) : state.layout.sheets[id];
  const at = (ev) => {
    const rawX = origin.x + (ev.clientX - start.x) / view.scale;
    const rawY = origin.y + (ev.clientY - start.y) / view.scale;
    const spot = clampSheet(rawX, rawY, size.w, size.h);
    spot.raw = { x: rawX, y: rawY };
    return spot;
  };
  let moved = false;

  if (type === 'sheet') layoutOp('raise', { sheet_id: id });
  held.add(key);
  el.classList.add('dragging');
  document.body.classList.add('dragging');
  el.style.pointerEvents = 'none';

  captureDrag(e, {
    move(ev) {
      if (Math.abs(ev.clientX - start.x) > CLICK_SLOP || Math.abs(ev.clientY - start.y) > CLICK_SLOP) moved = true;
      const spot = at(ev);
      markEdge(spot, spot.raw.x, spot.raw.y);
      el.style.transform = 'translate(' + spot.x + 'px, ' + spot.y + 'px)';
      highlightDropTarget(ev, el, type);
    },
    up(ev) {
      // Read the trash zone before the class that shows it comes off: a
      // display:none element measures as a zero-size box at the origin, and
      // every drop would miss it.
      // Only a single sheet can be thrown away by dragging. A pile dropped
      // here just lands here: losing five figures to one gesture is not a
      // thing ticket 10 asks for, and not a thing to infer.
      const throwingAway = type !== 'pile' && overTrash(ev);
      held.delete(key);
      el.classList.remove('dragging');
      document.body.classList.remove('dragging');
      el.style.pointerEvents = '';
      clearDropHighlight();
      clearEdges();
      trashDrop.classList.remove('armed');

      if (!moved) {
        // A pile answers a click by fanning open; a sheet answers a second
        // click by going fullscreen. The native dblclick cannot be trusted
        // here — the drag shield retargets it away from the sheet — so the
        // pair is recognised from the sheet the pointer actually went down on.
        if (type === 'pile') return layoutOp('toggle_pile', { pile_id: pileId });
        if (completesDoubleClick(id)) {
          cancelActivation();
          return openFullscreen(id);
        }
        // A single click on an embedded page hands it the pointer; a click on
        // an image sheet has nothing to hand it to, so it only deactivates.
        if (isFramed(id) && !e.target.closest('.sheet-chrome')) activateSoon(id);
        else deactivate();
        return;
      }
      if (throwingAway) {
        trashSheet(id);
        return;
      }
      const spot = at(ev);
      const x = Math.round(spot.x);
      const y = Math.round(spot.y);

      if (type === 'pile') return layoutOp('move_pile', { pile_id: pileId, x, y });

      const onto = dropTarget(ev, el);
      if (onto && onto !== id) return layoutOp('pile', { sheet_id: id, onto });
      if (pileOf(id)) return layoutOp('unpile', { sheet_id: id, x, y });
      return layoutOp('move', { sheet_id: id, x, y });
    },
  });
}

function pileOf(id) {
  const placement = state.layout.sheets[id];
  return placement ? placement.pile : null;
}

function currentFannedPosition(id) {
  const pile = state.layout.piles[pileOf(id)];
  const { w, h } = pileSize(pile);
  const off = fanOffset(pile.members.indexOf(id), w, h);
  return { x: pile.x + off.x, y: pile.y + off.y };
}

/** The sheet under the pointer that a dragged sheet would be piled onto. */
function dropTarget(ev, dragged) {
  for (const el of document.elementsFromPoint(ev.clientX, ev.clientY)) {
    if (el === dragged || dragged.contains(el)) continue;
    const sheetEl = el.closest && el.closest('.sheet, .pile');
    if (!sheetEl || sheetEl === dragged) continue;
    return sheetEl.classList.contains('pile') ? sheetEl.dataset.topId : sheetEl.dataset.sheetId;
  }
  return null;
}

let highlighted = null;

function highlightDropTarget(ev, dragged, type) {
  const armed = type !== 'pile' && overTrash(ev);
  trashDrop.classList.toggle('armed', armed);
  const id = armed ? null : dropTarget(ev, dragged);
  const el = id ? nodes.get('sheet:' + id) || pileNodeFor(id) : null;
  if (el === highlighted) return;
  clearDropHighlight();
  if (el) {
    el.classList.add('drop-target');
    highlighted = el;
  }
}

function pileNodeFor(topId) {
  for (const [key, el] of nodes) {
    if (key.startsWith('pile:') && el.dataset.topId === topId) return el;
  }
  return null;
}

function clearDropHighlight() {
  if (highlighted) highlighted.classList.remove('drop-target');
  highlighted = null;
}

function overTrash(ev) {
  const r = trashDrop.getBoundingClientRect();
  return ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
}

function captureDrag(e, handlers) {
  // Deliberately no preventDefault here: it would suppress the compatibility
  // mouse events, and with them the dblclick that opens a sheet fullscreen.
  // Text selection is held off by `user-select: none` on the desk instead.
  const onMove = (ev) => handlers.move(ev);
  const onUp = (ev) => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
    handlers.up(ev);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
}

// --- dragging out of the inbox -------------------------------------------

let ghost = null;

inboxItems.addEventListener('pointerdown', (e) => {
  const item = e.target.closest('.inbox-item');
  if (!item || e.button !== 0) return;
  const id = item.dataset.inboxId;
  const sheet = sheetsById.get(id);
  const placement = state.layout.sheets[id];
  const rect = item.getBoundingClientRect();
  // Where in the item the pointer took hold, as a fraction. An inbox item is
  // not the size the sheet will be, and the desk may be at any zoom, so only a
  // fraction survives the journey — a pixel offset lands the sheet elsewhere.
  const grab = {
    fx: rect.width ? (e.clientX - rect.left) / rect.width : 0.5,
    fy: rect.height ? (e.clientY - rect.top) / rect.height : 0.5,
  };
  let moved = false;

  document.body.classList.add('dragging');
  captureDrag(e, {
    move(ev) {
      if (!moved && Math.hypot(ev.clientX - e.clientX, ev.clientY - e.clientY) < CLICK_SLOP) return;
      moved = true;
      if (!ghost) ghost = makeGhost(sheet, placement);
      ghost.style.left = ev.clientX - grab.fx * ghost.offsetWidth + 'px';
      ghost.style.top = ev.clientY - grab.fy * ghost.offsetHeight + 'px';
      trashDrop.classList.toggle('armed', overTrash(ev));
    },
    up(ev) {
      // Measured before the class that shows the zone comes off: a display:none
      // element is a zero-size box at the origin, and every drop would miss it.
      const throwingAway = overTrash(ev);
      document.body.classList.remove('dragging');
      trashDrop.classList.remove('armed');
      if (ghost) {
        ghost.remove();
        ghost = null;
      }
      if (!moved) {
        if (completesDoubleClick(id)) openFullscreen(id);
        return;
      }
      if (throwingAway) return trashSheet(id);
      const inboxRect = $('inbox').getBoundingClientRect();
      if (ev.clientX < inboxRect.right) return; // dropped back into the strip
      const at = toDesk(ev.clientX, ev.clientY);
      const spot = clampSheet(
        at.x - grab.fx * placement.w,
        at.y - grab.fy * placement.h,
        placement.w,
        placement.h,
      );
      layoutOp('place', {
        sheet_id: id,
        x: Math.round(spot.x),
        y: Math.round(spot.y),
      });
    },
  });
});

function makeGhost(sheet, placement) {
  const el = document.createElement('div');
  el.id = 'drag-ghost';
  el.style.width = Math.max(40, placement.w * view.scale) + 'px';
  el.style.height = Math.max(30, placement.h * view.scale) + 'px';
  const node = contentNode(sheet);
  node.src = sheet.content_url;
  node.style.width = '100%';
  node.style.height = '100%';
  if (node.tagName === 'IMG') node.style.objectFit = 'contain';
  el.appendChild(node);
  document.body.appendChild(el);
  return el;
}

// --- trash ----------------------------------------------------------------

async function trashSheet(id) {
  try {
    await apiPost('/api/trash', { sheet_id: id });
  } catch (err) {
    console.error('trash', err);
  }
  await refresh();
}

async function restoreSheet(id) {
  try {
    await apiPost('/api/restore', { sheet_id: id });
  } catch (err) {
    console.error('restore', err);
  }
  await refresh();
}

$('btn-clear').addEventListener('click', async () => {
  if (!state.sheets.length) return;
  const n = state.sheets.length;
  if (!confirm(`Clear the desk? ${n} sheet${n === 1 ? '' : 's'} will go to the trash.`)) return;
  try {
    await apiPost('/api/clear', {});
  } catch (err) {
    console.error('clear', err);
  }
  await refresh();
});

$('btn-trash').addEventListener('click', () => {
  trashPanel.hidden = !trashPanel.hidden;
});
$('btn-trash-close').addEventListener('click', () => {
  trashPanel.hidden = true;
});

// --- fullscreen -----------------------------------------------------------

const fullscreenEl = $('fullscreen');
const fullscreenStage = $('fullscreen-stage');
const fullscreenHolder = $('fullscreen-holder');
const pinsLayer = $('fullscreen-pins');
let fullscreenId = null;
let fullscreenView = { x: 0, y: 0, scale: 1 };
let naturalSize = { w: 1200, h: 900 };

viewportEl.addEventListener('dblclick', (e) => {
  const el = e.target.closest('.sheet, .pile');
  if (!el) return;
  const id = el.classList.contains('pile') ? el.dataset.topId : el.dataset.sheetId;
  if (id) openFullscreen(id);
});

function openFullscreen(id) {
  const sheet = sheetsById.get(id);
  if (!sheet) return;
  cancelActivation();
  cancelComment();
  closePopover();
  fullscreenId = id;
  $('fullscreen-name').textContent = whereFrom(sheet);
  fullscreenEl.hidden = false;
  fillFullscreen(sheet, { keepView: false });
  renderPins(sheet);
}

function fillFullscreen(sheet, { keepView }) {
  const existing = fullscreenHolder.querySelector('img, iframe');
  const wantsFrame = FRAME_KINDS.has(sheet.kind);
  if (!existing || (existing.tagName === 'IFRAME') !== wantsFrame) {
    // Only the content is replaced: the pins layer stays, so a sheet whose
    // kind changed under its comments keeps them on screen.
    if (existing) existing.remove();
    const node = contentNode(sheet);
    node.addEventListener('load', () => {
      if (node.tagName === 'IMG' && node.naturalWidth) {
        naturalSize = { w: node.naturalWidth, h: node.naturalHeight };
      }
      if (!keepView) fitFullscreen();
    });
    node.src = sheet.content_url;
    fullscreenHolder.insertBefore(node, pinsLayer);
  } else {
    existing.src = sheet.content_url;
    ring(fullscreenHolder);
  }
  if (!keepView) {
    naturalSize = wantsFrame ? { w: 1100, h: 800 } : naturalSize;
    fitFullscreen();
  }
}

function fitFullscreen() {
  const pad = 48;
  const availW = window.innerWidth - pad * 2;
  const availH = window.innerHeight - pad * 2;
  const scale = Math.min(availW / naturalSize.w, availH / naturalSize.h);
  fullscreenHolder.style.width = naturalSize.w + 'px';
  fullscreenHolder.style.height = naturalSize.h + 'px';
  fullscreenView = {
    scale,
    x: (window.innerWidth - naturalSize.w * scale) / 2,
    y: (window.innerHeight - naturalSize.h * scale) / 2,
  };
  applyFullscreenView();
}

function applyFullscreenView() {
  fullscreenHolder.style.transform =
    'translate(' + fullscreenView.x + 'px, ' + fullscreenView.y + 'px) scale(' + fullscreenView.scale + ')';
  // The pins ride inside the scaled holder and undo the scale on themselves,
  // so a pin is the same small circle at 2% and at 3200%.
  fullscreenHolder.style.setProperty('--pin-scale', 1 / fullscreenView.scale);
  closePopover();
  positionEditor();
}

function closeFullscreen() {
  cancelComment();
  closePopover();
  fullscreenEl.hidden = true;
  fullscreenId = null;
  const content = fullscreenHolder.querySelector('img, iframe');
  if (content) content.remove();
  pinsLayer.textContent = '';
  pinsInBar.textContent = '';
}

$('fullscreen-close').addEventListener('click', closeFullscreen);
$('fullscreen-reset').addEventListener('click', fitFullscreen);

fullscreenStage.addEventListener('pointerdown', (e) => {
  if (e.target.closest('#fullscreen-bar, .pin-label')) return;
  closePopover();
  if (commentMode && e.button === 0 && fullscreenHolder.contains(e.target)) return beginCommentDrag(e);
  const start = { x: e.clientX, y: e.clientY, vx: fullscreenView.x, vy: fullscreenView.y };
  fullscreenStage.classList.add('panning');
  captureDrag(e, {
    move(ev) {
      fullscreenView.x = start.vx + (ev.clientX - start.x);
      fullscreenView.y = start.vy + (ev.clientY - start.y);
      applyFullscreenView();
    },
    up() {
      fullscreenStage.classList.remove('panning');
    },
  });
});

fullscreenStage.addEventListener(
  'wheel',
  (e) => {
    e.preventDefault();
    const factor = e.ctrlKey || e.metaKey ? Math.exp(-e.deltaY * 0.01) : Math.exp(-e.deltaY * 0.0015);
    const next = Math.min(MAX_SCALE * 4, Math.max(0.02, fullscreenView.scale * factor));
    const k = next / fullscreenView.scale;
    fullscreenView = {
      scale: next,
      x: e.clientX - (e.clientX - fullscreenView.x) * k,
      y: e.clientY - (e.clientY - fullscreenView.y) * k,
    };
    applyFullscreenView();
  },
  { passive: false }
);

// --- comments: pins on the figure -----------------------------------------

/* The user pins a comment on a figure, in fullscreen: a rectangle, a point,
   or the whole sheet. A comment is a record on the sheet — not layout — so
   nothing here can move anything. The pins are the one accepted exception to
   "nothing paints over a figure", and they are kept small for it.

   The anchor is stored as fractions of the content's natural box. Nothing the
   browser measured is sent: the holder is the natural box, so a fraction of
   its rect is a fraction of the figure at any zoom. */

const pinsInBar = $('fullscreen-pins-sheet');
const commentBtn = $('fullscreen-comment');
const commentHint = $('comment-hint');
const editorEl = $('comment-editor');
const editorText = $('comment-text');
const editorHint = editorEl.querySelector('.hint');
const popoverEl = $('comment-popover');
const EDITOR_HINT = editorHint.textContent;

/** Armed: the next drag or click on the figure places a comment. */
let commentMode = false;
/** The anchor being typed for, once a spot has been chosen. `undefined` means
 *  no editor is open; `null` is a comment on the whole sheet. */
let pendingAnchor;
let pendingPin = null;
/** The comment whose popover is showing. */
let openCommentId = null;

function commentInProgress() {
  return commentMode || !editorEl.hidden;
}

function fullscreenSheet() {
  return fullscreenId === null ? null : sheetsById.get(fullscreenId) || null;
}

function setCommentBadge(el, sheet) {
  const badge = el.querySelector('.sheet-comments');
  if (!badge) return;
  const n = sheet.open_comments || 0;
  badge.hidden = n === 0;
  badge.textContent = n;
  badge.title = n === 1 ? '1 open comment' : n + ' open comments';
}

/** A sheet's record changed — a comment came or went. Update what shows it
 *  and nothing else: no ring, no re-render, no layout. */
function changeInPlace(sheet) {
  if (!sheetsById.has(sheet.id)) return;
  Object.assign(sheetsById.get(sheet.id), sheet);
  for (const el of nodes.values()) {
    if (el.dataset.sheetId === sheet.id) setCommentBadge(el, sheet);
  }
  if (fullscreenId === sheet.id) renderPins(sheet);
}

/** Draw every open pin for the sheet under the lamp. Anchored comments go on
 *  the figure; sheet-level ones line up in the bar. */
function renderPins(sheet) {
  pinsLayer.textContent = '';
  pinsInBar.textContent = '';
  if (pendingPin) pinsLayer.appendChild(pendingPin);
  for (const comment of sheet.comments || []) {
    if (comment.resolved_at !== null) continue;
    const hollow = comment.version !== sheet.version;
    const label = document.createElement('button');
    label.className = 'pin-label';
    label.dataset.commentId = comment.id;
    label.textContent = hollow ? comment.number + '·v' + comment.version : comment.number;
    label.title = (hollow ? 'made on v' + comment.version + ': ' : '') + comment.text;
    label.addEventListener('click', (e) => {
      e.stopPropagation();
      showPopover(comment.id, label);
    });
    if (!comment.anchor) {
      if (hollow) label.classList.add('hollow');
      pinsInBar.appendChild(label);
      continue;
    }
    const pin = document.createElement('div');
    const a = comment.anchor;
    pin.className = 'pin ' + (a.w > 0 || a.h > 0 ? 'rect' : 'point') + (hollow ? ' hollow' : '');
    placePin(pin, a);
    pin.appendChild(label);
    pinsLayer.appendChild(pin);
  }
  if (openCommentId !== null && !pinsLayer.querySelector('[data-comment-id="' + openCommentId + '"]') &&
      !pinsInBar.querySelector('[data-comment-id="' + openCommentId + '"]')) {
    closePopover();
  }
}

function placePin(pin, a) {
  pin.style.left = a.x * 100 + '%';
  pin.style.top = a.y * 100 + '%';
  pin.style.width = a.w * 100 + '%';
  pin.style.height = a.h * 100 + '%';
}

/** Enter comment mode, or for a framed sheet go straight to the editor: an
 *  iframe eats pointer events and has no natural box, so it takes comments on
 *  the whole sheet only. */
function armComment() {
  const sheet = fullscreenSheet();
  if (!sheet) return;
  closePopover();
  if (!editorEl.hidden) return editorText.focus();
  if (FRAME_KINDS.has(sheet.kind)) return openEditor(null);
  commentMode = !commentMode;
  fullscreenEl.classList.toggle('commenting', commentMode);
  commentBtn.classList.toggle('armed', commentMode);
  commentHint.hidden = !commentMode;
}

commentBtn.addEventListener('click', armComment);

/** Fractions of the figure under a point on screen, clamped to the box. */
function holderFraction(clientX, clientY) {
  const r = fullscreenHolder.getBoundingClientRect();
  const clamp = (v) => Math.min(1, Math.max(0, v));
  return {
    x: r.width ? clamp((clientX - r.left) / r.width) : 0,
    y: r.height ? clamp((clientY - r.top) / r.height) : 0,
  };
}

function beginCommentDrag(e) {
  const from = holderFraction(e.clientX, e.clientY);
  const anchorAt = (ev) => {
    const to = holderFraction(ev.clientX, ev.clientY);
    const x = Math.min(from.x, to.x);
    const y = Math.min(from.y, to.y);
    return { x, y, w: Math.max(from.x, to.x) - x, h: Math.max(from.y, to.y) - y };
  };
  let moved = false;
  pendingPin = document.createElement('div');
  pendingPin.className = 'pin rect pending';
  placePin(pendingPin, { ...from, w: 0, h: 0 });
  pinsLayer.appendChild(pendingPin);
  captureDrag(e, {
    move(ev) {
      if (Math.abs(ev.clientX - e.clientX) > CLICK_SLOP || Math.abs(ev.clientY - e.clientY) > CLICK_SLOP) moved = true;
      if (moved) placePin(pendingPin, anchorAt(ev));
    },
    up(ev) {
      // A click is a point: a rectangle of zero size.
      openEditor(moved ? anchorAt(ev) : { x: from.x, y: from.y, w: 0, h: 0 });
    },
  });
}

/** Ask for the text. The spot has been chosen; comment mode is over. */
function openEditor(anchor) {
  commentMode = false;
  commentBtn.classList.remove('armed');
  commentHint.hidden = true;
  fullscreenEl.classList.add('commenting');
  pendingAnchor = anchor;
  if (anchor) {
    if (!pendingPin) {
      pendingPin = document.createElement('div');
      pinsLayer.appendChild(pendingPin);
    }
    pendingPin.className = 'pin pending ' + (anchor.w > 0 || anchor.h > 0 ? 'rect' : 'point');
    placePin(pendingPin, anchor);
  } else if (pendingPin) {
    pendingPin.remove();
    pendingPin = null;
  }
  editorHint.textContent = anchor ? EDITOR_HINT : 'On the whole sheet · ' + EDITOR_HINT;
  editorEl.hidden = false;
  positionEditor();
  editorText.focus();
}

/** Keep the editor beside its spot as the figure pans and zooms. */
function positionEditor() {
  if (editorEl.hidden) return;
  const pad = 12;
  let left;
  let top;
  if (pendingAnchor) {
    const r = fullscreenHolder.getBoundingClientRect();
    left = r.left + (pendingAnchor.x + pendingAnchor.w) * r.width + pad;
    top = r.top + pendingAnchor.y * r.height;
  } else {
    left = (window.innerWidth - editorEl.offsetWidth) / 2;
    top = 64;
  }
  left = Math.max(pad, Math.min(left, window.innerWidth - editorEl.offsetWidth - pad));
  top = Math.max(pad, Math.min(top, window.innerHeight - editorEl.offsetHeight - pad));
  editorEl.style.left = left + 'px';
  editorEl.style.top = top + 'px';
}

function cancelComment() {
  commentMode = false;
  pendingAnchor = undefined;
  if (pendingPin) {
    pendingPin.remove();
    pendingPin = null;
  }
  editorEl.hidden = true;
  editorText.value = '';
  editorHint.textContent = EDITOR_HINT;
  commentHint.hidden = true;
  commentBtn.classList.remove('armed');
  fullscreenEl.classList.remove('commenting');
}

async function saveComment() {
  const text = editorText.value.trim();
  if (!text || pendingAnchor === undefined || fullscreenId === null) return;
  try {
    const { sheet } = await apiPost('/api/comments', {
      op: 'add',
      sheet_id: fullscreenId,
      anchor: pendingAnchor,
      text,
    });
    cancelComment();
    changeInPlace(sheet);
  } catch (err) {
    console.error('comment', err);
    editorHint.textContent = String(err.message || err);
  }
}

// The window's key handler ignores a textarea, so the editor answers for
// itself: Enter saves, Escape cancels the comment and nothing more.
editorText.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    saveComment();
  } else if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    cancelComment();
  }
});

function showPopover(commentId, at) {
  const sheet = fullscreenSheet();
  const comment = sheet && (sheet.comments || []).find((c) => c.id === commentId);
  if (!comment) return;
  openCommentId = commentId;
  const hollow = comment.version !== sheet.version;
  $('comment-popover-head').textContent =
    '#' + comment.number + ' · ' +
    (hollow ? 'made on v' + comment.version + ', now v' + sheet.version : 'v' + comment.version) +
    (comment.anchor ? '' : ' · whole sheet');
  $('comment-popover-text').textContent = comment.text;
  popoverEl.hidden = false;
  const r = at.getBoundingClientRect();
  const pad = 12;
  let left = r.right + 8;
  let top = r.top - 6;
  left = Math.max(pad, Math.min(left, window.innerWidth - popoverEl.offsetWidth - pad));
  top = Math.max(pad, Math.min(top, window.innerHeight - popoverEl.offsetHeight - pad));
  popoverEl.style.left = left + 'px';
  popoverEl.style.top = top + 'px';
}

function closePopover() {
  openCommentId = null;
  popoverEl.hidden = true;
}

async function commentOp(op) {
  if (openCommentId === null || fullscreenId === null) return;
  const params = { op, sheet_id: fullscreenId, comment_id: openCommentId };
  closePopover();
  try {
    const { sheet } = await apiPost('/api/comments', params);
    changeInPlace(sheet);
  } catch (err) {
    console.error('comment', op, err);
    await refresh();
  }
}

$('comment-resolve').addEventListener('click', () => commentOp('resolve'));
$('comment-remove').addEventListener('click', () => commentOp('remove'));

// --- wheel, keys ----------------------------------------------------------

viewportEl.addEventListener(
  'wheel',
  (e) => {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.01));
    } else {
      setView({ x: view.x - e.deltaX, y: view.y - e.deltaY, scale: view.scale });
    }
  },
  { passive: false }
);

window.addEventListener('keydown', (e) => {
  if (e.target.matches('input, textarea')) return;
  if (e.key === 'Escape') {
    if (!fullscreenEl.hidden) {
      // A comment in progress is what Escape cancels; the next one closes
      // the view, as before.
      if (commentInProgress()) return cancelComment();
      if (!popoverEl.hidden) return closePopover();
      return closeFullscreen();
    }
    if (!trashPanel.hidden) return (trashPanel.hidden = true);
    if (activeId !== null) return deactivate();
    if (anyPileOpen()) return layoutOp('close_piles', {});
  }
  if (!fullscreenEl.hidden) {
    if (e.key === 'c') armComment();
    if (e.key === 'Enter' && commentMode) openEditor(null);
    return;
  }
  // The sheet the user clicked goes in the trash. Same act as its ×, and as
  // recoverable: the trash corner brings it back.
  if ((e.key === 'Delete' || e.key === 'Backspace') && activeId !== null) {
    e.preventDefault();
    const id = activeId;
    deactivate();
    return trashSheet(id);
  }
  if (e.key === 't') cycleSkin();
  if (e.key === '0') goHome();
  if (e.key === 'f') goOverview();
  if (e.key === '=' || e.key === '+') zoomAt(window.innerWidth / 2, window.innerHeight / 2, 1.2);
  if (e.key === '-') zoomAt(window.innerWidth / 2, window.innerHeight / 2, 1 / 1.2);
});

$('btn-home').addEventListener('click', goHome);
$('btn-overview').addEventListener('click', goOverview);

// --- the live stream ------------------------------------------------------

let stream = null;
let reopenTimer = null;
let reopenDelay = RECONNECT_MIN_MS;

function listen() {
  clearTimeout(reopenTimer);
  reopenTimer = null;
  if (stream) stream.close();

  const source = new EventSource('/api/events');
  stream = source;

  source.addEventListener('sheet.created', (e) => {
    const data = JSON.parse(e.data);
    swapOrAdd(data.sheet);
    state.layout = data.layout;
    adoptGeometry(data.geometry);
    render();
    ring(inboxItems.querySelector('[data-sheet-content="' + data.sheet.id + '"]'));
  });

  // The whole point of the desk: no move, no scroll, no reflow, no refetch.
  source.addEventListener('sheet.version', (e) => swapInPlace(JSON.parse(e.data).sheet));

  // A comment was added, resolved, or removed — on this page or another. The
  // sheet's record changed and nothing else did: no ring, nothing moves.
  source.addEventListener('sheet.changed', (e) => changeInPlace(JSON.parse(e.data).sheet));

  source.addEventListener('sheet.trashed', (e) => {
    const data = JSON.parse(e.data);
    state.sheets = state.sheets.filter((s) => s.id !== data.sheet_id);
    indexSheets();
    state.layout = data.layout;
    adoptGeometry(data.geometry);
    refresh();
  });

  source.addEventListener('desk.cleared', (e) => {
    const data = JSON.parse(e.data);
    state.sheets = [];
    indexSheets();
    state.layout = data.layout;
    adoptGeometry(data.geometry);
    refresh();
  });

  // Another page, or the command, brought a different desk out.
  source.addEventListener('desk.changed', () => refresh());

  source.addEventListener('sheet.restored', (e) => {
    const data = JSON.parse(e.data);
    swapOrAdd(data.sheet);
    state.layout = data.layout;
    adoptGeometry(data.geometry);
    refresh();
  });

  source.addEventListener('open', () => {
    setLink(true);
    reopenDelay = RECONNECT_MIN_MS;
    // Whatever happened while the stream was down, this catches the desk up.
    refresh().catch(() => {});
  });

  // A browser retries a stream it thinks is merely interrupted, but a server
  // that goes away mid-response leaves it CLOSED for good — a restarted desk
  // would then be silently stale in an open tab. So the page reopens it itself.
  source.addEventListener('error', () => {
    setLink(false);
    if (source.readyState === EventSource.CLOSED) reopen(source);
  });
}

// --- the coffee: connection state, as an object rather than a banner ------

let staleTimer = null;

/** The stream's state, told in coffee. Steam while it is live; when it drops
 *  the steam wafts away over a second rather than snapping off, because a
 *  one-second blip must not flash at the user, and the cup goes cold — cold
 *  means still, which is the whole of the metaphor. A cup is a quieter alarm
 *  than a red banner was, so an outage that lasts escalates. The same fact
 *  goes out in words on the live region, for anything that cannot see steam. */
function setLink(live) {
  document.body.classList.toggle('offline', !live);
  clearTimeout(staleTimer);
  if (live) {
    document.body.classList.remove('stale');
    mug.title = 'Connected';
    connection.textContent = 'Connected';
    return;
  }
  mug.title = 'Reconnecting…';
  connection.textContent = 'Connection lost — reconnecting';
  staleTimer = setTimeout(() => document.body.classList.add('stale'), STALE_MS);
}

function reopen(source) {
  if (stream !== source || reopenTimer) return;
  const wait = reopenDelay;
  reopenDelay = Math.min(RECONNECT_MAX_MS, reopenDelay * 2);
  reopenTimer = setTimeout(() => {
    reopenTimer = null;
    listen();
  }, wait);
}

// A window that shrank can leave the desk clamped against a bound that has
// moved. Re-clamping is not persisted: the desk did not change, the window did.
window.addEventListener('resize', () => setView(view, { persist: false }));

// A laptop that slept through the outage should not have to be reloaded.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  if (!stream || stream.readyState === EventSource.CLOSED) {
    reopenDelay = RECONNECT_MIN_MS;
    listen();
  }
});

function swapOrAdd(sheet) {
  if (sheetsById.has(sheet.id)) Object.assign(sheetsById.get(sheet.id), sheet);
  else {
    state.sheets.push(sheet);
    indexSheets();
  }
}

// --- start ----------------------------------------------------------------

refresh()
  .then(() => {
    viewTouched = false;
    const stored = state.layout.viewport;
    if (stored) {
      view = clampView({ x: stored.x, y: stored.y, scale: stored.scale });
      applyView();
    }
  })
  .catch((err) => console.error('desk', err))
  .finally(listen);
