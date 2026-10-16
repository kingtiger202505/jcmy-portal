# -*- coding: utf-8 -*-
"""布局体检：截图两种方式对比 + 侧栏/标题/输入框位置 dump。

只读（不点不按键）。最小化的窗口会先被 capture_window 还原。
产物：debug_layout_bitblt.png / debug_layout_printwindow.png
"""
import ctypes
import io
import sys

import numpy as np
import uiautomation as auto
import win32gui
import win32ui
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

from wechat_bot import capture_window, find_wechat_hwnd

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PW_RENDERFULLCONTENT = 2
_user32 = ctypes.windll.user32


def printwindow_capture(hwnd):
    """wechat_multi_monitor 原来用的方式"""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, 9)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfcDC, w, h)
    saveDC.SelectObject(bmp)
    ret = _user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), PW_RENDERFULLCONTENT)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    saveDC.DeleteDC(); mfcDC.DeleteDC(); win32gui.ReleaseDC(hwnd, hwndDC)
    return ret, img


hwnd = find_wechat_hwnd()
if not hwnd:
    print("[X] 没找到微信窗口"); sys.exit(1)
print(f"hwnd={hwnd} 最小化={win32gui.IsIconic(hwnd)} 前台={win32gui.GetForegroundWindow()}")
rect = win32gui.GetWindowRect(hwnd)
print(f"rect={rect}")

ocr = RapidOCR()

print("\n--- 截图方式对比 ---")
bb = capture_window(hwnd)                       # BitBlt（wechat_bot 用）
ret, pw = printwindow_capture(hwnd)             # PrintWindow（multi 原来用）
for name, img in (("BitBlt", bb), ("PrintWindow", pw)):
    if img is None:
        print(f"  {name}: None"); continue
    a = np.array(img.convert("L"))
    print(f"  {name}: {img.size} 平均亮度={a.mean():.1f} 非黑像素占比={(a > 20).mean() * 100:.1f}% "
          f"最长边={max(img.size)}")
    img.save(f"debug_layout_{name.lower()}.png")
print(f"  PrintWindow 返回值={ret} (1=成功)")
rect = win32gui.GetWindowRect(hwnd)
if rect[0] > -30000:
    bb = capture_window(hwnd)
W, H = bb.size
print(f"  窗口 rect={rect} 尺寸 {W}x{H}")

print("\n--- 侧栏 OCR（0..0.25W, 0.08H..H）---")
side = bb.crop((0, int(H * 0.08), int(W * 0.25), H))
res, _ = ocr(np.array(side))
for box, text, score in sorted((res or []), key=lambda l: sum(p[1] for p in l[0]) / 4):
    cy = sum(p[1] for p in box) / 4
    print(f"  y={cy / side.height:.3f} ({score:.2f}) {text!r}")

print("\n--- 标题区 OCR（0.25W..W, 0..0.12H）---")
title = bb.crop((int(W * 0.25), 0, W, int(H * 0.12)))
res, _ = ocr(np.array(title))
print("  ", [(t, round(s, 2)) for _, t, s in (res or [])])

print("\n--- 全窗口 OCR，标出每行 y 比例（找输入框和发送按钮在哪）---")
res, _ = ocr(np.array(bb))
for box, text, score in sorted((res or []), key=lambda l: sum(p[1] for p in l[0]) / 4):
    cy = sum(p[1] for p in box) / 4
    cx = sum(p[0] for p in box) / 4
    if cy / H > 0.55:      # 只看下半部分
        print(f"  y={cy / H:.3f} x={cx / W:.3f} ({score:.2f}) {text!r}")