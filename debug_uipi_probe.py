# -*- coding: utf-8 -*-
"""UIPI 探针：确认"鼠标/键盘注入进不了微信"是不是因为微信是提权进程。

三个只读/无副作用的测试（只会临时移动一下光标，之后还原）：
  A. 微信是前台时 SetCursorPos → 报 access denied?
  B. 别的同权限窗口是前台时 SetCursorPos → 正常?
  C. SendMessageTimeout(hwnd, WM_NULL) → 0 表示被 UIPI 拦（这一段是判断目标窗口
     是否提权的经典方法）
"""
import ctypes
import ctypes.wintypes as wt
import csv
import io
import sys
import time

import win32api
import win32gui

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

user32 = ctypes.windll.user32
WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002


def find_wechat_hwnd():
    out = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            t, c = win32gui.GetWindowText(h), win32gui.GetClassName(h)
            if (t == "微信" and "Qt" in c) or c == "WeChatMainWndForPC":
                out.append(h)
    win32gui.EnumWindows(cb, None)
    return out[0] if out else None


def as_hex(v):
    return hex(v & 0xFFFFFFFF)


hwnd = find_wechat_hwnd()
if not hwnd:
    print("[X] 没找到微信窗口"); sys.exit(1)

saved_cursor = win32api.GetCursorPos()
print(f"微信 hwnd={hwnd}  当前前台={win32gui.GetForegroundWindow()}")
print(f"屏幕={user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}  保存光标位置={saved_cursor}")

# ---------- C. WM_NULL 探针（经典提权检测）----------
res = wt.DWORD(0)
ret = user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, 1000, ctypes.byref(res))
print(f"\n[C] SendMessageTimeout(WM_NULL) 返回={ret} (拿到的结果={as_hex(res.value)})")
print("    ret==0 → 消息被 UIPI 拦下：目标窗口在更高的完整性级别（提权）")

# ---------- A. 微信前台时 SetCursorPos ----------
print(f"\n[A] 把微信切到前台，再试 SetCursorPos")
user32.SetForegroundWindow(hwnd)
time.sleep(0.5)
fg = win32gui.GetForegroundWindow()
print(f"    前台现在={fg} ({win32gui.GetWindowText(fg)!r}) 是微信={fg == hwnd}")
try:
    ok = win32api.SetCursorPos((saved_cursor[0] + 3, saved_cursor[1] + 3))
    print(f"    SetCursorPos 成功={ok} → 现在光标={win32api.GetCursorPos()}")
    a_ok = True
except Exception as e:
    print(f"    SetCursorPos 失败: {e}")
    a_ok = False

try:
    n = user32.SendInput(0, None, 0)
    print(f"    SendInput(0) 返回={n}  err={ctypes.GetLastError()}")
except Exception as e:
    print(f"    SendInput 异常: {e}")

# ---------- B. 换成别的前台窗口再试（对照组）----------
print(f"\n[B] 对照组：让别的窗口当后台/前台，再试 SetCursorPos")
desktop = win32gui.GetDesktopWindow()
try:
    # 把光标移到桌面区域（不激活任何窗口），此时前台还是微信也没关系，
    # 关键看 SetCursorPos 本身在"微信不是前台"时是否正常
    user32.SetForegroundWindow(win32gui.FindWindow(None, "Claude"))
    time.sleep(0.4)
    fg2 = win32gui.GetForegroundWindow()
    print(f"    前台现在={fg2} ({win32gui.GetWindowText(fg2)!r})")
    ok = win32api.SetCursorPos((saved_cursor[0] + 5, saved_cursor[1] + 5))
    print(f"    SetCursorPos 成功={ok} → 光标={win32api.GetCursorPos()}")
    b_ok = True
except Exception as e:
    print(f"    SetCursorPos 失败: {e}")
    b_ok = False

# ---------- 进程完整性级别 ----------
adv = ctypes.windll.advapi32
adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]


def il(pid):
    h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
    lvl = "QUERY_INFORMATION 被拒"
    if h:
        tok = wt.HANDLE()
        if adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
            sz = wt.DWORD(0)
            adv.GetTokenInformation(tok, 25, None, 0, ctypes.byref(sz))
            buf = ctypes.create_string_buffer(sz.value)
            if adv.GetTokenInformation(tok, 25, buf, sz.value, ctypes.byref(sz)):
                sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
                o = ctypes.c_wchar_p()
                if adv.ConvertSidToStringSidW(sid, ctypes.byref(o)):
                    lvl = o.value
            ctypes.windll.kernel32.CloseHandle(tok)
        ctypes.windll.kernel32.CloseHandle(h)
    names = {"S-1-16-4096": "Low", "S-1-16-8192": "Medium(普通用户)",
             "S-1-16-12288": "High(提权/管理员)", "S-1-16-16384": "System"}
    return f"{lvl} {names.get(lvl, '')}"


print(f"\n[D] 完整性级别")
print(f"    本进程 pid={ctypes.windll.kernel32.GetCurrentProcessId()} → {il(ctypes.windll.kernel32.GetCurrentProcessId())}")
r = __import__("subprocess").run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, errors="replace")
for row in csv.reader(io.StringIO(r.stdout)):
    if len(row) >= 2 and row[1].isdigit():
        print(f"    Weixin.exe pid={row[1]} → {il(int(row[1]))}")

win32api.SetCursorPos(saved_cursor)
print(f"\n光标已还原到 {saved_cursor}")
print(f"\n结论：微信前台时 SetCursorPos {'被拒' if not a_ok else '正常'} / 其它窗口前台时 "
      f"{'被拒' if not b_ok else '正常'}" + (" → UIPI 拦的是'往高权限窗口注入'" if (not a_ok and b_ok) else ""))