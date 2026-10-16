# -*- coding: utf-8 -*-
"""只读诊断：定位"群里发消息发不出去"的原因。

全程不抢前台、不注入任何按键/鼠标。只做：
  1. 进程 DPI 感知级别 + 显示器缩放 + 窗口 rect（判断截图坐标 ↔ 屏幕坐标是否一致）
  2. 微信窗口 UIA BoundingRectangle vs Win32 GetWindowRect（判断 Click 的偏移量）
  3. UIA 控件树里的 Edit 控件清单（找出输入框到底是哪一个、坐标多少）
  4. ControlFromPoint：在"脚本要点的那个点"上，底下到底是什么控件
  5. 前台窗口 / 剪贴板（判断 keybd_event 会不会发错窗口）

用法：python debug_send_diag.py
"""
import ctypes
import ctypes.wintypes as wt
import io
import sys

import pyperclip
import uiautomation as auto
import win32gui

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SPI_GETSCREENREADER = 0x0046
_user32 = ctypes.windll.user32
_shcore = ctypes.windll.shcore


def section(t):
    print(f"\n{'=' * 6} {t} {'=' * 6}")


# ---------------- 1. DPI 感知 + 显示器 ----------------
section("1. DPI 感知 / 显示器")
awareness = ctypes.c_int()
try:
    _shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
    names = {0: "Unaware(未感知)", 1: "System(系统级)", 2: "PerMonitor(每显示器)"}
    print(f"python 进程 DPI 感知: {awareness.value} {names.get(awareness.value, '?')}")
except Exception as e:
    print(f"GetProcessDpiAwareness 失败: {e}")

try:
    ctx = ctypes.c_void_p()
    _user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    ctx = _user32.GetThreadDpiAwarenessContext()
    print(f"线程 DPI 上下文指针: {ctx}")
except Exception as e:
    print(f"GetThreadDpiAwarenessContext 失败: {e}")

print(f"GetSystemMetrics SM_CXSCREEN/CYSCREEN = {_user32.GetSystemMetrics(0)}x{_user32.GetSystemMetrics(1)}")
print(f"SM_CXVIRTUALSCREEN/CYVIRTUALSCREEN   = {_user32.GetSystemMetrics(78)}x{_user32.GetSystemMetrics(79)}")

try:
    import win32api
    for i, m in enumerate(win32api.EnumDisplayMonitors()):
        info = win32api.GetMonitorInfo(m[0])
        print(f"显示器{i}: rcMonitor={info['Monitor']} rcWork={info['Work']} primary={bool(info['Flags'] & 1)}")
except Exception as e:
    print(f"EnumDisplayMonitors 失败: {e}")


# ---------------- 定位微信窗口 ----------------
def find_wechat_hwnd():
    result = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            c = win32gui.GetClassName(hwnd)
            if (t == "微信" and "Qt" in c) or c == "WeChatMainWndForPC":
                result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None


section("2. 微信窗口")
hwnd = find_wechat_hwnd()
if not hwnd:
    print("[X] 没找到微信窗口")
    sys.exit(1)
rect = win32gui.GetWindowRect(hwnd)
W, H = rect[2] - rect[0], rect[3] - rect[1]
print(f"hwnd={hwnd} class={win32gui.GetClassName(hwnd)!r} title={win32gui.GetWindowText(hwnd)!r}")
print(f"GetWindowRect={rect}  尺寸={W}x{H}  最小化={win32gui.IsIconic(hwnd)}")
print(f"当前前台窗口: {win32gui.GetForegroundWindow()} ({win32gui.GetWindowText(win32gui.GetForegroundWindow())!r})")

won = auto.ControlFromHandle(hwnd)
br = won.BoundingRectangle
print(f"UIA BoundingRectangle=({br.left},{br.top})-({br.right},{br.bottom}) 尺寸={br.width()}x{br.height()}")
print(f"  → UIA 与 Win32 原点差: dx={br.left - rect[0]}, dy={br.top - rect[1]}  "
      f"尺寸差: dw={br.width() - W}, dh={br.height() - H}")
print("  (差 8px 左右是 Win10+ 隐形边框，属正常；差几十上百=DPI 缩放，Click 坐标会错位)")

# 截图尺寸 vs 窗口 rect：判断 capture_window 的图能不能当"窗口内坐标"用
from wechat_bot import capture_window   # noqa: E402
img = capture_window(hwnd)
if img:
    print(f"capture_window 截图尺寸={img.size}  (窗口 rect 尺寸={W}x{H})")
    if img.size == (W, H):
        print("  → 截图与窗口 rect 一一对应，截图像素坐标可直接当窗口内偏移")
    else:
        print("  >>> 截图尺寸和窗口 rect 不一致！截图坐标不能直接当 Click 的偏移用")
