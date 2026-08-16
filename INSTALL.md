# qyyjt-scraper 安装指南（接收方必读）

企业预警通（www.qyyjt.cn）通用抓取插件 —— 可在 Claude Code / Codex / DeepSeek Harness (DSH) 中调用，抓取企业详情页任意信息维度（股东/财务/债券/融资/风险/司法/信用/图谱/新闻等）或任意站内 URL。

> ⏱ 全程约 10 分钟：装依赖 5 分钟 + 登录 2 分钟 + 验证 2 分钟。

## 第 1 步：安装 Python 依赖

需要 Python 3.10+（Windows 可用官方安装包或已有解释器）。

```powershell
# 1) 安装依赖(任选其一)
pip install playwright openpyxl

# 2) 下载 Chromium 浏览器内核(Playwright 需要)
playwright install chromium
```

## 第 2 步：放置文件

把整个发布包解压后，将 `qyyjt-plugin/`、`.dsh/`、`.claude/`、`AGENTS.md` 放入**你的工作区根目录**（例如 `C:\Users\<你>\Desktop\我的项目\`）。最终布局：

```
<工作区根>/
├── AGENTS.md                                  # Codex/通用 agent 自动读取
├── qyyjt-plugin/
│   ├── README.md                              # 完整命令参考
│   └── scripts/                               # 抓取脚本(勿改名)
├── .dsh/skills/qyyjt-scraper/SKILL.md         # DSH 技能(自动发现)
└── .claude/skills/qyyjt-scraper/SKILL.md      # Claude Code 技能(自动发现)
```

## 第 3 步：登录（用自己的账号，一次即可）

```powershell
python qyyjt-plugin\scripts\login_browser.py
```

会弹出真实浏览器 → 扫码或短信登录 qyyjt.cn → 自动保存登录态到 `%USERPROFILE%\.config\qyyjt-cli\browser-profile`。

> ⚠️ 登录态含个人 Cookie，**不要**把 `browser-profile` 目录发给别人；每台机器各自登录一次。

## 第 4 步：验证

```powershell
python qyyjt-plugin\scripts\qyyjt_fetch.py "重庆三峡发展集团有限公司" --list
```

看到 30+ 个入口列表即成功。再试一次实际抓取：

```powershell
python qyyjt-plugin\scripts\qyyjt_fetch.py "重庆三峡发展集团有限公司" --entry "债券融资"
```

## 第 5 步：在 agent 中使用

- **DSH**：在对话中直接说"查一下 XX 公司在企业预警通上的股东信息"即可，技能自动匹配；技能目录刷新无需重启
- **Claude Code**：`claude` 启动后同样直接提需求；也可 `/skills` 查看
- **Codex / 其他**：agent 读取 `AGENTS.md` 后按其中命令执行

## 常用命令

```powershell
python qyyjt-plugin\scripts\qyyjt_fetch.py "企业名" --list                 # 列出全部可抓入口
python qyyjt-plugin\scripts\qyyjt_fetch.py "企业名" --entry "债券融资"      # 抓指定维度
python qyyjt-plugin\scripts\qyyjt_fetch.py "企业名" --entry 股东 --out 股东.xlsx
python qyyjt-plugin\scripts\qyyjt_fetch.py "企业名" --all --out 全部.xlsx   # 抓全部(耗配额)
python qyyjt-plugin\scripts\qyyjt_fetch.py --url "https://www.qyyjt.cn/s?tab=securities&k=重庆"
python qyyjt-plugin\scripts\discover_entries.py "企业名" --probe 5          # 入口+API 结构探测
```

## 常见问题

| 现象 | 处理 |
|---|---|
| `NOT LOGGED IN` / 退出码 3 | 登录态失效，重跑第 3 步 |
| `!!! 配额停止` / 退出码 2 | 平台每日查询次数用尽：**立即停止**，换账号或次日再跑，不要硬闯 |
| `No module named playwright` | 未装依赖，重跑第 1 步 |
| `--entry` 找不到入口 | 脚本会打印全部可用入口名，选一个再试；入口藏在树菜单/父页面时脚本会自动展开与级联查找 |
| 页面改版导致抓取异常 | 先 `--list` 看入口是否正常，再联系插件作者更新 `qyyjt_common.py` 的选择器 |

## 遵守配额纪律

平台有每日查询次数上限（账号维度）。脚本检测到即自动停止并保留已保存结果。**批量任务建议分天执行**；`--list`/`--url` 开销小，`--all`/`--probe N`/展开树开销大。
