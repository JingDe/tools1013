# -*- coding: utf-8 -*-
"""
小红书批量拉黑（安卓 App 自动化版）—— 仅用于管理你自己的账号。

====================================================================
⚠️ 重要声明（请先读完）
--------------------------------------------------------------------
1. 网页版个人主页的「···」通常没有「拉黑」入口（你已实测）。拉黑功能在
   手机 App 里是确定存在的，所以本脚本改用 uiautomator2(ADB) 驱动安卓
   上的小红书 App 自动拉黑。
2. 自动化可能违反小红书《用户协议》，存在账号被限流 / 风控 / 封禁风险；
   且小红书可能检测自动化环境。请充分了解后再用，并控制节奏。
3. 默认 DRY_RUN（不加 --apply）：只扫描、把命中名单导出 CSV，不真拉黑。
   先在手机上人工核对名单，再用 --apply 真正执行。
4. 仅用于你自己的账号、仅屏蔽你明确不想要的账号。
5. 小红书 UI 会改版，SEL 字典里是「按文字匹配」的最佳猜测，必须用
   本脚本自带的 --inspect 模式抓出来替换成你手机上的真实选择器
   （它会把各页面 dump 成 ui_*.xml / ui_*.png，照着改 SEL 即可，详见下方）。
====================================================================

---------------------------- 环境准备 ----------------------------
1) 电脑装好 ADB（Android Studio 自带的 platform-tools 即可）。
   - 把 platform-tools 目录加入 PATH，或设置 ANDROID_HOME。
   - 验证：命令行执行 `adb devices` 能看到你的设备。
2) 手机（或模拟器）开启「开发者选项 → USB 调试」，用 USB 连电脑。
   - 首次连会弹「允许 USB 调试」，勾选一律允许。
   - 如果是模拟器，直接用 `adb connect 127.0.0.1:5555` 之类连上。
3) 手机上小红书 App 已登录你的账号（脚本不负责登录）。
4) 安装 Python 依赖（建议在项目 venv 里）：
       pip install uiautomator2
   ⚠️ 不要装 weditor：它在中文 Windows(GBK) 上构建会报 UnicodeDecodeError，
      而且本脚本自带的 --inspect 已经能打印手机页面元素，weditor 完全可选。
      如果确实想用 weditor，先执行 `set PYTHONUTF8=1` 再 pip install weditor。
   uiautomator2 首次连接设备会自动往手机推一个 agent，按提示允许。

---------------------------- 选择器核对 ----------------------------
强烈建议先跑一次检视模式，把真实选择器抓出来填进 SEL：
       python blocker_android.py --inspect
它会在关键页面把「可点击元素 + 文字/描述」打印出来，对照着改 SEL 即可。
（也可以 `python -m weditor` 打开网页版检视器，点元素看 resourceId/text。）

---------------------------- 运行 ----------------------------
扫描（安全）：  python blocker_android.py
真实拉黑：      python blocker_android.py --apply
指定设备：      python blocker_android.py --serial 你的设备序列号
检视 UI：       python blocker_android.py --inspect
"""

from __future__ import annotations

import argparse
import csv
import logging
import random
import time
from pathlib import Path

import uiautomator2 as u2

# ============================ 配置区 ============================
# 要搜索并拉黑的关键词；昵称里「包含」任意一个串（子串匹配）即命中。可填多个。
TARGET_KEYWORDS = ["雷🚗"]

XHS_PACKAGE = "com.xingin.xhs"      # 小红书包名（一般不用改）

# 每个关键词最多向下滑动加载多少屏（防无限滚动）
MAX_SWIPES = 30
# 操作之间的随机等待(秒)，模拟真人节奏，降低风控概率
HUMAN_DELAY = (1.5, 3.5)
SCREENSHOT_ON_BLOCK = True
OUTPUT_CSV = "matched_users_android.csv"
# ===============================================================

