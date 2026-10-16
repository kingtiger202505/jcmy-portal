# -*- coding: utf-8 -*-
"""闭环自测：启动 wechat_multi_monitor 的监控 → 机器人自己发一条 → 监控发现 → 机器人自己回一条。

一条链路测三件事：
  1. 发送链路（send_text：切群/抢前台/粘贴/回车）能不能真把消息发出去
  2. 监控链路（_switch_chat + _read_chat + _poll_chat）能不能把新消息认出来
  3. 回复链路（发现新消息后自动发回复）

顺序执行、没有并发，避免"监控切群"和"发送粘贴"抢同一个窗口把群名粘进消息框里。
每轮切换/发送前都会确认微信是前台，抢不到就跳过，绝不会把按键打进你在用的窗口。

用法：
  python test_self_reply.py                       # 默认只测 ai测试2群（测试群，少刷真实群）
  python test_self_reply.py --groups "ai测试2群,视频号，剪映，微企"
"""
import argparse
import io
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from wechat_multi_monitor import WeChatMultiMonitor  # noqa: E402

TS = time.strftime("%H:%M:%S")
PROBE_TAG = "自测zb探针"
REPLY_TAG = "自测zb回复"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default="ai测试2群")
    ap.add_argument("--rounds", type=int, default=8, help="最多轮询几轮")
    args = ap.parse_args()
    groups = [g.strip() for g in args.groups.split(",") if g.strip()]

    print(f"[i] 监控群: {groups}  时间戳: {TS}")
    print("[i] 初始化 OCR（几秒钟）...")
    mon = WeChatMultiMonitor(groups, interval=3)
    if not mon.hwnd:
        print("[X] 没找到微信窗口"); return 1
    chat = next(c for c in mon.targets if c.name == groups[0])
    probe = f"{PROBE_TAG} {TS}"

    print(f"\n===== 1) 建立基线：切到 [{chat.name}] 读一次 =====")
    if not mon._switch_chat(chat.name):
        print("[X] 切不到目标群，中止"); return 1
    title = mon._current_chat_title()
    if not mon._title_matches(title, chat.name):
        print(f"[X] 当前聊天不是 [{chat.name}]（标题 OCR={title!r}），中止"); return 1
    lines = [m.text for m in mon._read_chat()]
    chat._last_lines = lines
    print(f"    基线 {len(lines)} 行，最后一行: {lines[-1] if lines else None!r}")

    print(f"\n===== 2) 机器人自己发一条: {probe!r} =====")
    if not mon.send_text(chat.name, probe):
        print("[X] 发送失败，中止"); return 1
    print("    [OK] 发送成功")

    print(f"\n===== 3) 轮询监控，看能不能发现刚才那条 =====")
    found = None
    for i in range(1, args.rounds + 1):
        time.sleep(2)
        if not mon._switch_chat(chat.name):
            print(f"    第{i}轮: 切不到群，跳过"); continue
        new_msgs = mon._poll_chat(chat)
        print(f"    第{i}轮: 新消息 {len(new_msgs)} 条 {[str(m) for m in new_msgs]}")
        hit = [m for m in new_msgs if PROBE_TAG in m.text]
        if hit:
            found = hit[0]
            print(f"    [OK] 监控认出来了: {found!r}  (is_self={found.is_self}, sender={found.sender!r})")
            break
    if not found:
        print(f"    [X] {args.rounds} 轮都没认出来，监控链路有问题（看上面每轮的输出）")
        return 1

    print(f"\n===== 4) 自动回一条: {REPLY_TAG} {TS} =====")
    reply = f"{REPLY_TAG} {TS} 收到你的 {found.text}"
    if not mon.send_text(chat.name, reply):
        print("[X] 回复发送失败"); return 1
    print("    [OK] 回复已发出")

    print("\n===== 结果 =====")
    print(f"  发送 ✓  监控 ✓  回复 ✓  群 [{chat.name}]")
    print(f"  群里应该能看到两条: {probe!r} 和 {reply!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())