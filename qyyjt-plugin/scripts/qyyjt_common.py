# -*- coding: utf-8 -*-
"""qyyjt 通用抓取库 — 企业预警通(www.qyyjt.cn)自动化

能力:
  1. 登录态复用(launch_persistent_context, 复用 browser-profile)
  2. 企业搜索 + 名称匹配 + 详情页跳转
  3. 入口发现: 枚举详情页所有可点导航入口(菜单/锚点/树节点/Tab), 不耗查询配额
  4. API 拦截: 捕获页面触发的后端接口(getData.action 等), 直接拿 JSON
  5. 通用表格提取: 任意 table -> {headers, rows}
  6. 配额/登录检测, 弹窗清理

被 qyyjt_fetch.py / discover_entries.py 复用。跨平台(Windows/macOS/Linux)兼容。
"""
import asyncio
import io
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright

if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE_URL = 'https://www.qyyjt.cn'
USER_DATA = Path.home() / '.config' / 'qyyjt-cli' / 'browser-profile'
QUOTA_MARKS = ['今日查询次数已达上限', '查询次数已达上限', '次数已达上限']
PERMISSION_MARKS = ['权限不足', '成为正式用户', '无权限查看', '开通会员', '会员专享', '付费解锁']
STATIC_RE = re.compile(r'\.(js|css|png|jpe?g|gif|svg|woff2?|ttf|ico|map)(\?|$)', re.I)

# 退出码约定: 0=成功 1=异常 2=配额停止 3=未登录 4=未找到 5=权限不足
EXIT_OK, EXIT_ERR, EXIT_QUOTA, EXIT_LOGIN, EXIT_NOTFOUND, EXIT_PERM = 0, 1, 2, 3, 4, 5


def resolve_profile(name):
    """--profile 参数归一化: 名字 -> ~/.config/qyyjt-cli/browser-profile-<名>; 路径 -> 原样。"""
    if not name:
        return USER_DATA
    p = Path(name)
    if p.is_absolute() or ('\\' in name) or ('/' in name):
        return p
    return USER_DATA.parent / f'browser-profile-{name}'


class QuotaExceeded(Exception):
    pass


class NotLoggedIn(Exception):
    pass


class PermissionDenied(Exception):
    """目标数据需要付费/会员权限, 页面明确提示无权查看。"""
    pass


# ═══════════════════════════════════════════════════════════
# 浏览器生命周期
# ═══════════════════════════════════════════════════════════
async def open_browser(headless=True, profile=None, timeout=20000):
    """打开持久化上下文并做登录检查。返回 (playwright, context, page)。"""
    ud = resolve_profile(profile)
    p = await async_playwright().start()
    b = await p.chromium.launch_persistent_context(
        user_data_dir=str(ud), headless=headless,
        args=['--disable-blink-features=AutomationControlled'])
    pg = await b.new_page()
    await pg.goto(BASE_URL, wait_until='domcontentloaded', timeout=timeout)
    await pg.wait_for_timeout(1200)
    if '/login' in pg.url:
        await b.close()
        await p.stop()
        raise NotLoggedIn('NOT LOGGED IN: 登录态无效, 请先运行 login_browser.py 扫码登录')
    return p, b, pg


async def close_browser(p, b):
    try:
        await b.close()
    except Exception:
        pass
    try:
        await p.stop()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# 配额 / 弹窗
# ═══════════════════════════════════════════════════════════
async def check_quota(page, where=''):
    """页面正文出现配额提示 -> 抛 QuotaExceeded"""
    body = await page.evaluate('() => document.body ? document.body.innerText : ""')
    for m in QUOTA_MARKS:
        i = body.find(m)
        if i >= 0:
            ctx = body[max(0, i - 40):i + 90].replace('\n', '|')
            raise QuotaExceeded(f'{where} 检测到 [{m}]: ...{ctx}')


