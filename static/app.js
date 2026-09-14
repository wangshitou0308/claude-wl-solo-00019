/* 剪报连载接续工作台 —— 前端逻辑 */
'use strict';

const CLUE_FIELDS = ['date_start', 'date_end', 'edition', 'section', 'column_name',
                     'first_phrase', 'last_phrase', 'cont_from', 'cont_to', 'notes'];
const CLUE_LABELS = {
  date_start: '日期起', date_end: '日期止', edition: '版次', section: '正/副刊',
  column_name: '栏目名', first_phrase: '段首短句', last_phrase: '段尾短句',
  cont_from: '上接指向', cont_to: '下转指向', notes: '备注'
};

let state = { pages: [], undo_depth: 0 };
let analysis = null;
let selectedId = null;

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
  if (selectedId && !pageById(selectedId)) { selectedId = null; clearForm(); }
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
  const probe = new Image();
  probe.onload = () => {
    readerImg.setAttribute('href', url);
    readerImg.setAttribute('width', probe.naturalWidth);
    readerImg.setAttribute('height', probe.naturalHeight);
    $('#readerEmpty').style.display = 'none';
    fitView();
  };
  probe.src = url;
}

svg.addEventListener('wheel', e => {
  e.preventDefault();
  const r = svg.getBoundingClientRect();
  zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.25 : 0.8);
}, { passive: false });

let panning = null;
svg.addEventListener('pointerdown', e => {
  panning = { x: e.clientX, y: e.clientY };
  svg.setPointerCapture(e.pointerId);
  svg.classList.add('panning');
});
svg.addEventListener('pointermove', e => {
  if (!panning) return;
  view.tx += e.clientX - panning.x;
  view.ty += e.clientY - panning.y;
  panning = { x: e.clientX, y: e.clientY };
  applyView();
});
svg.addEventListener('pointerup', () => { panning = null; svg.classList.remove('panning'); });

$$('.reader-toolbar [data-zoom]').forEach(btn => {
  btn.onclick = () => {
    const r = svg.getBoundingClientRect();
    const mode = btn.dataset.zoom;
    if (mode === 'in') zoomAt(r.width / 2, r.height / 2, 1.3);
    else if (mode === 'out') zoomAt(r.width / 2, r.height / 2, 1 / 1.3);
    else if (mode === 'fit') fitView();
    else resetView();
  };
});

/* ================= 线索表单 ================= */

function selectPage(id) {
  selectedId = id;
  const p = pageById(id);
  if (!p) return;
  setImage(`/scans/${p.stored_name}`);
  for (const f of CLUE_FIELDS) $(`#f_${f}`).value = p[f] || '';
  $('#formTitle').textContent = `—— ${pageLabel(p)}`;
  $('#saveCluesBtn').disabled = false;
  $('#formStatus').textContent = '';
  renderThumbs();
}

function clearForm() {
  for (const f of CLUE_FIELDS) $(`#f_${f}`).value = '';
  $('#formTitle').textContent = '';
  $('#saveCluesBtn').disabled = true;
  readerImg.setAttribute('href', '');
  $('#readerEmpty').style.display = 'flex';
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
    const clueTags = (c.clues || []).map(f =>
      `<span class="clue-tag">${CLUE_LABELS[f] || f}</span>`).join('');
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

/* ================= 启动 ================= */

(async () => {
  try {
    await refresh();
    await refreshAnalysis();
  } catch (err) {
    toast('初始化失败：' + err.message, 'error');
  }
})();
