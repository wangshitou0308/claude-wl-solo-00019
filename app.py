"""剪报连载接续工作台 —— Flask 后端。

用法：pip install -r requirements.txt 后执行 python app.py，
浏览器访问 http://127.0.0.1:5000

扫描图保存在 ./scans，全部数据保存在 ./clippings.db（SQLite），
刷新或重启后进度自动恢复。
"""
import json
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
"""

CLUE_FIELDS = ['date_start', 'date_end', 'edition', 'section', 'column_name',
               'first_phrase', 'last_phrase', 'cont_from', 'cont_to', 'notes']


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
    return [dict(r) for r in db.execute('SELECT * FROM pages ORDER BY id')]


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
        reading.append({
            'seq': i + 1, 'page_id': p['id'],
            'source_file': p['orig_name'],
            'stored_file': f"scans/{p['stored_name']}",
            'date_start': p['date_start'], 'date_end': p['date_end'],
            'edition': p['edition'], 'section': p['section'],
            'column_name': p['column_name'],
            'locked': bool(pl['locked']), 'stale': bool(pl['stale'])})
    placed_ids = {pl['page_id'] for pl in placements}
    payload = {
        'generated_at': now(),
        'reading_order': reading,
        'unplaced': [{'page_id': p['id'], 'source_file': p['orig_name']}
                     for p in pages if p['id'] not in placed_ids],
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
