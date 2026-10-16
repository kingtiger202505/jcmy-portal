# -*- coding: utf-8 -*-
"""一键体检：把"机器人为什么没反应"要用的信息全打出来，存两个文件发回来即可。

    python diagnose.py      （或双击 诊断.bat）

产出（脚本同目录）：
    diagnose-report.txt  环境 / 微信窗口 / 截图质量 / 布局测量 / 会话列表 OCR 原文 /
                         群名逐个比对 / 当前聊天标题 / Dify 检索  —— 逐项结果
    diagnose-screenshot.png   **机器人干活用的那张图**。最关键的一张：如果它是黑的或只有
                         半张脸，那就是截图拿不到画面，后面 OCR、切群、回复全都无从谈起。
    diagnose-shot-1~4-*.png   **四种截图取法各自拿到的画面**（BitBlt / PrintWindow /
                         PrintWindow 客户端区 / 抓屏幕）。为什么四种都存：客户那台存下来的
                         就是一张灰底图，光看它只能得出"截图不行"，而真正要知道的是
                         "另外三条路里有没有一条能用的"。哪张有微信界面，哪条路就是能用的。

跑之前请**先停掉机器人**（两边会抢前台窗口、也会互相干扰截图）。
"""
import ctypes
import ctypes.wintypes
import difflib
import os
import sys
import time
import traceback

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
# 文件名用英文：diagnose.bat 要把它印在命令行里，而 .bat 里一旦出现中文就会被 cmd
# 按控制台代码页解析、把整段脚本搞崩（这个坑今天踩了两次）。报告**内容**当然还是中文。
OUT_TXT = os.path.join(HERE, "diagnose-report.txt")
OUT_PNG = os.path.join(HERE, "diagnose-screenshot.png")

_lines = []
_hints = []
_files = []          # 本次真正存下来的文件（截图会有好几张），最后一起列出来


def p(s=""):
    print(s)
    _lines.append(str(s))


def hint(s):
    _hints.append(s)


def section(title):
    p()
    p("=" * 66)
    p(title)
    p("=" * 66)


# ---------------- 权限级别（完整性级别）----------------
# 为什么专门量这个：报告里原来那句"两者必须同一个权限级别"说得太糙，方向都反了 ——
# UIPI 只拦"低权限 → 高权限"。也就是说**微信提权、黑窗口没提权**时按键才会被系统丢掉；
# 反过来（黑窗口是管理员、微信是普通进程，也就是这份报告）**拦不到按键**，
# 按键这条路是通的。写反了会把人支到错的方向去折腾管理员权限。
_RID_NAMES = {
    0x0000: "未标记", 0x1000: "低", 0x2000: "中（普通用户）", 0x2100: "中+",
    0x3000: "高（管理员）", 0x4000: "系统",
}


def _rid_name(rid):
    if rid is None:
        return "读不到"
    return _RID_NAMES.get(rid, hex(rid))


def _integrity_rid(pid):
    """进程的完整性级别 RID（0x2000 中 / 0x3000 高）。读不到返回 None。

    读不到本身就是信息：普通权限的 python 去问一个提权了的微信，OpenProcess 会失败。

    **argtypes/restype 必须写全**：ctypes 默认把参数当 32 位 int，而 SID 指针是 64 位地址
    （实测直接报 "argument 1: int too long to convert"，整个函数静默返回失败）。
    """
    w = ctypes.wintypes
    k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
    k32.OpenProcess.restype = w.HANDLE
    k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k32.CloseHandle.argtypes = [w.HANDLE]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                        w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, w.DWORD]
    adv.GetSidSubAuthority.restype = ctypes.POINTER(w.DWORD)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenIntegrityLevel = 25
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        tok = w.HANDLE()
        if not adv.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            need = w.DWORD()
            adv.GetTokenInformation(tok, TokenIntegrityLevel, None, 0, ctypes.byref(need))
            buf = ctypes.create_string_buffer(need.value)
            if not adv.GetTokenInformation(tok, TokenIntegrityLevel, buf, need.value,
                                           ctypes.byref(need)):
                return None
            sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]   # TOKEN_MANDATORY_LABEL.Sid
            cnt = adv.GetSidSubAuthorityCount(sid)[0]
            return adv.GetSidSubAuthority(sid, cnt - 1)[0]
        finally:
            k32.CloseHandle(tok)
    finally:
        k32.CloseHandle(h)


