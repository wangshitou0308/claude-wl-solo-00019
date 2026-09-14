/* 剪报连载接续工作台 —— 前端逻辑 */
'use strict';

const CLUE_FIELDS = ['date_start', 'date_end', 'edition', 'section', 'column_name',
                     'first_phrase', 'last_phrase', 'cont_from', 'cont_to', 'notes'];
const EVIDENCE_FIELDS = ['first_phrase', 'last_phrase', 'cont_from', 'cont_to'];
const CLUE_LABELS = {
  date_start: '日期起', date_end: '日期止', edition: '版次', section: '正/副刊',
  column_name: '栏目名', first_phrase: '段首短句', last_phrase: '段尾短句',
  cont_from: '上接指向', cont_to: '下转指向', notes: '备注'
};

let state = { pages: [], undo_depth: 0 };
let analysis = null;
let selectedId = null;
let loadedStored = null;      // 阅读区当前已加载图像的存储名
let drawField = null;         // 非 null 时处于证据框选模式（字段名）

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}

function toast(msg, kind = 'info') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = `show ${kind}`;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.className = ''; }, 3200);
}

function pageLabel(p) {
  return `#${p.id}（${p.date_start || '日期未知'}·${p.edition || '?'}版）`;
}
function pageById(id) { return state.pages.find(p => p.id === id); }
function placedPages() {
  return state.pages.filter(p => p.placement)
                    .sort((a, b) => a.placement.position - b.placement.position);
}
function boardOrder() { return placedPages().map(p => p.id); }
function lockedIds() {
  return placedPages().filter(p => p.placement.locked).map(p => p.id);
}

/* ================= 刷新 ================= */

async function refresh() {
  state = await api('/api/state');
  if (selectedId && !pageById(selectedId)) {
    selectedId = null;
    setDrawMode(null);
    clearForm();
  } else {
    syncEvidenceControls();
    renderEvidenceLayer();
  }
  renderThumbs();
  renderBoard();
  renderUndo();
}

async function refreshAnalysis() {
  analysis = await api('/api/analysis');
  renderBoard();      // 接续箭头颜色随分析更新
  renderAnalysis();
}

function renderUndo() {
  $('#undoBtn').disabled = state.undo_depth === 0;
  $('#undoBtn').textContent = state.undo_depth ? `↩ 撤销（${state.undo_depth}）` : '↩ 撤销';
}

/* ================= 页库 ================= */

function renderThumbs() {
  const box = $('#thumbs');
  const scrollTop = box.scrollTop;
  box.innerHTML = '';
  $('#pageCount').textContent = `（共 ${state.pages.length} 页）`;
  for (const p of state.pages) {
    const card = document.createElement('div');
    card.className = 'thumb' + (p.id === selectedId ? ' selected' : '');

    const img = document.createElement('img');
    img.src = `/scans/${p.stored_name}`;
    img.alt = p.orig_name;
    card.appendChild(img);

    const info = document.createElement('div');
    info.className = 'tinfo';
    info.innerHTML = `<div class="tid">#${p.id} ${escapeHtml(p.orig_name)}</div>
      <div>${escapeHtml(p.date_start || '日期未知')} · ${escapeHtml(p.edition || '?')}版 · ${escapeHtml(p.section)}</div>
      <div class="badges"></div>`;
    const badges = $('.badges', info);
    if (p.column_name) badges.appendChild(makeBadge(escapeHtml(p.column_name)));
    if (p.placement) badges.appendChild(makeBadge(`排序区 #${p.placement.position + 1}`, 'placed'));
    if (p.placement && p.placement.locked) badges.appendChild(makeBadge('已锁定', 'lock'));
    if (p.placement && p.placement.stale) badges.appendChild(makeBadge('待复核', 'stale'));
    card.appendChild(info);

    const btns = document.createElement('div');
    btns.className = 'tbtns';
    const placeBtn = document.createElement('button');
    placeBtn.textContent = p.placement ? '移出' : '＋排序';
    placeBtn.onclick = e => { e.stopPropagation(); togglePlace(p); };
    const delBtn = document.createElement('button');
    delBtn.textContent = '删除';
    delBtn.className = 'danger';
    delBtn.onclick = e => { e.stopPropagation(); deletePage(p); };
    btns.append(placeBtn, delBtn);
    card.appendChild(btns);

    card.onclick = () => selectPage(p.id);
    box.appendChild(card);
  }
  box.scrollTop = scrollTop;
}

