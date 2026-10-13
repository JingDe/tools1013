"""
workout_tts.py — 跟练语音播报助手
================================
用途：打开 B 站跟练帕梅拉视频前，先打开本程序，框选播放器右上角显示动作名的区域。
      程序每隔约 0.8 秒识别一次该区域文字，发现动作名变了就把新动作名念出来。

原理：视频里的动作名是"烧录"在画面像素里的，不是网页文字，所以只能"看屏幕"。
      流程 = 区域截图(mss) → OCR 识别(pytesseract) → 变化检测 → 语音(edge-tts)。

依赖安装：
    pip install mss pytesseract pygame edge-tts pillow
额外：还需安装 Tesseract OCR 程序（Windows 用 UB Mannheim 安装包），
      装完后把它加入系统 PATH；或取消下方 tesseract_cmd 那行的注释并改成你的实际路径。

运行：
    python workout_tts.py
"""

import asyncio
import ctypes
import difflib
import threading
import time
from queue import Queue

import pytesseract
from mss import mss
from PIL import Image
import pygame
import tkinter as tk
from tkinter import messagebox

# ---------- 可选：指定 Tesseract 路径（若已加入 PATH 可留空）----------
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ---------- 可调参数 ----------
VOICE = "en-US-AriaNeural"      # edge-tts 英文女声；想换男声可改 en-US-GuyNeural
POLL_INTERVAL = 0.8             # 每隔多少秒识别一次（越小越灵敏，越费 CPU）
SIMILARITY_THRESHOLD = 0.85     # 相似度高于此值视为"同一段文字"，用来抗 OCR 抖动

# 让 tkinter 坐标与 mss 截图坐标 1:1 对应（Windows 高 DPI 缩放时很关键）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 按显示器 DPI 感知
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------- 全局状态 ----------
running = False
region = None                   # (left, top, width, height) 屏幕像素
last_spoken = ""
prev_text = ""
tts_queue = Queue()
app = None                      # tkinter 主窗口引用
last_var = None                 # 显示最近识别结果的变量


# ============================================================
# 1) 语音：edge-tts 生成 mp3，pygame 播放
# ============================================================
pygame.mixer.init()


def _play_mp3(path):
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
    except Exception as e:
        print("播放失败:", e)


async def _speak(text):
    tmp = "tts_tmp.mp3"
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(tmp)
    _play_mp3(tmp)


def tts_worker():
    """后台线程：从队列取文字并朗读。"""
    while True:
        text = tts_queue.get()
        if text is None:
            break
        try:
            asyncio.run(_speak(text))
        except Exception as e:
            print("TTS 失败:", e)
        tts_queue.task_done()


threading.Thread(target=tts_worker, daemon=True).start()


# ============================================================
# 2) OCR：截取区域 → 识别成文字
# ============================================================
def ocr_region():
    """截取已选区域，返回识别到的英文文字（已规整）。"""
    if not region:
        return ""
    left, top, width, height = region
    with mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    # mss 返回 BGRA 像素，转成 PIL 的 RGB 图像交给 tesseract
    img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)
    # 小字识别率不够时，可取消下一行注释做 2 倍放大（会更慢一点）
    # img = img.resize((shot.width * 2, shot.height * 2))
    text = pytesseract.image_to_string(img, lang="eng")
    return normalize(text)


def normalize(s):
    """转小写、合并多余空白，作为比对用的"标准文本"。"""
    return " ".join(s.lower().split())


# ============================================================
# 3) 监控循环：发现变化就朗读
# ============================================================
def update_ui(text):
    """线程安全地更新界面上的"最近动作"显示。"""
    if app is not None:
        app.after(0, last_var.set, text)


def monitor_loop():
    global running, last_spoken, prev_text
    last_spoken = ""
    prev_text = ""
    while running:
        text = ocr_region()
        if not text:
            prev_text = ""
            time.sleep(POLL_INTERVAL)
            continue

        # 稳定性判定：与上一帧足够相似才认为是"稳定画面"
        if prev_text and difflib.SequenceMatcher(None, prev_text, text).ratio() >= SIMILARITY_THRESHOLD:
            stable = text
        else:
            stable = None

        # 变了（且与上次念过的不一样）才念
        if stable and stable != last_spoken:
            tts_queue.put(stable)
            last_spoken = stable
            update_ui(stable)

        prev_text = text
        time.sleep(POLL_INTERVAL)


# ============================================================
# 4) 区域选择：全屏半透明，鼠标拖框
# ============================================================
def select_region():
    global region
    sel = tk.Tk()
    sel.attributes("-alpha", 0.3)
    sel.attributes("-fullscreen", True)
    sel.configure(background="gray")
    canvas = tk.Canvas(sel, cursor="cross")
    canvas.pack(fill="both", expand=True)

    start = []
    rect = [None]

    def on_down(e):
        start.clear()
        start.extend([e.x_root, e.y_root])
        if rect[0]:
            canvas.delete(rect[0])

    def on_move(e):
        if not start:
            return
        if rect[0]:
            canvas.delete(rect[0])
        rect[0] = canvas.create_rectangle(
            start[0], start[1], e.x_root, e.y_root, outline="red", width=3
        )

    def on_up(e):
        if not start:
            return
        x1, y1 = start
        x2, y2 = e.x_root, e.y_root
        left, top = min(x1, x2), min(y1, y2)
        width, height = abs(x2 - x1), abs(y2 - y1)
        if width > 5 and height > 5:
            region = (left, top, width, height)
            sel.destroy()
        else:
            messagebox.showwarning("提示", "框选区域太小，请重新框选")

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_move)
    canvas.bind("<ButtonRelease-1>", on_up)
    sel.mainloop()


# ============================================================
# 5) 主界面
# ============================================================
def main():
    global running, app, last_var
    app = tk.Tk()
    app.title("跟练语音播报助手")
    app.geometry("380x230")

    status = tk.StringVar(value="未选择区域")
    last_var = tk.StringVar(value="—")

    def on_select():
        select_region()
        if region:
            status.set(f"区域已选: {region}")

    def on_start():
        global running
        if not region:
            messagebox.showwarning("提示", "请先选择区域")
            return
        running = True
        status.set("监控中…（动作变化时会朗读）")
        threading.Thread(target=monitor_loop, daemon=True).start()

    def on_stop():
        global running
        running = False
        status.set("已停止")

    def on_test():
        """只识别一次、不朗读，用来校准框选位置和 OCR 准确度。"""
        if not region:
            messagebox.showwarning("提示", "请先选择区域")
            return
        t = ocr_region()
        last_var.set(t if t else "(未识别到文字，请调整区域/放大)")

    tk.Button(app, text="① 选择区域（拖框）", command=on_select, width=30).pack(pady=4)
    tk.Button(app, text="② 开始监控", command=on_start, width=30).pack(pady=4)
    tk.Button(app, text="③ 停止", command=on_stop, width=30).pack(pady=4)
    tk.Button(app, text="④ 测试识别（不朗读）", command=on_test, width=30).pack(pady=4)
    tk.Label(app, text="最近识别到的动作:").pack(pady=(8, 0))
    tk.Label(app, textvariable=last_var, fg="blue", wraplength=350).pack()
    tk.Label(app, textvariable=status, fg="gray").pack(pady=(8, 0))

    app.mainloop()


if __name__ == "__main__":
    main()
