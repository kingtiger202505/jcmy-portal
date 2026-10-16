# -*- coding: utf-8 -*-
"""发群消息全流程带断言调试：定位"发不出去"卡在哪一步。

安全约定（重要，避免打到用户正在编辑的窗口）：
  * 只在「微信窗口确实是前台窗口」时才注入按键；不是前台就立刻中止，绝不盲发。
  * 粘贴后必须 OCR 到输入框里出现了这条消息，才会按回车；否则不按回车，直接中止。
  * 结束时还原原来的前台窗口。

用法：
  python debug_send_verify.py              # 只体检，不污染群（不发送）
  python debug_send_verify.py --send       # 真发一条测试消息（会短暂抢前台 ~5s）
  python debug_send_verify.py --send --group "ai测试2群"
"""
import argparse
import ctypes
import ctypes.wintypes as wt
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

# 输入框垂直位置（窗口内比例）。874/1041≈0.84 是文字区中心，避开发送按钮和工具栏
INPUT_Y_RATIOS = [0.80, 0.92, 0.72]

_user32 = ctypes.windll.user32


# ---------------------------------------------------------------- 注入
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL, VK_V, VK_RETURN = 0x11, 0x56, 0x0D


def send_key(vk, ctrl=False):
    """SendInput 发一次按键（比 keybd_event 更可靠，不会被 IME 吞）"""
    seq = []
    if ctrl:
        seq.append((VK_CONTROL, 0))
    seq.append((vk, 0))
    seq.append((vk, KEYEVENTF_KEYUP))
    if ctrl:
        seq.append((VK_CONTROL, KEYEVENTF_KEYUP))
    arr = (INPUT * len(seq))()
    for i, (v, flags) in enumerate(seq):
        arr[i].type = INPUT_KEYBOARD
        arr[i].u.ki = KEYBDINPUT(wVk=v, wScan=0, dwFlags=flags, time=0, dwExtraInfo=None)
    n = _user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(INPUT))
    if n != len(seq):
        print(f"    [!] SendInput 只发出 {n}/{len(seq)} 个事件，err={ctypes.GetLastError()}")


def click_at(win, off_x, off_y, screen_pt):
    """用 SetCursorPos + mouse_event 点击屏幕绝对坐标（绕开 uiautomation 的控件内偏移语义）"""
    win32api.SetCursorPos(screen_pt)
    time.sleep(0.05)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.05)
    _user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
    time.sleep(0.2)


