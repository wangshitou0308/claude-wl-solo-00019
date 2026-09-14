"""后端接口冒烟测试（临时目录，不影响真实数据）：python test_app.py"""
import base64
import io
import json
import os
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

    # 删除页面（可撤销排序变化）
    client.delete('/api/pages/3')
    state = client.get('/api/state').get_json()
    assert len(state['pages']) == 2
    res = client.post('/api/undo')
    assert res.get_json()['ok']

    # 首页可访问
    assert client.get('/').status_code == 200
    print('全部接口冒烟测试通过')


if __name__ == '__main__':
    main()