function makeBadge(text, cls = '') {
  const b = document.createElement('span');
  b.className = `badge ${cls}`;
  b.innerHTML = text;
  return b;
}

async function togglePlace(p) {
  const order = boardOrder();
  const next = p.placement ? order.filter(id => id !== p.id) : order.concat(p.id);
  await mutate('/api/arrange', { order: next }, p.placement ? '已移出排序区' : '已加入排序区');
}

async function deletePage(p) {
  if (!confirm(`确定删除 #${p.id}「${p.orig_name}」？\n（排序变化可撤销，图片文件将一并删除）`)) return;
  await api(`/api/pages/${p.id}`, { method: 'DELETE' });
  toast(`已删除 #${p.id}（排序变化可撤销）`);
  await refresh();
  await refreshAnalysis();
}

/* ================= 阅读区（SVG 缩放/平移） ================= */

const svg = $('#readerSvg'), readerG = $('#readerG'), readerImg = $('#readerImg');
const evidenceLayer = $('#evidenceLayer');   // 无变换覆盖组：坐标即视口像素
const view = { scale: 1, tx: 0, ty: 0 };

function applyView() {
  readerG.setAttribute('transform', `translate(${view.tx},${view.ty}) scale(${view.scale})`);
}
function zoomAt(cx, cy, k) {
  view.tx = cx - (cx - view.tx) * k;
  view.ty = cy - (cy - view.ty) * k;
  view.scale *= k;
  applyView();
}
function fitView() {
  const r = svg.getBoundingClientRect();
  const w = +readerImg.getAttribute('width') || 1;
  const h = +readerImg.getAttribute('height') || 1;
  view.scale = Math.min(r.width / w, r.height / h, 1);
  view.tx = (r.width - w * view.scale) / 2;
  view.ty = (r.height - h * view.scale) / 2;
  applyView();
}
function resetView() {
  const r = svg.getBoundingClientRect();
  const w = +readerImg.getAttribute('width') || 1;
  const h = +readerImg.getAttribute('height') || 1;
  view.scale = 1;
  view.tx = (r.width - w) / 2;
  view.ty = (r.height - h) / 2;
  applyView();
}

function setImage(url) {
  return new Promise(resolve => {
    const probe = new Image();
    probe.onload = () => {
      readerImg.setAttribute('href', url);
      readerImg.setAttribute('width', probe.naturalWidth);
      readerImg.setAttribute('height', probe.naturalHeight);
      $('#readerEmpty').style.display = 'none';
      fitView();
      renderEvidenceLayer();
      resolve(probe);
    };
    probe.onerror = () => resolve(null);
    probe.src = url;
  });
}

svg.addEventListener('wheel', e => {
  e.preventDefault();
  const r = svg.getBoundingClientRect();
  zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.25 : 0.8);
  renderEvidenceLayer();   // 证据框以像素矩形绘制在 readerG 之外，缩放后重画
}, { passive: false });

/* 同一组指针事件承担两种交互：框选模式拖矩形，否则平移画面 */
let drag = null;
let drawRectEl = null;
const NS = 'http://www.w3.org/2000/svg';