# --- 选择器（按文字匹配的最佳猜测，必须用 weditor 核对替换！） ---
# 每个动作都给了一组候选；脚本会依次尝试，点中第一个真实存在的。
# spec 写法：{"text": "精确文字"} / {"textContains": "包含文字"} /
#            {"desc": "内容描述"} / {"descContains": "描述包含"} /
#            {"resourceId": "com.xingin.xhs:id/xxx"} / {"className": "..."}
SEL = {
    # 首页顶部的搜索入口（点它会进入搜索页）
    "search_entry": [
        {"textContains": "搜索"},
        {"desc": "搜索"},
        {"resourceId": "com.xingin.xhs:id/search_entry"},
    ],
    # 搜索页里的输入框
    "search_box": [
        {"className": "android.widget.EditText"},
        {"resourceId": "com.xingin.xhs:id/et_search"},
    ],
    # 搜索结果里的「用户」标签页（切到只看用户）
    "user_tab": [
        {"text": "用户"},
    ],
    # 用户主页右上角的「···」更多按钮（点开后有「拉黑」）
    "more_btn": [
        {"desc": "更多"},
        {"descContains": "更多"},
        {"text": "···"},
        {"resourceId": "com.xingin.xhs:id/iv_more"},
    ],
    # 弹层里的「拉黑」选项
    "block_item": [
        {"text": "拉黑"},
        {"textContains": "拉黑"},
    ],
    # 确认拉黑的按钮（文案可能是 确定 / 确认）
    "confirm_btn": [
        {"text": "确定"},
        {"text": "确认"},
        {"textContains": "确认拉黑"},
    ],
}

USER_AGENT = None  # App 端不需要 UA


def setup_log() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("xhs-android")


def human_sleep() -> None:
    time.sleep(random.uniform(*HUMAN_DELAY))


# ----------------------- uiautomator2 辅助 -----------------------
def make_selector(d: "u2.Device", spec: dict):
    if "text" in spec:
        return d(text=spec["text"])
    if "textContains" in spec:
        return d(textContains=spec["textContains"])
    if "desc" in spec:
        return d(description=spec["desc"])
    if "descContains" in spec:
        return d(descriptionContains=spec["descContains"])
    if "resourceId" in spec:
        return d(resourceId=spec["resourceId"])
    if "className" in spec:
        return d(className=spec["className"])
    raise ValueError(f"未知选择器: {spec}")


def find_click(d: "u2.Device", candidates: list[dict], log: logging.Logger,
               wait_each: float = 2.0) -> bool:
    """依次尝试候选选择器，点中第一个真实存在的元素。返回是否点到了。"""
    for spec in candidates:
        sel = make_selector(d, spec)
        try:
            if sel.wait_exists(timeout=wait_each):
                sel.click()
                return True
        except Exception:
            continue
    return False


def dump_clickable(d: "u2.Device", log: logging.Logger, tag: str = "") -> None:
    """把当前页面所有「可点击」元素的文字/描述打印出来，用于核对选择器。"""
    log.info("---- 可点击元素 dump %s ----", tag)
    try:
        for el in d(clickable=True):
            info = el.info
            txt = (info.get("text") or "").strip()
            desc = (info.get("contentDescription") or "").strip()
            rid = info.get("resourceName") or ""
            if txt or desc:
                log.info("  text=%-20s desc=%-20s rid=%s", txt[:20], desc[:20], rid)
    except Exception as e:
        log.warning("  dump 失败: %s", e)


def dump_hierarchy(d: "u2.Device", path: "Path", log: logging.Logger) -> None:
    """把当前界面的完整 XML 层级保存到文件（含所有 resourceId），方便精确定位。"""
    try:
        xml = d.dump_hierarchy()
        path.write_text(xml, encoding="utf-8")
        log.info("已保存界面层级到 %s", path)
    except Exception as e:
        log.warning("dump_hierarchy 失败: %s", e)