async def check_permission(page, where=''):
    """页面正文出现付费/会员权限提示 -> 抛 PermissionDenied

    用途: 点击入口后若目标数据需要正式用户权限, 立即识别并把结果标记为
    permission_denied, 而不是把残留的旧表格当作正常数据返回。
    """
    body = await page.evaluate('() => document.body ? document.body.innerText : ""')
    for m in PERMISSION_MARKS:
        i = body.find(m)
        if i >= 0:
            ctx = body[max(0, i - 40):i + 110].replace('\n', '|')
            raise PermissionDenied(f'{where} 检测到权限提示 [{m}]: ...{ctx}')


async def dismiss_modals(page):
    """关掉 antd 弹窗(登录提醒/公告等)"""
    try:
        await page.evaluate("""(function() {
            document.querySelectorAll('.ant-modal-close').forEach(function(m) { m.click(); });
            document.querySelectorAll('.ant-modal-footer button, .ant-modal-confirm-btns button')
                .forEach(function(b) {
                    var t = b.innerText || '';
                    if (t.indexOf('取消') >= 0 || t.indexOf('Cancel') >= 0) b.click();
                });
        })()""")
        await page.wait_for_timeout(400)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# 搜索与匹配
# ═══════════════════════════════════════════════════════════
async def search_company(page, name, limit=10):
    """首页搜索 -> [{name, code}]"""
    await page.goto(BASE_URL, wait_until='domcontentloaded', timeout=20000)
    await page.wait_for_timeout(1200)
    await dismiss_modals(page)
    inp = page.locator('input.ant-input').last
    await inp.click()
    await page.wait_for_timeout(150)
    await page.keyboard.press('Control+a')
    await page.keyboard.press('Backspace')
    await page.wait_for_timeout(100)
    await inp.type(name, delay=20)
    await page.keyboard.press('Enter')
    await page.wait_for_timeout(3500)
    await check_quota(page, '搜索')
    return await page.evaluate("""(function(limit) {
        var links = document.querySelectorAll('a[href*="detail/enterprise"]');
        var seen = new Set(), items = [];
        links.forEach(function(a) {
            var n = (a.innerText || '').trim().split('\\n')[0];
            var m = (a.getAttribute('href') || '').match(/code=([A-F0-9]+)/);
            if (!m || !n || n.length < 4 || seen.has(n)) return;
            seen.add(n);
            items.push({name: n, code: m[1]});
        });
        return items.slice(0, limit);
    })""", limit)


def core_name(n):
    """归一化公司名用于匹配: 去空格/括号, 去'重庆'前缀"""
    n = re.sub(r'[（()）\s·]', '', str(n))
    return re.sub(r'^重庆市?', '', n)


def pick_best(name, results):
    """双向子串匹配, 返回最佳结果或 None"""
    c = core_name(name)
    for r in results:
        rc = core_name(r['name'])
        if c in rc or (len(c) >= 6 and rc in c):
            return r
    return None


# ═══════════════════════════════════════════════════════════
# 详情页
# ═══════════════════════════════════════════════════════════
async def goto_overview(page, code, wait_menu=True, timeout=20000):
    """进入企业详情页, 等左侧菜单加载完成"""
    url = f'{BASE_URL}/detail/enterprise/overview?code={code}&type=company'
    await page.goto(url, wait_until='domcontentloaded', timeout=timeout)
    if wait_menu:
        for _ in range(15):
            n = await page.evaluate(
                '() => document.querySelectorAll(".menu-item-wrapper").length')
            if n > 5:
                break
            await page.wait_for_timeout(1000)
    await check_quota(page, '详情页')


# ═══════════════════════════════════════════════════════════
# 入口发现 (不点击, 不耗配额)
# ═══════════════════════════════════════════════════════════
ENTRY_KINDS = [
    ('menu', '.menu-item-wrapper'),          # 左侧主菜单
    ('anchor', '.ant-anchor-link-title'),    # 锚点导航(右侧)
    ('tree', '.ant-tree-node-content-wrapper'),  # 树节点(企业融资等)
    ('tab', '.ant-tabs-tab'),                # Tab 页签
]


