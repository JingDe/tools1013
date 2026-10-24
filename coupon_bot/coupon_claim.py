# coupon_claim.py
# 用途：自动领取外卖券。按 claim_config.json 里配置的按钮选择器，依次点击领券页上的
#       "立即领取"按钮，处理跳转/新标签页/登录墙，并保存截图与结果日志。
# 用法：
#   python coupon_claim.py            # 正式领券（会真实点击）
#   python coupon_claim.py --dry-run  # 只定位并打印将点击的目标，不真正点击（调试用）
# 前置：先在 claim_config.json 的 url 填入当天从微信复制的链接。
import sys, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright


def pause(msg):
    """非交互（无 tty / 管道 EOF）时只打印不等待；交互时等待回车。避免定时任务挂死。"""
    print(msg)
    if sys.stdin.isatty():
        try:
            input()
        except EOFError:
            pass

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "claim_config.json"
PROFILE = HERE / "edge_profile"      # 持久化浏览器目录：登录态只需要在首次手动登一次
OUT = HERE / "claim_output"


def find_system_browser():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def load_config():
    if not CONFIG_PATH.exists():
        default = {
            "url": "在此粘贴每日从微信复制的链接",
            "targets": [
                "button.old-btn-get:not(.eleme):not(.random)",
                "button.trial-btn-get",
                "button.old-btn-get.eleme",
                "button.old-btn-get.random"
            ],
            "wait_after_click_ms": 4000,
            "headless": True
        }
        CONFIG_PATH.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
        return default
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = load_config()
    url = cfg.get("url", "")
    targets = cfg.get("targets", [])
    wait_ms = int(cfg.get("wait_after_click_ms", 4000))
    headless = bool(cfg.get("headless", True))

    if not url or url.startswith("在此"):
        print("请在 claim_config.json 的 url 字段填入当天从微信复制的链接，再运行。")
        return

    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(exist_ok=True)
    exe = find_system_browser()
    launch_kwargs = dict(
        headless=headless,
        user_data_dir=str(PROFILE),
        viewport={"width": 420, "height": 900},
        args=["--no-sandbox"],
    )
    if exe:
        launch_kwargs["executable_path"] = exe
        print(f"使用系统浏览器: {exe}")
    else:
        print("未找到系统 Edge/Chrome，回退到 Playwright 自带 Chromium（需先 playwright install chromium）")

    results = []
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(**launch_kwargs)
        page = context.new_page()
        popups = []

        def on_popup(pu):
            try:
                pu.wait_for_load_state(timeout=8000)
            except Exception:
                pass
            popups.append(pu)
            print(f"  [新标签页] {pu.url}")
            try:
                pu.screenshot(path=str(OUT / f"popup_{len(popups):03d}.png"))
            except Exception:
                pass

        page.on("popup", on_popup)

        def on_dialog(d):
            print(f"  [弹窗] {d.type}: {d.message}")
            d.accept()

        page.on("dialog", on_dialog)

        print(f"打开: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)

        # 登录墙检测：若页面要求登录/授权，暂停等待用户手动处理
        txt = page.evaluate("() => document.body.innerText")
        if any(k in txt for k in ["请登录", "登录后", "微信授权", "未登录", "请先登录", "授权登录"]):
            if sys.stdin.isatty():
                print("检测到需要登录/授权，请在打开的浏览器中手动登录，完成后回到这里按回车继续…")
                input("按回车继续…")
                page.reload()
                page.wait_for_timeout(4000)
            else:
                print("检测到需要登录/授权，但当前为非交互模式（如定时任务）。请先用 headless=false 手动运行一次完成登录，再交给定时任务。")
                context.close()
                return

        # 定位所有可见的可点击目标
        plan = []
        for sel in targets:
            els = page.query_selector_all(sel)
            for i, el in enumerate(els):
                box = el.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    plan.append((sel, i))
        print(f"共定位到 {len(plan)} 个可点击目标")

        if dry_run:
            for n, (sel, i) in enumerate(plan, 1):
                print(f"  [{n}] 将点击: {sel} #{i}")
            pause("dry-run 结束，按回车关闭浏览器…" if sys.stdin.isatty() else "dry-run 结束。")
            context.close()
            return

        for n, (sel, i) in enumerate(plan, 1):
            els = page.query_selector_all(sel)
            if i >= len(els):
                results.append({"sel": sel, "i": i, "ok": False, "reason": "元素已不存在"})
                continue
            el = els[i]
            box = el.bounding_box()
            if not (box and box["width"] > 0 and box["height"] > 0):
                results.append({"sel": sel, "i": i, "ok": False, "reason": "不可见/已消失"})
                continue
            print(f"\n=== [{n}/{len(plan)}] 点击 {sel} #{i} ===")
            before = page.evaluate("() => document.body.innerText")
            bu = page.url
            try:
                el.scroll_into_view_if_needed()
                el.evaluate("e => e.click()")  # JS click 绕开微信小程序浮层
            except Exception as e:
                results.append({"sel": sel, "i": i, "ok": False, "reason": str(e)[:200]})
                continue
            page.wait_for_timeout(wait_ms)
            after = page.evaluate("() => document.body.innerText")
            new_lines = [l.strip() for l in after.splitlines() if l.strip() and l.strip() not in before]
            rec = {"sel": sel, "i": i, "ok": True, "new_text": new_lines[:6], "popups": len(popups)}
            if page.url != bu:
                rec["navigated_to"] = page.url
                try:
                    page.go_back(wait_until="load")
                    page.wait_for_timeout(2500)
                except Exception as e:
                    rec["back_error"] = str(e)[:200]
            page.screenshot(path=str(OUT / f"{n:02d}_after.png"))
            results.append(rec)
            print(f"  结果: {rec}")

        for pu in popups:
            try:
                pu.close()
            except Exception:
                pass

        (OUT / "last_run.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完成，结果见 {OUT / 'last_run.json'}")
        pause("按回车关闭浏览器…" if not headless else "（headless 模式，自动结束）")
        context.close()


if __name__ == "__main__":
    main()
