# -*- coding: utf-8 -*-
"""分步调试：微信群发消息全流程，每步截图 + OCR 验证，定位卡在哪一步。

流程（每步独立验证，前一步 FAIL 就停）：
  1. 找到微信窗口
  2. 侧栏点击切群   → OCR 标题区验证是否切成功
  3. 抢前台         → GetForegroundWindow 验证（keybd_event 只发给前台窗口！）
  4. 聚焦输入框     → 优先 UIA EditControl，兜底用"窗口内偏移"点击（非屏幕绝对坐标）
  5. Ctrl+V 粘贴    → OCR 输入区，验证文字真的进了输入框
  6. 回车发送       → OCR 验证输入区已清空 + 消息出现在聊天区

输入框定位不用屏幕绝对坐标：UIA 控件优先，坐标兜底是窗口内偏移 (W//2, 0.92H)。
"""
import difflib
import io
import sys
import time

import numpy as np
import pyperclip
import uiautomation as auto
import win32api
import win32con
import win32gui
import win32process
from rapidocr_onnxruntime import RapidOCR

from wechat_bot import capture_window, find_wechat_hwnd

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

GROUP = "ai测试2群"
TAG = "调试测试"
MSG = f"{TAG} {time.strftime('%H:%M:%S')}"


def step(name, ok, detail=""):
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}  {detail}")
    return ok


def ocr_region(ocr, img, box):
    """OCR 截图某区域，返回 [(text, score)]"""
    region = img.crop(box)
    result, _ = ocr(np.array(region))
    if not result:
        return []
    return [(t, s) for _, t, s in result]


def force_foreground(hwnd):
    """SetActive 失败时的兜底：AttachThreadInput 绕过前台锁"""
    fg = win32gui.GetForegroundWindow()
    if fg == hwnd:
        return True
    try:
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
        tid = win32api.GetCurrentThreadId()
        win32process.AttachThreadInput(tid, fg_tid, True)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32process.AttachThreadInput(tid, fg_tid, False)
        time.sleep(0.3)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception as e:
        print(f"  AttachThreadInput 失败: {e}")
        return False


