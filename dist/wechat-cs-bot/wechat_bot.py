"""微信群消息多群轮询监控 + Gemini 自动回复

监控方式：OCR 左侧会话列表的消息预览（不切窗口、不抢前台），
预览变化时才打开该群读完整消息并回复。
- 自动 @发送者 回复（群聊场景）
- 支持每个群独立开关自动回复

注意：被监控的群要在会话列表里可见，不要收进"折叠的群聊"。
"""
import ctypes
import ctypes.wintypes as wt
import difflib
import re
import time
import pyperclip
import uiautomation as auto
import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
import numpy as np
import requests


# ===================== Gemini (9router) 配置 =====================
GEMINI_API_KEY = "sk-de4b8d36c3240731-8mtha9-e7063e73"
GEMINI_BASE_URL = "http://43.128.144.27:20128/v1"
GEMINI_MODEL = "combo1"

# 每个群的自动回复配置：True=自动回复，False=只监控不回复
AUTO_REPLY_GROUPS = {
    "视频号，剪映，微企": True,
    "ai测试2群": True,
}

# 系统提示词，控制回复风格
SYSTEM_PROMPT = """你是一个微信群聊助手。规则：
1. 回复要简短，像正常聊天一样，不要太正式
2. 不要回复所有消息，只在被问到问题或被@时才回复
3. 如果不确定怎么回，就回一个表情或简短的话
4. 回复控制在 50 字以内"""
# ====================================================


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


LAST_CAPTURE_METHOD = ""     # 最近一次截图的取法（诊断脚本和启动自检都会打出来）
LAST_CAPTURE_OK = False      # 这次拿到的图到底有没有内容；读不到就当瞎的，别硬干
LAST_CAPTURE_INFO = []       # [(取法, 尺寸, content_stats 的字典, 合格?, 备注)]，日志/诊断用
CAPTURE_OVERRIDE = ""        # config.json 的 "capture"：钉死用哪一种取法（空 = 自动挑）

BLANK_STDDEV = 6.0           # 内部灰度标准差低于它 = 一片纯色 = 没内容
BLANK_FLAT_RATIO = 0.97      # 内部近单色像素占比高于它 = 一片纯色 = 没内容
BLANK_EDGE_RATIO = 0.002     # 内部"有边"的像素占比低于它 = 连字和框都没有 = 没内容
CONTENT_MARGIN = 0.05        # 只统计窗口内部：四周各丢掉这么多，边框那一圈不算数


def content_stats(img):
    """量一张窗口截图"到底有没有东西"，返回 {"sd":…, "flat":…, "edge":…}。

    三个指标一起看（任一条踩线就算空白），阈值是对着真实截图标定出来的
    —— 本机 30 多张真微信窗口截图：内部标准差 38~67、近单色 0.44~0.63、边缘 4.4%~9%；
    全黑图（PrintWindow 失败）：0 / 1.0 / 0。客户那台 BitBlt 的灰底图：近单色 0.99、边缘 ≈0。
    三条都留着是因为它们的**失效方式不一样**：
      * sd   —— 一片纯色（全黑、全白、合成失败）；
      * flat —— 大半张图同色（客户那台就是：整片窗口底色，只有边框那圈有几块碎片；
                 只看 sd 会被那圈碎片骗过去，实测整图标准差 16.97 ≫ 旧的 3.0 阈值）；
      * edge —— 连一条字/框的边都找不到（真界面再空也有会话列表和输入框）。
    另外**只统计窗口内部**（四周各丢 5%）—— 碎片恰好长在边框那一圈。
    """
    if img is None:
        return {"sd": 0.0, "flat": 1.0, "edge": 0.0}
    try:
        g = img.convert("L")
        W, H = g.size
        mx, my = int(W * CONTENT_MARGIN), int(H * CONTENT_MARGIN)
        if W - 2 * mx > 8 and H - 2 * my > 8:
            g = g.crop((mx, my, W - mx, H - my))
        hist = g.histogram()          # 256 个桶
        total = sum(hist) or 1
        acc, med = 0, 0
        for v, c in enumerate(hist):  # 中位数
            acc += c
            if acc * 2 >= total:
                med = v
                break
        mean = sum(v * c for v, c in enumerate(hist)) / total
        sd = (sum(c * (v - mean) ** 2 for v, c in enumerate(hist)) / total) ** 0.5
        flat = sum(hist[max(0, med - 8):med + 9]) / total
        # 边缘像素占比：FIND_EDGES 之后还亮着的像素。字、气泡、分隔线都会留下边，
        # 纯底色不会（实测真界面 0.044~0.090，灰底图 0）。
        # 必须先切掉 2 像素边框：PIL 的 FIND_EDGES 把图外当成 0，会给整张图**人造一圈边**
        # （实测 808x584 的纯色图能凭空生出 2780 个"边缘"像素，占比 0.0059 ＞ 阈值）。
        from PIL import ImageFilter
        e = g.filter(ImageFilter.FIND_EDGES)
        if e.width > 6 and e.height > 6:
            e = e.crop((2, 2, e.width - 2, e.height - 2))
        eh = e.histogram()
        edge = sum(eh[40:]) / (sum(eh) or 1)
        return {"sd": sd, "flat": flat, "edge": edge}
    except Exception:
        return {"sd": 0.0, "flat": 1.0, "edge": 0.0}


def _stats_blank(s):
    """三个指标任一条踩线 = 没内容。踩线就往下一条取法走，不是"差不多能用"。"""
    return (s["sd"] < BLANK_STDDEV or s["flat"] > BLANK_FLAT_RATIO
            or s["edge"] < BLANK_EDGE_RATIO)


def _looks_blank(img):
    """这张图是不是"没有内容"（= 没渲染出来）。真画面 → False。"""
    return img is None or _stats_blank(content_stats(img))


def capture_ok():
    """最近一次截图拿到的图能不能用。

    写成函数而不是让调用方 import 那个全局变量：全局的更新只在本模块里，
    `from wechat_bot import LAST_CAPTURE_OK` 拿到的是 import 那一刻的快照（永远是 False）。
    """
    return LAST_CAPTURE_OK


def capture_method():
    """最近一次用的取法（同上，必须是函数）"""
    return LAST_CAPTURE_METHOD