def screenshot(d: "u2.Device", path: "Path", log: logging.Logger) -> None:
    try:
        d.screenshot(str(path))
        log.info("已保存截图到 %s", path)
    except Exception as e:
        log.warning("截图失败: %s", e)


def _parse_hierarchy(xml_str: str) -> list[dict]:
    """解析 uiautomator2 的 dump XML，返回元素列表（兼容带命名空间的标准格式）。"""
    import xml.etree.ElementTree as ET
    out: list[dict] = []

    def g(node, attr):
        for k, v in node.attrib.items():
            if k == attr or k.endswith("}" + attr):
                return v or ""
        return ""

    try:
        root = ET.fromstring(xml_str)
    except Exception:
        return out
    for node in root.iter():
        out.append({
            "text": g(node, "text"),
            "desc": g(node, "content-desc"),
            "rid": g(node, "resource-id"),
            "cls": g(node, "class"),
            "bounds": g(node, "bounds"),
        })
    return out


def dump_and_print(d: "u2.Device", path: "Path", keywords: list[str],
                   log: logging.Logger, tag: str = "") -> None:
    """dump 当前界面 XML 到文件，并就地打印出含任意 keyword 的元素信息。"""
    try:
        xml = d.dump_hierarchy()
        path.write_text(xml, encoding="utf-8")
        log.info("已保存界面层级到 %s", path)
    except Exception as e:
        log.warning("dump_hierarchy 失败: %s", e)
        return
    items = _parse_hierarchy(xml)
    for kw in keywords:
        log.info("---- 含 %r 的元素 %s ----", kw, tag)
        n = 0
        for it in items:
            if kw in it["text"] or kw in it["desc"]:
                log.info("  text=%r desc=%r rid=%s cls=%s bounds=%s",
                         it["text"][:24], it["desc"][:24], it["rid"], it["cls"], it["bounds"])
                n += 1
        if n == 0:
            log.info("  (没有找到含 %r 的元素)", kw)


def click_by_text_contains(d: "u2.Device", keyword: str, log: logging.Logger,
                           timeout: float = 3.0) -> bool:
    """不依赖 SEL，直接按「文字/描述包含 keyword」点第一个命中的元素。用于检视探索。"""
    for getter in (lambda: d(textContains=keyword), lambda: d(descriptionContains=keyword)):
        sel = getter()
        try:
            if sel.wait_exists(timeout=timeout):
                sel[0].click()
                return True
        except Exception:
            continue
    return False



# ----------------------- 业务流程 -----------------------
def connect(serial: str | None, log: logging.Logger) -> "u2.Device":
    d = u2.connect(serial) if serial else u2.connect()
    log.info("已连接设备: %s", d.serial)
    d.implicitly_wait(5)
    return d


def launch_app(d: "u2.Device", log: logging.Logger) -> None:
    log.info("启动小红书 App ...")
    d.app_start(XHS_PACKAGE)
    # 等开屏/广告，进首页
    time.sleep(4)
    # 若误点开开屏广告，回到首页
    if not find_click(d, SEL["search_entry"], log, wait_each=2):
        log.warning("没找到首页搜索入口，可能卡在开屏。请手动回到首页后重试。")


def do_search(d: "u2.Device", keyword: str, log: logging.Logger) -> None:
    log.info("搜索关键词: %s", keyword)
    if not find_click(d, SEL["search_entry"], log):
        raise RuntimeError("找不到搜索入口，请核对 SEL['search_entry']")
    time.sleep(1.5)
    # 在输入框输入关键词
    box = None
    for spec in SEL["search_box"]:
        s = make_selector(d, spec)
        if s.wait_exists(timeout=2):
            box = s
            break
    if not box:
        raise RuntimeError("找不到搜索输入框，请核对 SEL['search_box']")
    box.click()
    time.sleep(0.5)
    d.send_keys(keyword)
    time.sleep(1)
    d.press("enter")
    time.sleep(2.5)
    # 切到「用户」标签页
    if find_click(d, SEL["user_tab"], log):
        log.info("已切到「用户」标签页。")
    else:
        log.warning("没切到「用户」标签页，可能仍在综合页（混有笔记）。请核对 SEL['user_tab']。")
    time.sleep(1.5)


