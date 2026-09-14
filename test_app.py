"""后端接口冒烟测试（临时目录，不影响真实数据）：python test_app.py"""
import base64
import io
import json
import os
import sqlite3
import tempfile

import app as appmod

tmp = tempfile.mkdtemp(prefix='clippings_test_')
appmod.DB_PATH = os.path.join(tmp, 't.db')
appmod.SCANS_DIR = os.path.join(tmp, 'scans')
appmod.init_db()

client = appmod.app.test_client()
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')

CLUES = [
    dict(date_start='1953-04-01', edition='3', section='正刊', column_name='连载',
         last_phrase='他转身走进小巷', cont_to='下转第5版'),
    dict(date_start='1953-04-01', edition='5', section='正刊', column_name='连载',
         first_phrase='走进小巷深处', last_phrase='谁知天色骤变', cont_from='上接第3版'),
    dict(date_start='1953-04-02', edition='3', section='正刊', column_name='连载',
         first_phrase='天色骤变之后'),
]


def upload(name):
    res = client.post('/api/pages', data={'files': [(io.BytesIO(PNG), name)]},
                      content_type='multipart/form-data')
    assert res.status_code == 200, res.data
    return res.get_json()['created'][0]


def patch(pid, clues):
    res = client.patch(f'/api/pages/{pid}', json=clues)
    assert res.status_code == 200, res.data
    return res.get_json()


def state_pages():
    return {p['id']: p for p in client.get('/api/state').get_json()['pages']}


def put_evidence(pid, field, box, value='线索文字'):
    return client.put(f'/api/pages/{pid}/evidence/{field}',
                      json={'box': box, 'value': value})


def main():
    ids = [upload(f'page{i}.png') for i in range(1, 4)]
    assert ids == [1, 2, 3]

    for pid, clues in zip(ids, CLUES):
        patch(pid, clues)

    # 排序
    res = client.post('/api/arrange', json={'order': [3, 1, 2]})
    assert res.get_json()['order'] == [3, 1, 2]

    # 撤销 → 恢复空排序
    res = client.post('/api/undo')
    assert res.get_json()['ok']
    state = client.get('/api/state').get_json()
    assert all(p['placement'] is None for p in state['pages'])

    # 重新排序并锁定
    client.post('/api/arrange', json={'order': [1, 2, 3]})
    res = client.post('/api/lock', json={'page_id': 2, 'locked': True})
    assert res.get_json()['ok']

    # 修改线索 → 仅相邻接续过期（1、2、3 相邻，改 2 应使三者都标过期）
    res = patch(2, dict(last_phrase='谁知天色骤变（改）'))
    assert set(res['stale']) == {1, 2, 3}, res
    state = client.get('/api/state').get_json()
    stale_ids = {p['id'] for p in state['pages'] if p['placement'] and p['placement']['stale']}
    assert stale_ids == {1, 2, 3}

    # 复核清除
    client.post('/api/review', json={'page_ids': [1, 3]})
    state = client.get('/api/state').get_json()
    stale_ids = {p['id'] for p in state['pages'] if p['placement'] and p['placement']['stale']}
    assert stale_ids == {2}

    # ---------------- 证据框 ----------------
    # 未框选的页面维持原响应：不出现 evidence 键
    before = state_pages()
    assert 'evidence' not in before[1]
    assert 'evidence' not in before[3]

    # 保存（坐标为原图比例 0~1；页面 1 的 last_phrase 已填写）
    box1 = {'x': 0.1, 'y': 0.2, 'w': 0.3, 'h': 0.4}
    res = put_evidence(1, 'last_phrase', box1, value=before[1]['last_phrase'])
    assert res.status_code == 200, res.data
    assert res.get_json()['evidence'] == {'last_phrase': box1}

    after = state_pages()
    assert after[1]['evidence'] == {'last_phrase': box1}
    assert 'evidence' not in after[2] and 'evidence' not in after[3]

    # 同字段重画只替换该字段旧框（UPSERT，不新增行）
    box1b = {'x': 0.15, 'y': 0.25, 'w': 0.2, 'h': 0.3}
    assert put_evidence(1, 'last_phrase', box1b, value='x').status_code == 200
    p1 = state_pages()[1]
    assert p1['evidence'] == {'last_phrase': box1b}, p1['evidence']

    # 页面 1 再加一个字段；页面 2 两个字段——各字段框互不影响
    assert put_evidence(1, 'cont_to', {'x': 0.0, 'y': 0.0, 'w': 1.0, 'h': 1.0},
                        value='下转第5版').status_code == 200
    box2a = {'x': 0.01, 'y': 0.02, 'w': 0.5, 'h': 0.6}
    box2b = {'x': 0.2, 'y': 0.3, 'w': 0.1, 'h': 0.1}
    assert put_evidence(2, 'first_phrase', box2a, value='走进小巷深处').status_code == 200
    assert put_evidence(2, 'cont_from', box2b, value='上接第3版').status_code == 200
    p2 = state_pages()[2]
    assert p2['evidence'] == {'first_phrase': box2a, 'cont_from': box2b}, p2['evidence']

    # 非法请求：失败前后读取结果一致，证明写入没有发生
    snapshot = json.dumps(client.get('/api/state').get_json(),
                          ensure_ascii=False, sort_keys=True)
    ok_box = {'x': 0.1, 'y': 0.1, 'w': .2, 'h': .2}
    bad_cases = [
        # (pid, field, box, value, 预期状态码)
        (1, 'notes', ok_box, '备注', 400),                          # 字段不支持框选
        (99, 'last_phrase', ok_box, '线索文字', 404),               # 页面不存在
        (1, 'last_phrase', ok_box, '', 400),                        # 字段为空
        (1, 'last_phrase', {'x': .1, 'y': .1, 'w': 0, 'h': .2}, 'x', 400),   # 宽非正
        (1, 'last_phrase', {'x': .1, 'y': .1, 'w': .2, 'h': -.1}, 'x', 400), # 高非正
        (1, 'last_phrase', {'x': 1.05, 'y': 0, 'w': .1, 'h': .1}, 'x', 400), # x 越界
        (1, 'last_phrase', {'x': .9, 'y': .9, 'w': .2, 'h': .2}, 'x', 400),  # 右下角越界
        (1, 'last_phrase', {'x': '0', 'y': 0, 'w': .2, 'h': .2}, 'x', 400),  # 非数字
        (1, 'last_phrase', None, 'x', 400),                                   # 缺少矩形
    ]
    for pid, field, box, val, code in bad_cases:
        r = put_evidence(pid, field, box, value=val)
        assert r.status_code == code, (pid, field, r.status_code, r.data)
    snapshot2 = json.dumps(client.get('/api/state').get_json(),
                           ensure_ascii=False, sort_keys=True)
    assert snapshot == snapshot2, '失败的保存不得改变任何已有证据框'

    # 边界 0/1 合法
    assert put_evidence(3, 'first_phrase',
                        {'x': 0, 'y': 0, 'w': 1, 'h': 1}, value='天色骤变之后').status_code == 200

    # 导出 JSON 索引返回同一证据结构
    res = client.get('/api/export')
    payload = json.loads(res.data)
    ev_by_page = {r['page_id']: r.get('evidence') for r in payload['reading_order']}
    assert ev_by_page[1] == {'last_phrase': box1b,
                             'cont_to': {'x': 0, 'y': 0, 'w': 1, 'h': 1}}
    assert ev_by_page[2] == {'first_phrase': box2a, 'cont_from': box2b}
    assert ev_by_page[3] == {'first_phrase': {'x': 0, 'y': 0, 'w': 1, 'h': 1}}

    # 分析
    ana = client.get('/api/analysis').get_json()
    assert any(l['from'] == 1 and l['to'] == 2 for l in ana['links'])
    assert ana['solutions'][0]['orders'][0]['order'] == [1, 2, 3]

    # 导出
    res = client.get('/api/export')
    assert res.status_code == 200
    assert 'attachment' in res.headers['Content-Disposition']
    payload = json.loads(res.data)
    assert [r['page_id'] for r in payload['reading_order']] == [1, 2, 3]
    assert payload['reading_order'][0]['source_file'] == 'page1.png'
    assert 'gaps' in payload and 'rules_note' in payload

    # 删除页面（可撤销排序变化；证据框随页面级联删除）
    def evidence_count():
        conn = sqlite3.connect(appmod.DB_PATH)
        n = conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]
        conn.close()
        return n
    n_before = evidence_count()
    client.delete('/api/pages/3')
    state = client.get('/api/state').get_json()
    assert len(state['pages']) == 2
    assert evidence_count() == n_before - 1, '删除页面应一并删除其证据框'
    res = client.post('/api/undo')
    assert res.get_json()['ok']

    # 首页可访问
    assert client.get('/').status_code == 200
    print('全部接口冒烟测试通过')


