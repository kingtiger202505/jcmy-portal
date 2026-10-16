# -*- coding: utf-8 -*-
"""注入可达性测试：判断"点了没反应"是坐标错，还是被 UIPI（权限隔离）挡住。

做法：OCR 侧栏随便挑一个**别的**聊天，用正确的控件内偏移点它，看标题区变不变。
  - 标题变了 → 鼠标注入能进微信，问题在别处（坐标/焦点）
  - 标题没变 → 注入被 UIPI 挡了：微信是**提权(elevated)**运行的，非提权的 python
               发的鼠标/键盘事件进不去。这就是"发消息总是不行"的根因。

全程不输入文字、不按回车，只点聊天切换（最多把某个群标记成已读）。
"""
import ctypes
import ctypes.wintypes as wt
import csv
import io
import subprocess
import sys
import time

import numpy as np
import uiautomation as auto
import win32api
import win32gui
from rapidocr_onnxruntime import RapidOCR

from wechat_bot import capture_window, find_wechat_hwnd

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

k32 = ctypes.windll.kernel32
adv = ctypes.windll.advapi32
adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]


def integrity_of(pid):
    """读进程令牌的完整性级别（S-1-16-8192=Medium 普通，S-1-16-12288=High 提权）"""
    h = k32.OpenProcess(0x0400, False, pid)          # PROCESS_QUERY_INFORMATION
    if not h:
        acc = k32.OpenProcess(0x1000, False, pid)    # 退一步 QUERY_LIMITED
        if not acc:
            return f"OpenProcess 全部失败 err={ctypes.GetLastError()}"
        tok = wt.HANDLE()
        ok = adv.OpenProcessToken(acc, 0x0008, ctypes.byref(tok))
        k32.CloseHandle(acc)
        return f"QUERY_INFORMATION 被拒(err=5) / LIMITED 的令牌也读不到(ok={ok}) → 大概率是提权进程"
    tok = wt.HANDLE()
    if not adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
        e = ctypes.GetLastError(); k32.CloseHandle(h)
        return f"OpenProcessToken err={e}"
    sz = wt.DWORD(0)
    adv.GetTokenInformation(tok, 25, None, 0, ctypes.byref(sz))
    buf = ctypes.create_string_buffer(sz.value)
    ok = adv.GetTokenInformation(tok, 25, buf, sz.value, ctypes.byref(sz))
    sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
    out = ctypes.c_wchar_p()
    s = out.value if adv.ConvertSidToStringSidW(sid, ctypes.byref(out)) else "?"
    k32.CloseHandle(tok); k32.CloseHandle(h)
    return s if ok else "?"


def ocr_lines(ocr, img, box):
    """OCR 某区域。图放大 2 倍跑 OCR（小字更准），返回的坐标要除回 2，
    得到的是**原图（也就是窗口）坐标系**里的位置。"""
    crop = img.crop(box)
    up = crop.resize((crop.width * 2, crop.height * 2), 1)
    res, _ = ocr(np.array(up))
    out = []
    for b, t, s in (res or []):
        cx = sum(p[0] for p in b) / 4 / 2
        cy = sum(p[1] for p in b) / 4 / 2
        out.append(((cx, cy), t.strip(), s))
    return sorted(out, key=lambda l: l[0][1])


def title(ocr, img, W, H):
    return " ".join(t for _, t, _ in ocr_lines(ocr, img, (int(W * 0.25), 0, W, int(H * 0.12))))


def main():
    hwnd = find_wechat_hwnd()
    if not hwnd:
        print("[X] 没找到微信窗口"); return 1
    win = auto.ControlFromHandle(hwnd)

    print("--- 1. 权限/完整性级别 ---")
    print(f"  当前 python pid={k32.GetCurrentProcessId()} 完整性={integrity_of(k32.GetCurrentProcessId())} "
          f"IsUserAnAdmin={bool(ctypes.windll.shell32.IsUserAnAdmin())}")
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, errors="replace")
    for row in csv.reader(io.StringIO(r.stdout)):
        if len(row) >= 2 and row[1].isdigit():
            print(f"  Weixin.exe pid={row[1]} 完整性={integrity_of(int(row[1]))}")

    print("\n--- 2. 窗口/前台 ---")
    if win32gui.IsIconic(hwnd):
        capture_window(hwnd); time.sleep(0.8)
    rect = win32gui.GetWindowRect(hwnd)
    left, top, right, bottom = rect
    W, H = right - left, bottom - top
    print(f"  rect={rect} 尺寸={W}x{H} 前台={win32gui.GetForegroundWindow()} (微信={hwnd})")
    win.SetActive(); time.sleep(0.4)
    fg_ok = win32gui.GetForegroundWindow() == hwnd
    print(f"  SetActive 后前台={'微信' if fg_ok else win32gui.GetForegroundWindow()}")

    ocr = RapidOCR()
    img = capture_window(hwnd)
    cur_title = title(ocr, img, W, H)
    print(f"  标题区（点击前）: {cur_title!r}")
    lines = ocr_lines(ocr, img, (0, int(H * 0.08), int(W * 0.25), H))
    print("  侧栏行（坐标=窗口内偏移）:")
    for i, ((cx, cy), t, s) in enumerate(lines):
        print(f"    [{i}] ({int(cx)},{int(cy + H * 0.08)}) ({s:.2f}) {t!r}")

    # 挑一个"别的聊天"：名字够长、不是纯数字/时间、而且不在当前标题里
    cand = [((cx, cy), t) for (cx, cy), t, s in lines
            if len(t) > 3 and not t.isdigit() and t[:3] not in cur_title
            and "搜索" not in t]
    if not cand:
        print("  [X] 侧栏没有可点的别的聊天"); return 1
    (cx, cy), name = cand[len(cand) // 2]
    off_x, off_y = int(cx), int(cy + H * 0.08)
    screen_pt = (left + off_x, top + off_y)
    print(f"\n--- 3. 点击侧栏 {name!r}（窗口内偏移 {off_x},{off_y} → 屏幕 {screen_pt}）---")

    sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    if not (0 <= screen_pt[0] < sw and 0 <= screen_pt[1] < sh):
        print(f"  [X] 目标点在屏幕外（屏幕 {sw}x{sh}），跳过"); return 1
    win32api.SetCursorPos(screen_pt); time.sleep(0.1)
    print(f"  点击前光标位置={win32api.GetCursorPos()}（预期 {screen_pt}）")
    win32api.SetCursorPos(screen_pt)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(1.2)

    img2 = capture_window(hwnd)
    t2 = title(ocr, img2, W, H)
    print(f"  标题区（点击后）: {t2!r}")
    print(f"  点击后光标位置={win32api.GetCursorPos()}")
    changed = (t2 and name[:3] in t2)
    print(f"\n  => {'[PASS] 点击生效，鼠标注入能进微信' if changed else '[FAIL] 点了没反应：鼠标注入进不了微信'}")

    if not changed:
        print("     结论：非提权进程的注入被 UIPI 挡了（微信多半是提权运行的）。")
        print("     验证/解决办法：用管理员权限跑这个脚本，或让微信以普通权限启动（别用 elevate_restart_wechat.py 提权重启）。")
        # 再补一个只读的旁证：能不能给微信发个允许的窗口消息
        WM_NULL = 0x0000
        ok = ctypes.windll.user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, 0x0002, 500,
                                                      ctypes.byref(wt.DWORD(0)))
        print(f"     SendMessageTimeout(WM_NULL) 返回={ok}（0=被 UIPI 拒绝或超时）")
    return 0


if __name__ == "__main__":
    sys.exit(main())