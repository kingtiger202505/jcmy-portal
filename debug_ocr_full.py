# -*- coding: utf-8 -*-
"""OCR 全窗口，打印所有文本行 + 位置，判断当前显示的是哪个聊天 + 布局比例"""
import io
import sys
import time

import numpy as np
from rapidocr_onnxruntime import RapidOCR

from wechat_bot import capture_window, find_wechat_hwnd
import win32gui

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

hwnd = find_wechat_hwnd()
rect = win32gui.GetWindowRect(hwnd)
W, H = rect[2] - rect[0], rect[3] - rect[1]
print(f"窗口 {W}x{H}")
img = capture_window(hwnd)
img.save("debug_fullwin_now.png")

ocr = RapidOCR()
result, _ = ocr(np.array(img))
if not result:
    print("OCR 无结果")
    sys.exit(0)

items = sorted(result, key=lambda l: sum(p[1] for p in l[0]) / 4)
for box, text, score in items:
    cx = sum(p[0] for p in box) / 4 / W
    cy = sum(p[1] for p in box) / 4 / H
    side = "侧栏" if cx < 0.25 else "聊天区"
    print(f"  y={cy:.3f} x={cx:.3f} [{side}] ({score:.2f}) {text!r}")
