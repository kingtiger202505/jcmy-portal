# -*- coding: utf-8 -*-
"""直接往两个群发测试消息，验证发送链路（wechat_bot 和 wechat_multi_monitor 两条路都测）。

每个群各发一条：一条走 wechat_bot.send_text，一条走 wechat_multi_monitor.send_text。
每条都做完整校验：切群后确认标题 → 抢前台（抢不到就放弃）→ 粘贴 → OCR 验证进输入框
→ 才按回车 → 再确认输入框已清空。所以打印 "成功" 就是真发出去了。

用法：
  python test_send_groups.py            # 两个群、两条路都测
  python test_send_groups.py --path bot # 只测 wechat_bot
  python test_send_groups.py --path multi
"""
import argparse
import io
import sys
import time

import win32gui

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

GROUPS = ["视频号，剪映，微企", "ai测试2群"]

from wechat_bot import WeChatMultiMonitor as BotMonitor, find_wechat_hwnd  # noqa: E402
from wechat_multi_monitor import WeChatMultiMonitor as PlainMonitor        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", choices=["all", "bot", "multi"], default="all")
    args = ap.parse_args()

    hwnd = find_wechat_hwnd()
    if not hwnd:
        print("[X] 没找到微信窗口")
        return 1
    rect = win32gui.GetWindowRect(hwnd)
    print(f"微信 hwnd={hwnd} rect={rect} 尺寸={rect[2]-rect[0]}x{rect[3]-rect[1]}  "
          f"前台={win32gui.GetForegroundWindow()}")

    ts = time.strftime("%H:%M:%S")
    results = []

    if args.path in ("all", "bot"):
        print(f"\n########## wechat_bot.send_text ##########")
        print("（初始化 OCR，几秒钟…）")
        bot = BotMonitor(GROUPS, interval=3)
        for g in GROUPS:
            msg = f"调试zb bot {ts}"
            print(f"\n--- [{g}] 发送 {msg!r} ---")
            ok = bot.send_text(g, msg)
            results.append(("wechat_bot", g, ok))
            print(f"  => {'成功' if ok else '失败'}")
            time.sleep(1.5)

    if args.path in ("all", "multi"):
        print(f"\n########## wechat_multi_monitor.send_text ##########")
        print("（初始化 OCR，几秒钟…）")
        plain = PlainMonitor(GROUPS, interval=3)
        for g in GROUPS:
            msg = f"调试zb multi {ts}"
            print(f"\n--- [{g}] 发送 {msg!r} ---")
            ok = plain.send_text(g, msg)
            results.append(("wechat_multi_monitor", g, ok))
            print(f"  => {'成功' if ok else '失败'}")
            time.sleep(1.5)

    print("\n========== 汇总 ==========")
    for path, g, ok in results:
        print(f"  {'[OK]  ' if ok else '[FAIL]'} {path:24s} → [{g}]")
    bad = [r for r in results if not r[2]]
    print(f"\n共 {len(results)} 条，失败 {len(bad)} 条")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())