#!/usr/bin/env python3
"""
豆瓣租房小组爬虫 v2
- 搜索「深圳租房」小组 (ID: 613105) 中的整租房源帖子
- 优先 CDP 接管你真实的 Arc 浏览器（最强反检测）
- 降级：playwright-stealth + Arc cookie 注入
- 若仍触发 misc/sorry：暂停等你手动过验证，再继续
- 输出：data/douban_raw.jsonl（每行一条帖子）

使用方法：
  正常模式（自动尝试 CDP → stealth → 手动）：
    python3 scripts/scrape_douban.py

  强制 CDP 模式（需要先启动 Arc 并开启调试端口）：
    /Applications/Arc.app/Contents/MacOS/Arc --remote-debugging-port=9222
    python3 scripts/scrape_douban.py --cdp

  强制 stealth 模式：
    python3 scripts/scrape_douban.py --stealth
"""

import asyncio
import json
import re
import subprocess
import sys
import argparse
from datetime import datetime
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("请先安装：pip install playwright && playwright install chromium")
    sys.exit(1)

# ── 配置 ──────────────────────────────────────────────────────────────────────
GROUP_ID = "613105"  # 深圳租房小组
CDP_PORT = 9222

INCLUDE_RE = re.compile(r"整租|整套|两室|两房|2室|2房|两卧|大两房")
EXCLUDE_RE = re.compile(r"次卧|主卧|单间|合租|床位|隔断|短租|日租|仅限女|隔断间")
AREA_RE    = re.compile(r"后海|南山|南油|前海|科技园|深圳湾|桃源|蛇口|湾厦|登良")

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "douban_raw.jsonl"
SESSION_PATH = Path(__file__).parent.parent / "data" / "douban_session.json"


# ── Arc Cookie 导入（降级用）──────────────────────────────────────────────────
def get_arc_cookies():
    """用 gstack browse 导出已解密的 Arc douban cookie"""
    browse_bin = Path.home() / ".claude/skills/gstack/browse/dist/browse"
    if not browse_bin.exists():
        return []
    try:
        chain = json.dumps([
            ["goto", "https://www.douban.com/"],
            ["cookie-import-browser", "arc", "--domain", ".douban.com"],
            ["cookies"],
        ])
        result = subprocess.run(
            [str(browse_bin), "chain"],
            input=chain, capture_output=True, text=True, timeout=30
        )
        output = result.stdout
        match = re.search(r"BEGIN UNTRUSTED.*?(\[.*?\]).*?END UNTRUSTED", output, re.DOTALL)
        if not match:
            match = re.search(r'(\[\s*\{.*?\}\s*\])', output, re.DOTALL)
        if not match:
            return []
        raw = json.loads(match.group(1))
        cookies = []
        for c in raw:
            if "douban.com" not in c.get("domain", ""):
                continue
            cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": "Lax",
            }
            if c.get("expires") and c["expires"] > 0:
                cookie["expires"] = float(c["expires"])
            cookies.append(cookie)
        print(f"✓ 导入 {len(cookies)} 条 Arc cookie")
        return cookies
    except Exception as e:
        print(f"⚠ Cookie 导入失败: {e}")
        return []


# ── misc/sorry 检测 & 等待人工过验证 ─────────────────────────────────────────
async def handle_sorry_page(page, original_url: str) -> bool:
    """
    检测是否在 misc/sorry 页面。
    如果是：尝试点击验证按钮（打开 TCaptcha），等待用户手动解题后继续。
    返回 True 表示已恢复，False 表示放弃。
    """
    if "misc/sorry" not in page.url and "douban.com/misc/sorry" not in await page.content():
        return False  # 不在 sorry 页，无需处理

    print("\n⚠ 触发豆瓣人机验证页 (TCaptcha):")
    print("  1. 浏览器里已打开 Tencent 滑块验证")
    print("  2. 请手动拖动滑块完成验证")
    print("  3. 验证完成后，按 Enter 继续...\n")

    # 尝试点击验证按钮（打开滑块弹窗）
    try:
        btn = await page.query_selector("#tcaptcha_btn, .btn-submit, [id*='captcha']")
        if btn:
            await btn.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass  # 若点击失败，让用户自行操作

    # 等待用户手动解题
    input("  → 验证完成后按 Enter: ")

    # 等待页面跳转离开 sorry 页
    try:
        await page.wait_for_url(
            lambda url: "misc/sorry" not in url,
            timeout=10000
        )
    except Exception:
        pass

    # 若还在 sorry 页，尝试重新访问原 URL
    if "misc/sorry" in page.url:
        await page.goto(original_url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)

    if "misc/sorry" in page.url:
        print("  ✗ 验证后仍被拦截，跳过此页")
        return False

    print("  ✓ 验证通过，继续抓取")
    return True


