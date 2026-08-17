---
name: qyyjt-scraper
description: 企业预警通(www.qyyjt.cn)信息抓取。可抓取企业详情页任意信息维度(股东/财务/债券/风险/融资/司法/信用/图谱/新闻等全部菜单与锚点入口, 含树形菜单自动展开与父入口级联查找), 也可抓取任意站内 URL。提供入口发现(界面入口编码)、API 响应拦截(getData.action 等 JSON 直取)、通用表格提取、JSON/Excel 导出。适用于债券核查、企业尽调、名单批量补数据等任务。
---

# 企业预警通抓取工具 (qyyjt-scraper)

通过 Playwright 复用已登录浏览器态，在 www.qyyjt.cn 上抓取**任意可获取信息**。脚本位于 `qyyjt-plugin/scripts/`，运行前确认 Python 环境与登录态（见下）。

## 何时使用

- 用户要求查询某企业在企业预警通上的任何信息：股东、高管、财务、债券、融资、司法风险、信用评级、经营、新闻舆情、股权穿透等
- 债券核查类任务（存续债券判定）
- 抓取任意站内页面（搜索页/榜单页/公告页）
- 需要"这个网站能查什么"的入口清单（界面入口编码）

## 前置条件

1. Python: 已装 playwright、openpyxl（`pip install playwright openpyxl && playwright install chromium`）
2. 登录态: `%USERPROFILE%\.config\qyyjt-cli\browser-profile`（已持久化）
   - 失效时（脚本输出 `NOT LOGGED IN` / 退出码 3）: 运行 `qyyjt-plugin\scripts\login_browser.py` 扫码登录（有头浏览器）
3. 配额: 平台有"今日查询次数已达上限"限制；触发时脚本打印 `!!! 配额停止` 退出码 2，**立即停止并汇报**，不可硬闯

## 命令速查

```powershell
cd <工作区根目录>
$PY = 'python'   # 或你的 Python 解释器完整路径
$S  = 'qyyjt-plugin\scripts'

# 1) 列出某企业全部可用入口(不耗配额, 先看有什么可抓)
& $PY $S\qyyjt_fetch.py "企业名" --list

# 2) 抓指定入口(自动匹配: 中文子串/英文别名/路径导航, 自动展开树/级联父入口)
& $PY $S\qyyjt_fetch.py "企业名" --entry "债券融资"
& $PY $S\qyyjt_fetch.py "企业名" --entry "财务数据/资产负债表"   # 路径导航(父/子)
& $PY $S\qyyjt_fetch.py "企业名" --entry 股东
& $PY $S\qyyjt_fetch.py "企业名" --entry shareholder
& $PY $S\qyyjt_fetch.py "企业名" --entry "债券融资" --out 结果.xlsx
& $PY $S\qyyjt_fetch.py "企业名" --entry "债券融资" --out 结果.json --full-api
& $PY $S\qyyjt_fetch.py "企业名" --entry "财务数据/资产负债表" --params "报告期=2025年报" --out 报表.xlsx
& $PY $S\qyyjt_fetch.py "企业名" --entry "财务数据/资产负债表" --map data\site_map.json

# 3) 抓全部入口(耗配额, 谨慎; 中途配额用尽会自动保存已抓部分)
& $PY $S\qyyjt_fetch.py "企业名" --all --out 全部.xlsx

# 4) 抓任意站内 URL(搜索页/榜单页等)
& $PY $S\qyyjt_fetch.py --url "https://www.qyyjt.cn/s?tab=securities&k=重庆"

# 5) 入口编码发现(回答"网站能查什么"): 枚举+点击探测, 生成入口地图
& $PY $S\discover_entries.py "企业名" --list          # 只枚举(不耗配额)
& $PY $S\discover_entries.py "企业名" --probe 5       # 探测前5个入口的API+表格结构
& $PY $S\discover_entries.py --site                   # 站点级入口(首页导航链接)
```

## 输出说明

- 默认 stdout 摘要: 命中的 API 端点(编码/行数/键) + 每张表(列数/行数/表头) + 页面统计行
- `--out x.json`: 完整结构化结果 `[{company, entry, kind, api:[{code,url,count,rowCount,keys}], tables:[{headers,rows}], stats, text_snapshot}]`
- `--out x.xlsx`: 多 Sheet Excel（概览 + 每入口一表）
- `--full-api`: 在 json 中附带各 API 端点的完整 JSON 响应
- 退出码: 0=成功 2=配额停止 3=未登录 4=未找到/入口不存在 5=权限不足(需正式会员)

## 权限不足处理（重要）

部分数据(如司法案件明细)需要正式会员。脚本自动识别"权限不足/成为正式用户"提示:
结果标记 `permission_denied: true`、单入口抓取退出码 5、**不会把页面残留表格当正常数据**;
`--all` 时逐个标记继续。遇到退出码 5 应如实告知用户该维度需会员权限, 不要当作"无数据"。

## 入口匹配规则（--entry）

精确名 → 子串(入口含查询词) → 别名(如 bond/shareholder/financial/risk) → **树节点下沉**(可展开树父节点如"司法诉讼"自动展开后继续找真实数据入口如"司法案件 14") → 级联点击父入口(锚点型入口如"债券融资"藏在"融资速览"页面) → 模糊匹配。匹配失败会打印全部可用入口供选择。

## 数据形态适配

- 表格: 兼容 antd 固定列拆分结构; `--max-pages N` 自动翻页合并(翻页耗查询配额)
- 非表格(div/卡片/列表渲染): 自动提取 `blocks`(键值对/列表/卡片) + `text_snapshot` 正文兜底
- 每次抓取同时拦截后端 API(端点编码 + 完整 JSON, `--full-api` 开启)

## 债券核查任务模板（与旧脚本同标准）

1. `qyyjt_fetch.py "公司名" --entry "债券融资"`：判据 = 页面出现存续债券数据（债券表有数据行 / 存量概览"债券N只"N>0）
2. 对名单批量执行: 逐行取 B/C/D 列公司名 → 上述命令 → 按结果在 Excel A 列标"发债主体"
3. 亦可先 `--list` 确认入口名再定向抓取

## 注意事项

- 每次页面操作后脚本自动检测配额，命中即停止并保留已保存结果
- `--all` / `--probe N` / 展开树菜单会消耗较多查询配额；`--list` / `--url` 相对省
- 名单自带的"城投发债/城投子公司/民企/风险N"标签是源数据，与核查结论无关
- 页面结构若改版导致选择器失效，先跑 `--list` 观察入口，再针对性调整 `qyyjt_common.py` 中的 `ENTRY_KINDS`
- 不要并发运行多个抓取实例（共享同一浏览器 profile 会冲突）
