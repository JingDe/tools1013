# coupon_explore.py
# 用途：打开公众号领券链接，截图 + 列出页面上所有可点击区域，
#       便于定位「美团」和「淘宝闪购」那 3~4 个按钮的选择器。
# 用法：python coupon_explore.py "这里换成你的链接"
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "在此粘贴链接"

OUT = Path(__file__).parent / "explore_output"
OUT.mkdir(exist_ok=True)


def find_system_browser():
    """优先用系统 Edge/Chrome，避免下载 Playwright 自带 Chromium（国内 CDN 常被重置连接 ECONNRESET）。"""
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
        # 非无头模式：便于你处理可能出现的登录/验证码；视口设为手机宽度（公众号页多为移动端布局）
        exe = find_system_browser()
        if exe:
            print(f"使用系统浏览器: {exe}")
            browser = p.chromium.launch(headless=False, executable_path=exe)
        else:
            print("未找到系统 Edge/Chrome，回退到 Playwright 自带 Chromium（请先 playwright install chromium）")
            browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 420, "height": 900})

        # 监听「点击后打开新标签页」的情况（很多领券页会弹新页）
        def on_popup(popup):
            try:
                popup.wait_for_load_state(timeout=8000)
            except Exception:
                pass
            print(f"  [新标签页打开] {popup.url}")
        page.on("popup", on_popup)

        print(f"正在打开: {URL}")
        page.goto(URL, wait_until="load", timeout=30000)
        page.wait_for_timeout(5000)  # 等页面脚本跑完

        # 截图（整页）
        shot = OUT / "page.png"
        page.screenshot(path=str(shot), full_page=True)
        print(f"截图已保存: {shot}")

        # 保存 HTML，便于离线分析
        (OUT / "page.html").write_text(page.content(), encoding="utf-8")

        # 抓取各类可点击元素
        selectors = "a[href], button, [role='button'], [onclick], .btn, div[data-link]"
        els = page.query_selector_all(selectors)
        print(f"\n找到 {len(els)} 个可点击元素：\n")
        for i, el in enumerate(els):
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                text = (el.inner_text() or "").strip().replace("\n", " ")[:40]
                href = el.get_attribute("href") or ""
                box = el.bounding_box()
                sel = el.evaluate("""e => {
                    if (e.id) return '#' + e.id;
                    const dl = e.getAttribute('data-link');
                    if (dl) return '[data-link=\"' + dl + '\"]';
                    const cls = String(e.className || '').trim().split(/\\s+/).filter(Boolean).join('.');
                    return e.tagName.toLowerCase() + (cls ? '.' + cls : '');
                }""")
                pos = f"x={int(box['x'])} y={int(box['y'])}" if box else "位置未知"
                print(f"[{i:02d}] <{tag}> 文字={text!r} href={href[:60]!r} {pos}")
                print(f"     选择器: {sel}")
            except Exception as e:
                print(f"[{i:02d}] 解析失败: {e}")

        print("\n把上面的输出 + explore_output/page.png 发给我，我就能写出精准的点击脚本。")
        input("按回车关闭浏览器...")
        browser.close()


if __name__ == "__main__":
    main()