def capture_summary():
    """一行字说明这次截图是怎么来的 —— 写日志、启动自检都用它。"""
    if not LAST_CAPTURE_INFO:
        return "还没截过图"
    parts = []
    for name, size, st, ok, note in LAST_CAPTURE_INFO:
        sz = "拿不到" if size is None else f"{size[0]}x{size[1]}"
        # "空白"和"有内容但不算数（被挡住）"要分开写：后者一追究就知道是窗口被压住了
        verdict = "合格" if ok else ("有内容但不可用" if not _stats_blank(st) else "空白")
        parts.append(f"{name}: {sz} 标准差{st['sd']:.1f} 单色{st['flat']:.2f} "
                     f"边缘{st['edge']:.4f} {verdict}"
                     + (f"（{note}）" if note else ""))
    return "；".join(parts)


def set_capture_override(value):
    """config.json 里钉死截图取法（'' 或 'auto' = 自动挑）。换机器只改数据不改代码。"""
    global CAPTURE_OVERRIDE
    CAPTURE_OVERRIDE = "" if str(value or "").strip().lower() in ("", "auto") else str(value).strip()
    return CAPTURE_OVERRIDE


def occlusion(hwnd, probes=3):
    """窗口露在最前面的探针点数（分母 probes²）。

    为什么必须量："抓屏幕"那条路要求窗口没被别的窗口压住，否则抓回来的是**别人的
    画面**。客户那份诊断里屏幕抓取的标准差最高（115.43），可当时前台根本不是微信
    （报告第 3 节）—— 那 115 很可能是压在微信上面的另一个窗口。真拿它当"画面"去
    OCR，机器人会照着别人的界面切群、发消息，比读不到更危险。
    """
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
    except Exception:
        return 0, probes * probes
    shown = 0
    for iy in range(probes):
        for ix in range(probes):
            x = l + int((r - l) * (ix + 0.5) / probes)
            y = t + int((b - t) * (iy + 0.5) / probes)
            try:
                h = win32gui.WindowFromPoint((x, y))
                if h and win32gui.GetAncestor(h, win32con.GA_ROOT) == root:
                    shown += 1
            except Exception:
                pass
    return shown, probes * probes


def _bitmap_to_image(bitmap):
    bmpinfo = bitmap.GetInfo()
    bmpstr = bitmap.GetBitmapBits(True)
    return Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                            bmpstr, "raw", "BGRX", 0, 1)


def _capture_bitblt(hwnd, w, h):
    """从窗口 DC 直接拷（最快，窗口被别的窗口压住也能读到它自己的缓冲）。"""
    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfcDC, w, h)
    saveDC.SelectObject(bitmap)
    saveDC.BitBlt((0, 0), (w, h), mfcDC, (0, 0), 0x00CC0020)  # SRCCOPY
    img = _bitmap_to_image(bitmap)
    win32gui.DeleteObject(bitmap.GetHandle())
    saveDC.DeleteDC(); mfcDC.DeleteDC(); win32gui.ReleaseDC(hwnd, hwndDC)
    return img


def _capture_printwindow(hwnd, w, h):
    return _printwindow(hwnd, w, h, 2)      # PW_RENDERFULLCONTENT


def _capture_printwindow_client(hwnd, w, h):
    """只让窗口画**客户区**（PW_CLIENTONLY）。

    微信 4.x 是无边框窗口（客户区左上角 == 窗口左上角，报告实测边框只有 8x0 像素），
    整窗口渲染和客户区渲染本该一样；但实测有些机器上两种 flag 结果并不一致 ——
    多一条路就多一次拿到内容的机会，代价只是一次 PrintWindow。
    """
    return _printwindow(hwnd, w, h, 1)


def _printwindow(hwnd, w, h, flags):
    """PrintWindow：让窗口**自己**把内容画进我们的 DC。

    本机上微信 4.x 走这条路返回全黑（所以以前只用 BitBlt）；但在 BitBlt 只拿到
    底色的机器上（远程桌面/合成方式不同的），它常常是唯一能拿到内容的一条路。
    """
    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfcDC, w, h)
    saveDC.SelectObject(bitmap)
    ok = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), flags)
    img = _bitmap_to_image(bitmap) if ok else None
    win32gui.DeleteObject(bitmap.GetHandle())
    saveDC.DeleteDC(); mfcDC.DeleteDC(); win32gui.ReleaseDC(hwnd, hwndDC)
    return img


def _capture_screen(hwnd, w, h):
    """直接从屏幕上抓这一块。

    最后一条路，要求窗口没被别的窗口压住（调用方一般已经把微信抬到前台了）。
    代价：抓到的可能是压在上面的别的窗口 —— 所以只在前两种都空白时才用。
    """
    from PIL import ImageGrab
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)


CAPTURE_METHODS = (
    ("BitBlt", _capture_bitblt),
    ("PrintWindow", _capture_printwindow),
    ("PrintWindow客户端区", _capture_printwindow_client),
    ("屏幕抓取", _capture_screen),
)

# config.json 里 "capture" 的取值 → 取法名（换机器时钉死，不用改代码）
_CAPTURE_KEYS = {
    "bitblt": "BitBlt",
    "printwindow": "PrintWindow",
    "printwindow-client": "PrintWindow客户端区",
    "printwindow_client": "PrintWindow客户端区",
    "screen": "屏幕抓取",
    "屏幕抓取": "屏幕抓取",
}