# ═══════════════════════════════════════════════════════════
# 入口发现 V2: 树形结构 (不点击, 不耗配额)
# ═══════════════════════════════════════════════════════════
async def collect_tree(page):
    """读取左侧菜单树(ant-tree 扁平节点 + level class 重建嵌套)。

    返回嵌套树: [{text, level, kind:'tree', expandable, sw, index, children}]
    - expandable=True 表示可展开目录节点(点击只展开子树, 不加载数据)
    - index 为该节点 .ant-tree-node-content-wrapper 的当前 DOM 序号
    - 展开目录后 DOM 序号会变化, 使用前需重新 collect_tree
    """
    flat = await page.evaluate("""(function() {
        var out = [];
        var wrappers = Array.prototype.slice.call(
            document.querySelectorAll('.ant-tree-node-content-wrapper'));
        document.querySelectorAll('.ant-tree-treenode').forEach(function(n) {
            var title = n.querySelector('.ant-tree-title');
            var sw = n.querySelector('.ant-tree-switcher');
            var text = title ? (title.innerText || '').replace(/\\s+/g, ' ').trim() : '';
            if (!text) return;
            var cls = n.className || '';
            var m = cls.match(/menu-(?:subTree|treeItem) menu-[^ ]*level-(\\d)/);
            var swCls = sw ? sw.className : '';
            var swState = swCls.indexOf('open') >= 0 ? 'open'
                        : swCls.indexOf('close') >= 0 ? 'close' : 'leaf';
            var w = n.querySelector('.ant-tree-node-content-wrapper');
            out.push({text: text, level: m ? parseInt(m[1]) : 0,
                      sw: swState, index: w ? wrappers.indexOf(w) : -1});
        });
        return out;
    })()""")
    return rebuild_tree(flat)


def rebuild_tree(flat):
    """深度优先扁平节点(带 level) -> 嵌套树。level 递增压栈, 递减弹栈。"""
    roots, stack = [], []
    for f in flat:
        node = {'text': f['text'], 'level': f['level'], 'kind': 'tree',
                'expandable': f['sw'] in ('open', 'close'),
                'sw': f['sw'], 'index': f['index'], 'children': []}
        while stack and stack[-1][1] >= f['level']:
            stack.pop()
        if stack:
            stack[-1][0]['children'].append(node)
        else:
            roots.append(node)
        if node['expandable']:
            stack.append((node, f['level']))
    return roots


def flatten_tree(tree, path=None):
    """嵌套树 -> 扁平入口列表 [{kind, text, path, index, expandable}] (深度优先)"""
    path = path or []
    out = []
    for n in tree:
        p = path + [n['text']]
        out.append({'kind': n['kind'], 'text': n['text'], 'path': p,
                    'index': n['index'], 'expandable': n.get('expandable', False)})
        if n.get('children'):
            out.extend(flatten_tree(n['children'], p))
    return out


def locate_path(tree, path):
    """按文本路径逐级定位树节点: locate_path(tree, ['财务数据','资产负债表'])"""
    level = tree
    for seg in path:
        e = find_in_level(level, seg)
        if e is None:
            return None
        level = e.get('children', [])
    return e


def find_in_level(nodes, seg):
    """当前层匹配: 精确 -> 子串 -> 数字后缀归一化('司法案件 14' vs '司法案件')"""
    seg = (seg or '').strip()
    if not seg:
        return None
    for n in nodes:
        if n['text'] == seg:
            return n
    for n in nodes:
        if seg in n['text'] or n['text'] in seg:
            return n
    for n in nodes:
        t = re.sub(r'\s*\d+$', '', n['text'])
        if t == seg or (seg in t):
            return n
    return None