def main():
    p("微信客服机器人 —— 诊断报告")
    p(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # ---------------- 环境 ----------------
    section("[1] 环境")
    p(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    p(f"脚本目录    : {HERE}")
    try:
        import platform
        p(f"系统        : {platform.platform()}")
    except Exception:
        pass
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        p(f"管理员权限  : {admin}")
        if admin:
            hint("这个黑窗口是**以管理员身份**跑的。按现在这样它**不会**拦掉按键"
                 "（UIPI 只拦「低权限→高权限」），但反过来就出事：**微信提权、这个窗口没提权**时，"
                 "系统会把按键和粘贴全丢掉，机器人能读能判、就是发不出去。最省事的是两边都别用"
                 "管理员 —— 关掉黑窗口，直接双击 start.bat 重来。具体级别看第 3 节的实测值。")
    except Exception:
        pass
    try:
        u32 = ctypes.windll.user32
        p(f"屏幕        : {u32.GetSystemMetrics(0)}x{u32.GetSystemMetrics(1)}"
          f"  系统DPI={u32.GetDpiForSystem()}")
    except Exception:
        pass

    # ---------------- 配置 ----------------
    section("[2] 配置（config.json / groups.txt）")
    try:
        import wechat_group_bot as B
        cfg, notes = B.apply_config(os.path.join(HERE, B.CONFIG_FILE))
        for n in notes:
            p(f"  {n}")
        groups = list(B.GROUPS)
        p(f"要盯的群    : {groups}")
        p(f"知识库文件  : {B.KNOWLEDGE_FILE}  " +
          (f"存在，{len(open(os.path.join(HERE, B.KNOWLEDGE_FILE), encoding='utf-8').read())} 字"
           if os.path.exists(os.path.join(HERE, B.KNOWLEDGE_FILE)) else "**没找到**"))
        p(f"Dify 检索   : {'已配 ' + B.DIFY_BASE_URL if (B.DIFY_DATASET_ID and B.DIFY_API_KEY) else '没配（只用 knowledge.md）'}")
        p(f"企微推送    : {'已配' if B.WECOM_WEBHOOK else '没配（转人工只写本地日志）'}")
    except Exception as e:
        p(f"读配置失败: {e}")
        hint(f"读配置失败：{e}")
        return finish(1)

    # ---------------- 微信窗口 ----------------
    section("[3] 微信窗口")
    from wechat_bot import (capture_window, click_at, find_wechat_hwnd, read_chat_title,  # noqa: F401
                            sidebar_ratio, title_matches)
    import wechat_bot as _W      # 诊断要直接调三种截法，所以留个模块引用
    import win32gui

    hwnd = find_wechat_hwnd()
    if not hwnd:
        p("**没找到微信窗口**")
        hint("没找到微信窗口：先把微信登录、窗口打开，再跑诊断。")
        return finish(1)
    p(f"hwnd        : {hwnd}")
    p(f"窗口标题    : {win32gui.GetWindowText(hwnd)!r}")
    p(f"窗口类名    : {win32gui.GetClassName(hwnd)}")
    rect = win32gui.GetWindowRect(hwnd)
    p(f"窗口矩形    : {rect}  （{rect[2]-rect[0]} x {rect[3]-rect[1]}，左上角 {rect[0]},{rect[1]}）")
    try:
        crect = win32gui.GetClientRect(hwnd)
        origin = win32gui.ClientToScreen(hwnd, (0, 0))
        p(f"客户区      : {crect[2]}x{crect[3]}，客户区左上角在 {origin}  "
          f"→ 标题栏/边框占 {origin[0]-rect[0]} x {origin[1]-rect[1]} 像素")
        p(f"最大化      : {rect[0] <= -8 and rect[1] <= -8}")
    except Exception as e:
        p(f"客户区读取失败: {e}")
    p(f"最小化      : {bool(win32gui.IsIconic(hwnd))}")
    _stale = bool(win32gui.IsIconic(hwnd)) or rect[0] <= -30000 or rect[1] <= -30000
    p(f"当前前台    : {win32gui.GetForegroundWindow()}"
      f"{'（就是微信）' if win32gui.GetForegroundWindow() == hwnd else '（不是微信！）'}")

    # 微信有没有被别的窗口压着：只有"抓屏幕"那条取法在乎这个。量出来才知道
    # 第 4 节里"屏幕抓取有内容"到底是微信的画面，还是压在它上面的那个窗口的画面。
    probe = None          # (露在外面的点数, 总点数)：第 4 节判断"抓屏幕"能不能信要用
    if _stale:
        p("露出探针    : 窗口最小化/在屏幕外，跳过")
    else:
        try:
            probe = _W.occlusion(hwnd)
            shown, total = probe
            p(f"露出探针    : {shown}/{total} 个点在微信上" + (
                "（微信露在最前面）" if shown * 2 >= total else
                "（**有窗口压在微信上面** —— 这时「抓屏幕」抓到的可能是那个窗口的画面）"))
        except Exception as e:
            p(f"露出探针失败: {e}")

    # 权限级别实测：把"两者必须同一权限级别"从提示变成数字（方向别记反，见文件头的注释）
    try:
        import win32process
        me = _integrity_rid(os.getpid())
        wx_rid = _integrity_rid(win32process.GetWindowThreadProcessId(hwnd)[1])
        p(f"权限级别    : python={_rid_name(me)}  微信={_rid_name(wx_rid)}")
        if me and wx_rid and me < wx_rid:
            hint("python 的权限级别**低于**微信 —— 这正是 UIPI 把按键、粘贴全丢掉的情形"
                 "（机器人能读能判，就是发不出去）。两边都别用管理员最省事："
                 "关掉黑窗口，直接双击 start.bat 重来。")
        elif me and wx_rid and me > wx_rid:
            p("              （python 比微信高：UIPI 只拦「低→高」，所以按键这条路是通的；"
              "不放心就两边都别用管理员。）")
    except Exception as e:
        p(f"权限级别读取失败: {e}")

    if _stale:
        hint("微信是**最小化**状态（窗口被挪到 -32000,-32000）。此时截出来的**不是黑屏而是"
             "最后一帧旧画面**，下面第 6、7、8 节读到的内容可能都是**过时的** —— "
             "先点任务栏把微信恢复出来再跑一次诊断。")
    if rect[2] - rect[0] < 900 and not _stale:
        hint(f"微信窗口只有 {rect[2]-rect[0]} 像素宽，太窄了：消息区/输入框都挤在一起，"
             "建议把窗口最大化。")

    # ---------------- 截图质量 ----------------
    section("[4] 截图质量（这是最要紧的一项）")
    from PIL import ImageStat
    # 四种取法各试一遍，逐个报「拿到的是内容还是空白」，**而且每种都存一张图**。
    # 只存"机器人实际用的那张"看不出是哪条路坏的：客户那台存下来的就是一张灰底图，
    # 光看它只能得出"截图不行"，而真正要知道的是"另外三条路里有没有一条能用的"。
    win_w, win_h = rect[2] - rect[0], rect[3] - rect[1]
    shots = (("BitBlt", _W._capture_bitblt, "1-bitblt"),
             ("PrintWindow", _W._capture_printwindow, "2-printwindow"),
             ("PrintWindow客户端区", _W._capture_printwindow_client, "3-printwindow-client"),
             ("屏幕抓取", _W._capture_screen, "4-screen"))
    for name, fn, tag in shots:
        try:
            im = fn(hwnd, win_w, win_h)
        except Exception as e:
            im = None
            p(f"  {name:22s} → 报错 {e}")
        if im is None:
            p(f"  {name:22s} → 拿不到图")
            continue
        st = _W.content_stats(im)
        blank = _W._looks_blank(im)
        note = ""
        if name == "屏幕抓取" and probe and probe[0] * 2 < probe[1]:
            # 有内容也不能信：抓到的很可能是压在微信上面的那个窗口（客户那份报告里
            # 屏幕抓取的标准差最高，可当时前台根本不是微信 —— 就是这个坑）。
            note = "　← 有内容，但微信被别窗口压着，这条不算数"
        p(f"  {name:22s} → {im.size[0]}x{im.size[1]}  内部标准差 {st['sd']:6.2f}  "
          f"近单色像素 {st['flat']:.3f}  边缘像素 {st['edge']:.4f}  "
          f"{'**空白**（没内容）' if blank else '有内容'}{note}")
        path = os.path.join(HERE, f"diagnose-shot-{tag}.png")
        try:
            im.save(path)
            _files.append(path)
        except Exception as e:
            p(f"      存图失败: {e}")

    img = capture_window(hwnd)
    if img is None:
        p("**capture_window 返回 None：四种截法一张都没拿到**")
        hint("截图全失败 —— 机器人等于瞎了，什么都不会回。")
        return finish(1)
    W, H = img.size
    method = _W.capture_method()
    p(f"机器人用的取法: {method}" + ("" if _W.capture_ok() else "   ← 而且判定为**空白**"))
    p(f"截图尺寸    : {W} x {H}")
    try:
        bright = ImageStat.Stat(img.convert("L")).mean[0]
        p(f"平均亮度    : {bright:.1f} / 255")
    except Exception as e:
        p(f"亮度计算失败: {e}")
    try:
        img.save(OUT_PNG)
        _files.append(OUT_PNG)
        p(f"已存        : {OUT_PNG}（这张就是机器人干活用的那张）")
    except Exception as e:
        p(f"存图失败: {e}")

    if method == "全空白":
        hint("**四种截图办法都只拿到空白图**（窗口底色，没有内容）—— 机器人读不到任何东西，"
             "所以什么都不回。按可能性排：① 这台机器是**远程桌面**，而远程桌面窗口被最小化或"
             "已断开（断开后 Windows 就停止渲染桌面，所有截图都会变空白 —— 云主机上十有八九是这个）；"
             "② 微信**最小化**了；③ 微信窗口被别的窗口完全盖住。"
             "请保持远程桌面窗口开着（别最小化、别断开），微信恢复出来，再跑一次诊断。")
    elif method == "屏幕抓取（疑似被遮挡）":
        hint("BitBlt / PrintWindow 都拿不到内容，只剩「抓屏幕」这条路 —— 但露出探针说明"
             "**微信被别的窗口压着**，抓回来的多半是那个窗口的画面（拿它当画面去 OCR，"
             "机器人会照着别人的界面切群、发消息）。把微信窗口露到最前面（或最大化）再跑一次。")
    elif method == "屏幕抓取":
        hint("只有「抓屏幕」这条路有内容（BitBlt / PrintWindow 在这台机器上都拿不到）—— "
             "机器人能跑，但**微信窗口必须一直露在最前面**：一旦被浏览器之类的窗口挡住，"
             "它读到的就是那个窗口。")
    elif method in ("PrintWindow", "PrintWindow客户端区"):
        hint(f"BitBlt 在这台机器上只能拿到窗口底色，已**自动改用 {method}** 拿到了内容 —— "
             "机器人能跑，不用管它。")

    # ---------------- 布局 ----------------
    section("[5] 布局测量")
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    try:
        sw_px = sidebar_ratio(img)
    except Exception as e:
        sw_px = 0.33
        p(f"像素量法失败: {e}")
    left_px = int(W * sw_px)
    p(f"像素量法    : 侧栏宽度比 {sw_px:.3f}（聊天区从 {left_px} 像素开始）")
    if not (0.2 < sw_px < 0.5):
        hint(f"像素量出来的侧栏宽度比是 {sw_px:.3f}，超出正常范围（0.2~0.5）—— "
             "窗口布局和机器人预期的不一样（可能太窄/太宽），OCR 会读错区域。")

    # 会话列表 OCR（第 6、7 节都用这份结果，所以只 OCR 一次）
    top = int(H * B.TOP_RATIO)
    items = []
    try:
        region = img.crop((0, top, left_px, H))
        up = region.resize((region.width * 2, region.height * 2), 1)
        result, _ = ocr(np.array(up))
        items = sorted((top + sum(q[1] for q in b) / 4 / 2,
                        sum(q[0] for q in b) / 4 / 2,
                        max(q[0] for q in b) / 2,            # 右缘（OCR 图放大过 2 倍）
                        t.strip())
                       for b, t, _ in (result or []))
    except Exception as e:
        p(f"OCR 失败: {e}")
        traceback.print_exc()
        hint(f"OCR 直接报错：{e}（依赖没装全？先用 start.bat 跑一次把依赖装上）")

    # 实测锚：用"时间列"量出真正的聊天区左边界（像素量法会被聊天区的头像/气泡顶到右边）
    anchor = _W.sidebar_left_from_items(items, W, left_px)
    _W.set_sidebar_left(anchor)
    dropped = [it for it in items if it[1] >= anchor]
    p(f"时间列锚定  : 聊天区从 {anchor} 像素开始"
      + ("" if anchor == left_px else f"（像素量法 {left_px}，差 {anchor - left_px:+d}）"))
    p(f"会话列表裁剪区: x 0~{anchor}, y {top}~{H}")
    p(f"聊天标题裁剪区: x {anchor}~{W}, y 0~{int(H * 0.14)}")
    if anchor < left_px:
        p(f"              （像素量法多框进来 {len(dropped)} 项 —— 量宽了会把聊天标题"
          f"{'（就是这一项）' if dropped else ''}当成会话列表里的一行，机器人会去点它）")
    if anchor == left_px:
        hint("这次没能量出『时间列锚』（会话列表里没有时间/日期文本）—— 用的是像素量法，"
             "它在聊天区有头像时会偏右，切群/标题可能读不准。把微信窗口**最大化**再跑一次。")

    # ---------------- 会话列表 OCR 原文 ----------------
    section("[6] 会话列表 OCR 原文（机器人在这里找群名）")
    if not _W.capture_ok():
        p("（注：本次截图判定为**空白**，下面读到的多半是垃圾 —— 先看第 4 节）")
    p(f"共 {len(items)} 项（前面打 ✗ 的落在实测边界右边，机器人会当噪音剔掉）:")
    for y, x, right, t in items:
        p(f"  {'✗' if x >= anchor else ' '} y={int(y):5d} x={int(x):5d} 右缘={int(right):5d}  {t!r}")
    kept = [it for it in items if it[1] < anchor]
    if not items:
        hint("会话列表**一个字都没 OCR 出来**：要么截图是黑的（看第 4 节），"
             "要么微信被别的窗口完全盖住了，要么窗口里没有会话列表。")

    # ---------------- 群名逐个比对 ----------------
    section("[7] 群名比对（配的名字 vs 列表里读到的）")
    for g in groups:
        # 机器人的口径：按 y 从上到下**第一个**认得出的就是它要点的行（不是"最像的那个"）
        hit = next(((y, x, t) for y, x, _r, t in kept if B.GroupBot._name_matches(t, g)), None)
        cands = sorted(((difflib.SequenceMatcher(None, t, g).ratio(), t, _y)
                        for _y, _x, _r, t in kept), reverse=True)
        p(f"  「{g}」→ "
          + (f"认出来了，机器人会点 ({int(hit[1])}, {int(hit[0])})" if hit else "**没认出来**"))
        for r, t, y in cands[:3]:
            p(f"        最像的: {t!r} 相似度 {r:.2f} y={int(y)}")
        if not hit:
            if cands and cands[0][0] > 0.5:
                hint(f"「{g}」没认出来，但列表里有 {cands[0][1]!r}（相似度 {cands[0][0]:.2f}）—— "
                     f"多半是**群名写得不一样**（多个字/少个字/标点或空格不同）。"
                     f"把 groups.txt 里的名字改成列表里显示的那个。")
            else:
                hint(f"「{g}」在会话列表里根本没找到相似的 —— 要么这个群被挤到列表下面去了"
                     f"（在微信里**置顶**它），要么它的名字和配置差太远。")

    # ---------------- 当前聊天标题 ----------------
    section("[8] 当前聊天标题（切群/发送前的校验就靠它）")
    try:
        # 把机器人**实际喂给 OCR 的两条裁剪带**存下来：标题被切字 / 侧栏多出假行，
        # 看这两张图最直接（报告里的文字只是 OCR 的结果，看不出"切在哪儿"）。
        for tag, box in (("sidebar", (0, top, anchor, H)),
                         ("title", (anchor, 0, W, int(H * 0.14)))):
            crop = img.crop(box).resize(((box[2] - box[0]) * 2, (box[3] - box[1]) * 2), 1)
            path = os.path.join(HERE, f"diagnose-crop-{tag}.png")
            crop.save(path)
            _files.append(path)
        title = read_chat_title(ocr, hwnd)
        p(f"read_chat_title() = {title!r}")
        for g in groups:
            p(f"   title_matches({g!r}) = {title_matches(title, g)}")
        if not title.strip():
            hint("标题读出来是空的：说明聊天区标题那一带没有文字。如果微信当前没打开任何会话"
                 "（聊天区显示微信大头占位），这就是正常的；如果明明开着群还读不到，"
                 "看第 5 节的裁剪区是不是量偏了。")
    except Exception as e:
        p(f"标题读取失败: {e}")

    # ---------------- Dify ----------------
    section("[9] Dify 知识库检索")
    try:
        import wechat_group_bot as B
        if B.DIFY_DATASET_ID and B.DIFY_API_KEY:
            kb = B.DifyKB(B.DIFY_BASE_URL, B.DIFY_DATASET_ID, B.DIFY_API_KEY)
            hit = kb.retrieve("发什么快递")
            p(f"检索 '发什么快递' → {'通，召回 ' + str(len(hit.split(chr(10) + '---' + chr(10)))) + ' 条' if hit else '没召回内容'}")
            p(f"接口 {kb.url}")
            p(f"调用 {kb.calls} 次，失败 {kb.fails} 次")
            if kb.fails and not hit:
                hint("Dify 检索不通（机器人会退回只用 knowledge.md）。看看是不是网络/端口变了。")
        else:
            p("没配 Dify（只用 knowledge.md），跳过")
    except Exception as e:
        p(f"检索失败: {e}")
        hint(f"Dify 检索异常：{e}")

    # ---------------- 结论 ----------------
    section("[10] 结论 / 先查这几条")
    if _hints:
        for i, h in enumerate(_hints, 1):
            p(f"  {i}. {h}")
    else:
        p("  没发现明显问题：窗口、截图、布局、群名都对得上。")
        p("  那「发了消息不回复」就得看运行日志 bot.log 里的判定行"
          "（act=reply / ignore / handoff，以及每条回复的发送结果）。")

    return finish(0)


def finish(code):
    p()
    p("-" * 66)
    p(f"报告写到: {OUT_TXT}")
    try:
        with open(OUT_TXT, "w", encoding="utf-8-sig") as f:      # BOM：记事本直接打开不乱码
            f.write("\n".join(_lines) + "\n")
    except Exception as e:
        print(f"[!] 写报告失败: {e}")
    print(f"\n请把这些文件一起发回给开发者：")
    print(f"    {OUT_TXT}")
    for f in _files:
        print(f"    {f}")
    print("（几张 diagnose-shot-*.png 是四种截图取法各自拿到的画面 —— 哪张有微信界面，"
          "哪条路就是这台机器上能用的那条。）")
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        p("诊断脚本自己崩了，把上面这段报错一起发回来。")
        finish(2)
        sys.exit(2)