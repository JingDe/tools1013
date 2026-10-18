# -*- coding: utf-8 -*-
"""
小红书批量拉黑脚本（演示 / 学习用途）—— 仅用于管理你自己的账号。

====================================================================
⚠️ 重要声明（请先读完）
--------------------------------------------------------------------
1. 小红书没有公开的「拉黑」官方 API。本脚本通过 Playwright 自动化网页
   操作，本质是用程序模拟你在浏览器里的手动点击。签名由页面自身生成，
   比直接伪造 HTTP 请求更安全、更贴近真人操作。
2. 自动化操作可能违反小红书《用户协议》，存在账号被限流 / 风控 / 封禁
   的风险。请充分了解后再用。
3. 默认只扫描(DRY_RUN)：用「搜索」功能找出相关用户，导出名单 CSV，不真正拉黑。
   先人工核对名单，确认无误后再用 --apply 真正执行。
4. 仅用于你自己的账号、仅屏蔽你明确不想要的账号。切勿用于骚扰、
   批量攻击他人或任何违法用途。
5. 小红书前端会改版，脚本里的「选择器」(SEL 字典) 只是示例，必须按
   说明在你浏览器里用 F12 核对后替换成真实值，否则点不到按钮。
====================================================================

依赖安装：
    pip install playwright
    playwright install chromium

运行（先扫描，安全）：
    python main.py
运行（确认名单后，真正拉黑）：
    python main.py --apply

首次运行会弹出浏览器，请手动登录一次；之后用持久化目录保存 cookie，
下次不用再登录。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import random
from pathlib import Path

from playwright.async_api import async_playwright

# ============================ 配置区 ============================
# 要搜索并拉黑的关键词；昵称里「包含」其中任意一个串（子串匹配）即命中。
# 可填多个关键词。注意：搜索结果里会混有「笔记」，所以最终仍按昵称再过滤一遍，
# 避免误拉黑只是笔记命中、本人昵称并不相关的人。
TARGET_KEYWORDS = ["雷🚗"]

# 每个关键词最多向下滚动加载多少屏（搜索是无限滚动，防止一直滚）
MAX_SCROLLS = 30
# 每次操作之间的随机等待(秒)，模拟真人节奏，降低风控概率
HUMAN_DELAY = (1.5, 3.5)
SCREENSHOT_ON_BLOCK = True
OUTPUT_CSV = "matched_users.csv"
USER_DATA_DIR = "xbrowser_profile"   # 持久化登录态的目录，别提交到 git
# ===============================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --- 选择器（必须按下面注释核对后替换！） ---
# 打开对应页面 → 按 F12 → 用「元素选择器」工具点一下目标元素，
# 把得到的 CSS / 文本选择器填到这里。
SEL = {
    # 搜索结果页里切到「用户」标签页的候选选择器（依次尝试，点第一个存在的）
    "user_tab_candidates": [
        "div[class*='tab']:has-text('用户')",
        "a:has-text('用户')",
        "span:has-text('用户')",
        "text=用户",
    ],
    # 搜索结果中每个「用户卡片」容器（卡片内应含有指向 /user/profile/ 的链接）
    "user_card":    "[class*='user']:has(a[href*='/user/profile/'])",
    # 卡片里显示「昵称」的元素
    "user_name":    "a.name, .name, [class*='name']",
    # 卡片中指向用户主页的链接
    "profile_link": "a[href*='/user/profile/']",
    # —— 以下在主页(进入主页后)使用 ——
    # 主页上的「···」更多按钮（点开后有「拉黑」）
    "more_btn":     "button:has-text('...'), .more, [class*='more']",
    # 弹层里的「拉黑」选项
    "block_item":   "text=拉黑",
    # 确认拉黑的按钮
    "confirm_btn":  "button:has-text('确定'), button.primary",
}


def setup_log() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("xhs-blocker")


async def human_sleep() -> None:
    await asyncio.sleep(random.uniform(*HUMAN_DELAY))


async def click_first_present(page, candidates: list[str]) -> bool:
    """依次尝试候选选择器，点中第一个真实存在的元素。返回是否点到了。"""
    for sel in candidates:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                return True
        except Exception:
            continue
    return False


async def search_users(page, keyword: str, log: logging.Logger) -> list[tuple[str, str]]:
    """用搜索功能找出与关键词相关的用户，返回 [(昵称, 主页URL), ...]。"""
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    url = f"https://www.xiaohongshu.com/search_result?keyword={keyword}"
    log.info("  打开搜索页: %s", url)
    await page.goto(url, wait_until="domcontentloaded")
    await human_sleep()

    # 切到「用户」标签页，只显示用户而非笔记
    if await click_first_present(page, SEL["user_tab_candidates"]):
        log.info("  已切到「用户」标签页。")
    else:
        log.warning("  没找到「用户」标签页，可能仍以笔记为主，请按 F12 核对选择器。")
    await human_sleep()

    # 无限滚动加载更多用户，按主页链接去重
    for i in range(MAX_SCROLLS):
        cards = await page.query_selector_all(SEL["user_card"])
        for card in cards:
            link = await card.query_selector(SEL["profile_link"])
            if not link:
                continue
            href = (await link.get_attribute("href")) or ""
            if not href:
                continue
            if not href.startswith("http"):
                href = "https://www.xiaohongshu.com" + href
            if href in seen:
                continue
            seen.add(href)
            name = ""
            nm = await card.query_selector(SEL["user_name"])
            if nm:
                name = (await nm.inner_text()).strip()
            results.append((name, href))

        log.info("  已加载 %d 个用户（滚动第 %d/%d 屏）", len(results), i + 1, MAX_SCROLLS)

        # 滚到底部触发下一屏加载
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await human_sleep()

        # 没有新增卡片，认为已到底，提前结束
        new_cards = await page.query_selector_all(SEL["user_card"])
        if len(new_cards) == len(cards):
            await human_sleep()
            new_cards2 = await page.query_selector_all(SEL["user_card"])
            if len(new_cards2) == len(cards):
                log.info("  貌似已加载全部用户，停止滚动。")
                break

    return results


async def block_on_profile(page, name: str, log: logging.Logger) -> bool:
    """在当前已打开的用户主页上：点开更多 → 拉黑 → 确认。返回是否成功。"""
    more = await page.query_selector(SEL["more_btn"])
    if not more:
        log.warning("  主页上找不到「更多」按钮，跳过: %s", name)
        return False
    await more.click()
    await human_sleep()

    block_item = await page.query_selector(SEL["block_item"])
    if not block_item:
        log.warning("  弹层里找不到「拉黑」选项，跳过: %s", name)
        return False
    await block_item.click()
    await human_sleep()

    confirm = await page.query_selector(SEL["confirm_btn"])
    if confirm:
        await confirm.click()
    await human_sleep()
    return True


async def run(apply: bool) -> None:
    log = setup_log()
    if apply:
        log.warning("⚠️ 已进入 --apply 模式，将对命中用户执行真实拉黑！")
    else:
        log.info("当前为扫描模式(DRY_RUN)，只搜索并导出命中名单，不会拉黑任何人。")

    matched: list[str] = []
    profile_dir = Path(USER_DATA_DIR)
    profile_dir.mkdir(exist_ok=True)

    async with async_playwright() as p:
        # 持久化上下文：登录态会保存到 USER_DATA_DIR，下次免登录
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=[f"--user-agent={USER_AGENT}"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        log.info("浏览器已打开。若未登录，请先手动登录你的小红书账号。")
        log.info("登录完成后，回到这里按 Enter 继续...")
        input("按 Enter 继续...")

        for keyword in TARGET_KEYWORDS:
            log.info("==== 搜索关键词: %s ====", keyword)
            users = await search_users(page, keyword, log)
            log.info("搜索到 %d 个用户，开始按昵称比对关键词...", len(users))
            for name, href in users:
                if any(k in name for k in TARGET_KEYWORDS):
                    matched.append(name)
                    if apply:
                        log.info("  [命中] %s -> 打开主页并拉黑: %s", name, href)
                        await page.goto(href, wait_until="domcontentloaded")
                        await human_sleep()
                        ok = await block_on_profile(page, name, log)
                        log.info("    拉黑%s", "成功" if ok else "失败")
                        if SCREENSHOT_ON_BLOCK and ok:
                            Path(f"shot_{len(matched)}.png").write_bytes(
                                await page.screenshot()
                            )
                    else:
                        log.info("  [命中(仅扫描)] %s  (%s)", name, href)
                else:
                    log.debug("  跳过(昵称不相关): %s", name)

        await context.close()

    # 导出命中名单
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["nickname", "keyword"])
        for n in matched:
            w.writerow([n, ",".join(TARGET_KEYWORDS)])
    log.info("完成。共命中 %d 个用户，名单已写入 %s", len(matched), OUTPUT_CSV)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="小红书批量拉黑（搜索+扫描/执行）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行拉黑。不加此参数则只搜索并导出名单。",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(apply=args.apply))