# ── 解析帖子列表页 ─────────────────────────────────────────────────────────────
async def extract_topic_links(page):
    topics = []
    items = await page.query_selector_all(".olt tr, .topic-item, table.olt tbody tr")
    for item in items:
        title_el = await item.query_selector("td.title a, .title a")
        if not title_el:
            continue
        title = (await title_el.inner_text()).strip()
        href  = await title_el.get_attribute("href")
        if href and "/group/topic/" in href:
            topics.append({"title": title, "url": href})
    return topics


# ── 获取帖子正文 ──────────────────────────────────────────────────────────────
async def fetch_topic_content(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(800)

        # 检测 sorry 页
        if "misc/sorry" in page.url:
            solved = await handle_sorry_page(page, url)
            if not solved:
                return {"title": "", "body": "", "pub_date": "", "url": url, "error": "blocked"}

        title_el = await page.query_selector("#content h1, .article-title h1")
        title = (await title_el.inner_text()).strip() if title_el else ""

        body_el = await page.query_selector("#link-report .topic-content, .topic-content")
        body  = (await body_el.inner_text()).strip() if body_el else ""

        time_el = await page.query_selector(".create-time, .pub-date")
        pub_date = (await time_el.inner_text()).strip() if time_el else ""

        return {"title": title, "body": body, "pub_date": pub_date, "url": url}
    except Exception as e:
        return {"title": "", "body": "", "pub_date": "", "url": url, "error": str(e)}


# ── 过滤 ──────────────────────────────────────────────────────────────────────
def is_relevant(item):
    text = item["title"] + " " + item.get("body", "")
    if EXCLUDE_RE.search(text): return False
    if not INCLUDE_RE.search(text): return False
    if not AREA_RE.search(text): return False
    return True


# ── CDP 模式：接管真实 Arc 浏览器 ─────────────────────────────────────────────
async def try_cdp_mode(playwright):
    """
    尝试连接到已运行的 Arc 浏览器（需开启 --remote-debugging-port=9222）。
    返回 (browser, context, page) 或 None（连接失败）。
    """
    try:
        browser = await playwright.chromium.connect_over_cdp(
            f"http://localhost:{CDP_PORT}",
            timeout=3000
        )
        contexts = browser.contexts
        if not contexts:
            print("⚠ CDP 连接成功但没有浏览器上下文")
            return None

        context = contexts[0]
        # 复用已有页面（Arc 的 tab 管理很特殊，新建 page 可能导致崩溃）
        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        # 保存 session 供后续 headless 模式使用
        try:
            await context.storage_state(path=str(SESSION_PATH))
            print(f"✓ Arc session 已保存到 {SESSION_PATH}")
        except Exception:
            pass

        print(f"✓ CDP 模式：已接管 Arc 浏览器（{len(pages)} 个标签页）")
        return browser, context, page

    except Exception as e:
        print(f"  CDP 连接失败: {e}")
        return None


# ── Stealth 模式：playwright-stealth + cookie ─────────────────────────────────
async def stealth_mode(playwright, cookies):
    """
    使用 playwright-stealth 反检测补丁 + Arc cookie 注入。
    如果没有安装 playwright-stealth，降级为普通模式。
    """
    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
        print("✓ playwright-stealth 已加载")
    except ImportError:
        stealth = None
        print("⚠ playwright-stealth 未安装（pip install playwright-stealth），使用普通模式")

    # 如有保存的 session，直接使用（比 cookie 更完整，含 localStorage 等）
    storage_state = None
    if SESSION_PATH.exists():
        print(f"✓ 使用保存的 Arc session: {SESSION_PATH}")
        storage_state = str(SESSION_PATH)

    browser = await playwright.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context_kwargs = dict(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    if storage_state:
        context_kwargs["storage_state"] = storage_state
    context = await browser.new_context(**context_kwargs)

    # 注入 stealth 脚本（apply_stealth_async 在 context 上操作）
    if stealth:
        await stealth.apply_stealth_async(context)
        print("✓ stealth 补丁已注入")
    else:
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
        """)

    if cookies and not storage_state:
        await context.add_cookies(cookies)
    page = await context.new_page()
    return browser, context, page


# ── 主流程 ────────────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", action="store_true", help="强制使用 CDP 模式")
    parser.add_argument("--stealth", action="store_true", help="强制使用 stealth 模式")
    args = parser.parse_args()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    seen_urls = set()
    if OUTPUT_PATH.exists():
        for line in OUTPUT_PATH.read_text().splitlines():
            try:
                seen_urls.add(json.loads(line)["url"])
            except Exception:
                pass

    async with async_playwright() as p:
        browser = context = page = None
        mode_used = None

        # 1. 尝试 CDP（接管真实 Arc 浏览器）
        if not args.stealth:
            print("\n🔌 尝试 CDP 模式（连接 Arc 浏览器）...")
            print(f"   需要 Arc 以调试模式运行：")
            print(f"   /Applications/Arc.app/Contents/MacOS/Arc --remote-debugging-port={CDP_PORT}")
            result = await try_cdp_mode(p)
            if result:
                browser, context, page = result
                mode_used = "CDP"

        # 2. 降级：playwright-stealth + cookies
        if not browser:
            if not args.cdp:
                print("\n🛡  使用 stealth 模式（playwright-stealth + Arc cookies）...")
                cookies = get_arc_cookies()
                browser, context, page = await stealth_mode(p, cookies)
                mode_used = "stealth"
            else:
                print("\n✗ CDP 模式失败，且指定了 --cdp，退出")
                sys.exit(1)

        print(f"\n模式：{mode_used}")

        # 如果是 stealth 模式且没有 session，先验证登录状态
        if mode_used == "stealth":
            await page.goto("https://www.douban.com/", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1000)
            # 检查是否已登录（有用户名显示）
            logged_in = await page.query_selector(".nav-user-account, #db-nav-sns .pl2")
            if not logged_in:
                print("\n⚠ 未检测到豆瓣登录状态，请在浏览器中手动登录")
                input("  → 登录完成后按 Enter: ")

        # 翻页浏览组内最新讨论
        all_topics = []
        print(f"\n📖 翻阅「深圳租房」组最新帖子（最多 8 页）...")
        consecutive_sorry = 0

        for start in range(0, 200, 25):
            url = f"https://www.douban.com/group/{GROUP_ID}/discussion?start={start}&orderby=time"
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1200)

            # 检测并处理 sorry 页
            if "misc/sorry" in page.url:
                solved = await handle_sorry_page(page, url)
                if not solved:
                    consecutive_sorry += 1
                    if consecutive_sorry >= 2:
                        print("  连续 2 次验证失败，终止翻页")
                        break
                    continue
                consecutive_sorry = 0

            topics = await extract_topic_links(page)
            if not topics:
                print(f"  第 {start//25+1} 页无内容，停止")
                break

            relevant = [t for t in topics if INCLUDE_RE.search(t["title"]) or AREA_RE.search(t["title"])]
            all_topics.extend(relevant)
            print(f"  第 {start//25+1} 页：{len(topics)} 条，命中 {len(relevant)} 条")
            await page.wait_for_timeout(800)

        # 去重
        unique = {t["url"]: t for t in all_topics if t["url"] not in seen_urls}
        print(f"\n共 {len(unique)} 条新帖子，开始读取内容...")

        results = []
        with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
            for i, (url, meta) in enumerate(unique.items(), 1):
                print(f"  [{i}/{len(unique)}] {meta['title'][:50]}")
                item = await fetch_topic_content(page, url)
                item["title"] = item["title"] or meta["title"]
                item["scraped_at"] = datetime.now().isoformat()

                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                if is_relevant(item):
                    results.append(item)

                await page.wait_for_timeout(800)

        # CDP 模式：保存最新 session（包含本次访问积累的 cookie）
        if mode_used == "CDP":
            try:
                await context.storage_state(path=str(SESSION_PATH))
                print(f"✓ Session 已更新：{SESSION_PATH}")
            except Exception:
                pass

        if mode_used != "CDP":
            await browser.close()

    print(f"\n✅ 完成。共写入 {len(unique)} 条，其中 {len(results)} 条符合筛选：")
    for r in results:
        price = re.search(r"(\d{4,5})\s*[元/]", r["title"] + r.get("body", ""))
        price_str = price.group(1) + "元" if price else "价格待询"
        print(f"  • [{price_str}] {r['title'][:60]}")
        print(f"    {r['url']}")

    summary_path = OUTPUT_PATH.parent / "douban_filtered.jsonl"
    with open(summary_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n筛选结果已保存到 {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
