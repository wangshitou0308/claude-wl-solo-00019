"""剪报连载接续工作台 —— Flask 后端。

用法：pip install -r requirements.txt 后执行 python app.py，
浏览器访问 http://127.0.0.1:5000

扫描图保存在 ./scans，全部数据保存在 ./clippings.db（SQLite），
刷新或重启后进度自动恢复。
"""
import json
import math
import os
import sqlite3
import uuid
from datetime import datetime

from flask import (Flask, Response, g, jsonify, render_template, request,
                   send_from_directory)

import solver

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCANS_DIR = os.path.join(BASE_DIR, 'scans')
DB_PATH = os.path.join(BASE_DIR, 'clippings.db')
ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 300 * 1024 * 1024  # 扫描图较大

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    orig_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    date_start TEXT NOT NULL DEFAULT '',
    date_end TEXT NOT NULL DEFAULT '',
    edition TEXT NOT NULL DEFAULT '',
    section TEXT NOT NULL DEFAULT '未知',
    column_name TEXT NOT NULL DEFAULT '',
    first_phrase TEXT NOT NULL DEFAULT '',
    last_phrase TEXT NOT NULL DEFAULT '',
    cont_from TEXT NOT NULL DEFAULT '',
    cont_to TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS placements (
    page_id INTEGER PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    stale INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS undo_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (page_id, field)
);
"""

CLUE_FIELDS = ['date_start', 'date_end', 'edition', 'section', 'column_name',
               'first_phrase', 'last_phrase', 'cont_from', 'cont_to', 'notes']

# 可保存证据框的字段：段首、段尾及上接、下转线索
EVIDENCE_FIELDS = ['first_phrase', 'last_phrase', 'cont_from', 'cont_to']


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(SCANS_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def fetch_pages(db):
    pages = [dict(r) for r in db.execute('SELECT * FROM pages ORDER BY id')]
    attach_evidence(db, pages)
    return pages


def fetch_evidence_map(db):
    """读取全部证据框，结构：{page_id: {field: {x,y,w,h}}}（坐标为原图比例）。"""
    out = {}
    for r in db.execute('SELECT page_id, field, x, y, w, h FROM evidence'):
        box = {'x': r['x'], 'y': r['y'], 'w': r['w'], 'h': r['h']}
        out.setdefault(r['page_id'], {})[r['field']] = box
    return out


def attach_evidence(db, pages):
    """把证据框挂到各页；未框选的页面维持原响应（不出现 evidence 键）。"""
    ev = fetch_evidence_map(db)
    for p in pages:
        if p['id'] in ev:
            p['evidence'] = ev[p['id']]


def fetch_placements(db):
    return [dict(r) for r in db.execute(
        'SELECT page_id, position, locked, stale FROM placements ORDER BY position')]


def push_undo(db, label):
    """在改动排序前保存快照，供撤销恢复。"""
    snapshot = json.dumps(fetch_placements(db), ensure_ascii=False)
    db.execute('INSERT INTO undo_log (label, snapshot, created_at) VALUES (?,?,?)',
               (label, snapshot, now()))
    db.execute('DELETE FROM undo_log WHERE id NOT IN '
               '(SELECT id FROM undo_log ORDER BY id DESC LIMIT 100)')


# ---------------------------------------------------------------- 页面与静态资源

@app.get('/')
def index():
    return render_template('index.html')


@app.get('/scans/<path:filename>')
def scans(filename):
    return send_from_directory(SCANS_DIR, filename)


# ---------------------------------------------------------------- 状态与页面

@app.get('/api/state')
def api_state():
    db = get_db()
    pages = fetch_pages(db)
    pl = {p['page_id']: p for p in fetch_placements(db)}
    for p in pages:
        p['placement'] = pl.get(p['id'])
    undo_depth = db.execute('SELECT COUNT(*) c FROM undo_log').fetchone()['c']
    return jsonify({'pages': pages, 'undo_depth': undo_depth})


@app.post('/api/pages')
def api_upload():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': '未收到文件'}), 400
    db = get_db()
    created, skipped = [], []
    for f in files:
        ext = os.path.splitext(f.filename or '')[1].lower()
        if ext not in ALLOWED_EXT:
            skipped.append(f.filename)
            continue
        stored = f'{uuid.uuid4().hex}{ext}'
        f.save(os.path.join(SCANS_DIR, stored))
        cur = db.execute('INSERT INTO pages (orig_name, stored_name, created_at) '
                         'VALUES (?,?,?)', (f.filename, stored, now()))
        created.append(cur.lastrowid)
    db.commit()
    return jsonify({'ok': True, 'created': created, 'skipped': skipped})


@app.patch('/api/pages/<int:pid>')
def api_update_page(pid):
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    row = db.execute('SELECT * FROM pages WHERE id=?', (pid,)).fetchone()
    if row is None:
        return jsonify({'error': '页面不存在'}), 404
    changed = [f for f in CLUE_FIELDS
               if f in data and str(data.get(f) or '') != (row[f] or '')]
    stale = []
    if changed:
        db.execute(
            'UPDATE pages SET ' + ', '.join(f'{f}=?' for f in changed) + ' WHERE id=?',
            [str(data.get(f) or '') for f in changed] + [pid])
        # 仅使相关接续过期：本页及排序区中相邻两页，其余排序不受影响
        pl = db.execute('SELECT position FROM placements WHERE page_id=?', (pid,)).fetchone()
        if pl:
            pos = pl['position']
            stale = [r['page_id'] for r in db.execute(
                'SELECT page_id FROM placements WHERE position IN (?,?,?)',
                (pos - 1, pos, pos + 1))]
            db.executemany(
                'UPDATE placements SET stale=1, updated_at=? WHERE page_id=?',
                [(now(), x) for x in stale])
        db.commit()
    return jsonify({'ok': True, 'changed': changed, 'stale': stale})


@app.put('/api/pages/<int:pid>/evidence/<field>')
def api_save_evidence(pid, field):
    """保存（替换）某页某线索字段的证据框。

    坐标一律为原图比例（0~1）；重画时只 UPSERT 该字段一行，旧框自然被替换，
    其他字段的框不受影响。参数不合法时直接说明原因且不写入。
    """
    data = request.get_json(force=True, silent=True) or {}
    if field not in EVIDENCE_FIELDS:
        return jsonify({'error': f'字段「{field}」不支持框选证据'}), 400
    db = get_db()
    row = db.execute('SELECT 1 FROM pages WHERE id=?', (pid,)).fetchone()
    if row is None:
        return jsonify({'error': '页面不存在'}), 404
    val = data.get('value')
    if not isinstance(val, str) or not val.strip():
        return jsonify({'error': '字段为空：请先在该字段填写线索并保存后再框选原文'}), 400

    box, err = parse_box(data.get('box'))
    if err:
        return jsonify({'error': err}), 400
    db.execute(
        'INSERT INTO evidence (page_id, field, x, y, w, h, updated_at) '
        'VALUES (?,?,?,?,?,?,?) '
        'ON CONFLICT(page_id, field) DO UPDATE SET '
        'x=excluded.x, y=excluded.y, w=excluded.w, h=excluded.h, updated_at=excluded.updated_at',
        (pid, field, box['x'], box['y'], box['w'], box['h'], now()))
    db.commit()
    return jsonify({'ok': True, 'page_id': pid, 'field': field,
                    'evidence': {field: box}})


def parse_box(raw, eps=1e-6):
    """校验前端换算后的比例框，返回 (box, None) 或 (None, 原因)。"""
    if not isinstance(raw, dict):
        return None, '坐标格式不正确：缺少矩形数据'
    coords = {}
    for k in ('x', 'y', 'w', 'h'):
        v = raw.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None, f'坐标 {k} 必须是数字'
        coords[k] = round(float(v), 6)
    x, y, w, h = coords['x'], coords['y'], coords['w'], coords['h']
    if not math.isfinite(x) or not math.isfinite(y) \
            or not math.isfinite(w) or not math.isfinite(h):
        return None, '坐标必须是有限数字'
    if w <= 0 or h <= 0:
        return None, '矩形宽高必须为正数'
    if x < -eps or y < -eps or x + w > 1 + eps or y + h > 1 + eps:
        return None, '坐标越界：矩形必须完全落在原图范围内（0~1）'
    return {'x': min(max(x, 0.0), 1.0), 'y': min(max(y, 0.0), 1.0),
            'w': min(w, 1.0), 'h': min(h, 1.0)}, None


@app.delete('/api/pages/<int:pid>')
def api_delete_page(pid):
    db = get_db()
    row = db.execute('SELECT stored_name FROM pages WHERE id=?', (pid,)).fetchone()
    if row is None:
        return jsonify({'error': '页面不存在'}), 404
    push_undo(db, f'删除页面 #{pid}')
    db.execute('DELETE FROM pages WHERE id=?', (pid,))
    db.commit()
    try:
        os.remove(os.path.join(SCANS_DIR, row['stored_name']))
    except OSError:
        pass
    return jsonify({'ok': True})


# ---------------------------------------------------------------- 排序 / 锁定 / 复核 / 撤销

@app.post('/api/arrange')
def api_arrange():
    """整体重排排序区；未列入的页面回到页库。可撤销。"""
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    valid = {r['id'] for r in db.execute('SELECT id FROM pages')}
    order, seen = [], set()
    for x in data.get('order') or []:
        try:
            x = int(x)
        except (TypeError, ValueError):
            continue
        if x in valid and x not in seen:
            seen.add(x)
            order.append(x)
    push_undo(db, '调整排序')
    old = {r['page_id']: r for r in db.execute('SELECT * FROM placements')}
    db.execute('DELETE FROM placements')
    for i, pid in enumerate(order):
        o = old.get(pid)
        db.execute('INSERT INTO placements (page_id, position, locked, stale, updated_at) '
                   'VALUES (?,?,?,?,?)',
                   (pid, i, o['locked'] if o else 0, o['stale'] if o else 0, now()))
    db.commit()
    return jsonify({'ok': True, 'order': order})


@app.post('/api/lock')
def api_lock():
    """锁定/解锁确认页；锁定时同时清除其过期标记。"""
    data = request.get_json(force=True, silent=True) or {}
    pid, locked = int(data.get('page_id')), 1 if data.get('locked') else 0
    db = get_db()
    cur = db.execute(
        'UPDATE placements SET locked=?, stale=CASE WHEN ?=1 THEN 0 ELSE stale END, '
        'updated_at=? WHERE page_id=?', (locked, locked, now(), pid))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({'error': '该页尚未放入排序区'}), 404
    return jsonify({'ok': True})


@app.post('/api/review')
def api_review():
    """复核后清除过期标记（单页、多页或全部）。"""
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    if data.get('all'):
        db.execute('UPDATE placements SET stale=0')
    else:
        ids = [int(x) for x in data.get('page_ids') or []]
        db.executemany('UPDATE placements SET stale=0 WHERE page_id=?',
                       [(i,) for i in ids])
    db.commit()
    return jsonify({'ok': True})


@app.post('/api/undo')
def api_undo():
    """撤销上一次排序改动（含删除页面造成的排序变化）。"""
    db = get_db()
    row = db.execute('SELECT * FROM undo_log ORDER BY id DESC LIMIT 1').fetchone()
    if row is None:
        return jsonify({'error': '没有可撤销的操作'}), 400
    snap = json.loads(row['snapshot'])
    valid = {r['id'] for r in db.execute('SELECT id FROM pages')}
    db.execute('DELETE FROM placements')
    i = 0
    for item in snap:
        if item['page_id'] not in valid:
            continue
        db.execute('INSERT INTO placements (page_id, position, locked, stale, updated_at) '
                   'VALUES (?,?,?,?,?)',
                   (item['page_id'], i, item['locked'], item['stale'], now()))
        i += 1
    db.execute('DELETE FROM undo_log WHERE id=?', (row['id'],))
    db.commit()
    return jsonify({'ok': True, 'label': row['label']})


# ---------------------------------------------------------------- 分析与导出

@app.get('/api/analysis')
def api_analysis():
    db = get_db()
    result = solver.analyze(fetch_pages(db), fetch_placements(db))
    return jsonify(result)


@app.get('/api/export')
def api_export():
    """导出含阅读顺序、来源与缺口的 JSON 索引。"""
    db = get_db()
    pages = fetch_pages(db)
    placements = fetch_placements(db)
    by_id = {p['id']: p for p in pages}
    result = solver.analyze(pages, placements)

    reading = []
    for i, pl in enumerate(placements):
        p = by_id.get(pl['page_id'])
        if p is None:
            continue
        item = {
            'seq': i + 1, 'page_id': p['id'],
            'source_file': p['orig_name'],
            'stored_file': f"scans/{p['stored_name']}",
            'date_start': p['date_start'], 'date_end': p['date_end'],
            'edition': p['edition'], 'section': p['section'],
            'column_name': p['column_name'],
            'locked': bool(pl['locked']), 'stale': bool(pl['stale'])}
        if p.get('evidence'):
            item['evidence'] = p['evidence']
        reading.append(item)
    placed_ids = {pl['page_id'] for pl in placements}
    unplaced = []
    for p in pages:
        if p['id'] in placed_ids:
            continue
        item = {'page_id': p['id'], 'source_file': p['orig_name']}
        if p.get('evidence'):
            item['evidence'] = p['evidence']
        unplaced.append(item)
    payload = {
        'generated_at': now(),
        'reading_order': reading,
        'unplaced': unplaced,
        'links': result['links'],
        'gaps': result['gaps'],
        'contradictions': result['contradictions'],
        'rules_note': '同版次（版次重用）与同栏目名不作为相邻依据；'
                      '接续依据为日期先后、正/副刊归属、接续短句与上接/下转指向。',
    }
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return Response(json.dumps(payload, ensure_ascii=False, indent=2),
                    mimetype='application/json; charset=utf-8',
                    headers={'Content-Disposition':
                             f'attachment; filename=serial_index_{ts}.json'})


init_db()

if __name__ == '__main__':
    print('剪报连载接续工作台已启动，请在浏览器打开： http://127.0.0.1:5000')
    app.run(host='127.0.0.1', port=5000)
