"""微信群消息监控（PrintWindow 后台截图 + RapidOCR）

特性：
- PrintWindow 后台截图，窗口被遮挡/最小化也能截，全程不落盘
- 消息归属判断：右侧气泡为自己，左侧为别人，并提取发送者昵称
- 支持回复/引用：send_text 发送文本，reply_to 预留（需 GUI 点击引用）
"""
import ctypes
import re
import time
import pyperclip
import uiautomation as auto
import win32gui
import win32ui
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
import numpy as np

PW_RENDERFULLCONTENT = 2
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


def capture_window(hwnd):
    """PrintWindow 后台截图，返回 PIL Image（内存，不落盘）。

    完全最小化的窗口 PrintWindow 无效，先还原（不激活，不打扰前台）。
    """
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] == 2:   # 最小化
        win32gui.ShowWindow(hwnd, 9)   # SW_RESTORE，不抢焦点
        time.sleep(0.3)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return None
    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfcDC, w, h)
    saveDC.SelectObject(bitmap)
    ret = _user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), PW_RENDERFULLCONTENT)
    if ret != 1:
        win32gui.DeleteObject(bitmap.GetHandle())
        saveDC.DeleteDC(); mfcDC.DeleteDC(); win32gui.ReleaseDC(hwnd, hwndDC)
        return None
    bmpinfo = bitmap.GetInfo()
    bmpstr = bitmap.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpstr, "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bitmap.GetHandle())
    saveDC.DeleteDC(); mfcDC.DeleteDC(); win32gui.ReleaseDC(hwnd, hwndDC)
    return img


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


class WeChatMonitor:
    def __init__(self, target_name, interval=3):
        self.target_name = target_name
        self.interval = interval
        self.ocr = RapidOCR()
        self.hwnd = find_wechat_hwnd()
        self._win = None
        self._last_lines = []
        self.my_name = "海妖"   # 自己在群里的昵称，用于区分（可从群信息获取）
        if self.hwnd:
            try:
                self._win = auto.ControlFromHandle(self.hwnd)
            except Exception:
                pass

    def open_chat(self):
        if not self._win:
            raise RuntimeError("未找到微信窗口")
        print(f"打开聊天: {self.target_name}")
        self._win.SetActive()
        time.sleep(0.5)
        self._win.SendKeys("{Ctrl}f")
        time.sleep(1.0)
        pyperclip.copy(self.target_name)
        self._win.SendKeys("{Ctrl}v")
        time.sleep(2.0)
        self._win.SendKeys("{Enter}")
        time.sleep(1.5)

    def _with_temp_foreground(self, fn):
        """短暂激活微信执行 fn，完成后还原之前的前台窗口。

        发消息必须用键盘模拟，而键盘输入需要焦点。
        整个抢前台过程约 0.8 秒，发完立即还回。
        """
        prev_foreground = win32gui.GetForegroundWindow()
        self._win.SetActive()
        time.sleep(0.2)
        try:
            fn()
        finally:
            # 还原前台窗口（用户可能在别的窗口打字）
            if prev_foreground and prev_foreground != self.hwnd:
                try:
                    win32gui.SetForegroundWindow(prev_foreground)
                except Exception:
                    pass

    def send_text(self, message):
        """向当前聊天发送文本，发完立即还原前台窗口"""
        def _do():
            pyperclip.copy(message)
            time.sleep(0.1)
            self._win.SendKeys("{Ctrl}v")
            time.sleep(0.3)
            self._win.SendKeys("{Enter}")
            time.sleep(0.3)
        self._with_temp_foreground(_do)

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

    def poll_once(self):
        """轮询一次，返回新增的 Message 列表。

        帧对齐改用"尾行模糊匹配"：滚动/换行可能导致文本微调，
        只要上帧尾行仍出现在当前帧（任一位置），其后即新消息；否则视为滚动，
        把上帧已有的文本行过滤掉，剩下的才是新消息（避免整帧误判）。
        """
        msgs = self._read_chat()
        lines = [m.text for m in msgs]
        if not self._last_lines:
            self._last_lines = lines
            return msgs

        last_set = set(self._last_lines)
        last_tail = self._last_lines[-1]
        if last_tail in lines:
            # 尾行还在：其后为新消息
            idx = len(lines) - 1 - lines[::-1].index(last_tail)
            new_msgs = msgs[idx + 1:]
        else:
            # 尾行被滚走：过滤掉上帧已见过的行，剩下的为新
            new_msgs = [m for m in msgs if m.text not in last_set]
        self._last_lines = lines
        return new_msgs

    def reset_baseline(self):
        """发送消息后重建基线，避免自己发的消息被当成新消息"""
        self._last_lines = [m.text for m in self._read_chat()]

    def run(self, on_message, duration=None):
        self.open_chat()
        print(f"开始监控 [{self.target_name}]，每 {self.interval}s 轮询...")
        start = time.time()
        while True:
            try:
                new_msgs = self.poll_once()
                if new_msgs:
                    on_message(new_msgs)
            except Exception as e:
                print(f"轮询异常: {e}")
            time.sleep(self.interval)
            if duration and time.time() - start > duration:
                break


def test_monitor():
    mon = WeChatMonitor("视频号，剪映，微企", interval=4)
    def on_msg(msgs):
        print("=== 新消息 ===")
        for msg in msgs:
            print(f"  {msg}")
    mon.run(on_msg, duration=60)


if __name__ == "__main__":
    print("请在 3 秒内切好屏幕...")
    time.sleep(3)
    test_monitor()
