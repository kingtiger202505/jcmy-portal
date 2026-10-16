# -*- coding: utf-8 -*-
"""用**普通权限**重启微信（替代 elevate_restart_wechat.py 的提权重启）。

为什么必须普通权限：微信提权运行后，普通权限的 python 机器人往里注入鼠标/键盘会被
Windows UIPI 拦掉（SetCursorPos 直接 error 5），点击、Ctrl+V、回车全部失效 ——
表现就是"群里发消息总是不行"。机器人必须和微信处在**同一个完整性级别**。

提权本来是为了开 UIA 无障碍，但实测微信 4.x(Qt) 根本不暴露 Edit 控件，
这条提权没有意义，所以退回普通权限启动。

用法：
  python restart_wechat_normal.py          # 杀进程 + 普通权限启动 + 校验完整性级别
  python restart_wechat_normal.py --check  # 只看当前进程的完整性级别，不重启
"""
import csv
import ctypes
import ctypes.wintypes as wt
import io
import os
import subprocess
import sys
import time

import win32gui

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

EXE_CANDIDATES = [
    r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
    r"D:\Program Files\Tencent\Weixin\Weixin.exe",
]
KILL_LIST = ["Weixin.exe", "WeChatAppEx.exe", "WeChatPlayer.exe"]

_k32 = ctypes.windll.kernel32
_adv = ctypes.windll.advapi32
_adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
_NAMES = {"S-1-16-4096": "Low", "S-1-16-8192": "Medium(普通)",
          "S-1-16-12288": "High(提权/管理员)", "S-1-16-16384": "System"}


def integrity(pid):
    """读进程令牌完整性级别。提权进程会拒绝，返回的字符串里会体现出来。"""
    h = _k32.OpenProcess(0x0400, False, pid)          # PROCESS_QUERY_INFORMATION
    if not h:
        return "拒绝访问(QUERY_INFORMATION) → 说明它在更高的权限级别(提权)"
    tok = wt.HANDLE()
    if not _adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
        _k32.CloseHandle(h)
        return f"OpenProcessToken 失败 err={ctypes.GetLastError()}"
    sz = wt.DWORD(0)
    _adv.GetTokenInformation(tok, 25, None, 0, ctypes.byref(sz))
    buf = ctypes.create_string_buffer(sz.value)
    ok = _adv.GetTokenInformation(tok, 25, buf, sz.value, ctypes.byref(sz))
    sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
    out = ctypes.c_wchar_p()
    s = out.value if _adv.ConvertSidToStringSidW(sid, ctypes.byref(out)) else "?"
    _k32.CloseHandle(tok); _k32.CloseHandle(h)
    return f"{s} {_NAMES.get(s, '')}" if ok else "?"


def pids_of(name):
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, errors="replace")
    return [int(row[1]) for row in csv.reader(io.StringIO(r.stdout))
            if len(row) >= 2 and row[1].isdigit()]


def wechat_hwnd():
    out = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            t, c = win32gui.GetWindowText(h), win32gui.GetClassName(h)
            if (t == "微信" and "Qt" in c) or c == "WeChatMainWndForPC":
                out.append(h)
    win32gui.EnumWindows(cb, None)
    return out[0] if out else None


def report(tag):
    self_pid = _k32.GetCurrentProcessId()
    print(f"{tag} 本进程 pid={self_pid} → {integrity(self_pid)} "
          f"IsUserAnAdmin={bool(ctypes.windll.shell32.IsUserAnAdmin())}")
    for name in ("Weixin.exe",):
        for p in pids_of(name):
            print(f"{tag} {name} pid={p} → {integrity(p)}")


def elevated_kill():
    """普通权限杀不掉提权微信（taskkill 拒绝访问），退回提权 kill：会弹一次 UAC。

    只用来"结束那个提权进程"，启动微信仍然是普通权限 —— 这才是重点。
    """
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_HIDE = 0

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong),
            ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_ulong), ("hIcon", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]

    params = " /c " + " & ".join(f"taskkill /F /IM {n}" for n in KILL_LIST)
    sei = SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFO)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "runas"
    sei.lpFile = "cmd.exe"
    sei.lpParameters = params
    sei.lpDirectory = None
    sei.nShow = SW_HIDE
    print("[!] 旧微信是提权进程，普通权限杀不掉 → 弹 UAC 提权 kill（请点“是”）")
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        print(f"[X] UAC 被拒绝或出错 (GetLastError={ctypes.GetLastError()})")
        return False
    ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, -1)
    ctypes.windll.kernel32.CloseHandle(sei.hProcess)
    return True


def find_exe():
    for p in EXE_CANDIDATES:
        if os.path.exists(p):
            return p
    r = subprocess.run(["where", "Weixin.exe"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().splitlines()[0]
    return None


def main():
    check_only = "--check" in sys.argv
    report("[重启前]")
    if check_only:
        return 0

    exe = find_exe()
    if not exe:
        print("[X] 没找到 Weixin.exe"); return 1
    print(f"[+] 微信路径: {exe}")

    for name in KILL_LIST:
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, text=True)
    for i in range(8):
        time.sleep(1)
        if not pids_of("Weixin.exe"):
            print(f"[+] 旧微信进程已退出（{i + 1}s）")
            break
    else:
        # 普通权限杀不掉提权进程，走 UAC 提权 kill（只这一次）
        elevated_kill()
        for i in range(20):
            time.sleep(1)
            if not pids_of("Weixin.exe"):
                print(f"[+] 旧微信进程已退出（提权后 {i + 1}s）")
                break
        else:
            print("[!] Weixin.exe 还在。请手动退出微信（托盘图标右键 → 退出微信）后重跑本脚本")
            return 1

    # 普通权限启动：os.startfile 走 ShellExecute，不带 runas，继承当前(普通)令牌
    print(f"[+] 普通权限启动（注意：不要用 runas / 管理员方式）")
    os.startfile(exe)
    time.sleep(3)

    hwnd = None
    for i in range(60):
        hwnd = wechat_hwnd()
        if hwnd:
            print(f"[+] 微信窗口已出现 hwnd={hwnd}（等了 {i * 2 + 3}s）")
            break
        time.sleep(2)
    if not hwnd:
        print("[!] 60 多秒没等到微信窗口：可能还在登录/手机确认，或需要手动点一下")
        return 1

    time.sleep(1)
    report("[重启后]")
    rect = win32gui.GetWindowRect(hwnd)
    print(f"[+] 窗口 rect={rect} 尺寸={rect[2] - rect[0]}x{rect[3] - rect[1]} 最小化={win32gui.IsIconic(hwnd)}")
    print("\n接下来验证：python debug_uipi_probe.py  （A/B 两项应该都是'正常'）")
    return 0


if __name__ == "__main__":
    sys.exit(main())