def capture_window(hwnd):
    """截图，返回 PIL Image（内存，不落盘）。

    依次试四种取法，**第一个"真的有内容"的就用**（早停：正常机器开销和以前一样）：
        1. BitBlt            —— 最快，窗口被压住也能读它自己的缓冲
        2. PrintWindow(2)    —— 让窗口自己重画（BitBlt 只拿到底色时用它）
        3. PrintWindow(1)    —— 同上，只画客户区（无边框窗口多一条路）
        4. 抓屏幕            —— 最后一条路，要求窗口露在最前面，所以先数探针点
    "有没有内容"由 content_stats 判（内部标准差 + 近单色占比两道关）。为什么不是只看
    整图标准差：客户那台 BitBlt 返回一张灰底图，只有边框那一圈有几块碎片，标准差
    16.97 就把旧判断（<3.0 才算空白）骗过去了，机器人从此拿它当画面用（2026-10）。
    结果记在 LAST_CAPTURE_METHOD / LAST_CAPTURE_OK / LAST_CAPTURE_INFO 里。
    """
    global LAST_CAPTURE_METHOD, LAST_CAPTURE_OK, LAST_CAPTURE_INFO
    LAST_CAPTURE_METHOD, LAST_CAPTURE_OK, LAST_CAPTURE_INFO = "", False, []
    # 最小化时先还原（最小化时拿到的是旧画面）
    if win32gui.IsIconic(hwnd):
        win32gui.SendMessage(hwnd, 0x0112, 0xF120, 0)  # WM_SYSCOMMAND + SC_RESTORE
        time.sleep(0.5)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return None

    only = _CAPTURE_KEYS.get(CAPTURE_OVERRIDE.lower()) if CAPTURE_OVERRIDE else None
    if CAPTURE_OVERRIDE and not only:
        print(f"    [!] config 里的 capture={CAPTURE_OVERRIDE!r} 不认识，按自动挑走")
    shown, total = occlusion(hwnd)     # 只跟"抓屏幕"有关，顺手先量一次
    best, best_score, screen_blocked = None, -1.0, False

    for name, fn in CAPTURE_METHODS:
        if only and name != only:
            continue
        try:
            img = fn(hwnd, w, h)
        except Exception as e:
            LAST_CAPTURE_INFO.append((name, None, content_stats(None), False, f"报错 {e}"))
            continue
        if img is None:
            LAST_CAPTURE_INFO.append((name, None, content_stats(None), False, "拿不到图"))
            continue
        st = content_stats(img)
        ok = not _stats_blank(st)
        note = ""
        if name == "屏幕抓取":
            if shown * 2 < total:      # 露在外面的点不到一半 = 被压住了
                note = f"微信被挡住（只露 {shown}/{total} 点），抓到的可能是别的窗口"
                # 只有"本来有内容、纯粹因为被挡住才不算数"才记这一笔：四条路全空白时
                # 该报的是"全空白"（提示去查远程桌面/最小化），报"被遮挡"会把人带偏。
                screen_blocked = ok
                ok = False
            else:
                note = f"微信露在最前面（{shown}/{total} 点）"
        LAST_CAPTURE_INFO.append((name, img.size, st, ok, note))
        if ok:
            LAST_CAPTURE_METHOD, LAST_CAPTURE_OK = name, True
            return img
        score = st["sd"] * (1.0 - min(st["flat"], 1.0))   # 越"有层次"分越高，全败时留最好的一张给诊断
        if score > best_score:
            best, best_score = img, score

    LAST_CAPTURE_METHOD = "屏幕抓取（疑似被遮挡）" if screen_blocked else "全空白"
    LAST_CAPTURE_OK = False
    return best


def _ocr_lines(ocr, img):
    """OCR 一张图，返回按阅读顺序（先 y 后 x）排序的文本行列表"""
    result, _ = ocr(np.array(img))
    if not result:
        return []
    items = sorted(result, key=lambda l: (sum(p[1] for p in l[0]) / 4,
                                          sum(p[0] for p in l[0]) / 4))
    return [t for _, t, _ in items]


# ============ 按键注入 / 抢前台 ============
# 输入框在窗口内的垂直位置（比例）。0.80 是文字输入区中心，
# 避开发送按钮(约 0.92H)和底部工具栏(约 0.94H)。多试几个高度做兜底。
INPUT_CLICK_Y_RATIOS = (0.80, 0.92, 0.72)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


def send_key(vk, ctrl=False):
    """SendInput 发一次按键。

    按键只会送给**前台窗口**，所以调用前必须确认微信是前台，否则等于往
    用户正在编辑的窗口里打字。
    """
    seq = []
    if ctrl:
        seq.append((win32con.VK_CONTROL, 0))
    seq.append((vk, 0))
    seq.append((vk, win32con.KEYEVENTF_KEYUP))
    if ctrl:
        seq.append((win32con.VK_CONTROL, win32con.KEYEVENTF_KEYUP))
    arr = (_INPUT * len(seq))()
    for i, (v, flags) in enumerate(seq):
        arr[i].type = 1   # INPUT_KEYBOARD
        arr[i].u.ki = _KEYBDINPUT(wVk=v, wScan=0, dwFlags=flags, time=0, dwExtraInfo=None)
    n = ctypes.windll.user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(_INPUT))
    if n != len(seq):
        print(f"    [!] SendInput 只发出 {n}/{len(seq)} 个事件 err={ctypes.GetLastError()}")


def force_foreground(hwnd):
    """SetForegroundWindow 被前台锁挡住时，用 AttachThreadInput 绕过。

    微信窗口不是前台时，SetActive 经常静默失败（Windows 只允许"当前前台进程"
    或刚被用户点过的进程抢前台），此时按键会打到最后那个前台窗口上。
    """
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    fg = win32gui.GetForegroundWindow()
    try:
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
        cur_tid = win32api.GetCurrentThreadId()
        win32process.AttachThreadInput(cur_tid, fg_tid, True)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32process.AttachThreadInput(cur_tid, fg_tid, False)
        time.sleep(0.3)
    except Exception as e:
        print(f"  AttachThreadInput 抢前台失败: {e}")
    return win32gui.GetForegroundWindow() == hwnd


def _fuzzy_equal(a, b, threshold=0.6):
    """OCR 噪声容错的模糊相等"""
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def looks_sent(hit_after, texts_after, y_after, y_paste):
    """回车后判断消息到底发出去没有。

    坑：输入框验证区域(0.64H~0.99H)同时也盖住了**聊天区最后一条消息** —— 而发出去的
    消息正好落在那里，所以"还能 OCR 到这条消息"并不能证明它还在输入框里（早期版本就是
    这么误报失败的）。三个正向信号任一成立就算发出去了：
      1. 输入框占位符回来了（"按住鼠标语音输入文字"）＝ 输入框已清空
      2. 这条消息的 y 明显上移了（从输入框挪进了聊天区）
      3. 整段都 OCR 不到这条消息了（滚走了/位置变了）
    """
    placeholder = any(("语音" in t and "输入" in t) or "按住" in t for t in texts_after)
    moved_up = bool(hit_after and y_paste is not None and y_after is not None
                    and y_after < y_paste - 0.03)
    return placeholder or moved_up or (not hit_after)


