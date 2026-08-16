# -*- coding: utf-8 -*-
"""企业预警通 通用抓取 CLI — 抓取网站上任意可获取的信息

三种抓取对象(信息维度不限, 由入口发现器动态支持, 不硬编码):
  1. 企业详情页的任意入口: --entry "债券融资" / "股东" / "风险" ... (模糊匹配, 实时枚举)
  2. 企业全部入口:         --all (逐个抓取, 注意配额)
  3. 任意站内 URL:         --url https://www.qyyjt.cn/... (搜索页/榜单页/公告页等)

用法:
  python qyyjt_fetch.py "企业名" --list                 # 列出该企业所有可用入口
  python qyyjt_fetch.py "企业名" --entry "债券融资"      # 抓指定入口(中文/英文别名/部分匹配)
  python qyyjt_fetch.py "企业名" --entry 股东 --all-rows # 抓入口并保留完整行数据
  python qyyjt_fetch.py "企业名" --all                  # 抓全部入口(耗配额, 谨慎)
  python qyyjt_fetch.py --url "https://www.qyyjt.cn/s?tab=securities&k=重庆"  # 任意 URL
  python qyyjt_fetch.py "企业名" --entry "债券融资" --out result.xlsx
  python qyyjt_fetch.py "企业名" --entry "债券融资" --out result.json --full-api

输出: 默认 stdout 打印摘要; --out .json 存结构化结果; --out .xlsx 存多 Sheet Excel。
退出码: 0=成功 2=配额停止 3=未登录 4=未找到/入口不存在
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from qyyjt_common import (  # noqa: E402
    ApiCapture, EXIT_LOGIN, EXIT_NOTFOUND, EXIT_OK, EXIT_QUOTA,
    QuotaExceeded, NotLoggedIn, click_entry, close_browser, collect_entries,
    expand_tree_for_keyword, expand_tree_nodes, extract_tables, goto_overview,
    open_browser, page_text, pick_best, search_company, stat_lines,
)

DATA_DIR = Path(__file__).parent.parent / 'data'
ALIASES = {
    # 英文/拼音别名 -> 中文子串(匹配入口文本)
    'overview': '概览', 'basic_info': '基本信息', 'basic': '基本信息',
    'shareholder': '股东', 'stock': '股东', 'guquan': '股东',
    'financial': '财务', 'finance': '财务', 'caiwu': '财务',
    'risk': '风险', 'lawsuit': '诉讼', 'judicial': '司法',
    'admin': '处罚', 'abnormal': '异常', 'credit': '信用',
    'rating': '评级', 'bond': '债券', 'zhaiq': '债券',
    'financing': '融资', 'rongzi': '融资', 'graph': '图谱',
    'equity': '股权', 'change': '变更', 'operation': '经营',
    'bidding': '招投标', 'ip': '知识产权', 'patent': '专利',
    'trademark': '商标', 'news': '新闻', 'notice': '公告',
}


def log(msg):
    print(msg, flush=True)


def match_entry(entries, query, fuzzy=True):
    """入口匹配: 精确 -> 子串(入口含查询词优先) -> 别名 -> 模糊(可选)。返回 entry 或 None"""
    q = (query or '').strip()
    if not q:
        return None
    for e in entries:
        if e['text'] == q:
            return e
    for e in entries:
        if q in e['text']:
            return e
    for e in entries:
        if e['text'] in q:
            return e
    alias = ALIASES.get(q.lower())
    if alias:
        for e in entries:
            if alias in e['text'] or e['text'] in alias:
                return e
    if fuzzy:
        import difflib
        best = difflib.get_close_matches(q, [e['text'] for e in entries], n=1, cutoff=0.66)
        if best:
            for e in entries:
                if e['text'] == best[0]:
                    return e
    return None


def summarize(rec):
    """结果记录 -> 打印摘要"""
    log(f"  [{rec.get('entry','?')}]")
    apis = rec.get('api', [])
    if apis:
        for a in apis:
            keys = ','.join(a['keys'][:6])
            log(f"    API {a['code']}  (命中{a['count']}次, 数据{a['rowCount']}行, 键: {keys})")
    for t in rec.get('tables', []):
        log(f"    表格: {len(t['headers'])}列 {t['rowCount']}行  表头: {','.join(t['headers'][:8])}")
    st = rec.get('stats', [])
    if st:
        log(f"    统计: {' | '.join(st[:6])}")


def to_excel(out_path, results):
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '概览'
    ws.append(['公司', '入口', 'API端点', '表格数', '总行数', '抓取时间'])
    for r in results:
        n_tables = len(r.get('tables', []))
        n_rows = sum(t['rowCount'] for t in r.get('tables', []))
        api = '; '.join(a['code'] for a in r.get('api', [])[:3])
        ws.append([r.get('company', ''), r.get('entry', ''), api, n_tables, n_rows,
                   r.get('time', '')])
    for i, r in enumerate(results, start=2):
        for t in r.get('tables', []):
            name = f"{r.get('entry', 'entry')}_{t.get('table', i)}"[:31] or 'sheet'
            s = wb.create_sheet(title=name)
            s.append(t['headers'])
            for row in t['rows']:
                s.append(row)
            for c in s[1]:
                c.font = Font(bold=True)
    wb.save(str(out_path))
    log(f'已写入 Excel: {out_path}  ({len(results)} 个入口, {len(wb.sheetnames)} 个 Sheet)')


async def fetch_entry(pg, entry, company_name, keep_full=False, wait_ms=5000):
    """点击入口并抓取: API 摘要 + 表格 + 统计 + 正文快照"""
    cap = ApiCapture(pg, keep_full=keep_full)
    ok = await click_entry(pg, entry, wait_ms=wait_ms)
    await cap.drain()
    if not ok:
        return None
    tables = await extract_tables(pg)
    stats = await stat_lines(pg, 20)
    rec = {
        'company': company_name,
        'entry': entry['text'],
        'kind': entry['kind'],
        'time': datetime.now().isoformat(timespec='seconds'),
        'api': cap.summary(),
        'tables': tables,
        'stats': stats,
        'text_snapshot': await page_text(pg, 4000),
    }
    if keep_full:
        rec['api_full'] = cap.full()
    return rec


async def cascade_find_entry(pg, query, code, max_parents=4):
    """级联查找入口: 当前详情页匹配失败时, 依次点击含关键词片段的父入口
    (menu/tree), 在新页面重新枚举再匹配(锚点/tab 型入口如 '债券融资' 藏在
    '融资速览' 页面里)。返回 (最终入口列表, 匹配入口) 或 (None, None)。"""
    entries = await collect_entries(pg)
    e = match_entry(entries, query, fuzzy=False)
    if e is not None:
        return entries, e

    # 候选父入口: 文本含查询词片段的 menu/tree 入口
    cands = [c for c in [query, query[-2:], query[:2], query[-3:], query[:3]] if c]
    parents, seen = [], set()
    for c in cands:
        for x in entries:
            k = (x['kind'], x['text'])
            if k in seen:
                continue
            if x['kind'] in ('menu', 'tree') and (c in x['text'] or x['text'] in c):
                seen.add(k)
                parents.append(x)
    if not parents:
        parents = [x for x in entries if x['kind'] == 'menu'][:max_parents]
    parents = parents[:max_parents]
    log(f'    级联候选父入口: {[p["text"] for p in parents]}')

    for parent in parents:
        try:
            if parent['kind'] == 'tree':
                entries2 = await expand_tree_for_keyword(pg, parent['text'])
            else:
                await click_entry(pg, parent, wait_ms=4000)
                entries2 = await collect_entries(pg)
        except QuotaExceeded as ex:
            log(f'    配额/异常: {ex}')
            break
        e2 = match_entry(entries2, query, fuzzy=False)
        if e2 is not None:
            return entries2, e2
        # 回到详情页再试下一个父入口
        await goto_overview(pg, code, wait_menu=False)
    return None, None


async def do_fetch(args):
    p, b, pg = await open_browser(headless=not args.headed, profile=args.profile)

    # ── 任意 URL 模式 ──
    if args.url:
        log(f'=== 抓取 URL: {args.url} ===')
        cap = ApiCapture(pg, keep_full=args.full_api)
        await pg.goto(args.url, wait_until='domcontentloaded', timeout=25000)
        await pg.wait_for_timeout(4000)
        await cap.drain()
        tables = await extract_tables(pg)
        stats = await stat_lines(pg, 30)
        rec = {'company': '', 'entry': args.url, 'kind': 'url',
               'time': datetime.now().isoformat(timespec='seconds'),
               'api': cap.summary(), 'tables': tables, 'stats': stats,
               'text_snapshot': await page_text(pg, 6000)}
        if args.full_api:
            rec['api_full'] = cap.full()
        results = [rec]
        summarize(rec)
        await close_browser(p, b)
        return finish(args, results)

    # ── 企业模式 ──
    results = await search_company(pg, args.company)
    if not results:
        log('!! 搜索无结果')
        await close_browser(p, b)
        return EXIT_NOTFOUND
    best = pick_best(args.company, results)
    if best is None:
        log(f'!! 无相似匹配, 候选: {[r["name"] for r in results[:3]]}')
        await close_browser(p, b)
        return EXIT_NOTFOUND
    code, matched = best['code'], best['name']
    log(f'匹配企业: {matched} (code={code})')
    await goto_overview(pg, code)

    entries = await collect_entries(pg)
    log(f'详情页入口 {len(entries)} 个')

    # ── 列出入口 ──
    if args.list:
        for i, e in enumerate(entries):
            log(f'  [{i}] ({e["kind"]:6s}) {e["text"]}')
        log('\n用法示例:')
        log(f'  python qyyjt_fetch.py "{args.company}" --entry 债券融资')
        await close_browser(p, b)
        return EXIT_OK

    # ── 抓指定入口 ──
    if args.entry:
        e = match_entry(entries, args.entry, fuzzy=False)
        if e is None:
            # 级联查找: 展开树 / 点击父入口(锚点型入口如'债券融资'在'融资速览'页内)
            log('入口未直接匹配, 级联查找(展开树/父入口)...')
            try:
                entries, e = await cascade_find_entry(pg, args.entry, code)
            except QuotaExceeded as ex:
                log(f'!! 级联查找触发配额: {ex}')
                await close_browser(p, b)
                return EXIT_QUOTA
        if e is None and args.expand:
            log('--expand: 全量展开树菜单...')
            try:
                entries = await expand_tree_nodes(pg)
                e = match_entry(entries, args.entry, fuzzy=False)
            except QuotaExceeded as ex:
                log(f'!! 展开树触发配额: {ex}')
        if e is None:
            e = match_entry(entries, args.entry, fuzzy=True)
        if e is None:
            log(f'!! 未找到入口匹配: {args.entry}; 可用入口: {[x["text"] for x in entries]}')
            await close_browser(p, b)
            return EXIT_NOTFOUND
        log(f'=== 抓取入口 [{e["kind"]}] {e["text"]} ===')
        rec = await fetch_entry(pg, e, matched, keep_full=args.full_api, wait_ms=args.wait)
        if rec is None:
            log('!! 点击入口失败')
            await close_browser(p, b)
            return EXIT_ERR
        summarize(rec)
        results = [rec]

    # ── 抓全部入口 ──
    elif args.all:
        log(f'=== 抓取全部 {len(entries)} 个入口 (注意配额) ===')
        status = EXIT_OK
        for i, e in enumerate(entries, 1):
            log(f'[{i}/{len(entries)}] 点击 ({e["kind"]}) {e["text"]} ...')
            try:
                rec = await fetch_entry(pg, e, matched, keep_full=args.full_api,
                                        wait_ms=args.wait)
                if rec:
                    summarize(rec)
                    results.append(rec)
            except QuotaExceeded as ex:
                log(f'!!! 配额停止: {ex}')
                status = EXIT_QUOTA
                break
            # 回到详情页
            await goto_overview(pg, code, wait_menu=False)
        if status != EXIT_OK:
            await close_browser(p, b)
            if args.out:
                finish(args, results)
            return status

    else:
        log('!! 需要 --entry / --all / --list / --url 之一')
        await close_browser(p, b)
        return EXIT_OK

    await close_browser(p, b)
    return finish(args, results)


def finish(args, results):
    if args.out:
        out = Path(args.out)
        if out.suffix.lower() == '.xlsx':
            to_excel(out, results)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=1)
            log(f'已写入: {out}')
    return EXIT_OK


async def main():
    ap = argparse.ArgumentParser(description='企业预警通 通用抓取 CLI')
    ap.add_argument('company', nargs='?', help='企业名')
    ap.add_argument('--entry', default=None, help='入口: 中文子串/英文别名(如 债券融资/bond/shareholder)')
    ap.add_argument('--list', action='store_true', help='列出该企业所有入口')
    ap.add_argument('--all', action='store_true', help='抓取全部入口(耗配额)')
    ap.add_argument('--expand', action='store_true', help='匹配失败时全量展开树菜单(耗配额)')
    ap.add_argument('--url', default=None, help='直接抓取任意站内 URL')
    ap.add_argument('--out', default=None, help='输出文件 (.json/.xlsx)')
    ap.add_argument('--full-api', action='store_true', help='保留完整 API JSON')
    ap.add_argument('--wait', type=int, default=5000, help='点击后等待毫秒数(默认5000)')
    ap.add_argument('--profile', default=None, help='浏览器 profile 目录')
    ap.add_argument('--headed', action='store_true', help='有头模式(调试)')
    args = ap.parse_args()

    if not args.company and not args.url:
        log('用法: python qyyjt_fetch.py "企业名" --entry 入口  |  --list  |  --all  |  --url <URL>')
        return EXIT_OK
    if args.company and not (args.entry or args.list or args.all):
        log('!! 企业模式下需要 --entry / --list / --all 之一')
        return EXIT_OK

    try:
        return await do_fetch(args)
    except NotLoggedIn as ex:
        log(f'!! {ex}')
        return EXIT_LOGIN
    except QuotaExceeded as ex:
        log(f'!!! {ex}')
        return EXIT_QUOTA
    except Exception as ex:
        log(f'!! 异常 {type(ex).__name__}: {ex}')
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