def main():
    ocr = RapidOCR()

    # ---------- 1. 窗口 ----------
    hwnd = find_wechat_hwnd()
    if not hwnd:
        print("[X] 没找到微信窗口")
        return
    rect = win32gui.GetWindowRect(hwnd)
    W, H = rect[2] - rect[0], rect[3] - rect[1]
    print(f"微信 hwnd={hwnd} rect={rect} 尺寸 {W}x{H}（物理像素，DPI 已感知）")
    if not step("1 找到微信窗口", True):
        return

    win = auto.ControlFromHandle(hwnd)

    # ---------- 2. 侧栏点击切群 ----------
    img = capture_window(hwnd)
    if img is None:
        step("2 窗口截图", False, "capture_window 返回 None")
        return
    img.save("debug_flow_step2_sidebar.png")
    side = img.crop((0, int(H * 0.08), int(W * 0.25), H))
    result, _ = ocr(np.array(side))
    target = None
    if result:
        for b, t, s in result:
            t = t.strip()
            if GROUP in t or difflib.SequenceMatcher(None, t, GROUP).ratio() > 0.7:
                target = (b, t, s)
                break
    if not target:
        lines = [t for _, t, _ in (result or [])][:12]
        step("2 侧栏找到群名", False, f"OCR 侧栏行: {lines}")
        return
    box, text, score = target
    # 窗口内偏移 = 侧栏内坐标 + crop 偏移
    cx = int(sum(p[0] for p in box) / 4)
    cy = int(sum(p[1] for p in box) / 4 + H * 0.08)
    print(f"  群名 '{text}' (置信度 {score:.2f}) → 点击窗口内偏移 ({cx}, {cy})")
    win.Click(cx, cy)
    time.sleep(1.2)

    img = capture_window(hwnd)
    img.save("debug_flow_step2_after_switch.png")
    title = ocr_region(ocr, img, (int(W * 0.25), 0, W, int(H * 0.14)))
    title_text = " ".join(t for t, _ in title)
    print(f"  标题区 OCR: {title_text!r}")
    switched = GROUP[:4] in title_text or difflib.SequenceMatcher(
        None, title_text, GROUP).ratio() > 0.5
    if not step("2 切换到目标群", switched, f"标题: {title_text!r}"):
        print("    点击没切中群（看 debug_flow_step2_after_switch.png 确认点到了哪）")
        return

    # ---------- 3. 抢前台 ----------
    win.SetActive()
    time.sleep(0.2)
    fg = win32gui.GetForegroundWindow()
    print(f"  SetActive 后前台: {fg} ({win32gui.GetWindowText(fg)})")
    if fg != hwnd:
        ok = force_foreground(hwnd)
        fg = win32gui.GetForegroundWindow()
        print(f"  兜底抢前台后: {fg} ({win32gui.GetWindowText(fg)})")
    else:
        ok = True
    if not step("3 抢到前台", ok, "keybd_event 只发给前台窗口，抢不到后面全白做"):
        return

    # ---------- 4. 聚焦输入框 ----------
    edit = None
    try:
        edit = win.EditControl(searchDepth=20)
        if edit.Exists(2, 0.5):
            r = edit.BoundingRectangle
            print(f"  UIA 找到输入框: ({r.left},{r.top})-({r.right},{r.bottom})")
            edit.Click()
            time.sleep(0.3)
        else:
            edit = None
    except Exception as e:
        print(f"  UIA 搜索输入框异常: {e}")
        edit = None
    if edit is None:
        # 兜底：窗口内偏移。输入框在窗口底部约 0.92H 处（聊天区到 0.88H 为止）
        off_x, off_y = W // 2, int(H * 0.92)
        print(f"  UIA 没找到输入框，用窗口内偏移点击 ({off_x}, {off_y})")
        win.Click(off_x, off_y)
        time.sleep(0.3)
    step("4 聚焦输入框", True, "是否真的聚焦看第 5 步粘贴验证")

    # ---------- 5. Ctrl+V 粘贴 ----------
    pyperclip.copy(MSG)
    time.sleep(0.2)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('V'), 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.8)

    img = capture_window(hwnd)
    img.save("debug_flow_step5_after_paste.png")
    input_area = ocr_region(ocr, img, (int(W * 0.25), int(H * 0.86), W, H))
    texts = [t for t, _ in input_area]
    print(f"  输入区 OCR: {texts}")
    pasted = any(TAG in t for t in texts) or any(
        difflib.SequenceMatcher(None, t, MSG).ratio() > 0.6 for t in texts)
    if not step("5 粘贴进输入框", pasted):
        print("    >>> 文字没进输入框。可能：输入框没点中 / 窗口不是前台 / Ctrl+V 被截")
        return

    # ---------- 6. 回车发送 ----------
    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(1.0)

    img = capture_window(hwnd)
    img.save("debug_flow_step6_after_enter.png")
    input_after = [t for t, _ in ocr_region(ocr, img, (int(W * 0.25), int(H * 0.86), W, H))]
    chat_after = [t for t, _ in ocr_region(ocr, img, (int(W * 0.25), int(H * 0.09), W, int(H * 0.86)))]
    still_in_input = any(TAG in t for t in input_after)
    in_chat = any(TAG in t for t in chat_after)
    print(f"  发送后输入区: {input_after}")
    print(f"  发送后聊天区出现消息: {in_chat}")
    if still_in_input:
        step("6 回车发出", False, "文字还在输入框，回车没触发发送")
    elif in_chat:
        step("6 回车发出", True)
    else:
        step("6 回车发出", True, "输入区已清空（聊天区 OCR 没扫到，人工看 debug_flow_step6_after_enter.png 确认）")

    print("\n===== 结论 =====")
    print("第 5 步是分水岭：粘贴进输入框成功 = 窗口/焦点/输入框都 OK；失败 = 卡在这之前。")


if __name__ == "__main__":
    print(f"3 秒后开始，目标群 [{GROUP}]，消息 [{MSG}]...")
    time.sleep(3)
    main()