def click_at(hwnd, off_x, off_y):
    """在窗口内偏移 (off_x, off_y) 处点一下鼠标。

    不用 uiautomation 的 Control.Click，两个原因：
      1. 它的 x/y 是"控件内偏移"（源码 x = rect.left + x），和屏幕坐标容易记混 —— 老代码
         就是传了屏幕坐标出去，窗口一挪点到窗口外。
      2. 它内部用 mouse_event(MOUSEEVENTF_ABSOLUTE) 做归一化，在双显示器 + 200% 缩放
         环境下实测会点偏（本该点屏幕 (781,673)，光标蹦到 (739,363)）。
    这里直接 SetCursorPos 到屏幕绝对坐标再发按下/抬起，位置是准的。

    注意：这个点必须真的落在微信窗口上 —— 微信被别的窗口（浏览器等）压住时，点击会打到
    那个窗口上，微信这边什么都不会发生（而且前台窗口也不会变，很好认）。
    """
    rect = win32gui.GetWindowRect(hwnd)
    pt = (rect[0] + int(off_x), rect[1] + int(off_y))
    win32api.SetCursorPos(pt)
    time.sleep(0.08)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
    time.sleep(0.08)
    return pt


# ============ 下面几个是给各版本共用的读屏小函数（都在窗口内比例坐标下工作）============
SIDEBAR_W_FALLBACK = 0.33


