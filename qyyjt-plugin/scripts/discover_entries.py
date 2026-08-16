# -*- coding: utf-8 -*-
"""企业预警通 入口编码发现器 — 获取站点/企业详情页所有界面入口的"编码"

用途:
  - 回答"这个网站能查什么": 把每个可点入口的中文名、DOM 定位器(kind+index)、
    点击后触发的后端 API 端点编码、返回的表格结构 全部记录成 entries_map.json。
  - 生成的 map 可直接给 qyyjt_fetch.py --entries-map 复用(免重复点击探测)。

用法:
  python discover_entries.py "企业名" --list            # 只枚举入口(不点击, 不耗配额)
  python discover_entries.py "企业名" --probe [N]       # 枚举并逐个点击前 N 个入口, 记录 API+表格
  python discover_entries.py "企业名" --probe --expand  # 先递归展开'企业融资'等树菜单再探测
  python discover_entries.py --site                     # 站点级入口发现(首页导航/链接)
  python discover_entries.py "企业名" --out 文件.json    # 指定输出(默认 data/entries_map.json)

退出码: 0=成功 2=配额停止 3=未登录 4=企业未找到
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
    PermissionDenied, QuotaExceeded, NotLoggedIn, check_permission,
    check_quota, click_entry, close_browser, collect_entries,
    expand_tree_nodes, extract_blocks, extract_tables, goto_overview,
    open_browser, pick_best, search_company, stat_lines,
)

DATA_DIR = Path(__file__).parent.parent / 'data'


def log(msg):
    print(msg, flush=True)


async def resolve_company(pg, name):
    """搜索并匹配企业, 返回 (code, matched_name) 或 None"""
    results = await search_company(pg, name)
    if not results:
        return None
    best = pick_best(name, results)
    if best is None:
        log(f'!! 无相似匹配, 前几个结果: {[r["name"] for r in results[:3]]}')
        return None
    return best['code'], best['name']


async def do_discover(args):
    p, b, pg = await open_browser(headless=not args.headed, profile=args.profile)

    company = None
    if args.company:
        log(f'=== 搜索企业: {args.company} ===')
        company = await resolve_company(pg, args.company)
        if company is None:
            await close_browser(p, b)
            return EXIT_NOTFOUND
        code, matched = company
        log(f'匹配: {matched} (code={code})')
        await goto_overview(pg, code)

    entries = await collect_entries(pg)
    if args.expand:
        log('展开树菜单...')
        entries = await expand_tree_nodes(pg)

    log(f'\n发现 {len(entries)} 个入口:')
    for i, e in enumerate(entries):
        log(f'  [{i}] ({e["kind"]:6s}) {e["text"]}')

    if args.site:
        from qyyjt_common import collect_site_links
        links = await collect_site_links(pg)
        log(f'\n站点链接 {len(links)} 条:')
        for l in links[:60]:
            log(f'  {l["text"][:24]:24s} -> {l["href"][:90]}')
        site_map = {'links': links, 'search_url_template': '/s?tab=securities&k=<关键词>',
                    'detail_url_template': '/detail/enterprise/overview?code=<CODE>&type=company'}
    else:
        site_map = {}

    map_data = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'mode': 'site' if args.site else 'company',
        'company': company,
        'entries': [],
        'site': site_map,
    }

    # ---- 探测: 逐个点击记录 API + 表格 ----
    if args.probe:
        n = int(args.probe) if str(args.probe).isdigit() else None
        todo = entries if n is None else entries[:n]
        log(f'\n=== 探测 {len(todo)} 个入口(每个约 6-8 秒) ===')
        status = EXIT_OK
        for i, e in enumerate(todo, 1):
            cap = ApiCapture(pg, keep_full=False)
            log(f'[{i}/{len(todo)}] 点击 ({e["kind"]}) {e["text"]} ...')
            try:
                ok = await click_entry(pg, e)
                await cap.drain()
                rec = dict(e)
                rec['api'] = cap.summary()
                try:
                    await check_permission(pg, f"入口[{e['text']}]")
                    tables = await extract_tables(pg)
                    blocks = await extract_blocks(pg)
                    stats = await stat_lines(pg, 12)
                    rec['tables'] = [{'headers': t['headers'], 'rowCount': t['rowCount']}
                                     for t in tables]
                    rec['blocks'] = blocks
                    rec['stats'] = stats
                    api_txt = '; '.join(f"{a['code']}({a['rowCount']}行)" for a in rec['api']) or '无API'
                    log(f'    API: {api_txt}')
                    log(f'    表格: {len(tables)} 张, 内容块: {len(blocks)} 个')
                except PermissionDenied as ex:
                    rec['permission_denied'] = True
                    rec['permission_msg'] = str(ex)
                    log(f'    !! 权限不足(需正式会员), 已标记')
                map_data['entries'].append(rec)
            except QuotaExceeded as ex:
                log(f'!!! 配额停止: {ex}')
                status = EXIT_QUOTA
                break
            except Exception as ex:
                log(f'    ERROR {type(ex).__name__}: {ex}')
            await pg.goto('https://www.qyyjt.cn/detail/enterprise/overview?code=%s&type=company'
                          % (company[0] if company else ''), wait_until='domcontentloaded')
            await pg.wait_for_timeout(2500)
            if company:
                await goto_overview(pg, company[0], wait_menu=False)
        if status != EXIT_OK:
            await close_browser(p, b)
            return status

    out = Path(args.out) if args.out else DATA_DIR / 'entries_map.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(map_data, f, ensure_ascii=False, indent=1)
    log(f'\n已写入: {out}')
    await close_browser(p, b)
    return EXIT_OK


async def main():
    ap = argparse.ArgumentParser(description='企业预警通 入口编码发现器')
    ap.add_argument('company', nargs='?', help='企业名(缺省配合 --site 用)')
    ap.add_argument('--list', action='store_true', help='只枚举入口, 不点击(不耗配额)')
    ap.add_argument('--probe', nargs='?', const='all', help='逐个点击探测并记录 API/表格(可给数字 N)')
    ap.add_argument('--expand', action='store_true', help='先递归展开树菜单(可能耗配额)')
    ap.add_argument('--site', action='store_true', help='站点级入口发现(首页导航链接)')
    ap.add_argument('--out', default=None, help='输出 json 路径(默认 data/entries_map.json)')
    ap.add_argument('--profile', default=None, help='浏览器 profile 目录(默认 ~/.config/qyyjt-cli/browser-profile)')
    ap.add_argument('--headed', action='store_true', help='有头模式(调试用)')
    args = ap.parse_args()

    if not args.company and not args.site:
        log('用法: python discover_entries.py "企业名" [--list|--probe [N]] 或 --site')
        return EXIT_OK

    try:
        return await do_discover(args)
    except NotLoggedIn as ex:
        log(f'!! {ex}')
        return EXIT_LOGIN
    except QuotaExceeded as ex:
        log(f'!!! {ex}')
        return EXIT_QUOTA
    except Exception as ex:
        log(f'!! 异常 {type(ex).__name__}: {ex}')
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