function svgPoint(e) {
  const r = svg.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

svg.addEventListener('pointerdown', e => {
  const pt = svgPoint(e);
  if (drawField) {
    drag = { kind: 'draw', x0: pt.x, y0: pt.y, x1: pt.x, y1: pt.y };
    drawRectEl = document.createElementNS(NS, 'rect');
    drawRectEl.setAttribute('class', 'evidence-draft');
    evidenceLayer.appendChild(drawRectEl);
  } else {
    drag = { kind: 'pan', x: e.clientX, y: e.clientY };
    svg.classList.add('panning');
  }
  svg.setPointerCapture(e.pointerId);
});
svg.addEventListener('pointermove', e => {
  if (!drag) return;
  if (drag.kind === 'pan') {
    view.tx += e.clientX - drag.x;
    view.ty += e.clientY - drag.y;
    drag = { kind: 'pan', x: e.clientX, y: e.clientY };
    applyView();
    renderEvidenceLayer();
    return;
  }
  const pt = svgPoint(e);
  drag.x1 = pt.x; drag.y1 = pt.y;
  const x = Math.min(drag.x0, pt.x), y = Math.min(drag.y0, pt.y);
  drawRectEl.setAttribute('x', x);
  drawRectEl.setAttribute('y', y);
  drawRectEl.setAttribute('width', Math.abs(pt.x - drag.x0));
  drawRectEl.setAttribute('height', Math.abs(pt.y - drag.y0));
});
svg.addEventListener('pointerup', () => {
  if (!drag) return;
  if (drag.kind === 'draw') finishDraw(drag);
  drag = null;
  svg.classList.remove('panning');
  if (drawRectEl) { drawRectEl.remove(); drawRectEl = null; }
});

async function finishDraw(d) {
  const field = drawField;
  const p = pageById(selectedId);
  const imgW = +readerImg.getAttribute('width') || 0;
  const imgH = +readerImg.getAttribute('height') || 0;
  setDrawMode(null);
  if (!p || !imgW || !imgH) return;
  const moved = Math.max(Math.abs(d.x1 - d.x0), Math.abs(d.y1 - d.y0));
  if (moved <= 3) {
    fieldMsg(field, '拖选范围太小，请在原图上拖出一个矩形', 'error');
    return;
  }
  const box = EvidenceGeom.proportionRect(
    { x: d.x0, y: d.y0 }, { x: d.x1, y: d.y1 }, view, imgW, imgH);
  const err = EvidenceGeom.validateBox(box, $(`#f_${field}`).value);
  if (err) { fieldMsg(field, err, 'error'); return; }
  await saveEvidence(p.id, field, box, $(`#f_${field}`).value.trim());
}

/* ---------------- 证据框：字段旁入口、绘制、查看原文 ---------------- */

function initEvidenceControls() {
  for (const f of EVIDENCE_FIELDS) {
    const input = $(`#f_${f}`);
    const wrap = document.createElement('span');
    wrap.className = 'evidence-tools';
    const drawBtn = document.createElement('button');
    drawBtn.type = 'button';
    drawBtn.className = 'evidence-btn draw';
    drawBtn.textContent = drawField === f ? '取消框选' : '框选原文';
    drawBtn.onclick = ev => {
      ev.preventDefault();
      if (drawField === f) setDrawMode(null);
      else enterDrawModeCheck(f);
    };
    const viewBtn = document.createElement('button');
    viewBtn.type = 'button';
    viewBtn.className = 'evidence-btn view';
    viewBtn.dataset.evidenceView = f;
    viewBtn.textContent = '查看原文';
    viewBtn.onclick = ev => { ev.preventDefault(); openEvidence(selectedId, f); };
    const msg = document.createElement('span');
    msg.className = 'field-msg';
    msg.dataset.fieldMsg = f;
    wrap.append(drawBtn, viewBtn, msg);
    input.closest('label').appendChild(wrap);
  }
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && drawField) setDrawMode(null);
  });
}

function setDrawMode(field) {
  drawField = field;
  svg.classList.toggle('drawing', !!field);
  syncEvidenceControls();
  if (field) {
    fieldMsg(field, `请在阅读区拖出「${CLUE_LABELS[field]}」对应的原文范围（Esc 取消）`);
  } else {
    $('#drawHint').style.display = 'none';
  }
}

function enterDrawModeCheck(field) {
  const p = pageById(selectedId);
  if (!p) return;
  const cur = $(`#f_${field}`).value.trim();
  if (!cur) {
    fieldMsg(field, '字段为空：请先填写线索并「保存线索」，再框选原文', 'error');
    return;
  }
  if (cur !== (p[field] || '')) {
    fieldMsg(field, '字段有未保存的修改：请先「保存线索」，再框选原文', 'error');
    return;
  }
  setDrawMode(field);
}

function syncEvidenceControls() {
  const p = pageById(selectedId);
  for (const f of EVIDENCE_FIELDS) {
    const wrap = $(`[data-field-msg="${f}"]`);
    const label = $(`#f_${f}`).closest('label');
    const drawBtn = $('.evidence-btn.draw', label);
    const viewBtn = $('.evidence-btn.view', label);
    const has = !!(p && p.evidence && p.evidence[f]);
    const filled = !!(p && (p[f] || '').trim());
    drawBtn.disabled = !p;
    drawBtn.classList.toggle('active', drawField === f);
    drawBtn.textContent = drawField === f ? '取消框选' : '框选原文';
    viewBtn.style.display = has ? '' : 'none';
    label.classList.toggle('has-evidence', has);
    label.classList.toggle('evidence-field-active', drawField === f);
    if (!drawField && (!filled || has)) wrap.textContent = '';
  }
  if (drawField) {
    $('#drawHint').style.display = '';
    $('#drawHint').textContent =
      `框选模式：正在为「${CLUE_LABELS[drawField]}」拖选原文证据，松开即保存（Esc 取消）`;
  }
}

