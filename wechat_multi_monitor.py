"""微信群消息多群轮询监控（PrintWindow 后台截图 + RapidOCR）

支持同时监控多个群，轮流切换聊天窗口，每个群独立维护消息基线。
- PrintWindow 后台截图，窗口被遮挡/最小化也能截，全程不落盘
- 消息归属判断：右侧气泡为自己，左侧为别人，并提取发送者昵称
- 发送消息时短暂抢前台，发完立即还原
"""
import ctypes
import difflib
import re
import time
import pyperclip
import uiautomation as auto
import win32con
import win32gui
import win32ui
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
import numpy as np

# 按键注入 / 抢前台 / 输入框高度这些和 wechat_bot 共用同一份实现，
# 免得两边各写一套、修了一边漏一边。
# capture_window 也换成 wechat_bot 的 BitBlt 版：微信 4.x(Qt) 的 PrintWindow
# 实测返回 0 + 全黑图，OCR 什么都读不到（侧栏/标题/输入框全瞎），切群自然全失败。
from wechat_bot import (send_key, force_foreground, INPUT_CLICK_Y_RATIOS,
                        capture_window, looks_sent, input_box_has, click_at,
                        read_chat_title, title_matches)

_user32 = ctypes.windll.user32


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


# 注意：原来这里有个本地 capture_window（PrintWindow, PW_RENDERFULLCONTENT）。
# 对微信 4.x(Qt51514 窗口) 实测 PrintWindow 返回 0、图是全黑（平均亮度 0.0），
# OCR 读出来是空的：侧栏认不出群名、标题认不出当前群、输入框验证全瞎 ——
# 这个脚本切群和发消息全线失败，看起来"像"是坐标/焦点问题，实际是截图方式不对。
# 已删除本地实现，统一用文件头 import 进来的 wechat_bot.capture_window（BitBlt）。


class Message:
    """一条解析后的消息"""
    def __init__(self, sender, text, is_self, cx, cy):
        self.sender = sender      # 发送者昵称（自己的消息为"我"）
        self.text = text
        self.is_self = is_self    # 是否自己发的
        self.cx = cx              # 气泡中心 x（聊天区内坐标）
        self.cy = cy              # 气泡中心 y

    def __repr__(self):
        return f"[{'我' if self.is_self else self.sender}] {self.text}"


class ChatMonitor:
    """单个聊天的监控状态"""
    def __init__(self, name):
        self.name = name              # 群名/联系人名
        self._last_lines = []         # 上一帧的消息行，用于增量检测


