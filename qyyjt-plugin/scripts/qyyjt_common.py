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
STATIC_RE = re.compile(r'\.(js|css|png|jpe?g|gif|svg|woff2?|ttf|ico|map)(\?|$)', re.I)

# 退出码约定: 0=成功 1=异常 2=配额停止 3=未登录 4=未找到
EXIT_OK, EXIT_ERR, EXIT_QUOTA, EXIT_LOGIN, EXIT_NOTFOUND = 0, 1, 2, 3, 4


class QuotaExceeded(Exception):
    pass


class NotLoggedIn(Exception):
    pass


# ═══════════════════════════════════════════════════════════
# 浏览器生命周期
# ═══════════════════════════════════════════════════════════
async def open_browser(headless=True, profile=None, timeout=20000):
    """打开持久化上下文并做登录检查。返回 (playwright, context, page)。"""
    ud = Path(profile) if profile else USER_DATA
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


async def collect_entries(page):
    """枚举当前页面所有可点入口 -> [{kind, text, index}] (去重, 不点击)"""
    entries = await page.evaluate("""(function() {
        var out = [];
        function push(kind, els) {
            els.forEach(function(el, i) {
                var t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!t || t.length > 40) return;
                out.push({kind: kind, text: t, index: i});
            });
        }
        push('menu', document.querySelectorAll('.menu-item-wrapper'));
        push('anchor', document.querySelectorAll('.ant-anchor-link-title'));
        push('tree', document.querySelectorAll('.ant-tree-node-content-wrapper'));
        push('tab', document.querySelectorAll('.ant-tabs-tab'));
        return out;
    })()""")
    seen, uniq = set(), []
    for e in entries:
        k = (e['kind'], e['text'])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


async def expand_tree_nodes(page, depth=4):
    """全量展开树菜单(点击所有未展开子树的节点标题)。可能触发较多数据加载, 注意配额。"""
    for _ in range(depth):
        clicked = await page.evaluate("""(function() {
            var nodes = document.querySelectorAll('.ant-tree-node-content-wrapper');
            for (var i = 0; i < nodes.length; i++) {
                var treenode = nodes[i].closest('.ant-tree-treenode');
                if (!treenode) continue;
                if (treenode.className.indexOf('switcher-close') >= 0) {
                    nodes[i].click();
                    return true;
                }
            }
            return false;
        })()""")
        if not clicked:
            break
        await page.wait_for_timeout(2500)
        await check_quota(page, '展开树节点')
    return await collect_entries(page)


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
        await check_quota(page, f"点击[{entry['text']}]")
    return ok


# ═══════════════════════════════════════════════════════════
# 数据提取
# ═══════════════════════════════════════════════════════════
async def extract_tables(page):
    """提取页面所有表格 -> [{table, headers, rows, rowCount}]"""
    return await page.evaluate("""(function() {
        var out = [];
        document.querySelectorAll('table').forEach(function(t, i) {
            var headers = [];
            t.querySelectorAll('th').forEach(function(th) {
                var h = (th.innerText || '').trim().split('\\n')[0];
                if (h && headers.indexOf(h) < 0) headers.push(h);
            });
            if (!headers.length) return;
            var rows = [];
            t.querySelectorAll('tbody tr').forEach(function(tr) {
                var cells = [];
                tr.querySelectorAll('td').forEach(function(td) {
                    cells.push((td.innerText || '').trim());
                });
                if (cells.length) rows.push(cells);
            });
            out.push({table: i + 1, headers: headers, rows: rows, rowCount: rows.length});
        });
        return out;
    })()""")


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