function fieldMsg(field, text, kind = 'info') {
  const el = $(`[data-field-msg="${field}"]`);
  if (!el) return;
  el.textContent = text;
  el.className = 'field-msg' + (kind === 'error' ? ' error' : '');
}

async function saveEvidence(pid, field, box, value) {
  try {
    const res = await api(`/api/pages/${pid}/evidence/${field}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ box, value })
    });
    const p = pageById(pid);
    if (p) {
      p.evidence = Object.assign({}, p.evidence, res.evidence);  // 只替换该字段旧框
    }
    renderEvidenceLayer();
    syncEvidenceControls();
    fieldMsg(field, '证据框已保存（旧框已替换）');
    toast(`已保存 #${pid}「${CLUE_LABELS[field]}」的原文证据框`);
  } catch (err) {
    fieldMsg(field, err.message, 'error');
    toast(err.message, 'error');
  }
}

function imgSize() {
  return { w: +readerImg.getAttribute('width') || 0,
           h: +readerImg.getAttribute('height') || 0 };
}

function renderEvidenceLayer(flashField = null) {
  const layer = $('#evidenceLayer');
  layer.innerHTML = '';
  const p = pageById(selectedId);
  const { w: imgW, h: imgH } = imgSize();
  if (!p || !p.evidence || !imgW || !imgH || loadedStored !== p.stored_name) return;
  for (const [field, box] of Object.entries(p.evidence)) {
    const r = EvidenceGeom.toScreenRect(box, view, imgW, imgH);
    const el = document.createElementNS(NS, 'rect');
    el.setAttribute('class',
      `evidence-box evidence-${field}` + (field === flashField ? ' flash' : ''));
    el.setAttribute('x', r.x); el.setAttribute('y', r.y);
    el.setAttribute('width', Math.max(r.w, 2));
    el.setAttribute('height', Math.max(r.h, 2));
    el.dataset.field = field;
    el.style.cursor = 'pointer';
    el.appendChild(Object.assign(document.createElementNS(NS, 'title'),
      { textContent: `${CLUE_LABELS[field]}原文证据` }));
    el.addEventListener('pointerdown', ev => ev.stopPropagation());
    el.addEventListener('click', ev => {
      ev.stopPropagation();
      flashFieldOnce(field);
      fieldMsg(field, `此框为「${CLUE_LABELS[field]}」的原文证据；可重新「框选原文」替换`);
    });
    layer.appendChild(el);
  }
}

function flashFieldOnce(field) {
  renderEvidenceLayer(field);
  setTimeout(() => {
    const cur = $(`.evidence-box.${'evidence-' + field}.flash`);
    if (cur) cur.classList.remove('flash');
  }, 1600);
}

async function openEvidence(pid, field) {
  const p = pageById(pid);
  if (!p) { toast('页面不存在', 'error'); return; }
  setDrawMode(null);
  const box = p.evidence && p.evidence[field];
  if (!box) {
    toast(`#${pid} 的「${CLUE_LABELS[field]}」还没有证据框`, 'error');
    return;
  }
  if (selectedId !== pid) {
    selectedId = pid;
    await selectPage(pid, { keepView: false });
  }
  history.replaceState(null, '', `#p=${pid}&f=${field}`);
  centerOnBox(box, field);
}

function centerOnBox(box, field) {
  const { w: imgW, h: imgH } = imgSize();
  if (!imgW || !imgH) return;
  const r = svg.getBoundingClientRect();
  Object.assign(view, EvidenceGeom.centerView(box, imgW, imgH, r.width, r.height));
  applyView();
  renderEvidenceLayer(field);
  $('#readerPanel').scrollIntoView({ behavior: 'smooth', block: 'center' });
  setTimeout(() => {
    const cur = $(`.evidence-box.evidence-${field}.flash`);
    if (cur) cur.classList.remove('flash');
  }, 2000);
}

$$('.reader-toolbar [data-zoom]').forEach(btn => {
  btn.onclick = () => {
    const r = svg.getBoundingClientRect();
    const mode = btn.dataset.zoom;
    if (mode === 'in') zoomAt(r.width / 2, r.height / 2, 1.3);
    else if (mode === 'out') zoomAt(r.width / 2, r.height / 2, 1 / 1.3);
    else if (mode === 'fit') fitView();
    else resetView();
    renderEvidenceLayer();
  };
});

/* ================= 线索表单 ================= */

async function selectPage(id, opts = {}) {
  selectedId = id;
  setDrawMode(null);
  const p = pageById(id);
  if (!p) return;
  if (loadedStored !== p.stored_name || opts.keepView === false) {
    loadedStored = p.stored_name;
    await setImage(`/scans/${p.stored_name}`);
  }
  for (const f of CLUE_FIELDS) $(`#f_${f}`).value = p[f] || '';
  $('#formTitle').textContent = `—— ${pageLabel(p)}`;
  $('#saveCluesBtn').disabled = false;
  $('#formStatus').textContent = '';
  for (const f of EVIDENCE_FIELDS) fieldMsg(f, '');
  syncEvidenceControls();
  renderEvidenceLayer();
  renderThumbs();
}

function clearForm() {
  setDrawMode(null);
  loadedStored = null;
  for (const f of CLUE_FIELDS) $(`#f_${f}`).value = '';
  $('#formTitle').textContent = '';
  $('#saveCluesBtn').disabled = true;
  readerImg.setAttribute('href', '');
  $('#readerEmpty').style.display = 'flex';
  renderEvidenceLayer();
}

$('#clueForm').addEventListener('submit', async e => {
  e.preventDefault();
  if (!selectedId) return;
  const body = {};
  for (const f of CLUE_FIELDS) body[f] = $(`#f_${f}`).value.trim();
  try {
    const res = await api(`/api/pages/${selectedId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (res.changed.length === 0) {
      $('#formStatus').textContent = '内容无变化';
    } else {
      const fields = res.changed.map(f => CLUE_LABELS[f]).join('、');
      $('#formStatus').textContent = `已保存：${fields}`;
      toast(res.stale.length
        ? `线索已保存，${res.stale.length} 处相关接续已标记过期（其余排序不受影响）`
        : '线索已保存');
    }
    await refresh();
    await refreshAnalysis();
    for (const f of EVIDENCE_FIELDS) fieldMsg(f, '');  // 线索已保存，清除“先保存线索”等提示
  } catch (err) { toast(err.message, 'error'); }
});

/* ================= 排序区 ================= */

function joinInfo(fromId, toId) {
  if (!analysis) return null;
  return analysis.joins.find(j => j.from === fromId && j.to === toId) || null;
}

function renderBoard() {
  const board = $('#board');
  board.innerHTML = '';
  const placed = placedPages();
  if (!placed.length) {
    board.innerHTML = '<span class="hint" id="boardEmpty">排序区为空——从页库把页面「＋排序」放进来</span>';
    return;
  }
  placed.forEach((p, i) => {
    if (i > 0) board.appendChild(makeJoin(placed[i - 1].id, p.id));
    board.appendChild(makeBoardCard(p, i));
  });
}

function makeJoin(fromId, toId) {
  const el = document.createElement('div');
  const j = joinInfo(fromId, toId);
  const status = j ? j.status : 'none';
  const stale = j && j.stale;
  el.className = `join ${stale ? 'stale' : status}`;
  const tips = [];
  if (j && j.reasons.length) tips.push(...j.reasons);
  if (j && j.warnings.length) tips.push(...j.warnings.map(w => `疑点：${w}`));
  if (stale) tips.push('线索已修改，此接续待复核');
  if (!tips.length) tips.push('无直接接续线索');
  el.title = tips.join('\n');
  el.innerHTML = `<svg viewBox="0 0 24 24" width="30" height="30" fill="none"
      stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
      <path d="M3 12h14M13 6l6 6-6 6"/></svg>`;
  return el;
}

function makeBoardCard(p, idx) {
  const card = document.createElement('div');
  card.className = 'bcard' + (p.placement.locked ? ' locked' : '');
  card.draggable = true;
  card.dataset.id = p.id;

  const seq = document.createElement('span');
  seq.className = 'seq-no';
  seq.textContent = idx + 1;
  card.appendChild(seq);

  if (p.placement.stale) {
    const dot = document.createElement('span');
    dot.className = 'stale-dot';
    dot.textContent = '!';
    dot.title = '线索已修改，相关接续待复核——点击标记为已复核';
    dot.onclick = async () => {
      await api('/api/review', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page_ids: [p.id] })
      });
      toast(`#${p.id} 已复核`);
      await refresh(); await refreshAnalysis();
    };
    card.appendChild(dot);
  }

  const img = document.createElement('img');
  img.src = `/scans/${p.stored_name}`;
  card.appendChild(img);

  const label = document.createElement('div');
  label.className = 'blabel';
  label.textContent = `#${p.id} ${p.date_start || '日期未知'}·${p.edition || '?'}版`;
  card.appendChild(label);

  const tools = document.createElement('div');
  tools.className = 'btools';
  const lockBtn = document.createElement('button');
  lockBtn.textContent = p.placement.locked ? '🔒' : '🔓';
  lockBtn.title = p.placement.locked ? '已锁定（点击解锁）' : '确认无误后锁定';
  lockBtn.onclick = () => toggleLock(p);
  const viewBtn = document.createElement('button');
  viewBtn.textContent = '查看';
  viewBtn.onclick = () => selectPage(p.id);
  const outBtn = document.createElement('button');
  outBtn.textContent = '✕';
  outBtn.title = '移出排序区';
  outBtn.onclick = () => togglePlace(p);
  tools.append(lockBtn, viewBtn, outBtn);
  card.appendChild(tools);

  card.addEventListener('dragstart', e => {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(p.id));
    card.classList.add('dragging');
  });
  card.addEventListener('dragend', () => {
    card.classList.remove('dragging');
    $$('.bcard').forEach(c => c.classList.remove('drop-before', 'drop-after'));
  });
  card.addEventListener('dragover', e => {
    e.preventDefault();
    const rect = card.getBoundingClientRect();
    const before = e.clientX < rect.left + rect.width / 2;
    card.classList.toggle('drop-before', before);
    card.classList.toggle('drop-after', !before);
  });
  card.addEventListener('dragleave', () => card.classList.remove('drop-before', 'drop-after'));
  card.addEventListener('drop', async e => {
    e.preventDefault();
    const dragId = +e.dataTransfer.getData('text/plain');
    if (!dragId || dragId === p.id) return;
    const rect = card.getBoundingClientRect();
    const before = e.clientX < rect.left + rect.width / 2;
    const order = boardOrder().filter(id => id !== dragId);
    let pos = order.indexOf(p.id);
    if (!before) pos += 1;
    order.splice(pos, 0, dragId);
    await mutate('/api/arrange', { order }, '顺序已调整（可撤销）');
  });
  return card;
}

async function toggleLock(p) {
  await api('/api/lock', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ page_id: p.id, locked: !p.placement.locked })
  });
  toast(p.placement.locked ? `#${p.id} 已解锁` : `#${p.id} 已锁定（过期标记一并清除）`);
  await refresh(); await refreshAnalysis();
}