def collect_users(d: "u2.Device", keyword: str, log: logging.Logger) -> list[str]:
    """在「用户」标签页滚动加载，收集昵称含关键词的用户（去重）。"""
    seen: set[str] = set()
    results: list[str] = []
    for i in range(MAX_SWIPES):
        # 当前屏所有含关键词的文字元素（基本就是用户昵称）
        els = d(textContains=keyword)
        try:
            count = els.count
        except Exception:
            count = 0
        for j in range(count):
            try:
                txt = (els[j].info.get("text") or "").strip()
            except Exception:
                continue
            if not txt or txt in seen:
                continue
            seen.add(txt)
            results.append(txt)
        log.info("  已收集 %d 个候选（滑动第 %d/%d 屏）", len(results), i + 1, MAX_SWIPES)

        # 向上滑一屏加载更多
        before = len(results)
        d.swipe(0.5, 0.82, 0.5, 0.18, duration=0.3)
        time.sleep(1.5)
        # 没有新增 + 再滑一次仍无新增 → 到底，结束
        if len(results) == before:
            time.sleep(2)
            if len(results) == before:
                log.info("  貌似已加载全部用户，停止滑动。")
                break
    return results


def open_and_block(d: "u2.Device", name: str, log: logging.Logger) -> bool:
    """打开该用户主页并执行拉黑（前提是已经在搜索结果页）。"""
    # 按昵称找到并点进主页（uiautomator2 会自动滚动到可见）
    name_el = d(textContains=name)
    if not name_el.wait_exists(timeout=3):
        log.warning("  在当前结果页找不到: %s", name)
        return False
    name_el.click()
    time.sleep(2.5)  # 等主页加载

    if not find_click(d, SEL["more_btn"], log):
        log.warning("  主页找不到「更多」按钮，跳过: %s", name)
        dump_clickable(d, log, tag=f"profile[{name}]")
        d.press("back")
        time.sleep(1)
        return False
    time.sleep(1)

    if not find_click(d, SEL["block_item"], log):
        log.warning("  弹层找不到「拉黑」选项，跳过: %s", name)
        d.press("back")
        time.sleep(1)
        return False
    time.sleep(1)

    if not find_click(d, SEL["confirm_btn"], log):
        log.warning("  找不到确认按钮，跳过: %s", name)
        d.press("back")
        time.sleep(1)
        return False
    time.sleep(1.5)

    if SCREENSHOT_ON_BLOCK:
        try:
            Path(f"shot_{name}.png").write_bytes(d.screenshot())
        except Exception:
            pass
    # 回到搜索结果页，准备下一个
    d.press("back")
    time.sleep(1.5)
    return True


