"""求解器单元测试：python test_solver.py"""
import solver


def pg(id, **kw):
    base = dict(id=id, date_start='', date_end='', edition='', section='未知',
                column_name='', first_phrase='', last_phrase='',
                cont_from='', cont_to='', notes='')
    base.update(kw)
    return base


def test_basics():
    assert solver.cn_to_int('三') == 3
    assert solver.cn_to_int('十二') == 12
    assert solver.cn_to_int('４') == 4
    assert solver.cn_to_int('') is None
    assert solver.parse_date('1953-04-12') == (1953, 4, 12)
    assert solver.parse_date('1953年4月') == (1953, 4, None)
    assert solver.parse_date('1953') == (1953, None, None)
    assert solver.parse_date('4月5日') is None          # 无年份不作硬约束
    assert solver.parse_md('下转4月5日') == [(4, 5)]
    assert solver.parse_editions('下转第5版') == [5]
    assert solver.parse_editions('下转五版') == [5]
    assert solver.suffix_prefix_overlap('他转身走进小巷', '走进小巷深处') == 4
    print('✓ 基础解析')


def test_chain_and_rules():
    pages = [
        pg(1, date_start='1953-04-01', edition='3', section='正刊', column_name='连载',
           first_phrase='他转身走进小巷深处', last_phrase='他转身走进小巷',
           cont_to='下转第5版'),
        pg(2, date_start='1953-04-01', edition='5', section='正刊', column_name='连载',
           first_phrase='走进小巷深处', last_phrase='谁知天色骤变',
           cont_from='上接第3版', cont_to='下转第7版'),   # 第7版不存在 → 悬空指向
        pg(3, date_start='1953-04-02', edition='3', section='正刊', column_name='连载',
           first_phrase='天色骤变之后', last_phrase='故事待续'),
        # 与 1 同版次同栏目（版次重用），但无任何短句/指向证据 → 不得成链
        pg(4, date_start='1953-04-08', edition='3', section='正刊', column_name='连载',
           first_phrase='另一条新闻', last_phrase='无关内容'),
        # 短句指向 1，但日期更晚 → 日期倒挂矛盾
        pg(5, date_start='1953-04-05', edition='2', section='正刊',
           last_phrase='他转身走进小巷'),
    ]
    res = solver.analyze(pages, [])
    links = {(e['from'], e['to']): e for e in res['links']}

    assert (1, 2) in links, '指针+短句应成链'
    assert links[(1, 2)]['score'] >= 10
    assert (2, 3) in links, '短句重叠应成链'
    assert (1, 4) not in links and (4, 1) not in links, '同版次同栏目不得单独成链'
    assert (5, 1) not in links, '日期倒挂不得成链'

    dc = [c for c in res['contradictions'] if c['type'] == 'date_conflict']
    assert dc and dc[0]['pages'] == [5, 1], '应定位日期矛盾'
    assert 'last_phrase' in dc[0]['clues']

    gaps = res['gaps']
    assert any(g['after'] == 2 and g['min_missing'] == 1 for g in gaps), '悬空下转应报缺口'

    # 多解：1→2 与 5? 5 因日期倒挂不成链，链为 1→2→3
    orders = res['solutions'][0]['orders']
    assert orders[0]['order'] == [1, 2, 3]
    assert all(s['reasons'] for s in orders[0]['steps']), '每步都应有依据'
    print('✓ 成链规则 / 版次重用 / 矛盾 / 缺口')


def test_ambiguity_two_solutions():
    # 两个页面都能接在 1 后面（短句都衔接、日期都允许）→ 多解并列
    pages = [
        pg(1, date_start='1953-04-01', edition='3', last_phrase='夜深人未眠'),
        pg(2, date_start='1953-04-02', edition='4', first_phrase='夜深人未眠，灯下补旧报'),
        pg(3, date_start='1953-04-02', edition='6', first_phrase='夜深人未眠，窗外雨声急'),
    ]
    res = solver.analyze(pages, [])
    orders = res['solutions'][0]['orders']
    assert len(orders) >= 2, '应给出多个候选顺序'
    assert {o['order'][1] for o in orders[:2]} == {2, 3}
    print('✓ 多解并列')


def test_locked_and_stale():
    pages = [
        pg(1, date_start='1953-04-01', edition='3', last_phrase='他转身走进小巷'),
        pg(2, date_start='1953-04-01', edition='5', first_phrase='走进小巷深处',
           last_phrase='谁知天色骤变'),
        pg(3, date_start='1953-04-02', edition='3', first_phrase='天色骤变之后'),
    ]
    placements = [
        {'page_id': 3, 'position': 0, 'locked': 1, 'stale': 0},
        {'page_id': 1, 'position': 1, 'locked': 0, 'stale': 1},
        {'page_id': 2, 'position': 2, 'locked': 0, 'stale': 0},
    ]
    res = solver.analyze(pages, placements)
    # 锁定页 3 在最前，与日期硬约束冲突 → 放宽锁定后仍给出合法顺序
    assert res['solutions'][0]['orders'][0]['order'] == [1, 2, 3]
    # 当前排序 3→1 无接续；1→2 有接续且含过期页
    joins = {(j['from'], j['to']): j for j in res['joins']}
    assert joins[(3, 1)]['status'] == 'none'
    assert joins[(1, 2)]['status'] == 'strong' and joins[(1, 2)]['stale']
    print('✓ 锁定约束 / 过期标记')


def test_date_gap():
    pages = [
        pg(1, date_start='1953-04-01', edition='3', last_phrase='甲'),
        pg(2, date_start='1953-04-05', edition='3', first_phrase='乙'),
    ]
    placements = [{'page_id': 1, 'position': 0, 'locked': 0, 'stale': 0},
                  {'page_id': 2, 'position': 1, 'locked': 0, 'stale': 0}]
    res = solver.analyze(pages, placements)
    gap = [g for g in res['gaps'] if g['after'] == 1 and g['before'] == 2]
    assert gap and gap[0]['min_missing'] == 3, '按日刊应估算缺 3 期'
    print('✓ 日期间隔估算最小缺口')


if __name__ == '__main__':
    test_basics()
    test_chain_and_rules()
    test_ambiguity_two_solutions()
    test_locked_and_stale()
    test_date_gap()
    print('全部求解器测试通过')
