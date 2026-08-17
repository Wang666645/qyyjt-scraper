"""打开浏览器登录企业预警通，保存登录态到持久化 profile。

用法:
  python login_browser.py                  # 默认 profile: ~/.config/qyyjt-cli/browser-profile
  python login_browser.py --profile myacct # 自定义 profile(新用户/换账号): ~/.config/qyyjt-cli/browser-profile-<名>

流程: 弹出真实浏览器 -> 扫码/短信登录 -> 自动检测登录成功 -> 保存登录态。
登录后的抓取脚本用 --profile 指定同一目录即可复用该登录态。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from qyyjt_common import resolve_profile  # noqa: E402

BASE = Path.home() / '.config' / 'qyyjt-cli'


async def is_logged_in(page):
    """真实登录判定: 页面出现登录用户专属文案'欢迎回来'(游客首页也有 s_tk, 不能只信 token)"""
    try:
        text = await page.evaluate(
            '() => document.body ? document.body.innerText.slice(0, 400) : ""')
        return '欢迎回来' in text
    except Exception:
        return False


def resolve_storage(name):
    if not name:
        return BASE / 'storage.json'
    return BASE / f'storage-{name}.json'


async def main():
    ap = argparse.ArgumentParser(description='企业预警通 登录器')
    ap.add_argument('--profile', default=None,
                    help='profile 名或路径(可选; 默认 browser-profile; 如 myacct -> browser-profile-myacct)')
    ap.add_argument('--timeout', type=int, default=300,
                    help='等待登录超时秒数(默认 300)')
    args = ap.parse_args()

    user_data = resolve_profile(args.profile)
    storage = resolve_storage(args.profile)
    user_data.parent.mkdir(parents=True, exist_ok=True)
    print(f'profile 目录: {user_data}')
    if user_data.exists() and any(user_data.iterdir()):
        print('!! 该 profile 已存在登录痕迹; 如需换账号, 在浏览器内先退出当前账号再登录')

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data),
            headless=False,
            args=['--disable-blink-features=AutomationControlled'])
        page = await browser.new_page()
        await page.goto('https://www.qyyjt.cn', wait_until='domcontentloaded')
        print('浏览器已打开，请在浏览器中完成登录（扫码/短信验证码）...')

        # Wait until logged in
        for i in range(args.timeout):
            await asyncio.sleep(1)
            try:
                if await is_logged_in(page):
                    print('检测到登录成功!')
                    await asyncio.sleep(2)
                    break
            except Exception:
                pass
            if i % 30 == 0:
                print(f'  等待中... ({i}s)')
        else:
            print('!! 等待超时, 未检测到登录成功')
            await browser.close()
            return 1

        # Save storage_state for legacy scripts compatibility
        state = await browser.storage_state()
        with open(storage, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        print(f'登录态已保存到:')
        print(f'  browser-profile: {user_data}')
        print(f'  storage.json:    {storage}')
        print(f'后续抓取: python qyyjt_fetch.py "企业名" --entry xxx --profile '
              f'{args.profile or "browser-profile"}')
        await browser.close()
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