async function mutate(path, body, msg) {
  try {
    await api(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (msg) toast(msg);
    await refresh(); await refreshAnalysis();
  } catch (err) { toast(err.message, 'error'); }
}

/* ================= 分析面板 ================= */

function renderAnalysis() {
  const panel = $('#analysisPanel');
  panel.innerHTML = '';
  if (!analysis) return;

  // —— 候选顺序（多解并列）——
  const solSec = document.createElement('div');
  solSec.className = 'ana-section';
  solSec.innerHTML = '<h3>候选顺序（依据并列给出）</h3>';
  if (!analysis.solutions.length) {
    solSec.innerHTML += '<p class="hint">暂无可成链的页面。请先登记段首/段尾短句或「上接/下转」指向。</p>';
  }
  for (const sol of analysis.solutions) {
    const head = document.createElement('p');
    head.className = 'hint';
    head.textContent = `候选链：${sol.page_ids.length} 页（${sol.page_ids.map(id => '#' + id).join('、')}）`;
    solSec.appendChild(head);
    sol.orders.forEach((o, i) => solSec.appendChild(makeSolution(o, i)));
  }
  if (analysis.singletons.length) {
    const p = document.createElement('p');
    p.className = 'hint';
    p.textContent = '尚未发现关联的页面：' + analysis.singletons.map(id => '#' + id).join('、');
    solSec.appendChild(p);
  }
  panel.appendChild(solSec);

  // —— 矛盾与疑点 ——
  const conSec = document.createElement('div');
  conSec.className = 'ana-section';
  conSec.innerHTML = '<h3>矛盾与疑点（定位到具体线索）</h3>';
  if (!analysis.contradictions.length) {
    conSec.innerHTML += '<p class="hint">未发现矛盾。</p>';
  }
  for (const c of analysis.contradictions) {
    const div = document.createElement('div');
    div.className = `card-msg ${c.severity === 'warning' ? 'warning' : ''}`;
    const clueTags = (c.clues || []).map(f => {
      let tag = `<span class="clue-tag">${CLUE_LABELS[f] || f}</span>`;
      // 已框选原文的线索字段：标签旁出现「查看原文」
      if (EVIDENCE_FIELDS.includes(f)) {
        for (const pid of c.pages) {
          const p = pageById(pid);
          if (p && p.evidence && p.evidence[f]) {
            tag += ` <button class="locate-btn evidence-link" data-pid="${pid}" data-field="${f}">查看原文（#${pid}）</button>`;
          }
        }
      }
      return tag;
    }).join('');
    const evidence = (c.evidence || []).map(e => `<div class="sub">依据：${escapeHtml(e)}</div>`).join('');
    const locates = c.pages.map(pid =>
      `<button class="locate-btn" data-pid="${pid}">定位 #${pid}</button>`).join('');
    div.innerHTML = `<div class="msg">${escapeHtml(c.message)}</div>
      ${evidence}<div>${clueTags}</div><div style="margin-top:4px">${locates}</div>`;
    conSec.appendChild(div);
  }
  panel.appendChild(conSec);

  // —— 缺口 ——
  const gapSec = document.createElement('div');
  gapSec.className = 'ana-section';
  gapSec.innerHTML = '<h3>缺页与最小缺口范围</h3>';
  if (!analysis.gaps.length) {
    gapSec.innerHTML += '<p class="hint">未发现缺口。</p>';
  }
  for (const g of analysis.gaps) {
    const div = document.createElement('div');
    div.className = 'card-msg gap';
    const range = `缺口位置：${g.after ? '#' + g.after + ' 之后' : '开头之前'}` +
                  `${g.before ? '、#' + g.before + ' 之前' : '（其后页面未见）'}`;
    const locates = [g.after, g.before].filter(x => x != null)
      .map(pid => `<button class="locate-btn" data-pid="${pid}">定位 #${pid}</button>`).join('');
    div.innerHTML = `<div class="msg">${escapeHtml(g.message)}</div>
      <div class="sub">${range}；最小缺口：<b>${g.min_missing}</b> 页/期（${escapeHtml(g.basis)}）</div>
      <div style="margin-top:4px">${locates}</div>`;
    gapSec.appendChild(div);
  }
  panel.appendChild(gapSec);

  $$('.locate-btn', panel).forEach(btn => {
    btn.onclick = () => {
      selectPage(+btn.dataset.pid);
      $('#readerPanel').scrollIntoView({ behavior: 'smooth' });
    };
  });
  $$('.evidence-link', panel).forEach(btn => {
    btn.onclick = () => openEvidence(+btn.dataset.pid, btn.dataset.field);
  });
}

function makeSolution(order, idx) {
  const div = document.createElement('div');
  div.className = 'solution';
  const head = document.createElement('div');
  head.className = 'sol-head';
  head.innerHTML = `<b>方案 ${idx + 1}</b><span class="score">得分 ${order.score}</span>`;
  const applyBtn = document.createElement('button');
  applyBtn.className = 'btn small';
  applyBtn.textContent = '采用此顺序';
  applyBtn.onclick = () => applySolution(order.order);
  head.appendChild(applyBtn);
  div.appendChild(head);

  const chips = document.createElement('div');
  chips.className = 'chips';
  order.order.forEach((pid, i) => {
    if (i > 0) {
      const arrow = document.createElement('span');
      arrow.className = 'chip-arrow';
      arrow.textContent = '→';
      chips.appendChild(arrow);
    }
    const chip = document.createElement('span');
    chip.className = 'chip';
    const p = pageById(pid);
    chip.textContent = p ? pageLabel(p) : `#${pid}`;
    chips.appendChild(chip);
  });
  div.appendChild(chips);

  const det = document.createElement('details');
  det.className = 'rationale';
  const items = order.steps.map(s => {
    const a = pageById(s.from), b = pageById(s.to);
    const names = escapeHtml(`${a ? pageLabel(a) : '#' + s.from} → ${b ? pageLabel(b) : '#' + s.to}`);
    if (!s.reasons.length) return `<li class="step-none">${names}：无直接线索（仅日期不冲突）</li>`;
    const why = s.reasons.map(escapeHtml).join('；');
    const warn = s.warnings.map(w => `；疑点：${escapeHtml(w)}`).join('');
    return `<li>${names}：${why}${warn}</li>`;
  }).join('');
  det.innerHTML = `<summary>查看逐步依据</summary><ul>${items}</ul>`;
  div.appendChild(det);
  return div;
}

/* 采用候选顺序：锁定页保持当前位置，其余按方案相对顺序填充 */
function mergeSolution(board, proposed, locked) {
  const lockedSet = new Set(locked);
  const proposedSet = new Set(proposed);
  const queue = proposed.filter(id => !lockedSet.has(id));
  const stream = [];
  for (const id of board) {
    if (lockedSet.has(id)) continue;
    if (proposedSet.has(id)) { if (queue.length) stream.push(queue.shift()); }
    else stream.push(id);
  }
  while (queue.length) stream.push(queue.shift());
  const result = [];
  let si = 0;
  for (const id of board) {
    if (lockedSet.has(id)) result.push(id);
    else result.push(stream[si++]);
  }
  while (si < stream.length) result.push(stream[si++]);
  return result.filter(x => x !== undefined && x != null);
}

async function applySolution(proposed) {
  const merged = mergeSolution(boardOrder(), proposed, lockedIds());
  await mutate('/api/arrange', { order: merged }, '已采用候选顺序（锁定页保持不动，可撤销）');
}

/* ================= 顶栏动作 ================= */

$('#fileInput').addEventListener('change', async e => {
  if (!e.target.files.length) return;
  const fd = new FormData();
  for (const f of e.target.files) fd.append('files', f);
  try {
    const res = await api('/api/pages', { method: 'POST', body: fd });
    let msg = `已导入 ${res.created.length} 页`;
    if (res.skipped.length) msg += `，跳过不支持的格式 ${res.skipped.length} 个`;
    toast(msg);
    e.target.value = '';
    await refresh(); await refreshAnalysis();
  } catch (err) { toast(err.message, 'error'); }
});

$('#undoBtn').onclick = async () => {
  try {
    const res = await api('/api/undo', { method: 'POST' });
    toast(`已撤销：${res.label}`);
    await refresh(); await refreshAnalysis();
  } catch (err) { toast(err.message, 'error'); }
};

$('#analyzeBtn').onclick = async () => {
  await refreshAnalysis();
  $('#analysisPanel').scrollIntoView({ behavior: 'smooth' });
  toast('分析完成：候选顺序、矛盾与缺口已更新');
};

$('#reviewAllBtn').onclick = async () => {
  await api('/api/review', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ all: true })
  });
  toast('全部过期标记已复核清除');
  await refresh(); await refreshAnalysis();
};

/* ================= 工具 ================= */

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ================= 深链：#p=<页id>&f=<字段> → 打开页面并居中高亮证据框 ================= */

function parseHash() {
  const m = /p=(\d+)(?:&f=(\w+))?/.exec(location.hash || '');
  if (!m) return null;
  return { pid: +m[1], field: m[2] || null };
}

async function handleHash() {
  const h = parseHash();
  if (!h) return;
  if (!EVIDENCE_FIELDS.includes(h.field)) return;
  await openEvidence(h.pid, h.field);
}

/* ================= 启动 ================= */

(async () => {
  try {
    initEvidenceControls();
    await refresh();
    await refreshAnalysis();
    await handleHash();
  } catch (err) {
    toast('初始化失败：' + err.message, 'error');
  }
})();

window.addEventListener('hashchange', handleHash);

/* 供前端测试读取/驱动内部状态（浏览器中无副作用） */
window.appHooks = {
  selectPage, openEvidence,
  get view() { return view; },
  get selectedId() { return selectedId; }
};
