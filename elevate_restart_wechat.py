# -*- coding: utf-8 -*-
"""重启微信并强制开启 Qt 无障碍（QT_ACCESSIBILITY=1 + SPI_SETSCREENREADER=1）

微信 4.0 (Qt 5.15) 只在启动时读无障碍开关，且 SPI_SETSCREENREADER 有时不够，
需要同时设置 QT_ACCESSIBILITY=1 环境变量。
"""
import ctypes
import subprocess
import sys
import time
import os
from pathlib import Path

SPI_SETSCREENREADER = 0x0047
SPIF_SENDCHANGE = 0x0002

WEIXIN_PATHS = [
    r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
    r"D:\Program Files\Tencent\Weixin\Weixin.exe",
]
KILL_LIST = ["Weixin.exe", "WeChatAppEx.exe", "WeChatPlayer.exe"]


def find_weixin():
    for p in WEIXIN_PATHS:
        if Path(p).exists():
            return p
    r = subprocess.run(["where", "Weixin.exe"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().splitlines()[0]
    return None


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def main():
    exe = find_weixin()
    if not exe:
        print("[X] 没找到 Weixin.exe")
        sys.exit(1)
    print(f"[+] 微信路径: {exe}")

    # 1) 当前进程先开 SPI_SETSCREENREADER
    ok = ctypes.windll.user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1, None, SPIF_SENDCHANGE)
    print(f"[+] SPI_SETSCREENREADER=1 已设置（返回值={ok}）")

    # 2) 准备环境变量：QT_ACCESSIBILITY=1
    env = os.environ.copy()
    env["QT_ACCESSIBILITY"] = "1"

    # 3) 生成批处理：杀进程 -> 等 2 秒 -> 带环境变量启动微信
    bat_lines = ["@echo off", "set QT_ACCESSIBILITY=1"]
    for name in KILL_LIST:
        bat_lines.append(f'taskkill /F /IM {name} >nul 2>&1')
    bat_lines.append('timeout /t 2 /nobreak >nul')
    bat_lines.append(f'start "" /b "{exe}"')
    bat = "\r\n".join(bat_lines)

    import tempfile
    bat_path = os.path.join(tempfile.gettempdir(), "restart_weixin_qt_a11y.bat")
    with open(bat_path, "w", encoding="gbk") as f:
        f.write(bat)

    if is_admin():
        print("[+] 当前已是管理员，直接执行重启脚本")
        subprocess.run(["cmd", "/c", bat_path], env=env, check=False)
    else:
        print("[+] 当前非管理员，弹出 UAC 授权框…… 请点“是”")
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

        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFO)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = "cmd.exe"
        sei.lpParameters = f'/c "{bat_path}"'
        sei.lpDirectory = None
        sei.nShow = SW_HIDE

        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.GetLastError()
            print(f"[X] UAC 被拒绝或出错 (GetLastError={err})")
            sys.exit(1)
        ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, -1)
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)

    print("[+] 重启命令已下发，等待微信进程起来……")
    for i in range(30):
        time.sleep(1)
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/NH"],
                           capture_output=True, text=True, encoding="gbk", errors="replace")
        if "Weixin.exe" in r.stdout:
            print(f"[+] Weixin.exe 已启动（等了 {i+1}s）")
            break
    else:
        print("[!] 30 秒内没等到 Weixin.exe，可能启动较慢")

    print("[+] 完成。请登录/解锁微信窗口，然后再跑：python test_uia_dump.py")


if __name__ == "__main__":
    main()