async def click_path(page, path, wait_ms=5000):
    """按路径逐级导航并点击最后一级: click_path(page, ['财务数据','资产负债表'])

    中间级: 若可展开则点击展开(等 2.5s)后重新读树; 最后一级: 点击进入
    (expandable 目录则只展开不加载数据)。返回命中的入口 dict 或 None。
    """
    for i in range(len(path) - 1):
        tree = await collect_tree(page)
        e = locate_path(tree, path[:i + 1])
        if e is None:
            return None
        if e.get('expandable'):
            ok = await click_entry(page, {'kind': 'tree', 'index': e['index']}, wait_ms=2500)
            if not ok:
                return None
    tree = await collect_tree(page)
    e = locate_path(tree, path)
    if e is None:
        return None
    if e.get('expandable'):
        await click_entry(page, {'kind': 'tree', 'index': e['index']}, wait_ms=2500)
    else:
        ok = await click_entry(page, {'kind': 'tree', 'index': e['index']}, wait_ms=wait_ms)
        if not ok:
            return None
    return e


async def collect_entries(page):
    """枚举当前页面所有可点入口 -> [{kind, text, index, expandable, path}] (去重, 不点击)

    tree 菜单按嵌套树扁平化(带完整 path); 锚点/tab 作为页面级附加入口。
    expandable=True 表示可展开的树目录节点(如'财务数据'/'司法诉讼'),
    点击只展开子树、不加载数据。
    """
    entries = []
    tree = await collect_tree(page)
    entries.extend(flatten_tree(tree))
    extras = await page.evaluate("""(function() {
        var out = [];
        function push(kind, els) {
            els.forEach(function(el, i) {
                var t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!t || t.length > 40) return;
                out.push({kind: kind, text: t, index: i});
            });
        }
        push('anchor', document.querySelectorAll('.ant-anchor-link-title'));
        push('tab', document.querySelectorAll('.ant-tabs-tab'));
        return out;
    })()""")
    for x in extras:
        entries.append({'kind': x['kind'], 'text': x['text'], 'path': [x['text']],
                        'index': x['index'], 'expandable': False})
    return entries


async def expand_tree_nodes(page, depth=30):
    """全量展开树菜单: 每轮批量点击所有未展开子树, 直到无可展开节点。

    修复说明: 旧版每轮只展开 1 个节点且 depth=4, 10 个目录只能展开 4-5 个,
    未展开目录的子入口不在 DOM 中无法枚举。新版每轮点击全部 close 节点,
    循环至无 close 为止(depth 为轮次上限)。注意: 展开会触发子菜单数据加载,
    可能消耗查询配额, 命中即抛 QuotaExceeded。
    """
    for _ in range(depth):
        clicked = await page.evaluate("""(function() {
            var clicked = false;
            var nodes = document.querySelectorAll('.ant-tree-node-content-wrapper');
            for (var i = 0; i < nodes.length; i++) {
                var treenode = nodes[i].closest('.ant-tree-treenode');
                if (!treenode) continue;
                var cls = treenode.className || '';
                if (cls.indexOf('switcher-close') >= 0) {
                    nodes[i].click();
                    clicked = true;
                }
            }
            return clicked;
        })()""")
        if not clicked:
            break
        await page.wait_for_timeout(2500)
        await check_quota(page, '展开树节点')
    return await collect_tree(page)


