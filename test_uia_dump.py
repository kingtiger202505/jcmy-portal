# -*- coding: utf-8 -*-
# 微信 4.0 UIA 控件树诊断脚本
# 绕开 uiautomation 的 Name 搜索编码问题：先用 win32gui 找 hwnd，再 ControlFromHandle 绑定。
import ctypes
import sys
import io
import win32gui
import uiautomation as auto

SPI_SETSCREENREADER = 0x0047
SPI_GETSCREENREADER = 0x0046
SPIF_SENDCHANGE = 0x0002

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def find_wechat_hwnd():
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            c = win32gui.GetClassName(hwnd)
            if 'Qt' in c and 'QWindowIcon' in c and win32gui.GetWindowText(hwnd) == '微信':
                result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None


def is_weixin_running():
    import subprocess
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/NH"],
                         capture_output=True, text=True, encoding="gbk", errors="replace")
    return "Weixin.exe" in out.stdout


user32 = ctypes.windll.user32
if is_weixin_running():
    val = ctypes.c_int(0)
    user32.SystemParametersInfoW(SPI_GETSCREENREADER, 0, ctypes.byref(val), 0)
    print(f"[!] 微信已在运行（系统 screen-reader 标志={val.value}）"
          f"\n    若扫描结果很空，重启微信后再跑。")
else:
    ok = user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1, None, SPIF_SENDCHANGE)
    print(f"[+] 已设置 screen-reader 标志（返回值={ok}），请启动微信后再扫描。")

hwnd = find_wechat_hwnd()
if not hwnd:
    print("[X] 没找到微信窗口！请确保微信没有最小化到托盘。")
    sys.exit(1)

wx_win = auto.ControlFromHandle(hwnd)
print(f"[OK] 绑定微信 hwnd={hwnd} | UIA类名: {wx_win.ClassName} | PID: {wx_win.ProcessId}")
try:
    wx_win.SetActive()
except Exception as e:
    print(f"[!] SetActive 失败（不影响扫描）: {e}")


def dump_tree(ctrl, depth=0, max_depth=6, out=None):
    if depth > max_depth:
        return
    indent = "  " * depth
    if ctrl.Name or ctrl.ControlTypeName in ('ListControl', 'EditControl', 'TextControl',
                                             'ListItemControl', 'TreeControl'):
        line = (f"{indent}|- [{ctrl.ControlTypeName}] Name: '{ctrl.Name}' "
                f"| Class: '{ctrl.ClassName}'")
        print(line)
        if out is not None:
            out.append(line)
    try:
        children = ctrl.GetChildren()
    except Exception:
        return
    for child in children:
        try:
            dump_tree(child, depth + 1, max_depth, out)
        except Exception:
            continue


print(f"\n[扫描] 微信控件结构（前 6 层）：")
collected = []
dump_tree(wx_win, out=collected)

print(f"\n共打印 {len(collected)} 个关键控件。")
if len(collected) < 5:
    print("[!] 控件树很空 —— 大概率是微信启动时 screen-reader 标志没开。")
    print("    处理：彻底退出微信，重跑本脚本（它会先开标志），再启动微信。")