def run(apply: bool, serial: str | None, inspect: bool, log: logging.Logger) -> None:
    if apply:
        log.warning("⚠️ 已进入 --apply 模式，将对命中用户执行真实拉黑！")
    elif inspect:
        log.info("检视模式：仅打印 UI 元素，不拉黑。")
    else:
        log.info("扫描模式(DRY_RUN)：只收集名单，不拉黑任何人。")

    d = connect(serial, log)
    launch_app(d, log)

    if inspect:
        base = Path(__file__).resolve().parent
        kw = TARGET_KEYWORDS[0]
        # 等开屏/广告消掉，再 dump 首页（多等一会，避免卡在开屏）
        log.info("等待首页稳定 ...")
        time.sleep(8)
        dump_and_print(d, base / "ui_home.xml", ["搜索", "我"], log, tag="首页")
        screenshot(d, base / "ui_home.png", log)

        # 尽力点进搜索页（不依赖 SEL，按文字找）
        if click_by_text_contains(d, "搜索", log):
            log.info("已点击搜索入口，等待搜索页 ...")
            time.sleep(2.5)
            dump_and_print(d, base / "ui_search.xml", ["用户"], log, tag="搜索页-标签")
            screenshot(d, base / "ui_search.png", log)

            # 在输入框输入关键词
            box = d(className="android.widget.EditText")
            if box.wait_exists(timeout=3):
                box.click()
                time.sleep(0.5)
                try:
                    d.send_keys(kw)
                except Exception as e:
                    log.warning("send_keys 失败: %s（可手动在手机上输入 %s）", e, kw)
                time.sleep(0.8)
                d.press("enter")
                time.sleep(3)
                dump_and_print(d, base / "ui_result.xml", ["用户"], log, tag="结果页-标签")
                screenshot(d, base / "ui_result.png", log)
                # 切到「用户」标签
                click_by_text_contains(d, "用户", log)
                time.sleep(2)
                dump_and_print(d, base / "ui_userlist.xml", [kw], log, tag="用户列表")
                screenshot(d, base / "ui_userlist.png", log)
                log.info("---- 用户列表里含 %r 的昵称 ----", kw)
                try:
                    for el in d(textContains=kw):
                        t = (el.info.get("text") or "").strip()
                        if t:
                            log.info("  昵称=%r", t)
                except Exception as e:
                    log.warning("读取昵称失败: %s", e)

                # 点第一个用户进主页
                u = d(textContains=kw)
                if u.wait_exists(timeout=3) and u.count > 0:
                    u[0].click()
                    time.sleep(3)
                    dump_and_print(d, base / "ui_profile.xml", ["更多", "拉黑"], log, tag="主页")
                    screenshot(d, base / "ui_profile.png", log)
            else:
                log.warning("没找到搜索输入框（EditText），请手动在手机上点一下输入框。")
        else:
            log.warning("首页没找到文字含『搜索』的元素 —— 可能还卡在开屏/广告。"
                        "请手动回到首页，看 ui_home.png / ui_home.xml 里到底是什么。")

        log.info("检视完成。请把 ui_*.xml / ui_*.png 对照，把真实 rid/text/desc "
                 "填进文件顶部的 SEL 字典（尤其是 search_entry / search_box / "
                 "user_tab / more_btn / block_item / confirm_btn）。")
        return

    matched: list[str] = []
    for keyword in TARGET_KEYWORDS:
        do_search(d, keyword, log)
        users = collect_users(d, keyword, log)
        log.info("关键词 %s 共收集 %d 个候选用户。", keyword, len(users))
        for name in users:
            if any(k in name for k in TARGET_KEYWORDS):
                matched.append(name)
                if apply:
                    log.info("  [命中] %s -> 拉黑", name)
                    ok = open_and_block(d, name, log)
                    log.info("    拉黑%s", "成功" if ok else "失败")
                else:
                    log.info("  [命中(仅扫描)] %s", name)
            else:
                log.debug("  跳过(昵称不相关): %s", name)

    # 导出名单
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["nickname", "keyword"])
        for n in matched:
            w.writerow([n, ",".join(TARGET_KEYWORDS)])
    log.info("完成。共命中 %d 个用户，名单已写入 %s", len(matched), OUTPUT_CSV)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="小红书批量拉黑（安卓 App 自动化）")
    p.add_argument("--apply", action="store_true", help="真正执行拉黑。不加则只扫描。")
    p.add_argument("--serial", default=None, help="设备序列号；不填则连第一个设备。")
    p.add_argument("--inspect", action="store_true", help="检视模式：打印 UI 元素用于核对选择器。")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    log = setup_log()
    try:
        run(apply=args.apply, serial=args.serial, inspect=args.inspect, log=log)
    except Exception as e:
        log.exception("运行出错: %s", e)