# ---------------------------------------------------------------- 工具
def step(name, ok, detail="", fatal=False):
    print(f"  {'[PASS]' if ok else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))
    return ok


def ocr_texts(ocr, img):
    if img is None:
        return []
    result, _ = ocr(np.array(img))
    return [t for _, t, _ in (result or [])]


def ocr_lines_up(ocr, img, region, scale=2):
    """OCR 某区域（放大 scale 倍，小字更准），返回文本行"""
    crop = img.crop(region)
    crop = crop.resize((crop.width * scale, crop.height * scale), 1)  # 1=LANCZOS
    return ocr_texts(ocr, crop)


def force_foreground(hwnd):
    """SetForegroundWindow 被前台锁挡住时，用 AttachThreadInput 绕过"""
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    fg = win32gui.GetForegroundWindow()
    try:
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
        cur_tid = win32api.GetCurrentThreadId()
        win32process.AttachThreadInput(cur_tid, fg_tid, True)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32process.AttachThreadInput(cur_tid, fg_tid, False)
        time.sleep(0.3)
    except Exception as e:
        print(f"    AttachThreadInput 失败: {e}")
    return win32gui.GetForegroundWindow() == hwnd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="真发一条测试消息")
    ap.add_argument("--group", default="ai测试2群")
    ap.add_argument("--stay", action="store_true", help="结束后不还原前台窗口")
    args = ap.parse_args()

    ocr = RapidOCR()
    hwnd = find_wechat_hwnd()
    if not hwnd:
        print("[X] 没找到微信窗口")
        return 1
    win = auto.ControlFromHandle(hwnd)

    # ============ 0. 窗口几何 + 坐标空间 ============
    print("\n--- 0. 窗口几何 / 坐标空间 ---")
    if win32gui.IsIconic(hwnd):
        print("  [i] 微信当前是最小化状态，先看一下（capture_window 会顺带还原它）")
    rect = win32gui.GetWindowRect(hwnd)
    if rect[0] <= -30000:            # 最小化时 rect 是 (-32000,-32000)
        capture_window(hwnd)          # 触发还原
        time.sleep(0.8)
        rect = win32gui.GetWindowRect(hwnd)
    left, top, right, bottom = rect
    W, H = right - left, bottom - top
    print(f"  微信 rect={rect} 尺寸={W}x{H} 前台={win32gui.GetForegroundWindow()}")

    # 老代码的点：screen 绝对坐标被当成"控件内偏移"喂给 Click()
    buggy = ((right + left) // 2, bottom - 100)
    real_click = (left + buggy[0], top + buggy[1])
    print(f"  老代码算出的点 (left+right)//2, bottom-100 = {buggy}  ← 它以为是屏幕坐标")
    print(f"  uiautomation 会当成控件内偏移 → 实际鼠标落到屏幕点 {real_click}")
    inside = left <= real_click[0] <= right and top <= real_click[1] <= bottom
    step("老代码的点击落在微信窗口内", inside,
         "" if inside else f"落在窗口外（窗口 y 范围 {top}~{bottom}，实际点到 {real_click[1]}）→ 输入框拿不到焦点")
    print("  → 窗口最大化(左上角 0,0、高度够大)时这个巧合恰好点中输入框，所以『之前都可以』")

    # 正确写法：控件内偏移，或直接用比例
    fixed_off = (W // 2, int(H * INPUT_Y_RATIOS[0]))
    print(f"  正确写法：Click(ratioX=0.5, ratioY={INPUT_Y_RATIOS[0]}) "
          f"→ 控件内偏移 {fixed_off} → 屏幕点 {(left + fixed_off[0], top + fixed_off[1])}")

    if not args.send:
        print("\n（未加 --send，只做体检，不发送。加 --send 才会真的往群里发一条）")
        return 0

    # ============ 1. 抢前台（失败就中止，绝不盲发） ============
    print("\n--- 1. 抢前台 ---")
    prev_fg = win32gui.GetForegroundWindow()
    MSG = f"调试zb {time.strftime('%H:%M:%S')}"
    print(f"  待发消息: {MSG!r}  目标群: {args.group!r}")
    win.SetActive()
    time.sleep(0.3)
    fg = win32gui.GetForegroundWindow()
    if fg != hwnd:
        print(f"  SetActive 没抢到（前台仍为 {fg} {win32gui.GetWindowText(fg)!r}），走 AttachThreadInput 兜底")
        force_foreground(hwnd)
        fg = win32gui.GetForegroundWindow()
    if not step("微信是前台窗口", fg == hwnd, f"前台={fg} {win32gui.GetWindowText(fg)!r}"):
        print("  >>> 中止：keybd_event/SendInput 只发给前台窗口，抢不到前台发出去就是打到别的窗口上")
        return 1

    # ============ 2. 切到目标群 ============
    print("\n--- 2. 切到目标群 ---")
    img = capture_window(hwnd)
    if img is None:
        print("  [FAIL] 截图失败（窗口被挡住？）"); return 1
    side = img.crop((0, int(H * 0.08), int(W * 0.25), H))
    hit = None
    for box, text, score in (ocr(np.array(side))[0] or []):
        if args.group in text.strip() or difflib.SequenceMatcher(None, text.strip(), args.group).ratio() > 0.7:
            hit = (box, text.strip(), score); break
    if hit:
        box, text, score = hit
        off = (int(sum(p[0] for p in box) / 4), int(sum(p[1] for p in box) / 4 + H * 0.08))
        click_at(win, off[0], off[1], (left + off[0], top + off[1]))
        time.sleep(1.0)
        title = " ".join(ocr_lines_up(ocr, capture_window(hwnd), (int(W * 0.25), 0, W, int(H * 0.12)), 2))
        step("切换到目标群", args.group[:4] in title, f"标题区 OCR={title!r}")
    else:
        print(f"  [i] 侧栏没 OCR 到 {args.group!r}，跳过切换（当前聊天可能就是它）")

    # ============ 3. 点输入框 + 粘贴 + 验证 ============
    print("\n--- 3. 聚焦输入框 → Ctrl+V → OCR 验证 ---")
    pyperclip.copy(MSG)
    time.sleep(0.2)
    input_region = (int(W * 0.25), int(H * 0.64), W, int(H * 0.99))
    pasted_ok = False
    for attempt, ry in enumerate(INPUT_Y_RATIOS, 1):
        if win32gui.GetForegroundWindow() != hwnd:
            print("    [!] 前台已经不是微信了，中止"); break
        off_x, off_y = W // 2, int(H * ry)
        screen_pt = (left + off_x, top + off_y)
        print(f"  第 {attempt} 次: 点控件内偏移 ({off_x},{off_y}) = 屏幕 {screen_pt}  [ratioY={ry}]")
        click_at(win, off_x, off_y, screen_pt)
        before = ocr_lines_up(ocr, capture_window(hwnd), input_region, 2)
        send_key(VK_V, ctrl=True)
        time.sleep(0.6)
        texts = ocr_lines_up(ocr, capture_window(hwnd), input_region, 2)
        print(f"    粘贴后输入区 OCR: {texts}")
        if any("调试zb" in t or difflib.SequenceMatcher(None, t, MSG).ratio() > 0.6 for t in texts):
            pasted_ok = True
            print(f"    [PASS] 文字进了输入框（ratioY={ry} 这个位置是对的）")
            break
        print("    [FAIL] 输入框里没看到文字，换个高度重试")
        # 清掉可能残留在别处的粘贴结果
        pyperclip.copy(MSG)

    if not pasted_ok:
        step("粘贴进输入框", False, "所有候选高度都没进去 → 不是坐标问题就是焦点/注入被挡")
        print("  >>> 中止：没进输入框就不按回车（否则回车会把别的窗口里的内容发出去 / 或什么都发不出）")
        if not args.stay and prev_fg and prev_fg != hwnd:
            try: win32gui.SetForegroundWindow(prev_fg)
            except Exception: pass
        return 1

    # ============ 4. 回车发送 ============
    print("\n--- 4. 回车发送 ---")
    send_key(VK_RETURN)
    time.sleep(1.2)
    img = capture_window(hwnd)
    after_input = ocr_lines_up(ocr, img, input_region, 2)
    chat_region = (int(W * 0.25), int(H * 0.09), W, int(H * 0.86))
    after_chat = ocr_lines_up(ocr, img, chat_region, 1)
    still = any("调试zb" in t for t in after_input)
    print(f"  发送后输入区: {after_input}")
    print(f"  发送后聊天区含该消息: {any('调试zb' in t for t in after_chat)}")
    if still:
        step("回车发出", False, "文字还在输入框 → 回车没触发发送")
    else:
        step("回车发出", True, "输入框已清空（消息内容见聊天区截图确认）")

    if not args.stay and prev_fg and prev_fg != hwnd:
        try: win32gui.SetForegroundWindow(prev_fg)
        except Exception: pass
        print(f"  已还原前台窗口 {prev_fg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())