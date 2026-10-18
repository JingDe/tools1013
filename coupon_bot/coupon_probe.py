# coupon_probe.py
# 用途：逐个点击领券页上的"立即领取"按钮，记录每次点击后的反应
#       （页面跳转 / 新标签页 / 接口响应 / 页面新增提示文字），
#       用于确定正式自动领券脚本的确切行为。
# 用法：python coupon_probe.py "你的链接"
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "在此粘贴链接"

OUT = Path(__file__).parent / "probe_output"
OUT.mkdir(exist_ok=True)

STATIC_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".css", ".js",
              ".woff", ".woff2", ".ttf", ".ico", ".svg")


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


def main():
    with sync_playwright() as p:
        exe = find_system_browser()
        if exe:
            print(f"使用系统浏览器: {exe}")
            browser = p.chromium.launch(headless=False, executable_path=exe)
        else:
            browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 420, "height": 900})
        page = context.new_page()

        api_log = []

        def on_response(resp):
            url = resp.url
            path = url.split("?")[0]
            if path.endswith(STATIC_EXT):
                return
            try:
                body = resp.text()[:300]
            except Exception:
                body = "<no body>"
            entry = f"[{resp.status}] {url}\n    {body}"
            api_log.append(entry)
            print(f"  [接口] [{resp.status}] {url[:110]}")

        page.on("response", on_response)

        def on_popup(popup):
            try:
                popup.wait_for_load_state(timeout=8000)
            except Exception:
                pass
            print(f"  [新标签页] {popup.url}")
            try:
                popup.screenshot(path=str(OUT / f"popup_{len(api_log):03d}.png"))
            except Exception:
                pass

        page.on("popup", on_popup)

        def on_dialog(d):
            print(f"  [弹窗] {d.type}: {d.message}")
            d.accept()

        page.on("dialog", on_dialog)

        print(f"正在打开: {URL}")
        page.goto(URL, wait_until="load", timeout=30000)
        page.wait_for_timeout(5000)
        page.screenshot(path=str(OUT / "00_start.png"), full_page=True)
        print(f"页面加载完成: {page.url}\n")

        # 微信小程序启动器信息（脱离微信时它们不生效，但能告诉我们正式流程会拉起什么）
        weapps = page.evaluate(
            """() => Array.from(document.querySelectorAll('wx-open-launch-weapp')).map(e => ({
                id: e.id,
                platform: e.getAttribute('data-platform') || '',
                username: e.getAttribute('username') || e.getAttribute(':username') || '',
                path: e.getAttribute('path') || ''
            }))"""
        )
        print(f"微信小程序启动器 wx-open-launch-weapp 共 {len(weapps)} 个：")
        for w in weapps:
            print(f"  - {w}")

        # 只点"领取"类按钮；避开换账号/下单/攻略/会员卡选择等有副作用的按钮
        TARGET_SELECTORS = ["button.old-btn-get", "button.trial-btn-get", "button.action-btn"]

        idx = 0
        for sel in TARGET_SELECTORS:
            els = page.query_selector_all(sel)
            for i, el in enumerate(els):
                box = el.bounding_box()
                if box is None or box["width"] <= 0 or box["height"] <= 0:
                    continue  # 跳过隐藏元素
                idx += 1
                cls = el.get_attribute("class")
                print(f"\n=== [{idx:02d}] 点击 {sel} #{i} (class={cls}) ===")
                before_text = page.evaluate("() => document.body.innerText")
                before_url = page.url
                try:
                    el.scroll_into_view_if_needed()
                    # 用 JS click 直接触发 Vue 绑定的事件，绕开微信小程序浮层
                    el.evaluate("e => e.click()")
                except Exception as e:
                    print(f"  点击失败: {e}")
                    continue
                page.wait_for_timeout(4000)
                after_url = page.url
                after_text = page.evaluate("() => document.body.innerText")
                new_lines = [ln.strip() for ln in after_text.splitlines()
                             if ln.strip() and ln.strip() not in before_text]
                if new_lines:
                    print(f"  [页面新增文字] {new_lines[:6]}")
                if after_url != before_url:
                    print(f"  [跳转] {after_url}")
                    page.screenshot(path=str(OUT / f"{idx:02d}_after.png"))
                    try:
                        page.go_back(wait_until="load")
                        page.wait_for_timeout(3000)
                    except Exception as e:
                        print(f"  返回上一页失败（{e}），停止探查")
                        break
                else:
                    page.screenshot(path=str(OUT / f"{idx:02d}_after.png"))

        (OUT / "network_log.txt").write_text("\n\n".join(api_log), encoding="utf-8")
        print(f"\n接口日志已保存: {OUT / 'network_log.txt'}")
        print("把终端输出 + probe_output 里的截图发给我。")
        input("按回车关闭浏览器...")
        browser.close()


if __name__ == "__main__":
    main()