class WeChatMultiMonitor:
    def __init__(self, targets, interval=3, switch_delay=1.0):
        """
        targets: list[str] 要监控的群名列表
        interval: 每个群的轮询间隔（秒）
        switch_delay: 切换聊天后等待加载的时间（秒）
        """
        self.targets = [ChatMonitor(name) for name in targets]
        self.interval = interval
        self.switch_delay = switch_delay
        self.ocr = RapidOCR()
        self.hwnd = find_wechat_hwnd()
        self._win = None
        if self.hwnd:
            try:
                self._win = auto.ControlFromHandle(self.hwnd)
            except Exception:
                pass

    def _switch_chat(self, target_name):
        """Ctrl+F 搜索切换到指定聊天（群被滚出侧栏可见区也能切到）。返回是否切了。

        按键只送给前台窗口：先确保微信在前台，抢不到就返回 False —— 绝不硬发，
        否则 Ctrl+F/群名/回车会打进用户正在用的窗口里。
        """
        if win32gui.GetForegroundWindow() != self.hwnd:
            self._win.SetActive()
            time.sleep(0.3)
            if win32gui.GetForegroundWindow() != self.hwnd and not force_foreground(self.hwnd):
                print(f"    [X] 抢不到微信前台，跳过切换 [{target_name}]")
                return False
        send_key(ord('F'), ctrl=True)   # Ctrl+F 打开搜索
        time.sleep(0.8)
        pyperclip.copy(target_name)
        time.sleep(0.2)
        send_key(ord('V'), ctrl=True)   # 粘贴群名，避开输入法
        time.sleep(self.switch_delay)
        send_key(win32con.VK_RETURN)
        time.sleep(self.switch_delay)
        return True

    def _read_chat(self):
        """后台截图 + OCR，返回解析后的 Message 列表"""
        img = capture_window(self.hwnd)
        if img is None:
            return []
        W, H = img.size
        # 聊天消息区：左边界取在会话列表右侧（约 0.25W），避开顶部标题和底部输入框
        # 不能切太靠右，否则左侧别人消息的气泡/昵称会被裁掉
        chat = img.crop((int(W * 0.25), int(H * 0.08), W, int(H * 0.88)))
        cw, ch = chat.size
        result, _ = self.ocr(np.array(chat))
        if not result:
            return []

        # 按 y 排序（时间顺序）
        items = sorted(result, key=lambda l: sum(p[1] for p in l[0]) / 4)

        # 自己/别人分界：自己气泡 cx 大（右侧），别人 cx 小（左侧）
        self_cx_threshold = cw * 0.6

        # 左侧行的最小 cx：昵称顶着头像右缘，是整屏最靠左的文本
        left_cxs = [sum(p[0] for p in box) / 4 for box, t, _ in items
                    if sum(p[0] for p in box) / 4 <= self_cx_threshold and not re.fullmatch(r'\d{1,2}:\d{2}', t.strip())]
        nickname_cx = min(left_cxs) if left_cxs else 0

        messages = []
        cur_sender = None
        for box, text, score in items:
            cx = sum(p[0] for p in box) / 4
            cy = sum(p[1] for p in box) / 4
            is_self = cx > self_cx_threshold

            # 时间戳行：HH:MM 格式，居中，长度短
            if re.fullmatch(r'\d{1,2}:\d{2}', text.strip()):
                continue

            if not is_self:
                # 昵称行：cx 接近最小值（顶着头像）；正文 cx 更大
                if abs(cx - nickname_cx) <= 20:
                    cur_sender = text
                    continue
                messages.append(Message(
                    sender=cur_sender or "群成员",
                    text=text, is_self=False, cx=cx, cy=cy,
                ))
            else:
                messages.append(Message(
                    sender="我", text=text, is_self=True, cx=cx, cy=cy,
                ))
        return messages

    def _poll_chat(self, chat: ChatMonitor):
        """轮询一个聊天，返回新增消息列表"""
        msgs = self._read_chat()
        lines = [m.text for m in msgs]
        if not chat._last_lines:
            chat._last_lines = lines
            return msgs

        last_set = set(chat._last_lines)
        last_tail = chat._last_lines[-1]
        if last_tail in lines:
            idx = len(lines) - 1 - lines[::-1].index(last_tail)
            new_msgs = msgs[idx + 1:]
        else:
            new_msgs = [m for m in msgs if m.text not in last_set]
        chat._last_lines = lines
        return new_msgs

    def _input_box_has(self, marker):
        """实现见 wechat_bot.input_box_has（三个版本共用一份）"""
        return input_box_has(self.ocr, self.hwnd, marker)

    def _current_chat_title(self):
        """实现见 wechat_bot.read_chat_title"""
        return read_chat_title(self.ocr, self.hwnd)

    _title_matches = staticmethod(title_matches)

    def send_text(self, target_name, message):
        """向指定聊天发送文本，返回 True 表示确认发出去了。

        老实现的坑（2026-10 定位并修掉）：
        1. self._win.SendKeys() 之前只 SetActive 不校验返回值，而 SetActive 被
           Windows 前台锁挡住是常事 —— 那时 {Ctrl}v{Enter} 就打到**用户正在编辑的
           窗口**里去了（既发不出去，还会污染别人的文档）。
        2. 粘贴没验证就回车：粘贴失败时回车可能把别处的内容发出去。
        3. Ctrl+F 搜群可能搜不到/选错，切完先 OCR 标题确认，不是目标群就中止。
        """
        prev_foreground = win32gui.GetForegroundWindow()
        marker = message.strip()   # OCR 验证用的文本（整条消息，模糊匹配用）

        try:
            # 1) Ctrl+F 搜索切群，必须有前台才发得出去
            self._win.SetActive()
            time.sleep(0.3)
            if win32gui.GetForegroundWindow() != self.hwnd and not force_foreground(self.hwnd):
                print("    [X] 抢不到微信前台窗口，放弃发送（避免按键打到别的窗口）")
                return False
            self._switch_chat(target_name)

            # 2) 确认切对了群，否则消息可能落到别的群
            title = self._current_chat_title()
            if not self._title_matches(title, target_name):
                print(f"    [X] 当前聊天不是 [{target_name}]（标题区 OCR={title!r}），中止发送")
                return False

            pyperclip.copy(message)
            time.sleep(0.2)

            # 3) 点输入框 → 粘贴 → OCR 验证（没进去就不按回车）
            pasted = False
            y_paste = None
            for ratio_y in INPUT_CLICK_Y_RATIOS:
                if win32gui.GetForegroundWindow() != self.hwnd:
                    print("    [!] 前台已经不是微信了，中止发送")
                    return False
                # 点输入框：用比例（uiautomation 的 Click 是控件内偏移，不是屏幕坐标）
                # 点输入框：比例换算成窗口内偏移（不用屏幕坐标）
                rect = win32gui.GetWindowRect(self.hwnd)
                click_at(self.hwnd, (rect[2] - rect[0]) * 0.5, (rect[3] - rect[1]) * ratio_y)
                time.sleep(0.3)
                send_key(ord('V'), ctrl=True)
                time.sleep(0.6)
                hit, texts, y_hit = self._input_box_has(marker)
                print(f"    点 ratioY={ratio_y} 后输入框 OCR: {texts}")
                if hit:
                    pasted, y_paste = True, y_hit
                    break

            if not pasted:
                print("    [X] 文字没进输入框，不按回车（否则可能把别处的内容发出去）")
                return False

            # 4) 回车发送，再确认到底发出去了没
            send_key(win32con.VK_RETURN)
            time.sleep(0.8)
            hit2, texts2, y2 = self._input_box_has(marker)
            if looks_sent(hit2, texts2, y2, y_paste):
                print(f"    [OK] 已发送: {message}")
                return True
            print(f"    [X] 回车后文字还留在输入框里，没发出去: {texts2}")
            return False
        finally:
            # 发完立即还原前台，别把用户的窗口顶掉
            if prev_foreground and prev_foreground != self.hwnd:
                try:
                    win32gui.SetForegroundWindow(prev_foreground)
                except Exception:
                    pass

    def reset_baseline(self, target_name):
        """重置某个群的基线（比如发送消息后）"""
        for chat in self.targets:
            if chat.name == target_name:
                chat._last_lines = [m.text for m in self._read_chat()]
                break

    def run(self, on_message, duration=None):
        """
        on_message: callback(chat_name: str, new_msgs: list[Message])
        duration: 总运行时长（秒），None 表示无限
        """
        if not self._win:
            raise RuntimeError("未找到微信窗口")

        # 初始化：先切到每个群建立基线
        print("初始化，建立各群基线...")
        for chat in self.targets:
            print(f"  初始化 [{chat.name}]")
            if self._switch_chat(chat.name):
                chat._last_lines = [m.text for m in self._read_chat()]
            else:
                print(f"  [!] 切不到 [{chat.name}]，这一轮先跳过")

        print(f"开始监控 {len(self.targets)} 个群，每 {self.interval}s 轮询...")
        start = time.time()
        while True:
            for chat in self.targets:
                try:
                    if not self._switch_chat(chat.name):
                        continue
                    new_msgs = self._poll_chat(chat)
                    if new_msgs:
                        on_message(chat.name, new_msgs)
                except Exception as e:
                    print(f"[{chat.name}] 轮询异常: {e}")
                time.sleep(self.interval)

            if duration and time.time() - start > duration:
                break


def test_multi():
    groups = ["视频号，剪映，微企", "ai测试2群"]
    mon = WeChatMultiMonitor(groups, interval=3)

    def on_msg(chat_name, msgs):
        print(f"=== [{chat_name}] 新消息 ===")
        for msg in msgs:
            print(f"  {msg}")

    mon.run(on_msg, duration=120)


if __name__ == "__main__":
    print("3 秒后开始...")
    time.sleep(3)
    test_multi()
