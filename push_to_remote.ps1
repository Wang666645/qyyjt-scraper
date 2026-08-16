# push_to_remote.ps1 — 将 qyyjt-scraper 推送到远程仓库 (GitHub / Gitee)
#
# 用法:
#   1) 先在 GitHub/Gitee 网页上创建一个【私有(Private)】空仓库, 复制其 URL
#      (如 https://github.com/你的账号/qyyjt-scraper.git)
#   2) 在本仓库根目录执行:
#        .\push_to_remote.ps1 -RemoteUrl https://github.com/你的账号/qyyjt-scraper.git
#      或直接执行后按提示粘贴 URL
#   3) 首次推送会弹出浏览器/凭据窗口, 用你的账号登录授权即可

param(
    [string]$RemoteUrl = ""
)

$ErrorActionPreference = 'Stop'
$repo = (Get-Location).Path
Write-Host "仓库目录: $repo" -ForegroundColor Cyan

if (-not $RemoteUrl) {
    $RemoteUrl = Read-Host "请输入远程仓库 URL (https://...git)"
}
if (-not $RemoteUrl -or $RemoteUrl -notmatch '^https?://|^git@') {
    Write-Host "!! 无效的远程 URL" -ForegroundColor Red
    exit 1
}

# 检查是否已配置 origin
$origin = git remote get-url origin 2>$null
if ($LASTEXITCODE -eq 0 -and $origin) {
    Write-Host "已存在 origin: $origin" -ForegroundColor Yellow
    $ans = Read-Host "替换为 $RemoteUrl ? (y/N)"
    if ($ans -notin @('y', 'Y')) { exit 0 }
    git remote set-url origin $RemoteUrl
} else {
    git remote add origin $RemoteUrl
}

# 推送到远程(私有仓库需先完成账号认证)
Write-Host "开始推送 (首次会要求登录授权)..." -ForegroundColor Cyan
git push -u origin master
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n推送失败。常见原因与处理:" -ForegroundColor Yellow
    Write-Host "  1. 未认证: 安装 GitHub CLI 后执行 gh auth login (或安装 Git Credential Manager)"
    Write-Host "  2. 远程仓库不存在/权限不足: 确认已在网页创建【私有】空仓库"
    Write-Host "  3. 网络问题: 国内环境可尝试 Gitee, 或配置代理"
    exit 1
}

Write-Host "`n✅ 推送成功!" -ForegroundColor Green
Write-Host "以后更新: git add -A; git commit -m '...'; git push"
Write-Host "接收方:   git clone $RemoteUrl"
