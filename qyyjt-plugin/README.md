# qyyjt-plugin — 企业预警通通用抓取插件

一套**通用**的企业预警通（www.qyyjt.cn）抓取工具：不局限于固定字段，而是通过**入口发现 + API 拦截**动态支持网站上任意可获取的信息维度。适用于 Claude Code / Codex / DeepSeek Harness (DSH) 三种 agent 环境。

## 核心设计：为什么能抓"所有信息"

传统脚本为每个字段写死选择器；本插件反其道而行：

1. **入口发现**（`discover_entries.py`）：自动枚举企业详情页**全部可点入口**——左侧菜单（`.menu-item-wrapper`）、锚点导航（`.ant-anchor-link-title`）、树形菜单（`.ant-tree-node-content-wrapper`，自动递归展开）、Tab（`.ant-tabs-tab`），记录每个入口的中文名 + DOM 定位器（kind+index），即"界面入口编码"。
2. **API 响应拦截**（`ApiCapture`）：点击入口时拦截页面触发的所有后端接口（`getData.action`、`/finchinaAPP/v1/...` 等），把 URL 归一化为**端点编码**并保留 JSON——比 DOM 提取更可靠，且能拿到页面未渲染的隐藏字段。
3. **通用表格提取**：任意 `table` → `{headers, rows}`，覆盖所有表格型数据。
4. **级联匹配**：入口名不直接可见时（如"债券融资"藏在"融资速览"页面的锚点里），自动展开树菜单、点击父入口逐级查找。

实测（2026-08-06，重庆三峡发展集团）：
- 单企业详情页枚举出 **39 个入口**（11 菜单 + 21 树节点 + 6 Tab）
- "融资速览"入口捕获 **10 个债券相关 API 端点** + 9 张表（存续债券/融资租赁/股权质押/承兑逾期/授信/债券注册等）
- 搜索页 `--url` 模式捕获 13 个搜索类 API 端点

## 目录结构

```
qyyjt-plugin/
├── README.md
├── scripts/
│   ├── qyyjt_common.py        # 共享库(登录/搜索/入口发现/API拦截/表格提取)
│   ├── qyyjt_fetch.py         # 通用抓取 CLI
│   └── discover_entries.py    # 入口编码发现器
└── data/
    └── entries_map.json       # 入口地图样例(三峡发展集团, --probe 3)
```

## 三平台接入

| 平台 | 接入文件 | 说明 |
|---|---|---|
| **DeepSeek Harness** | `.dsh/skills/qyyjt-scraper/SKILL.md` | 项目级技能目录，DSH 自动发现并加入会话技能目录；frontmatter 含 `name`+`description` |
| **Claude Code** | `.claude/skills/qyyjt-scraper/SKILL.md` | Claude Code 技能目录（`/skills` 命令查看）；也可用 `claude --add-dir` 添加 |
| **Codex / 通用 agent** | `AGENTS.md`（工作区根） | 指令文件，agent 自动读取，含命令速查与关键规则 |

## 安装（首次）

```powershell
# 1. 依赖(若未安装)
pip install playwright openpyxl
playwright install chromium

# 2. 登录一次(有头浏览器, 扫码/短信; 用你自己的账号)
python qyyjt-plugin\scripts\login_browser.py
# 登录态持久化到 %USERPROFILE%\.config\qyyjt-cli\browser-profile
```

> 注意: 登录态含账号 Cookie, 请勿把 `%USERPROFILE%\.config\qyyjt-cli\browser-profile`
> 目录分享给他人; 每个使用者用自己的账号各自登录一次即可。

## 命令参考

```powershell
$PY = 'python'   # 或你的 Python 解释器完整路径(如 C:\Users\<你>\AppData\Local\Programs\Python\Python314\python.exe)
$S  = 'qyyjt-plugin\scripts'

# 列入口(不耗配额)
& $PY $S\qyyjt_fetch.py "企业名" --list

# 抓指定维度(自动匹配/展开树/级联/路径导航)
& $PY $S\qyyjt_fetch.py "企业名" --entry "债券融资"
& $PY $S\qyyjt_fetch.py "企业名" --entry "财务数据/资产负债表"   # 路径导航: 展开财务数据→资产负债表
& $PY $S\qyyjt_fetch.py "企业名" --entry shareholder --out 股东.xlsx
& $PY $S\qyyjt_fetch.py "企业名" --entry "债券融资" --out r.json --full-api

# 抓全部(耗配额)
& $PY $S\qyyjt_fetch.py "企业名" --all --out all.xlsx

# 任意 URL(搜索页/榜单页等)
& $PY $S\qyyjt_fetch.py --url "https://www.qyyjt.cn/s?tab=securities&k=重庆"

# 路径导航 + 筛选参数(报表等)
& $PY $S\qyyjt_fetch.py "企业名" --entry "财务数据/资产负债表" --params "报告期=2025年报&合并=合并期末" --out 报表.xlsx
& $PY $S\qyyjt_fetch.py "企业名" --entry "财务数据/资产负债表" --map data\site_map.json

# 结构扫描 -> 按需打开(两步走, 省配额)
& $PY $S\qyyjt_fetch.py "企业名" --scan                              # 扫描全部入口(约10-12次查询)
& $PY $S\qyyjt_fetch.py "企业名" --open --entry "财务数据/主要财务指标"  # 校验scan缓存后路径直达

# 入口编码发现 / 分支地图(逐目录递归采集, 耗配额)
& $PY $S\discover_entries.py "企业名" --list
& $PY $S\discover_entries.py "企业名" --probe-recursive 5 --out data\site_map.json
& $PY $S\discover_entries.py --site
```