async def expand_tree_for_keyword(page, query, depth=3):
    """按需展开: 点击文本含关键词片段的未展开子树, 返回展开后的完整入口列表。

    关键词候选: 原词 / 前2字 / 后2字 / 前3字 / 后3字(如 '债券融资' -> 命中父节点'企业融资')。
    """
    q = (query or '').strip()
    cands = []
    for c in [q, q[:2], q[-2:], q[:3], q[-3:]]:
        if c and c not in cands:
            cands.append(c)
    for kw in cands:
        for _ in range(depth):
            clicked = await page.evaluate("""(function(kw) {
                var nodes = document.querySelectorAll('.ant-tree-node-content-wrapper');
                for (var i = 0; i < nodes.length; i++) {
                    var t = (nodes[i].innerText || '').replace(/\\s+/g, '');
                    if (t.indexOf(kw) < 0) continue;
                    var treenode = nodes[i].closest('.ant-tree-treenode');
                    if (!treenode) continue;
                    if (treenode.className.indexOf('switcher-close') >= 0) {
                        nodes[i].click();
                        return true;
                    }
                }
                return false;
            })""", kw)
            if not clicked:
                break
            await page.wait_for_timeout(2500)
            await check_quota(page, f'展开[{kw}]')
        entries = await collect_entries(page)
        if any(kw in e['text'] or e['text'] in kw for e in entries):
            return entries
    return await collect_entries(page)


async def click_entry(page, entry, wait_ms=5000):
    """按入口定位信息点击, 等待页面更新。返回是否成功。"""
    ok = await page.evaluate("""(function(arg) {
        var kind = arg.kind, index = arg.index;
        var els;
        if (kind === 'menu') els = document.querySelectorAll('.menu-item-wrapper');
        else if (kind === 'anchor') els = document.querySelectorAll('.ant-anchor-link-title');
        else if (kind === 'tree') els = document.querySelectorAll('.ant-tree-node-content-wrapper');
        else if (kind === 'tab') els = document.querySelectorAll('.ant-tabs-tab');
        else return false;
        if (!els[index]) return false;
        els[index].click();
        return true;
    })""", {'kind': entry['kind'], 'index': entry['index']})
    if ok:
        await page.wait_for_timeout(wait_ms)
        await check_quota(page, f"点击[{entry.get('text', '')}]")
    return ok


# ═══════════════════════════════════════════════════════════
# 数据提取
# ═══════════════════════════════════════════════════════════
async def extract_tables_once(page):
    """提取页面所有表格(单次, 不翻页) -> [{table, headers, rows, rowCount}]

    兼容 antd 固定列结构: 表头与数据行可能被拆成两个 <table>(表头表 tbody 为空,
    数据表无 th)。以 .ant-table-wrapper 为容器合并 th + 全部 tbody 行。
    """
    return await page.evaluate("""(function() {
        var out = [];
        function grab(t) {
            var headers = [], rows = [];
            t.querySelectorAll('th').forEach(function(th) {
                var h = (th.innerText || '').trim().split('\\n')[0];
                if (h && headers.indexOf(h) < 0) headers.push(h);
            });
            t.querySelectorAll('tbody tr').forEach(function(tr) {
                var cells = [];
                tr.querySelectorAll('td').forEach(function(td) {
                    cells.push((td.innerText || '').trim());
                });
                if (cells.length) rows.push(cells);
            });
            return {headers: headers, rows: rows};
        }
        function pushTable(idx, tables) {
            var headers = [], rows = [];
            tables.forEach(function(t) {
                var g = grab(t);
                g.headers.forEach(function(h) { if (headers.indexOf(h) < 0) headers.push(h); });
                rows = rows.concat(g.rows);
            });
            if (!headers.length && !rows.length) return;
            out.push({table: idx, headers: headers, rows: rows, rowCount: rows.length});
        }
        var wrappers = document.querySelectorAll('.ant-table-wrapper');
        if (wrappers.length) {
            wrappers.forEach(function(w, i) {
                pushTable(i + 1, w.querySelectorAll('table'));
            });
        } else {
            document.querySelectorAll('table').forEach(function(t, i) {
                pushTable(i + 1, [t]);
            });
        }
        return out;
    })()""")


