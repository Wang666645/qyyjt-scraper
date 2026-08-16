# AGENTS.md — 企业预警通抓取工具（qyyjt-scraper）

本仓库含企业预警通（www.qyyjt.cn）通用抓取工具。**当用户要求查询企业预警通上的任意企业信息时，优先使用本工具，不要自行发明抓取方法。**

## 工具位置

| 文件 | 作用 |
|---|---|
| `qyyjt-plugin/scripts/qyyjt_common.py` | 共享库：登录态复用、搜索、入口发现、API 拦截、表格提取 |
| `qyyjt-plugin/scripts/qyyjt_fetch.py` | 通用抓取 CLI（企业任意入口 / 任意 URL） |
| `qyyjt-plugin/scripts/discover_entries.py` | 入口编码发现器（生成入口地图） |
| `qyyjt-plugin/data/entries_map.json` | 入口地图样例（三峡发展集团） |

## 标准用法

```powershell
$PY = 'python'   # 或你的 Python 解释器完整路径
$S  = 'qyyjt-plugin\scripts'

# 查看某企业全部可抓入口（先跑这个，不耗配额）
& $PY $S\qyyjt_fetch.py "企业名" --list

# 抓指定维度（中文/别名均可，自动展开树菜单并级联查找）
& $PY $S\qyyjt_fetch.py "企业名" --entry "债券融资"
& $PY $S\qyyjt_fetch.py "企业名" --entry 股东 --out 股东.xlsx

# 任意站内 URL
& $PY $S\qyyjt_fetch.py --url "https://www.qyyjt.cn/s?tab=securities&k=关键词"

# 入口发现（探测各入口触发的 API 与表格结构）
& $PY $S\discover_entries.py "企业名" --probe 5
```

## 关键规则

1. **登录态**：位于 `%USERPROFILE%\.config\qyyjt-cli\browser-profile`。脚本输出 `NOT LOGGED IN`（退出码 3）时，先运行 `qyyjt-plugin\scripts\login_browser.py` 扫码登录。
2. **配额**：平台有每日查询次数上限。脚本检测到"今日查询次数已达上限"会以退出码 2 停止——**立即停止并向用户汇报，不要重试、不要换参数硬闯**。
3. **退出码**：0=成功，2=配额停止，3=未登录，4=未找到/入口不存在。
4. **输出**：默认打印摘要；`--out .json` 结构化结果；`--out .xlsx` 多 Sheet Excel；`--full-api` 附带 API 完整 JSON。
5. **并发**：不要同时运行多个抓取实例（共享浏览器 profile，会冲突）。
6. **批量任务**（如债券核查）：逐公司跑 `--entry 债券融资`，判据为页面出现存续债券数据（债券表有数据行或存量概览"债券N只"N>0）；名单自带"城投发债/民企/风险N"标签是源数据，与核查结论无关。
7. **改版应对**：若选择器失效，先 `--list` 观察页面入口，再调整 `qyyjt_common.py` 的 `ENTRY_KINDS` 选择器。