## 常用参数

| 参数 | 说明 |
|---|---|
| `--entry 文本` | 入口匹配：**路径**（`父/子`，如 `财务数据/资产负债表`）→精确→子串→别名→**树节点下沉**（可展开树父节点自动展开后继续找数据入口）→级联父入口→模糊 |
| `--out 文件` | `.json` 结构化 / `.xlsx` 多 Sheet；缺省仅打印摘要 |
| `--full-api` | JSON 输出附带各 API 端点完整响应 |
| `--all` | 遍历全部入口逐个抓取（注意配额） |
| `--expand` | 匹配失败时全量展开树菜单（耗配额） |
| `--max-pages N` | 表格翻页上限（默认 0=不翻页；N=每表最多 N 页；**翻页耗查询配额**） |
| `--params 筛选` | 路径模式筛选参数：`报告期=2025年报&合并=合并期末&单位=万元`（中文名自动映射 API 参数） |
| `--map 文件` | 分支地图 site_map.json v2；命中分支自动复用其参数模板 |
| `--url URL` | 直接抓任意站内页面 |
| `--probe N` | (discover) 探测前 N 个入口的 API+表格结构 |
| `--headed` | 有头模式（调试） |
| `--profile 路径` | 自定义浏览器 profile |

## 分享给他人

本插件自包含（脚本 + 登录器 + 三平台接入文件）。分享时：

1. **不要分享**：`%USERPROFILE%\.config\qyyjt-cli\browser-profile`（含你的账号 Cookie/登录态）和任何抓取结果中含敏感数据的文件
2. **发送以下内容**（发布包 `qyyjt-scraper.zip`，见根目录生成命令）：

```
qyyjt-scraper/
├── INSTALL.md                    # 接收方安装指南(先读这个)
├── AGENTS.md                     # Codex / 通用 agent 接入
├── qyyjt-plugin/
│   ├── README.md
│   ├── scripts/                  # qyyjt_common.py / qyyjt_fetch.py /
│   │                             # discover_entries.py / login_browser.py
│   └── data/                     # 入口地图样例
├── .dsh/skills/qyyjt-scraper/SKILL.md      # DSH 接入
└── .claude/skills/qyyjt-scraper/SKILL.md   # Claude Code 接入
```

3. 接收方步骤（详见 `INSTALL.md`）：装依赖 → 用自己的账号登录一次 → 整个目录放进其工作区根 → 即可被三个平台的 agent 自动发现调用

## 退出码

`0` 成功 · `1` 异常 · `2` 配额停止（换账号/次日继续） · `3` 未登录（先 login_browser.py） · `4` 未找到/入口不存在 · `5` 权限不足（需正式会员）

## 权限不足处理（重要）

部分数据（如司法案件明细）需要**正式会员**权限。点击入口后页面会显示"权限不足/成为正式用户即可查看"：

- 脚本自动识别（`权限不足/成为正式用户/无权限查看/会员专享` 等提示），结果标记 `permission_denied: true`，单入口抓取退出码 5
- **不会**把页面残留的其它表格当作正常数据返回（识别先于数据提取）
- `--all` 批量时逐个标记并继续，最后汇总"其中 N 个入口因权限不足未取到数据"
- 处理方式：升级正式会员后重跑，或在结果中跳过该维度

## 分支导航与报表矩阵（P1-P3）

- **BranchNavigator**：路径导航自动重试——每步点击后比对页面内容签名，无变化视为未生效并重试；网络静默等待
- **ParamRewriter**：`--params` 拦截 API 改写查询参数（页面上下文自动带签名头）；参数名可用 `--full-api` 输出的 API query 或地图 paramTemplate
- **MatrixParser**：报表多期矩阵（科目/缩进/加粗/各期数值）结构化提取，Excel 输出含标题、单位、千分位格式
- **分支地图**（`--probe-recursive`）：逐目录展开采集全部分支（适配手风琴菜单），生成 site_map.json v2；`--map` 复用免重复探测

## 数据形态适配

- **表格**：兼容 antd 固定列结构（表头与数据行拆成两个 `<table>` 时自动合并）；`--max-pages N` 自动翻页合并（耗配额）
- **非表格**（div/卡片/列表渲染，如司法案件、舆情指数）：自动提取内容块 `blocks`（键值对/列表项/卡片），JSON 输出含完整正文快照 `text_snapshot` 兜底
- **API**：每次抓取同时拦截页面触发的后端接口，JSON 输出含端点编码与完整响应（`--full-api`）

## 与旧脚本的关系

- 旧脚本（`check_bonds_district.py` 等）为任务定制；本插件是其**通用化升级**：同一登录态、同一判据标准，但入口/API/表格全部动态发现，不再硬编码
- 债券核查任务模板：`qyyjt_fetch.py "公司" --entry "债券融资"` → 页面债券表有数据行或存量概览"债券N只"N>0 → A 列标"发债主体"

## 配额纪律（重要）

平台有每日查询次数上限。脚本检测到"今日查询次数已达上限"即停止（退出码 2）。**遇到配额请立即停止并汇报，不要重试硬闯**；换账号（登录你自己的备用账号）或次日再跑。`--list` 与 `--url` 开销小，`--all`/`--probe N`/展开树开销大。