def test_evidence_migration():
    """旧版本数据库（无 evidence 表）启动时自行补齐，且不影响既有数据。"""
    old_db, old_scans = appmod.DB_PATH, appmod.SCANS_DIR
    d = tempfile.mkdtemp(prefix='clippings_migrate_')
    try:
        path = os.path.join(d, 'old.db')
        conn = sqlite3.connect(path)
        conn.executescript(
            'CREATE TABLE pages (id INTEGER PRIMARY KEY AUTOINCREMENT, orig_name TEXT NOT NULL,'
            ' stored_name TEXT NOT NULL, date_start TEXT NOT NULL DEFAULT \'\','
            ' date_end TEXT NOT NULL DEFAULT \'\', edition TEXT NOT NULL DEFAULT \'\','
            ' section TEXT NOT NULL DEFAULT \'未知\', column_name TEXT NOT NULL DEFAULT \'\','
            ' first_phrase TEXT NOT NULL DEFAULT \'\', last_phrase TEXT NOT NULL DEFAULT \'\','
            ' cont_from TEXT NOT NULL DEFAULT \'\', cont_to TEXT NOT NULL DEFAULT \'\','
            ' notes TEXT NOT NULL DEFAULT \'\', created_at TEXT NOT NULL);')
        conn.execute("INSERT INTO pages (orig_name, stored_name, created_at) "
                     "VALUES ('old.png','x.png','2026-01-01 00:00:00')")
        conn.commit()
        conn.close()

        appmod.DB_PATH = path
        appmod.SCANS_DIR = os.path.join(d, 'scans')
        appmod.init_db()   # 应对旧库补齐 evidence 表
        c = appmod.app.test_client()
        st = c.get('/api/state').get_json()
        assert len(st['pages']) == 1
        assert 'evidence' not in st['pages'][0], '未框选页面维持原响应'
        # 补齐后可正常保存证据
        r = c.put('/api/pages/1/evidence/last_phrase',
                  json={'box': {'x': .1, 'y': .2, 'w': .3, 'h': .4}, 'value': '旧页短句'})
        assert r.status_code == 200, r.data
        assert c.get('/api/state').get_json()['pages'][0]['evidence'] == {
            'last_phrase': {'x': .1, 'y': .2, 'w': .3, 'h': .4}}
    finally:
        appmod.DB_PATH, appmod.SCANS_DIR = old_db, old_scans
    print('✓ 旧库迁移补齐证据存储')


if __name__ == '__main__':
    main()
    test_evidence_migration()