async def extract_tables(page, paginate=False, max_pages=5):
    """提取页面所有表格; paginate=True 时对每个 antd 表格自动翻页合并(耗配额!)

    注意: 点击下一页会触发后端分页请求, 消耗每日查询配额。max_pages 控制单表
    最大翻页数(默认 5 页, 即最多 4 次翻页)。行按内容去重。
    """
    tables = await extract_tables_once(page)
    if not paginate or not tables:
        return tables
    for ti, t in enumerate(tables):
        seen_rows = {tuple(r) for r in t['rows']}
        for _ in range(max(0, max_pages - 1)):
            clicked = await page.evaluate("""(function(ti) {
                var wrappers = document.querySelectorAll('.ant-table-wrapper');
                var w = wrappers[ti];
                if (!w) return false;
                var next = w.querySelector('.ant-pagination-next');
                if (!next || (next.className || '').indexOf('ant-pagination-disabled') >= 0)
                    return false;
                next.click();
                return true;
            })()""", ti)
            if not clicked:
                break
            await page.wait_for_timeout(1800)
            await check_quota(page, f'翻页(表{ti + 1})')
            rows = await page.evaluate("""(function(ti) {
                var w = document.querySelectorAll('.ant-table-wrapper')[ti];
                if (!w) return [];
                var rows = [];
                w.querySelectorAll('tbody tr').forEach(function(tr) {
                    var cells = [];
                    tr.querySelectorAll('td').forEach(function(td) {
                        cells.push((td.innerText || '').trim());
                    });
                    if (cells.length) rows.push(cells);
                });
                return rows;
            })()""", ti)
            new_rows = [r for r in rows if tuple(r) not in seen_rows]
            if not new_rows:
                break  # 下一页无新数据(或已到末页)
            for r in new_rows:
                seen_rows.add(tuple(r))
                t['rows'].append(r)
        t['rowCount'] = len(t['rows'])
        t['paginated'] = True
    return tables


async def extract_blocks(page, max_items=200):
    """提取非 table 渲染的数据: 键值对(descriptions)/列表项/卡片块。

    企业预警通大量数据(司法案件、舆情指数等)用 div/卡片渲染, 不进入 <table>。
    作为 extract_tables 的降级通道返回结构化块: [{type, label, title, value}]。
    """
    return await page.evaluate("""(function(maxItems) {
        var out = [];
        // 1) 键值对块 (antd descriptions)
        document.querySelectorAll('.ant-descriptions-item').forEach(function(it) {
            var label = it.querySelector('.ant-descriptions-item-label');
            var content = it.querySelector('.ant-descriptions-item-content');
            if (label && content) {
                var l = (label.innerText || '').replace(/\\s+/g, ' ').trim();
                var c = (content.innerText || '').replace(/\\s+/g, ' ').trim();
                if (l && c && out.length < maxItems)
                    out.push({type: 'kv', label: l.slice(0, 60), value: c.slice(0, 300)});
            }
        });
        // 2) 列表项
        var seenLi = new Set();
        document.querySelectorAll('.ant-list-item').forEach(function(li) {
            var t = (li.innerText || '').replace(/\\s+/g, ' ').trim();
            if (t && t.length >= 2 && t.length <= 400 && !seenLi.has(t) && out.length < maxItems) {
                seenLi.add(t);
                out.push({type: 'list', value: t});
            }
        });
        // 3) 卡片块
        var seenCard = new Set();
        document.querySelectorAll('.ant-card').forEach(function(c) {
            var t = (c.innerText || '').replace(/\\s+/g, ' ').trim();
            if (t && t.length <= 800 && !seenCard.has(t) && out.length < maxItems) {
                seenCard.add(t);
                var title = c.querySelector('.ant-card-head-title');
                out.push({type: 'card',
                          title: title ? (title.innerText || '').trim() : '',
                          value: t});
            }
        });
        return out.slice(0, maxItems);
    })()""", max_items)


async def page_text(page, maxlen=8000):
    """页面正文快照(供无表格/JSON 的入口)"""
    t = await page.evaluate('() => document.body ? document.body.innerText : ""')
    return t[:maxlen]


