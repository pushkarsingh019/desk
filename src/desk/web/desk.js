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
const MIN_SCALE = 0.05;
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
  state = fresh;
  indexSheets();
  const stored = state.layout.viewport;
  if (stored && !viewTouched) view = { x: stored.x, y: stored.y, scale: stored.scale };
  applyView();
  render();
}

// --- the desk transform ---------------------------------------------------

let viewTouched = false;
let viewSaveTimer = null;

function applyView() {
  surface.style.transform =
    'translate(' + view.x + 'px, ' + view.y + 'px) scale(' + view.scale + ')';
  zoomReadout.textContent = Math.round(view.scale * 100) + '%';
}

function setView(next, { persist = true } = {}) {
  view = {
    x: next.x,
    y: next.y,
    scale: Math.min(MAX_SCALE, Math.max(MIN_SCALE, next.scale)),
  };
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
  const box = state.geometry.bounds;
  if (!box || box.w <= 0 || box.h <= 0) return goHome();
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
  const version = document.createElement('span');
  version.className = 'sheet-version';
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
  chrome.append(name, version, grow, bin);

  const body = document.createElement('div');
  body.className = 'sheet-body';

  const handle = document.createElement('div');
  handle.className = 'resize-handle';
  handle.dataset.role = 'resize';

  el.append(chrome, body, handle);
  return el;
}

function updateSheetEl(el, sheet, placement, { z, x, y }) {
  el.querySelector('.sheet-name').textContent = sheet.name;
  el.querySelector('.sheet-name').title = sheet.source_path;
  el.querySelector('.sheet-version').textContent = 'v' + sheet.version;
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
    label.title = sheet.source_path;
    item.append(preview, label);
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
    name.textContent = sheet.name;
    name.title = sheet.source_path;
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
  if (fullscreenId === sheet.id) fillFullscreen(sheet, { keepView: true });
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
    return {
      w: Math.max(floor, start.w + (ev.clientX - start.x) / view.scale),
      h: Math.max(floor, start.h + (ev.clientY - start.y) / view.scale),
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
  let moved = false;

  if (type === 'sheet') layoutOp('raise', { sheet_id: id });
  held.add(key);
  el.classList.add('dragging');
  document.body.classList.add('dragging');
  el.style.pointerEvents = 'none';

  captureDrag(e, {
    move(ev) {
      const dx = (ev.clientX - start.x) / view.scale;
      const dy = (ev.clientY - start.y) / view.scale;
      if (Math.abs(ev.clientX - start.x) > CLICK_SLOP || Math.abs(ev.clientY - start.y) > CLICK_SLOP) moved = true;
      el.style.transform = 'translate(' + (origin.x + dx) + 'px, ' + (origin.y + dy) + 'px)';
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
      const dx = (ev.clientX - start.x) / view.scale;
      const dy = (ev.clientY - start.y) / view.scale;
      const x = Math.round(origin.x + dx);
      const y = Math.round(origin.y + dy);

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
      layoutOp('place', {
        sheet_id: id,
        x: Math.round(at.x - grab.fx * placement.w),
        y: Math.round(at.y - grab.fy * placement.h),
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
  fullscreenId = id;
  $('fullscreen-name').textContent = sheet.source_path;
  fullscreenEl.hidden = false;
  fillFullscreen(sheet, { keepView: false });
}

function fillFullscreen(sheet, { keepView }) {
  const existing = fullscreenHolder.querySelector('img, iframe');
  const wantsFrame = FRAME_KINDS.has(sheet.kind);
  if (!existing || (existing.tagName === 'IFRAME') !== wantsFrame) {
    fullscreenHolder.textContent = '';
    const node = contentNode(sheet);
    node.addEventListener('load', () => {
      if (node.tagName === 'IMG' && node.naturalWidth) {
        naturalSize = { w: node.naturalWidth, h: node.naturalHeight };
      }
      if (!keepView) fitFullscreen();
    });
    node.src = sheet.content_url;
    fullscreenHolder.appendChild(node);
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
}

function closeFullscreen() {
  fullscreenEl.hidden = true;
  fullscreenId = null;
  fullscreenHolder.textContent = '';
}

$('fullscreen-close').addEventListener('click', closeFullscreen);
$('fullscreen-reset').addEventListener('click', fitFullscreen);

fullscreenStage.addEventListener('pointerdown', (e) => {
  if (e.target.closest('#fullscreen-bar')) return;
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
    if (!fullscreenEl.hidden) return closeFullscreen();
    if (!trashPanel.hidden) return (trashPanel.hidden = true);
    if (activeId !== null) return deactivate();
    if (anyPileOpen()) return layoutOp('close_piles', {});
  }
  if (!fullscreenEl.hidden) return;
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

  source.addEventListener('sheet.trashed', (e) => {
    const data = JSON.parse(e.data);
    state.sheets = state.sheets.filter((s) => s.id !== data.sheet_id);
    indexSheets();
    state.layout = data.layout;
    adoptGeometry(data.geometry);
    refresh();
  });

  source.addEventListener('sheet.restored', (e) => {
    const data = JSON.parse(e.data);
    swapOrAdd(data.sheet);
    state.layout = data.layout;
    adoptGeometry(data.geometry);
    refresh();
  });

  source.addEventListener('open', () => {
    connection.hidden = true;
    reopenDelay = RECONNECT_MIN_MS;
    // Whatever happened while the stream was down, this catches the desk up.
    refresh().catch(() => {});
  });

  // A browser retries a stream it thinks is merely interrupted, but a server
  // that goes away mid-response leaves it CLOSED for good — a restarted desk
  // would then be silently stale in an open tab. So the page reopens it itself.
  source.addEventListener('error', () => {
    connection.hidden = false;
    if (source.readyState === EventSource.CLOSED) reopen(source);
  });
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
      view = { x: stored.x, y: stored.y, scale: stored.scale };
      applyView();
    }
  })
  .catch((err) => console.error('desk', err))
  .finally(listen);