def sidebar_ratio(img):
    """量出会话列表的右边界（窗口比例）。

    会话列表底色是灰的(约 238,238,240)，聊天区是白的(约 250,250,250)，扫几行找分界即可。
    这个比例**不能写死**：写小了会把会话列表里别的会话的预览当成聊天消息读进来
    （它们的 y 还排在最新消息下面，直接撑坏"取尾行之后"的增量判断）；写大了会把
    别人气泡/预览的文字切掉（原来写 0.25W 就是这个毛病，比实际 0.34W 窄了）。
    """
    W, H = img.size
    px = img.load()
    hits = []
    for frac in (0.35, 0.5, 0.65, 0.8):
        y = int(H * frac)
        for x in range(int(W * 0.15), int(W * 0.55)):
            run = [px[min(x + i, W - 1), y] for i in range(0, 24, 4)]
            if all(c[0] >= 246 and c[1] >= 246 and c[2] >= 246 for c in run):
                hits.append(x)
                break
    if not hits:
        return SIDEBAR_W_FALLBACK
    hits.sort()
    return hits[len(hits) // 2] / W


# ============ 会话列表右边界：别只靠像素量 ============
# 客户那台（2026-10-16）：像素量法给出 364，而会话列表实际只到 ~322 —— 量宽了 40 多像素。
# 后果有两个，都不轻：
#   1) 会话列表裁剪区把**聊天标题**那一行的字一起框了进来，侧栏里凭空多出一个和群名
#      一模一样的"行"（诊断报告 §6 里 y=57 那条），机器人会去点它 —— 点的是标题栏，白点；
#   2) 反过来，聊天标题的裁剪区从 364 开始，把群名**切掉三个字**（§8 只读到 '2 (8)'），
#      title_matches 永远不过 → 切群确认、发送前校验全废 → **能读、不能发**。
# 稳的锚是"时间列"：微信把每行的时间/日期**右对齐到会话行的右缘**，所以时间那几项的
# 右边界 ≈ 会话列表右边界。像素量法会被聊天区的头像/气泡顶到右边，时间列不会。
_SIDEBAR_LEFT = None
SIDEBAR_ANCHOR_PAD = 12      # 时间列右缘再往右留一点，别把行尾内边距切进去

_TIME_LIKE = re.compile(r'^\d{1,2}:\d{2}$|^\d{1,2}[/月-]\d{1,2}$'
                        r'|^(昨天|前天|星期[一二三四五六日天]|周[一二三四五六日天])$')


def set_sidebar_left(x):
    """把"实测出来的会话列表右边界"存下来（谁读过侧栏 OCR 谁负责更新它）。"""
    global _SIDEBAR_LEFT
    _SIDEBAR_LEFT = int(x) if x else None
    return _SIDEBAR_LEFT


def sidebar_left_from_items(items, W, fallback):
    """从会话列表 OCR 出来的项里量出聊天区左边界（图像 x）；量不出来就用 fallback。

    items 每项是 (y, x 中心, x 右缘, 文本)。
    """
    rights = [right for _, _, right, t in items if t and _TIME_LIKE.match(t.strip())]
    if not rights:
        return fallback
    x = int(max(rights) + SIDEBAR_ANCHOR_PAD)
    return x if W * 0.12 < x < W * 0.6 else fallback


def sidebar_left(img):
    """聊天区左边界（图像 x）：优先用侧栏 OCR 实测出来的锚，没有就现量像素。"""
    W = img.size[0]
    a = _SIDEBAR_LEFT
    if a and W * 0.12 < a < W * 0.6:
        return a
    return int(W * sidebar_ratio(img))


def read_chat_title(ocr, hwnd):
    """OCR 聊天区标题（群名 + 人数）。

    布局都是**当次现量**的，不按比例猜（这套比例在 896 宽的小窗上全都不对）：
      * 左边界 = sidebar_left()（时间列锚优先，量不出来退回像素量法）；
      * 标题条高度 = title_band_bottom()：标题条底色和聊天区底色不同，
        沿 x=锚+10 那一竖列往下找**第一次换色**。聊天区内容是白的（250），
        标题条是灰一档（238~246），第一次出现"连续 4 个白像素"就是标题条的底。
    混进窗口标题栏的"微信"两个字照旧滤掉。

    读不到画面（截图空白）时直接返回 ""：调用方是拿它当"切对群了没"的凭据用的，
    宁可报"读不出来"→ 中止发送，也不能拿垃圾 OCR 去猜。
    """
    img = capture_window(hwnd)
    if img is None or not LAST_CAPTURE_OK:
        return ""
    W, H = img.size
    x0 = min(sidebar_left(img) + 10, W - 1)
    bottom = title_band_bottom(img, x0, default=int(H * 0.14))
    region = img.crop((sidebar_left(img), 0, W, bottom))
    region = region.resize((region.width * 2, region.height * 2), 1)   # 1=LANCZOS
    result, _ = ocr(np.array(region))
    win_title = win32gui.GetWindowText(hwnd).strip()
    return " ".join(t for _, t, _ in (result or []) if t.strip() and t.strip() != win_title)


def title_band_bottom(img, x, default, dark=200, scan_to=0.3):
    """标题条的下边界（图像 y）—— 标题里**群名的文字**必须全在这条线以上。

    微信窗口（横贯聊天区的结构，多台机器实测一致）：
        y 0~13    窗口顶白边
        y 13~77   标题条**深底**（117,117,122）—— 放置返回按钮/会话操作按钮
        y 77~171  **群名 + 人数的文字**（白底 250 上深色字）
        y 171     一条 2px 的浅灰分隔线（240）→ 下面才是聊天消息
    所以分界要找的是**那条 240 分隔线**，不是深底结束的地方（77）—— 在 77 切会把
    群名整个切掉。分隔线比白底暗一档（240 vs 250）、比标题深底亮一档（240 vs 117），
    取"240±一档灰、连续 2 行、位置在深底之下"，三条件一起够挑出它。
    量不到就退回 default（0.14H，旧的比例值兜底）。
    """
    W, H = img.size
    px = img.load()
    x = min(x, W - 1)
    y_max = int(H * scan_to)
    # 先找到深底段落（确认我们在标题区里），再往下找分隔线
    seen_dark_band = False
    run_cnt = 0
    for y in range(2, y_max):
        c = px[x, y]
        lum = (c[0] + c[1] + c[2]) / 3
        if lum < dark:                     # 深底段
            seen_dark_band = True
            run_cnt = 0
            continue
        if seen_dark_band:
            if 232 <= lum <= 248:          # 浅灰分隔线那一档
                run_cnt += 1
                if run_cnt >= 2:
                    return y + 1           # y 是第二条线的位置，整条线到 y+1
            else:
                run_cnt = 0
    return default


def title_matches(title, target_name):
    """标题区 OCR 里是不是**这个**群。

    两个坑都长在客户那对群名上（「测试群」/「测试群2」）：
      1. 原来第一句是 `target_name[:4] in title` —— 「测试群」会命中「测试群2 (8)」，
         于是"已经在这个群了"成立、根本不切群，回复就发到隔壁群去了。
         现在要求**整名作为一段独立出现**：它后面必须跟空/括号/标点/结尾，
         不能紧跟名字里的字。
      2. 标题那一条裁剪带里通常还带着聊天区第一条消息
         （`X 测试群2 (8) 某某: 你好`），所以不能要求整条相等 —— 只能做包含判断。
    """
    if not title:
        return False
    t = re.sub(r'\s+', '', title)
    n = re.sub(r'\s+', '', target_name)
    if not n:
        return False
    for m in re.finditer(re.escape(n), t):
        nxt = t[m.end():m.end() + 1]
        if not nxt or not re.match(r'[\w一-鿿]', nxt):
            return True
    # OCR 噪声兜底（少一个字/全角半角不同）：**只把"群名没在标题里出现"当噪声**，
    # 出现过但没对上（比如「测试群」在「测试群2」里出现过、后面跟了个 2）就不兜
    # —— 那不是噪声，那就是另一个群（老版模糊兜底在这儿把发错群放过去了）。
    if re.search(re.escape(n[:2]) if len(n) >= 2 else re.escape(n), t):
        return False
    return (abs(len(t) - len(n)) <= 2
            and difflib.SequenceMatcher(None, t, n).ratio() > 0.5)


def input_box_top(img, x, default, dark=235, scan_from=0.45, scan_to=0.95):
    """输入框区域的上边界（图像 y）：从 x 那一竖列上找**输入区上沿的分隔线**。

    那条线是横贯整宽的一条浅灰（本机实测 221~224，聊天区白底 250、窗口侧栏 238）：
    在窗口中部往下扫，**连续 3+ 行都比 235 暗**的第一段就是它，线下面才是输入框。
    线本身只有 2~3 像素高（本机实测 1310~1311 两行 221/224），判据是
    "整行三通道都暗于 235 且至少连续 2 行" —— 单像素的噪声（气泡边缘）凑不出
    "整行三通道全暗"，够稳。
    量不到就退回 default（0.64H，旧的比例值）。

    为什么不沿用 0.64H：那是在 2546x1610 大窗上标定的（实测 1310/1610 = 0.81H，
    连本机自己都不是 0.64H，旧值靠 0.64H~0.99H 的宽裁剪兜住）；窗口大小一变
    比例就漂，客户 896x648 的小窗上更是把聊天区最后几条消息圈进"输入框"，
    验证"粘贴进去了没"就被别人的消息搅浑。
    """
    W, H = img.size
    px = img.load()
    y0, y1 = int(H * scan_from), int(H * scan_to)
    run, best = 0, None
    for y in range(y0, y1):
        c = px[min(x, W - 1), y]
        if c[0] < dark and c[1] < dark and c[2] < dark:
            run += 1
            if run >= 2 and best is None:
                best = y - 1
                break
        else:
            run = 0
    return best if best is not None else default


def input_box_has(ocr, hwnd, marker):
    """截图 + OCR 输入框区域，返回 (有没有这条消息, OCR 文本列表, 命中行的 y 比例)。

    微信 4.x 不暴露 UIA Edit 控件，读不到输入框内容，只能靠截图 OCR 验证。
    输入框区域的上下边界也是**现量**的（input_box_top）：
    输入框上沿有一条横向的浅灰分隔线（工具栏和输入区之间），从那条线往下到
    窗口底就是输入区。旧代码写死 0.64H~0.99H —— 在 896x648 的小窗上 0.64H
    切进聊天区半截，把别人的消息也 OCR 进来当"输入框内容"，验证就失真了。
    长消息会折行、OCR 还可能切行，所以去掉空白拼起来比对（取开头几个字当指纹）。
    """
    img = capture_window(hwnd)
    if img is None or not LAST_CAPTURE_OK:
        return False, [], None        # 读不到画面 = 没法证明文字进了输入框 → 不按回车
    W, H = img.size
    left = sidebar_left(img)
    top = input_box_top(img, left + 40, default=int(H * 0.64))
    region = img.crop((left, top, W, int(H * 0.99)))
    region = region.resize((region.width * 2, region.height * 2), 1)   # 1=LANCZOS
    result, _ = ocr(np.array(region))

    items = []   # (y比例[整窗口], 去掉空白的文本)
    for box, text, _ in (result or []):
        cy = top + sum(p[1] for p in box) / 4 / 2      # OCR 图放大过 2 倍，除回去
        items.append(((cy / H), re.sub(r'\s+', '', text)))

    flat_ocr = "".join(t for _, t in items)
    flat_marker = re.sub(r'\s+', '', marker)
    head = flat_marker[:5]
    hit = bool(flat_marker) and (
        head in flat_ocr
        or (len(flat_marker) > 5
            and difflib.SequenceMatcher(None, flat_ocr, flat_marker).ratio() > 0.6))

    y_hit = None
    if hit:
        for y, t in items:
            if (head and head in t) or difflib.SequenceMatcher(None, t, flat_marker).ratio() > 0.6:
                y_hit = y
                break
    return hit, [t for _, t in items], y_hit


class Message:
    """一条解析后的消息"""
    def __init__(self, sender, text, is_self, cx, cy):
        self.sender = sender
        self.text = text
        self.is_self = is_self
        self.cx = cx
        self.cy = cy

    def __repr__(self):
        return f"[{'我' if self.is_self else self.sender}] {self.text}"


class ChatMonitor:
    """单个聊天的监控状态"""
    def __init__(self, name):
        self.name = name
        self._last_lines = []
        self._last_preview = ""   # 会话列表里的消息预览，用于检测新消息


class GeminiClient:
    """Gemini API 封装（OpenAI 兼容接口），每个群维护对话历史保持上下文"""
    def __init__(self, api_key, base_url, model, system_prompt=None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        # 每个群独立的对话历史
        self._histories = {}
        # 连续失败计数：挂了的中转服务每批消息都等满超时（120s×2 次重试），
        # 一整天的消息都会卡死在这里。连续挂 3 次后进入"静默期"（5 分钟内不再
        # 尝试调用、直接返回 None），服务恢复后第一次成功调用就清零。
        # 上一台中转（9router）实测过"端口在听、HTTP 全部挂起"的僵死 ——
        # 表现就是日志里一排 Read timed out，机器人像死机了一样。
        self._fail_streak = 0
        self._silent_until = 0.0

    def chat(self, query, user, group_name):
        """调用 Gemini 生成回复"""
        import time as _t
        if _t.time() < self._silent_until:
            print("    [i] 模型接口连续失败中，静默期内跳过本次调用（恢复后自动重试）")
            return None
        if group_name not in self._histories:
            self._histories[group_name] = [
                {"role": "system", "content": self.system_prompt}
            ]

        history = self._histories[group_name]
        # user 为空时 query 已经是拼好的整段（比如多人群聊记录），不再加"谁:"前缀
        history.append({"role": "user", "content": f"{user}: {query}" if user else query})

        try:
            resp = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": history,
                    "max_tokens": 150,
                    "temperature": 0.7,
                    "stream": False,
                },
                timeout=(10, 120),   # (连接 10s, 读取 120s)：接口偶尔慢，别 30s 就掐断
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"].strip()
            history.append({"role": "assistant", "content": reply})
            self._fail_streak = 0        # 成功一次就恢复信任
            return reply
        except Exception as e:
            import time as _t
            print(f"Gemini API 错误: {e}")
            self._fail_streak += 1
            if self._fail_streak >= 3:
                self._silent_until = _t.time() + 300
                print(f"    [!] 模型接口连续失败 {self._fail_streak} 次：接下来 5 分钟"
                      f"不再调用（避免每批消息都干等 2 分钟超时）。**去检查中转服务**"
                      f"（{self.base_url}）是否还活着；恢复后会自动重试。")
            # 出错时移除最后一条 user 消息，避免污染历史
            history.pop()
            return None


class WeChatMultiMonitor:
    def __init__(self, targets, interval=3, switch_delay=1.0):
        self.targets = [ChatMonitor(name) for name in targets]
        self.interval = interval
        self.switch_delay = switch_delay
        self.ocr = RapidOCR()
        self.hwnd = find_wechat_hwnd()
        self._win = None
        self.gemini = GeminiClient(GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL)
        if self.hwnd:
            try:
                self._win = auto.ControlFromHandle(self.hwnd)
            except Exception:
                pass

    def _switch_chat(self, target_name):
        """切换到指定聊天：点击侧栏里的群名。返回 True 表示侧栏里找到并点了。"""
        img = capture_window(self.hwnd)
        if img is None:
            return False
        W, H = img.size
        sidebar = img.crop((0, int(H * 0.08), int(W * sidebar_ratio(img)), H))
        result, _ = self.ocr(np.array(sidebar))
        if not result:
            return False

        # 找到群名的位置，点击它
        for box, text, score in result:
            if _fuzzy_equal(text, target_name, 0.7) or target_name in text:
                # 计算在窗口坐标中的位置
                cy = sum(p[1] for p in box) / 4
                cx = sum(p[0] for p in box) / 4
                # 转换为窗口坐标（加上 crop 的偏移）
                win_x = int(cx)
                win_y = int(cy + H * 0.08)
                # 点击
                click_at(self.hwnd, win_x, win_y)
                time.sleep(0.5)
                return True
        return False

    def _search_box_has(self, text):
        """OCR 侧栏顶部搜索框，看里面是不是刚粘贴进去的群名"""
        img = capture_window(self.hwnd)
        if img is None:
            return False
        W, H = img.size
        box = img.crop((0, int(H * 0.09), int(W * 0.25), int(H * 0.18)))
        box = box.resize((box.width * 2, box.height * 2), 1)   # 1=LANCZOS
        result, _ = self.ocr(np.array(box))
        joined = re.sub(r'\s+', '', "".join(t for _, t, _ in (result or [])))
        want = re.sub(r'\s+', '', text)
        return bool(joined) and (want in joined
                                 or difflib.SequenceMatcher(None, joined, want).ratio() > 0.5)

    def _switch_chat_by_search(self, target_name):
        """Ctrl+F 搜索切群：侧栏里看不到目标群时用（群被滚出可视区、或收进了折叠列表）。

        安全性（重要）：粘贴完先确认文字进了**搜索框**，再按回车 —— 万一 Ctrl+F 没生效，
        粘贴的内容会落在聊天输入框里，一回车就把群名当消息发到群里了。
        回车后由调用方 OCR 标题确认，不是目标群就中止，不会发错群。
        """
        send_key(ord('F'), ctrl=True)   # Ctrl+F 打开搜索
        time.sleep(0.8)
        pyperclip.copy(target_name)
        time.sleep(0.2)
        send_key(ord('V'), ctrl=True)   # 粘贴群名，避开输入法
        time.sleep(1.4)
        if not self._search_box_has(target_name):
            print(f"    [X] 搜索框里没看到 [{target_name}]，可能 Ctrl+F 没打开搜索，"
                  f"不按回车（免得把群名当消息发出去）")
            send_key(win32con.VK_ESCAPE)
            return False
        send_key(win32con.VK_RETURN)
        time.sleep(1.5)
        return True

    def _read_chat(self):
        """读当前聊天区的完整消息（需要窗口已切到目标群）"""
        img = capture_window(self.hwnd)
        if img is None:
            return []
        W, H = img.size
        # 聊天消息区：左边界取在会话列表右侧（约 0.25W），避开顶部标题和底部输入框
        chat = img.crop((int(W * sidebar_ratio(img)), int(H * 0.08), W, int(H * 0.88)))
        cw, ch = chat.size
        result, _ = self.ocr(np.array(chat))
        if not result:
            return []

        items = sorted(result, key=lambda l: sum(p[1] for p in l[0]) / 4)
        self_cx_threshold = cw * 0.6

        left_cxs = [sum(p[0] for p in box) / 4 for box, t, _ in items
                    if sum(p[0] for p in box) / 4 <= self_cx_threshold and not re.fullmatch(r'\d{1,2}:\d{2}', t.strip())]
        nickname_cx = min(left_cxs) if left_cxs else 0

        messages = []
        cur_sender = None
        for box, text, score in items:
            cx = sum(p[0] for p in box) / 4
            cy = sum(p[1] for p in box) / 4
            is_self = cx > self_cx_threshold

            # 跳过标题栏区域（顶部 5% 高度内的文本，如群名+人数）
            if cy < ch * 0.05:
                continue
            # 时间戳行：HH:MM 格式
            if re.fullmatch(r'\d{1,2}:\d{2}', text.strip()):
                continue

            if not is_self:
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

    def _read_sidebar_previews(self):
        """OCR 左侧会话列表，返回 {群名: 消息预览}，全程后台不打扰"""
        img = capture_window(self.hwnd)
        if img is None:
            return {}
        W, H = img.size
        sidebar = img.crop((0, int(H * 0.08), int(W * sidebar_ratio(img)), H))
        lines = _ocr_lines(self.ocr, sidebar)

        previews = {}
        for chat in self.targets:
            for i, line in enumerate(lines):
                # OCR 群名可能有噪声，允许差 1 个字
                if _fuzzy_equal(line, chat.name, 0.7) or chat.name in line:
                    # 预览在群名下面，跳过时间/未读数等短行
                    for j in range(i + 1, min(i + 4, len(lines))):
                        nxt = lines[j]
                        if re.fullmatch(r'[\d:：昨星早期午上下\s]+', nxt) or nxt.isdigit():
                            continue
                        previews[chat.name] = nxt
                        break
                    else:
                        previews[chat.name] = ""
                    break
        return previews

    def _poll_chat(self, chat: ChatMonitor):
        """读当前聊天，返回新增的 Message 列表。

        策略：对比当前帧和基线，只取基线尾行之后的内容。
        如果基线尾行不在当前帧（滚走了），取当前帧最后几行作为新消息。
        """
        msgs = self._read_chat()
        lines = [m.text for m in msgs]
        if not chat._last_lines:
            chat._last_lines = lines
            return []

        # 找基线尾行在当前帧中的位置
        last_tail = chat._last_lines[-1]
        new_msgs = []
        if last_tail in lines:
            idx = len(lines) - 1 - lines[::-1].index(last_tail)
            new_msgs = msgs[idx + 1:]
        else:
            # 基线尾行被滚走：用模糊匹配找最近位置
            best_idx = -1
            best_ratio = 0
            for i in range(len(lines) - 1, max(len(lines) - 20, -1), -1):
                r = difflib.SequenceMatcher(None, last_tail, lines[i]).ratio()
                if r > best_ratio:
                    best_ratio = r
                    best_idx = i
            if best_ratio > 0.5:
                new_msgs = msgs[best_idx + 1:]
            else:
                # 完全对不上，只取最后 3 行作为新消息
                new_msgs = msgs[-3:]

        chat._last_lines = lines
        return new_msgs

    def _with_temp_foreground(self, fn):
        prev_foreground = win32gui.GetForegroundWindow()
        self._win.SetActive()
        time.sleep(0.2)
        try:
            fn()
        finally:
            # 立即还原，不管 fn 是否完成
            if prev_foreground and prev_foreground != self.hwnd:
                try:
                    win32gui.SetForegroundWindow(prev_foreground)
                except Exception:
                    pass

    def _input_box_has(self, marker):
        """截图 + OCR 输入框区域（实现见模块级 input_box_has）"""
        return input_box_has(self.ocr, self.hwnd, marker)

    def _current_chat_title(self):
        """OCR 聊天区标题（实现见模块级 read_chat_title）"""
        return read_chat_title(self.ocr, self.hwnd)

    _title_matches = staticmethod(title_matches)

    def send_text(self, target_name, message, at=None):
        """向指定聊天发送文本，可选 @某人。返回 True 表示确认发出去了。

        三个坑（2026-10 定位并修掉）：
        1. uiautomation 的 Control.Click(x, y) 里 x/y 是**控件内偏移**，不是屏幕绝对
           坐标（源码 MoveCursorToInnerPos: x = rect.left + x）。老代码把
           GetWindowRect 算出来的屏幕坐标直接喂进去 → 窗口一旦不是最大化（左上角不在
           0,0、或高度不够），点击就落到窗口外面（任务栏/桌面），输入框根本没拿到焦点，
           后面的 Ctrl+V 和回车全打空 —— 这就是"群里发消息总是不行"的原因。
        2. 按键只送给前台窗口。微信不是前台时（SetActive 被前台锁挡住是常事），
           Ctrl+V/回车会打进用户正在编辑的窗口。所以先确认前台，不是就中止。
        3. 切群是 OCR 点侧栏，可能点空。切完先 OCR 标题确认，不是目标群就中止，
           免得把消息发到别的群里。
        """
        prev_foreground = win32gui.GetForegroundWindow()

        # 微信 4.x @ 弹窗行为不稳定，暂时不用 @，直接在消息前加名字
        if at and at not in ("群成员", "我"):
            message_with_at = f"@{at} {message}"
        else:
            message_with_at = message
        marker = message_with_at.strip()   # OCR 验证用的文本（整条消息，模糊匹配用）

        try:
            # 1) 抢前台：抢不到就放弃，绝不盲发（切群、粘贴都要求前台）
            self._win.SetActive()
            time.sleep(0.3)
            if win32gui.GetForegroundWindow() != self.hwnd:
                print("    [!] SetActive 没抢到前台，走 AttachThreadInput 兜底")
                if not force_foreground(self.hwnd):
                    print("    [X] 抢不到微信前台窗口，放弃发送（避免按键打到别的窗口）")
                    return False

            # 2) 切群：先点侧栏（快），群不在侧栏可见区就退回 Ctrl+F 搜索
            found = self._switch_chat(target_name)
            time.sleep(0.5)
            title = self._current_chat_title()
            if not found or not self._title_matches(title, target_name):
                print(f"    侧栏没切到 [{target_name}]（标题区 OCR={title!r}），改用 Ctrl+F 搜索切群")
                self._switch_chat_by_search(target_name)
                title = self._current_chat_title()

            # 3) 确认切对了群，否则可能发到别的群
            if not self._title_matches(title, target_name):
                print(f"    [X] 当前聊天不是 [{target_name}]（标题区 OCR={title!r}），中止发送")
                return False

            pyperclip.copy(message_with_at)
            time.sleep(0.2)

            # 4) 点输入框 → 粘贴 → OCR 验证（没进去就不按回车）
            pasted = False
            y_paste = None
            for ratio_y in INPUT_CLICK_Y_RATIOS:
                if win32gui.GetForegroundWindow() != self.hwnd:
                    print("    [!] 前台已经不是微信了，中止发送")
                    return False
                # 点输入框：用比例（等价于控件内偏移 W*0.5, H*ratio_y），不用屏幕坐标
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

            # 5) 回车发送，再确认到底发出去了没
            send_key(win32con.VK_RETURN)
            time.sleep(0.8)
            hit2, texts2, y2 = self._input_box_has(marker)
            if looks_sent(hit2, texts2, y2, y_paste):
                print(f"    [OK] 已发送: {message_with_at}")
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

    def _should_reply(self, chat_name, msg):
        """判断是否应该回复这条消息"""
        # 不回复自己发的
        if msg.is_self:
            return False
        # 该群没开自动回复
        if not AUTO_REPLY_GROUPS.get(chat_name, False):
            return False
        # 太短的消息（表情、语气词）不回复
        if len(msg.text.strip()) < 2:
            return False
        return True

    def _handle_new_messages(self, chat_name, msgs):
        """处理新消息：打印 + 可选自动回复"""
        if not msgs:
            return
        replied = False
        for msg in msgs:
            print(f"  [{chat_name}] {msg}")

            if not self._should_reply(chat_name, msg):
                continue

            # 调用 Gemini 生成回复
            print(f"    → 调用 Gemini...")
            reply = self.gemini.chat(msg.text, user=msg.sender, group_name=chat_name)
            if reply:
                print(f"    → 回复: {reply[:50]}...")
                self.send_text(chat_name, reply, at=msg.sender)
                replied = True
            else:
                print(f"    → Gemini 无回复")

        # 发完消息后重建基线，避免自己的回复被当成新消息
        if replied:
            self.reset_baseline(chat_name)

    def reset_baseline(self, target_name):
        for chat in self.targets:
            if chat.name == target_name:
                chat._last_lines = [m.text for m in self._read_chat()]
                break

    def run(self, duration=None):
        if not self._win:
            raise RuntimeError("未找到微信窗口")

        # 初始化：读一次侧栏预览作为基线
        print("初始化...")
        previews = self._read_sidebar_previews()
        for chat in self.targets:
            chat._last_preview = previews.get(chat.name, "")
            status = "找到" if chat.name in previews else "未在列表中"
            print(f"  [{chat.name}] {status}, 预览: {chat._last_preview!r}")

        print(f"开始监控 {len(self.targets)} 个群，每 {self.interval}s 看一次会话列表（不抢前台）...")
        start = time.time()
        while True:
            try:
                previews = self._read_sidebar_previews()
                for chat in self.targets:
                    preview = previews.get(chat.name)
                    if preview is None:
                        continue
                    if _fuzzy_equal(preview, chat._last_preview):
                        continue  # 预览没变

                    old_preview = chat._last_preview
                    chat._last_preview = preview
                    print(f"[{chat.name}] 预览变化: {old_preview!r} → {preview!r}")

                    # 预览格式通常是 "发送者: 消息内容" 或直接是内容
                    # 提取发送者和内容
                    if ":" in preview or "：" in preview:
                        sep = ":" if ":" in preview else "："
                        sender, text = preview.split(sep, 1)
                        sender = sender.strip()
                        text = text.strip()
                    else:
                        sender = "群成员"
                        text = preview.strip()

                    if not text or len(text) < 2:
                        continue

                    # 跳过自己发的
                    if sender in ("我", "海妖"):
                        continue

                    print(f"=== [{chat.name}] 新消息 ===")
                    print(f"  [{chat.name}] [{sender}] {text}")

                    if not AUTO_REPLY_GROUPS.get(chat.name, False):
                        continue

                    # 调用 Gemini 回复
                    print(f"    → 调用 Gemini...")
                    reply = self.gemini.chat(text, user=sender, group_name=chat.name)
                    if reply:
                        print(f"    → 回复: {reply[:50]}...")
                        self.send_text(chat.name, reply, at=sender)
                        # 发完更新预览基线
                        time.sleep(1)
                        new_previews = self._read_sidebar_previews()
                        chat._last_preview = new_previews.get(chat.name, chat._last_preview)
                    else:
                        print(f"    → Gemini 无回复")

            except Exception as e:
                print(f"轮询异常: {e}")
            time.sleep(self.interval)

            if duration and time.time() - start > duration:
                break


if __name__ == "__main__":
    groups = list(AUTO_REPLY_GROUPS.keys())
    print(f"监控群: {groups}")
    print("3 秒后开始...")
    time.sleep(3)

    mon = WeChatMultiMonitor(groups, interval=3)
    mon.run()
