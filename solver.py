"""剪报连载接续约束求解器。

只依赖标准库：输入页面字典与排序记录，输出候选顺序、矛盾与缺口。

证据规则要点：
  * 段尾/段首短句重叠、「上接/下转」指向为强证据；
  * 日期先后为硬约束（强证据与日期冲突时列为矛盾并定位线索）；
  * 正刊/副刊归属不一致列为疑点；
  * 同版次（版次重用）与同栏目名只作弱参考，绝不单独成链。
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from difflib import SequenceMatcher

EDGE_MIN = 4   # 成链最低分：需短句或指向级证据
STRONG = 6     # 强接续阈值

CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
             '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
FULLWIDTH = str.maketrans('０１２３４５６７８９', '0123456789')

_DATE_FULL = re.compile(r'(\d{4})\s*[年\-/.]\s*(\d{1,2})(?:\s*[月\-/.]\s*(\d{1,2}))?')
_YEAR_ONLY = re.compile(r'(\d{4})')
_MD_ONLY = re.compile(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日?')
_EDITION_RE = re.compile(r'第?\s*([0-9０-９一二三四五六七八九十两]+)\s*版')
_NORM_RE = re.compile(r'[^\w]+')


# ---------------------------------------------------------------- 基础解析

def cn_to_int(s):
    """解析阿拉伯/中文小数字（版次用，≤99），失败返回 None。"""
    if s is None:
        return None
    s = str(s).strip().translate(FULLWIDTH)
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if '十' in s:
        left, _, right = s.partition('十')
        if (left and left not in CN_DIGITS) or (right and right not in CN_DIGITS):
            return None
        tens = CN_DIGITS.get(left, 1) if left else 1
        ones = CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(s) == 1 and s in CN_DIGITS:
        return CN_DIGITS[s]
    return None


def parse_date(text):
    """解析 '1953-04-12' / '1953-04' / '1953' / '1953年4月12日' 等。

    返回 (y, m, d)，m/d 可为 None；无法解析返回 None。
    """
    if not text:
        return None
    t = str(text).translate(FULLWIDTH)
    m = _DATE_FULL.search(t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            return None
        d = int(m.group(3)) if m.group(3) else None
        if d is not None and not 1 <= d <= 31:
            return None
        return (y, mo, d)
    m = _YEAR_ONLY.search(t)
    if m:
        return (int(m.group(1)), None, None)
    return None


def parse_md(text):
    """指针文本中的「M月D日」提及，返回 [(m, d), ...]。"""
    if not text:
        return []
    out = []
    for mm, dd in _MD_ONLY.findall(str(text).translate(FULLWIDTH)):
        mi, di = int(mm), int(dd)
        if 1 <= mi <= 12 and 1 <= di <= 31:
            out.append((mi, di))
    return out


def parse_editions(text):
    """指针文本中的版次，如「下转第5版」「下转五版」。"""
    if not text:
        return []
    out = []
    for m in _EDITION_RE.findall(str(text)):
        n = cn_to_int(m)
        if n is not None and n not in out:
            out.append(n)
    return out


def _bounds(parsed):
    y, m, d = parsed
    lo = (y, m or 1, d or 1)
    if m is None:
        hi = (y, 12, 31)
    elif d is None:
        hi = (y, m, calendar.monthrange(y, m)[1])
    else:
        hi = (y, m, d)
    return lo, hi


def norm_text(s):
    return _NORM_RE.sub('', s or '')


def suffix_prefix_overlap(a, b, min_k=3):
    """a 的后缀与 b 的前缀的最长重叠字数（≥min_k 才返回，否则 0）。"""
    for k in range(min(len(a), len(b)), min_k - 1, -1):
        if a.endswith(b[:k]):
            return k
    return 0


def label(p):
    return f"#{p['id']}（{p.get('date_start') or '日期未知'}·{p.get('edition') or '?'}版）"


# ---------------------------------------------------------------- 页面预处理

def _prep(pages):
    for p in pages:
        p['_lo'] = p['_hi'] = None
        p['_md'] = None
        parsed = parse_date(p.get('date_start'))
        if parsed:
            p['_lo'], p['_hi'] = _bounds(parsed)
            if parsed[2]:
                p['_md'] = (parsed[1], parsed[2])
        parsed_end = parse_date(p.get('date_end'))
        if parsed_end:
            lo2, hi2 = _bounds(parsed_end)
            if p['_lo'] is None or lo2 < p['_lo']:
                p['_lo'] = lo2
            if p['_hi'] is None or hi2 > p['_hi']:
                p['_hi'] = hi2
            if p['_md'] is None and parsed_end[2]:
                p['_md'] = (parsed_end[1], parsed_end[2])
        p['_ed'] = cn_to_int(p.get('edition'))


def _strictly_before(a, b):
    """a 的可见日期范围整体早于 b（硬约束）。"""
    return bool(a['_hi'] and b['_lo'] and a['_hi'] < b['_lo'])


def _days_gap(a, b):
    """b.lo 与 a.hi 相差天数；双方均需精确到日，否则 None。"""
    if not (a['_md'] and b['_md'] and a['_hi'] and b['_lo']):
        return None
    try:
        return (date(*b['_lo']) - date(*a['_hi'])).days
    except ValueError:
        return None


def _pointer_date_ok(a, b):
    """版次指向（上接/下转第X版）仅在同刊期或相邻刊期、且方向一致时有效。

    a 为指向来源、b 为指向目标：a 不得整体晚于 b；两页日期范围相交或
    相距不超过 1 天；日期精度不足时从宽。相隔明确的更远日期或方向相反
    均属版次重用，不作指向证据。
    """
    if a['_lo'] is None or b['_lo'] is None:
        return True
    if a['_lo'] > b['_hi']:
        return False
    if a['_lo'] <= b['_hi'] and b['_lo'] <= a['_hi']:
        return True
    gaps = [g for g in (_days_gap(a, b), _days_gap(b, a)) if g is not None]
    if not gaps:
        return True
    return any(0 <= g <= 1 for g in gaps)


# ---------------------------------------------------------------- 证据与约束

def pair_evidence(a, b):
    """评估 a→b 的接续证据，返回 (score, reasons, warnings, clue_fields)。"""
    score, reasons, warnings, fields = 0, [], [], set()

    la, fb = norm_text(a.get('last_phrase')), norm_text(b.get('first_phrase'))
    if la and fb:
        k = suffix_prefix_overlap(la, fb)
        if k >= 3:
            score += 6
            reasons.append(f"段尾「{la[-10:]}」与段首「{fb[:10]}」重叠 {k} 字")
            fields.update(('last_phrase', 'first_phrase'))
        elif len(la) >= 4 and len(fb) >= 4:
            r = SequenceMatcher(None, la[-12:], fb[:12]).ratio()
            if r >= 0.55:
                score += 3
                reasons.append(f"段尾/段首短句近似（相似度 {r:.2f}）")
                fields.update(('last_phrase', 'first_phrase'))

    to_eds = parse_editions(a.get('cont_to'))
    if b['_ed'] is not None and b['_ed'] in to_eds and _pointer_date_ok(a, b):
        score += 5
        reasons.append(f"「下转第{b['_ed']}版」与 {label(b)} 版次相符")
        fields.update(('cont_to', 'edition'))
    from_eds = parse_editions(b.get('cont_from'))
    if a['_ed'] is not None and a['_ed'] in from_eds and _pointer_date_ok(a, b):
        score += 5
        reasons.append(f"「上接第{a['_ed']}版」与 {label(a)} 版次相符")
        fields.update(('cont_from', 'edition'))

    to_md = parse_md(a.get('cont_to'))
    if b['_md'] and b['_md'] in to_md:
        score += 4
        reasons.append(f"「下转{b['_md'][0]}月{b['_md'][1]}日」与 {label(b)} 日期相符")
        fields.update(('cont_to', 'date_start'))
    from_md = parse_md(b.get('cont_from'))
    if a['_md'] and a['_md'] in from_md:
        score += 4
        reasons.append(f"「上接{a['_md'][0]}月{a['_md'][1]}日」与 {label(a)} 日期相符")
        fields.update(('cont_from', 'date_start'))

    sa, sb = (a.get('section') or '未知'), (b.get('section') or '未知')
    if sa != '未知' and sb != '未知':
        if sa == sb:
            score += 1
            reasons.append(f"同属{sa}")
            fields.add('section')
        else:
            score -= 2
            warnings.append(f"正刊/副刊归属不一致（{sa} → {sb}）")
            fields.add('section')

    ca, cb = (a.get('column_name') or '').strip(), (b.get('column_name') or '').strip()
    if ca and ca == cb:
        score += 1
        reasons.append(f"同栏目「{ca}」（弱证据）")
        fields.add('column_name')

    g = _days_gap(a, b)
    if g is not None and 0 <= g <= 1:
        score += 1
        reasons.append('日期相接')
        fields.add('date_start')

    # 注意：同版次（版次重用）不计分——每天都会有「第3版」，不能作为依据。
    return score, reasons, warnings, sorted(fields)


def build_links(pages):
    """返回 (edges, contradictions)。edges 键为 (from_id, to_id)。"""
    edges, contradictions = {}, []
    for a in pages:
        for b in pages:
            if a['id'] == b['id']:
                continue
            score, reasons, warnings, fields = pair_evidence(a, b)
            if score < EDGE_MIN:
                continue
            if _strictly_before(b, a):
                contradictions.append({
                    'type': 'date_conflict', 'severity': 'error',
                    'pages': [a['id'], b['id']], 'clues': fields,
                    'message': f"线索指向 {label(a)} → {label(b)}，但日期范围显示后者更早",
                    'evidence': reasons})
                continue
            edges[(a['id'], b['id'])] = {
                'from': a['id'], 'to': b['id'], 'score': score,
                'reasons': reasons, 'warnings': warnings}
            for w in warnings:
                contradictions.append({
                    'type': 'section_mismatch', 'severity': 'warning',
                    'pages': [a['id'], b['id']], 'clues': ['section'],
                    'message': f"{label(a)} → {label(b)}：{w}",
                    'evidence': reasons})
    return edges, contradictions


# ---------------------------------------------------------------- 候选顺序

def _components(pages, edges):
    parent = {p['id']: p['id'] for p in pages}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in edges:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
    comps = {}
    for p in pages:
        comps.setdefault(find(p['id']), []).append(p['id'])
    return list(comps.values())


def _date_consistency(order, by_id):
    c = 0
    for a, b in zip(order, order[1:]):
        pa, pb = by_id[a], by_id[b]
        if pa['_hi'] and pb['_lo'] and pa['_hi'] <= pb['_lo']:
            c += 1
    return c


def search_orders(comp, by_id, edges, locked_seq, top_k=3, beam_width=40):
    """在分量内搜索得分最高的若干顺序（束搜索）。

    硬约束：日期先后；锁定页保持相对顺序。
    """
    comp_set = set(comp)
    locked_in = [pid for pid in locked_seq if pid in comp_set]
    locked_rank = {pid: i for i, pid in enumerate(locked_in)}

    def can_add(x, used):
        p = by_id[x]
        for u in used:
            if _strictly_before(p, by_id[u]):
                return False
        r = locked_rank.get(x)
        if r is not None:
            for prev in locked_in[:r]:
                if prev not in used:
                    return False
        return True

    states = [((), frozenset(), 0.0)]
    for _ in range(len(comp)):
        nxt = {}
        for order, used, score in states:
            for x in comp:
                if x in used or not can_add(x, used):
                    continue
                gain = edges[(order[-1], x)]['score'] if order and (order[-1], x) in edges else 0
                key = (x, used | {x})
                s = score + gain
                if key not in nxt or nxt[key][2] < s:
                    nxt[key] = (order + (x,), used | {x}, s)
        if not nxt:
            break
        states = sorted(nxt.values(), key=lambda t: -t[2])[:beam_width]

    if not states or len(states[0][0]) < len(comp):
        # 锁定顺序与日期硬约束冲突（本身会列为矛盾），放宽锁定重搜
        if locked_in:
            return search_orders(comp, by_id, edges, [], top_k, beam_width)
        return []

    finals = sorted(states, key=lambda t: (-t[2], -_date_consistency(t[0], by_id)))
    out = []
    for order, _used, score in finals:
        steps = []
        for a, b in zip(order, order[1:]):
            e = edges.get((a, b))
            steps.append({'from': a, 'to': b,
                          'score': e['score'] if e else 0,
                          'reasons': e['reasons'] if e else [],
                          'warnings': e['warnings'] if e else []})
        out.append({'order': list(order), 'score': round(score, 2), 'steps': steps})
        if len(out) >= top_k:
            break
    return out


# ---------------------------------------------------------------- 缺口

def _next_later(p, order_ids, by_id):
    if p['id'] not in order_ids:
        return None
    i = order_ids.index(p['id'])
    for qid in order_ids[i + 1:]:
        q = by_id[qid]
        if p['_hi'] is None or q['_lo'] is None or p['_hi'] < q['_lo']:
            return qid
    return None


def _prev_earlier(p, order_ids, by_id):
    if p['id'] not in order_ids:
        return None
    i = order_ids.index(p['id'])
    for qid in reversed(order_ids[:i]):
        q = by_id[qid]
        if p['_lo'] is None or q['_hi'] is None or q['_hi'] < p['_lo']:
            return qid
    return None


def find_gaps(order_ids, pages, by_id, edges):
    gaps, reported = [], set()

    def add(after, before, min_missing, severity, message, basis):
        key = (after, before, message)
        if key not in reported:
            reported.add(key)
            gaps.append({'after': after, 'before': before,
                         'min_missing': min_missing, 'severity': severity,
                         'message': message, 'basis': basis})

    for p in pages:
        to_eds, to_md = parse_editions(p.get('cont_to')), parse_md(p.get('cont_to'))
        if to_eds or to_md:
            hit = any(q['id'] != p['id'] and (
                (q['_ed'] is not None and q['_ed'] in to_eds and _pointer_date_ok(p, q)) or
                (q['_md'] and q['_md'] in to_md)) for q in pages)
            if not hit:
                add(p['id'], _next_later(p, order_ids, by_id), 1, 'high',
                    f"{label(p)} 的「下转」指向（{p['cont_to']}）未找到对应页",
                    '下转指向悬空，最小缺口为 1 页')
        from_eds = parse_editions(p.get('cont_from'))
        from_md = parse_md(p.get('cont_from'))
        if from_eds or from_md:
            hit = any(q['id'] != p['id'] and (
                (q['_ed'] is not None and q['_ed'] in from_eds and _pointer_date_ok(q, p)) or
                (q['_md'] and q['_md'] in from_md)) for q in pages)
            if not hit:
                add(_prev_earlier(p, order_ids, by_id), p['id'], 1, 'high',
                    f"{label(p)} 的「上接」指向（{p['cont_from']}）未找到对应页",
                    '上接指向悬空，最小缺口为 1 页')

    for a_id, b_id in zip(order_ids, order_ids[1:]):
        if (a_id, b_id) in edges:
            continue
        a, b = by_id[a_id], by_id[b_id]
        g = _days_gap(a, b)
        if g is not None and g >= 2:
            add(a_id, b_id, g - 1, 'high',
                f"{label(a)} 与 {label(b)} 之间日期相隔 {g} 天且无接续线索",
                f'按日刊估算最小缺口为 {g - 1} 期')
        else:
            add(a_id, b_id, 1, 'low',
                f"{label(a)} 与 {label(b)} 相邻但无短句/指向线索",
                '疑似缺口，至少 1 页待核')
    return gaps


# ---------------------------------------------------------------- 总入口

def analyze(pages, placements):
    """pages: 页面字典列表；placements: 排序记录（含 position/locked/stale）。"""
    pages = [dict(p) for p in pages]
    _prep(pages)
    by_id = {p['id']: p for p in pages}
    edges, contradictions = build_links(pages)
    comps = _components(pages, edges)

    placed = sorted((pl for pl in placements if pl['page_id'] in by_id),
                    key=lambda pl: pl['position'])
    locked_seq = [pl['page_id'] for pl in placed if pl['locked']]

    solutions = []
    for comp in comps:
        if len(comp) < 2:
            continue
        orders = search_orders(sorted(comp), by_id, edges, locked_seq)
        if orders:
            solutions.append({'page_ids': sorted(comp), 'orders': orders})

    order_ids = [pl['page_id'] for pl in placed]
    gap_order = order_ids or [pid for sol in solutions
                              for pid in sol['orders'][0]['order']]
    gaps = find_gaps(gap_order, pages, by_id, edges)

    stale_ids = {pl['page_id'] for pl in placed if pl['stale']}
    joins = []
    for a, b in zip(order_ids, order_ids[1:]):
        e = edges.get((a, b))
        joins.append({
            'from': a, 'to': b,
            'status': ('strong' if e['score'] >= STRONG else 'ok') if e else 'none',
            'stale': a in stale_ids or b in stale_ids,
            'reasons': e['reasons'] if e else [],
            'warnings': e['warnings'] if e else []})

    return {
        'links': sorted(edges.values(), key=lambda e: -e['score']),
        'solutions': solutions,
        'singletons': [c[0] for c in comps if len(c) == 1],
        'contradictions': contradictions,
        'gaps': gaps,
        'joins': joins,
    }