else:
    print("[X] capture_window 返回 None（BitBlt 失败，窗口可能被遮挡）")


# ---------------- 3. UIA 找输入框 ----------------
section("3. UIA 控件树里的 Edit（输入框候选）")
edits = []


def walk(ctrl, depth=0, max_depth=14):
    if depth > max_depth:
        return
    try:
        t = ctrl.ControlTypeName
    except Exception:
        return
    if t in ("EditControl", "DocumentControl"):
        r = ctrl.BoundingRectangle
        edits.append((ctrl, r, depth))
    try:
        children = ctrl.GetChildren()
    except Exception:
        return
    for c in children:
        walk(c, depth + 1, max_depth)


walk(won)
if not edits:
    print("没有 EditControl —— 微信没开 UIA 无障碍（QT_ACCESSIBILITY/SPI_SETSCREENREADER），"
          "uiautomation 那条路走不通，只能靠坐标点击")
else:
    for ctrl, r, d in edits:
        print(f"  [depth {d}] Name={ctrl.Name!r} Class={ctrl.ClassName!r} "
              f"rect=({r.left},{r.top})-({r.right},{r.bottom}) "
              f"窗口内偏移=({r.left - rect[0]},{r.top - rect[1]})-({r.right - rect[0]},{r.bottom - rect[1]})")
        print(f"      相对窗口: x {100*(r.left-rect[0])/W:.0f}%~{100*(r.right-rect[0])/W:.0f}%  "
              f"y {100*(r.top-rect[1])/H:.0f}%~{100*(r.bottom-rect[1])/H:.0f}%")

# ---------------- 4. 老代码点的那几个点，底下是什么控件 ----------------
section("4. 老代码的点击落点对照（read-only，用 ControlFromPoint）")
points = {
    "wechat_bot.send_text 的输入框点": ((rect[0] + rect[2]) // 2, rect[3] - 100),
    "wechat_bot.send_text 的正确写法(窗口内偏移)": (rect[0] + W // 2, rect[1] + int(H * 0.92)),
    "multi_monitor._switch_chat 无点击(用Ctrl+F)": None,
}

screen = (rect[0] + rect[2]) // 2, rect[3] - 100
print(f"老代码 screen_x=(left+right)//2={screen[0]}, screen_y=bottom-100={screen[1]} → 屏幕点 {screen}")
print(f"  屏幕分辨率 {_user32.GetSystemMetrics(0)}x{_user32.GetSystemMetrics(1)}")
if screen[1] > _user32.GetSystemMetrics(1):
    print("  >>> 这个 y 已经在屏幕外了！mouse_event 会把它夹到屏幕边缘，点到别的窗口上")
try:
    c = auto.ControlFromPoint(*screen)
    r = c.BoundingRectangle
    print(f"  该点底下控件: [{c.ControlTypeName}] Name={c.Name!r} Class={c.ClassName!r} "
          f"rect=({r.left},{r.top})-({r.right},{r.bottom})")
except Exception as e:
    print(f"  ControlFromPoint 失败: {e}")

# 正确落点
px, py = rect[0] + W // 2, rect[1] + int(H * 0.92)
try:
    c = auto.ControlFromPoint(px, py)
    r = c.BoundingRectangle
    print(f"窗口内偏移 (W/2, 0.92H) → 屏幕点 ({px},{py}) 底下: [{c.ControlTypeName}] "
          f"Name={c.Name!r} Class={c.ClassName!r}")
except Exception as e:
    print(f"  失败: {e}")

# ---------------- 5. 剪贴板 / 前台 ----------------
section("5. 剪贴板 + 前台")
try:
    cb = pyperclip.paste()
    print(f"当前剪贴板: {cb[:80]!r}")
except Exception as e:
    print(f"读剪贴板失败: {e}")
print(f"前台窗口: {win32gui.GetForegroundWindow()} "
      f"({win32gui.GetWindowText(win32gui.GetForegroundWindow())!r}) 微信={hwnd}")
if win32gui.GetForegroundWindow() != hwnd:
    print("  >>> keybd_event / Ctrl+V 会发到上面那个窗口，不是微信！")
    print("      （这既是发不出去的原因，也是会往用户正在编辑的文档里打字的风险点）")

val = ctypes.c_int(0)
_user32.SystemParametersInfoW(SPI_GETSCREENREADER, 0, ctypes.byref(val), 0)
print(f"系统 screen-reader 标志 = {val.value}（微信 4.x 靠它开 UIA）")