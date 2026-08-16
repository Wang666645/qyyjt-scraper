"""打开浏览器登录企业预警通，保存登录态到 browser-profile（持久化上下文）。

同时兼容 storage.json 供 query_controllers.py 等旧脚本使用。
"""
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

USER_DATA = Path.home() / '.config' / 'qyyjt-cli' / 'browser-profile'
STORAGE = Path.home() / '.config' / 'qyyjt-cli' / 'storage.json'
USER_DATA.parent.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA),
            headless=False,
            args=['--disable-blink-features=AutomationControlled'])
        page = await browser.new_page()
        await page.goto('https://www.qyyjt.cn', wait_until='domcontentloaded')
        print('浏览器已打开，请在浏览器中完成登录（扫码/短信验证码）...')

        # Wait until logged in (max 5 min)
        for i in range(300):
            await asyncio.sleep(1)
            try:
                if '/login' not in page.url:
                    print('检测到登录成功!')
                    await asyncio.sleep(2)
                    break
            except:
                pass
            if i % 30 == 0:
                print(f'  等待中... ({i}s)')

        # Save storage_state for query_controllers.py compatibility
        state = await browser.storage_state()
        with open(STORAGE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        print(f'登录态已保存到:')
        print(f'  browser-profile: {USER_DATA}')
        print(f'  storage.json:    {STORAGE}')
        await browser.close()


asyncio.run(main())