async def stat_lines(page, limit=60):
    """页面关键统计行(如'债券存量规模…')"""
    t = await page.evaluate('() => document.body ? document.body.innerText : ""')
    return [ln.strip() for ln in t.split('\n') if ln.strip()][:limit]


# ═══════════════════════════════════════════════════════════
# API 拦截 (Playwright response 事件)
# ═══════════════════════════════════════════════════════════
def url_code(url):
    """API URL -> 稳定端点编码(去掉企业 code 等动态参数)"""
    u = urlparse(url)
    q = parse_qs(u.query)
    action = q.get('action', [''])[0]
    if action:
        return f'{u.path}?action={action}'
    name = u.path.rstrip('/').split('/')[-1]
    return f'{u.path}?{name}' if name else u.path


class ApiCapture:
    """拦截 qyyjt.cn 后端接口响应, 记录 端点编码 -> JSON 摘要/全文。

    用法:
        cap = ApiCapture(page)
        ... 页面操作 ...
        await cap.drain()          # 等挂起的响应处理完
        cap.summary()              # [{code, url, keys, rowCount}]
        cap.full()                 # {code: json}
    """

    def __init__(self, page, keep_full=False, min_len=30):
        self.page = page
        self.keep_full = keep_full
        self.min_len = min_len
        self._data = {}          # code -> {'url':..., 'keys':..., 'full':...}
        self._tasks = []
        page.on('response', self._on_response)

    def _on_response(self, resp):
        self._tasks.append(asyncio.ensure_future(self._handle(resp)))

    async def _handle(self, resp):
        try:
            url = resp.url
            if 'qyyjt.cn' not in url:
                return
            if STATIC_RE.search(url):
                return
            body = await resp.text()
            if not body or len(body) < self.min_len:
                return
            try:
                data = json.loads(body)
            except Exception:
                return
            if not isinstance(data, (dict, list)):
                return
            code = url_code(url)
            rec = self._data.get(code)
            if rec is None:
                rec = {'url': url, 'count': 0, 'keys': [], 'rowCount': 0}
                self._data[code] = rec
            rec['count'] += 1
            if isinstance(data, dict):
                if not rec['keys']:
                    rec['keys'] = list(data.keys())[:25]
                inner = data.get('data')
                if isinstance(inner, list) and not rec['rowCount']:
                    rec['rowCount'] = len(inner)
            if self.keep_full:
                rec.setdefault('full', data)
        except Exception:
            pass

    async def drain(self, timeout=6.0):
        """等待所有已触发的响应处理完"""
        if self._tasks:
            done, _ = await asyncio.wait(self._tasks, timeout=timeout)
            self._tasks = [t for t in self._tasks if t not in done]

    def summary(self):
        return [{'code': k, 'url': v['url'], 'count': v['count'],
                 'keys': v['keys'], 'rowCount': v['rowCount']}
                for k, v in self._data.items()]

    def full(self):
        return {k: v.get('full') for k, v in self._data.items()}


# ═══════════════════════════════════════════════════════════
# 站点级入口(首页导航/搜索 URL 模板)
# ═══════════════════════════════════════════════════════════
async def collect_site_links(page):
    """采集当前页所有站内链接(导航菜单等) -> [{text, href}] 去重"""
    links = await page.evaluate("""(function() {
        var out = [];
        document.querySelectorAll('a[href]').forEach(function(a) {
            var h = a.getAttribute('href');
            var t = (a.innerText || '').trim().split('\\n')[0].trim();
            if (!h || !t || t.length > 30 || h.length > 150) return;
            if (h.indexOf('javascript:') === 0) return;
            out.push({text: t, href: h});
        });
        return out;
    })()""")
    seen, uniq = set(), []
    for l in links:
        k = (l['text'], l['href'])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(l)
    return uniq


def search_url(keyword, tab='securities'):
    """搜索页 URL 模板: /s?tab=securities&k=关键词"""
    from urllib.parse import quote
    return f'{BASE_URL}/s?tab={tab}&k={quote(keyword)}'
