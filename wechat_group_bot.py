# -*- coding: utf-8 -*-
"""多群轮询机器人：切群点会话列表那一行，不再每次 Ctrl+F 搜索。

基本功能（本文件就是这些）：
  1. 每个周期**只截一次图、只 OCR 一次**左侧会话列表 → {群名: 变化指纹, 点击点}
     （会话列表本来就带着每个会话的最新消息预览，一次就把所有群的情况都拿到了）
  2. 指纹变了（或到了定期全量读的轮次）才点那一行切进去、读聊天区、算增量新消息。
     —— 不搜索；没事的时候完全不碰窗口，有事才切、切完还原你的前台窗口。
  3. 群不在可视会话列表里（滚出可视区、或收进"折叠的群聊"）：退回 Ctrl+F 搜索兜底，
     但只在"定期全量读"那一轮试，不是每次都搜。

自动回复（AI 客服方向）：
  4. **意图判断**（默认开）：一次模型调用就问明白"该不该回 + 回什么"，三个结论：
       reply   → 在咨询我们的业务（知识库里有依据）→ 发出去（只依据知识库，不编）
       ignore  → 群里闲聊、跟我们无关 → 不回（这就是"聊天和我无关"的处理方式）
       handoff → 退款/投诉/要查订单/知识库没有 → 不回，记日志等人工
     判定依据来自知识库：配了 DIFY_* 就每批新消息先去 Dify 检索，把召回的片段附在这次
     提问里；没配就退回"整份塞 knowledge.md"。knowledge.md 始终是**"什么相关"的定义**
     （"服务范围/不做什么"那一节写得越清楚，ignore 判得越准），也是检索挂掉时的兜底。
  5. 每次判定都往 cs_decisions.jsonl 追一行（时间/群/act/理由/回复/发了没），
     复盘时看这个文件，就知道哪些该回没回、哪些不该回瞎回了。
  6. 一批新消息**整批一起理解、只回一句**；同群回复至少隔 3s；同批内容 30s 内不重复回；
     自己的消息不回（不会自己跟自己聊）。回复开头带纯文本 @发送者（昵称猜不准就不带）。

发送走已验证的链路：点会话列表切群（点完确认标题）→ 抢前台 → 点输入框 → 粘贴 →
OCR 验证进输入框 → 回车 → 判定是否真发出去 → 还原前台。

几个必须知道的坑（都在这份里处理了，别再踩）：
  * **微信被别的窗口压住时，点击会被那个窗口收走**，微信毫无反应（连前台窗口都不变）；
    而 BitBlt 截图读的是窗口自己的后台缓冲，照样能 OCR 出正确内容 —— 很容易误判成
    "坐标算错了"。所以切群前先 _ensure_foreground() 把微信抬到前台。
  * 切群是"截图 → OCR 算坐标 → 点击"，中间隔着几百毫秒，期间列表可能重排/滚动，
    所以点完必须 OCR 标题确认；确认不了就重读列表再点（最多 3 次）。
  * 窗口刚切完时标题可能还没重绘（OCR 只读到 '口 X' 这种窗口按钮），要耐心重读，
    别当成"点歪了"就反复重点。
  * 会话列表的宽度不能写死：写窄了会把别的会话的预览当成聊天消息读进来。
    用 sidebar_ratio() 按底色（侧栏偏灰、聊天区纯白）现量。

用法：
  python wechat_group_bot.py                      # 轮询配置里的群 + 意图判断 + 自动回复
  python wechat_group_bot.py --config 别的.json    # 换一份部署配置（换机器/换群用这个）
  python wechat_group_bot.py --dry-run --probe-reply "在吗|怎么联系你们"
                                                  # 调提示词/知识库用：只跑模型，完全不碰窗口
                                                  # 配了 DIFY_* 时连检索一起跑，测知识库就靠这条
  python wechat_group_bot.py --dry-run            # 真轮询，但算出回复只打印不发送
  python wechat_group_bot.py --no-reply           # 只监控打印，不回复
  python wechat_group_bot.py --no-intent --reply-groups "ai测试2群"
                                                  # 关掉意图判断：别人说什么都回（只联调用）
  python wechat_group_bot.py --probe "自测文本"    # 自测：自己发一条，验证"发送→发现"闭环
  python wechat_group_bot.py --echo               # 不调模型，回固定文案（联调用）
  python wechat_group_bot.py --test-handoff "我要退款|订单号发你了"
                                                  # 自测：直接往企微表格写一行转人工样例
  python wechat_group_bot.py --duration 60        # 跑 60 秒后退出

配置（**config.json 优先**，配置里没写的用文件头这些默认值；命令行参数最高）：
  groups / reply_groups  要监控哪些群、哪些群自动回复 —— 部署时只改这个文件，别改代码
  AUTO_REPLY          哪些群自动回复（其余只监控）
  KNOWLEDGE_FILE      业务知识库 knowledge.md —— 兜底依据，也是"相关性"的定义
  DIFY_*              Dify 知识库检索（填了就不再整份塞提示词；留空则关，行为不变）
  WECOM_*             转人工推送到企微智能表格（填了每次 handoff 写一行给人跟进；留空只记日志）
  DECISION_LOG        判定日志 cs_decisions.jsonl，复盘用
  INTENT_PROMPT       意图判断提示词（act=reply/ignore/handoff 的规则）
  REPLY_SYSTEM_PROMPT --no-intent 模式用的提示词
  MIN_REPLY_GAP / DEDUPE_WINDOW / CTX_BEFORE / CTX_MAX_NEW   节奏和上下文窗口

后续要补的细节（没做，按需再加）：
  - handoff 推了表格**不等于有人被叫醒**：表里躺着没人看还是会漏。要真做到"有人被通知"，
    下一步是给负责人发条企微私聊（应用消息 /appchat/send 或群机器人），表格当账本。
  - 检索结果是附在**提问**里的，不是系统提示词里（GeminiClient 的系统提示词每个群第一次
    调用后就固化进历史了，改不动，见 wechat_bot.GeminiClient.chat）；副作用是每个群的
    对话历史会越攒越大，跑久了要给 _histories 加截断
  - 只测了群聊；1:1 私聊客户（客服的主战场）用同一套即可，把客户名加进 GROUPS 就行
  - 昵称识别更准（现在沿用"最靠左的文本 = 昵称"的猜法，会把别人的正文归错人）
"""
import argparse
import difflib
import io
import json
import os
import re
import sys
import time

# 版本闸门：依赖里的 OCR 引擎 rapidocr-onnxruntime 声明 Requires-Python <3.13，
# 在 3.13/3.14/3.15 上**根本没有可装的包**（报 "No matching distribution found"，
# 看着像网络问题）。在这儿拦一句人话，别让它在 import numpy 那里抛 traceback。
# 将来要是换成新版 OCR 包（支持 3.13+），记得把这段一起去掉。
if sys.version_info[:2] not in ((3, 10), (3, 11), (3, 12)):
    print(f"[X] 当前 Python {sys.version.split()[0]} 跑不了这个机器人。")
    print("    依赖里的 OCR 引擎只支持 Python 3.10~3.12（本机验证过的是 3.11）。")
    print("    装一个 Python 3.11：python.org → 页面找 'Looking for a specific release?' → "
          "Python 3.11.x → 'Windows installer (64-bit)'，")
    print("    安装时勾上 Add python.exe to PATH。装完把 .venv 删掉，再双击 start.bat。")
    sys.exit(1)

import numpy as np
import pyperclip
import requests
import uiautomation as auto
import win32con
import win32gui
from rapidocr_onnxruntime import RapidOCR

from wechat_bot import (GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL, INPUT_CLICK_Y_RATIOS,
                        GeminiClient, Message, _TIME_LIKE, capture_method, capture_ok,
                        capture_summary, capture_window, click_at, content_stats,
                        find_wechat_hwnd, force_foreground, input_box_has, input_box_top,
                        looks_sent, read_chat_title, send_key, set_capture_override,
                        set_sidebar_left, sidebar_left, sidebar_left_from_items,
                        sidebar_ratio, title_matches)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

GROUPS = ["ai测试2群", "视频号，剪映，微企"]

# 每次改完 .py 往上加一位。启动日志和诊断报告都会打出来 ——
# 远程排查的**第一件事**就是确认客户跑的到底是哪一版（来回改了几轮之后，
# "我改的没生效"和"改了还是不行"从日志上长得一模一样，白跑好几趟）。
BUILD = "2026-10-16.3"

# ============ 意图判断（AI 客服的核心）============
# 默认开着：只回"需要我们（客服）回答的"，群里跟我们无关的闲聊不回。
# 关掉（--no-intent）就退回"别人说什么都回"，只适合联调。
INTENT_ON = True
KNOWLEDGE_FILE = "knowledge.md"          # 业务知识库，存在就塞进提示词给模型当依据
DECISION_LOG = "cs_decisions.jsonl"      # 每次"回/不回"的判定记一行，方便复盘、调提示词

# ============ Dify 知识库检索（可选）============
# knowledge.md 整份塞进提示词大概 4000 字就到头了；知识库比这大就只能靠检索：
# 每批新消息先去 Dify 要最相关的几条，附在这次提问里。
# 两个值都在 Dify 里拿：知识库 → 右上角「API」→ 创建密钥（dataset id 是 URL /datasets/<这串> 的 uuid）。
# 留空 = 不启用，行为跟以前一模一样（还是整份读 KNOWLEDGE_FILE）。
# 注意端口：80 端口那台的 nginx 没有 /v1 转发（访问 http://115.29.198.190/v1/... 是 404），
# 能用的就是 Dify 自己的 8081。代码里会自己接上 /v1/datasets/...，所以这里写到端口为止。
DIFY_BASE_URL = "http://115.29.198.190:8081"
DIFY_DATASET_ID = "5f88035a-b79c-4863-8300-7cf069ee2dd3"   # 常见咨询问题汇总.xlsx（4032 字，1 个文档，已完成索引）
DIFY_API_KEY = "dataset-oxwB9j61p7MyVxwqEI0rwGTF"
DIFY_QUERY_MAX = 240        # 接口限制 query 最多 250 字，留点余量
DIFY_CACHE_TTL = 30.0       # 同一段提问 30s 内不重复检索（和 DEDUPE_WINDOW 一个用途）
DIFY_TIMEOUT = 15            # 秒；检索超时当这次没有结果，不能拖住回复（客户网络实测 8s 会掐，放宽到 15）

# ============ 转人工（handoff）推送到企业微信智能表格（可选）============
# 判 handoff 的消息现在只记 cs_decisions.jsonl，**没人会被通知** —— 得靠人自己翻日志。
# 填了下面这个地址，每次 handoff 就往企微表格写一行，人打开表就知道要跟谁。
#
# 怎么拿地址（不用建自建应用、不用 corp secret、不用管理员开权限）：
#   企微文档 → 新建/打开一个**智能表格** → 工作表右上角「…」→ 接收外部数据 → 开启
#   → 复制那个 webhook 地址（形如 https://qyapi.weixin.qq.com/cgi-bin/wedoc/smartsheet/webhook?key=xxxx）
# 同一个页面会给你一段「示例数据」，里面的 values 的 key 就是各列的**字段 ID**（不是列名！），
# 抄到 WECOM_FIELDS 里。留空 = 不推送，行为和以前一模一样。
WECOM_WEBHOOK = ""
WECOM_FIELDS = {            # 机器人里的名字 → 表格里的字段 ID
    "time": "",             # 判定时间（文本列）
    "group": "",            # 群名（文本列）
    "msgs": "",             # 客户消息原文，一批拼成一行（文本列）
    "reason": "",           # 为什么转人工（文本列）
    "status": "",           # 状态：默认写「待处理」（可留空不写）
}
WECOM_STATUS_NEW = "待处理"   # status 列的新值；人跟进完自己改成「已处理」
WECOM_SELECT_FIELDS = ("status",)   # 这几个字段若在表里是「单选」，写入要用 Option 结构
WECOM_DATE_FIELDS = ("time",)       # 这几个字段若在表里是「日期」，写入要传字符串（不是 CellTextValue）
WECOM_TIMEOUT = 6           # 秒；推送超时只记一行日志，不重试、不拖住回复
WECOM_DEDUPE = 600.0        # 同一批内容 10 分钟内不重复推（同一批被判两次时只留一行）

# ============ 部署配置 config.json（可选）============
# 换一台机器、换一批群，只应该改**数据**、不该改代码 —— 群名就写在这里，
# 免得部署时把群名、Dify 密钥散在 .py 里改漏。文件不存在就用上面那些默认值（行为不变）。
# 优先级：命令行参数 > config.json > 本文件头部的默认值。
#
# config.json 长这样（键都可以省，省的用默认值）：
# {
#   "groups": ["ai测试2群", "视频号，剪映，微企"],     // 要监控的群名，必须和会话列表里显示的**一模一样**
#   "reply_groups": ["ai测试2群"],                      // 只有这些群自动回复（不在表里的只监控）
#   "auto_reply": {"ai测试2群": true},                  // 想逐群精确指定就用这个（比 reply_groups 优先）
#   "interval": 3.0,                                    // 几秒读一次会话列表
#   "full_scan_every": 5,                               // 每隔几轮把每个群全文读一次（预览被截断的兜底）
#   "capture": "auto",                                  // 截图取法，一般不用写：默认自动挑
#                                                       // （bitblt/printwindow/printwindow-client/screen）
#   "knowledge_file": "knowledge.md",
#   "decision_log": "cs_decisions.jsonl",
#   "dify": {"base_url": "http://115.29.198.190:8081",
#            "dataset_id": "5f88035a-...", "api_key": "dataset-..."},
#   "wecom": {"webhook": "https://qyapi.weixin.qq.com/cgi-bin/wedoc/smartsheet/webhook?key=...",
#             "fields": {"time": "字段ID", "group": "字段ID", "msgs": "字段ID",
#                        "reason": "字段ID", "status": "字段ID"}}
# }
CONFIG_FILE = "config.json"
GROUPS_TXT = "groups.txt"      # 群名的"傻瓜版"配置：一行一个群名，改这个不用碰 JSON


def _read_groups_txt(path=GROUPS_TXT):
    """从纯文本读群名（一行一个，# 开头是注释）。

    为什么留这么一个入口：config.json 是 JSON，少个逗号、多个引号就整份失效，
    交给别人用的时候最容易卡在这。改一个纯文本文件，怎么改都不会把配置改坏。
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = [l.strip() for l in f]
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[!] 读 {path} 失败: {e}")
        return []
    return [l for l in lines if l and not l.startswith("#")]


def load_config(path):
    """读部署配置；文件不在/读坏了就返回 {}，一切照代码里的默认值走（不能让配置把人拦在门外）。"""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("配置文件的根应该是一个 JSON 对象")
        return cfg
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[!] 读 {path} 失败，改用代码里的默认配置: {e}")
        return {}


def apply_config(path=CONFIG_FILE):
    """把配置覆盖到本模块的全局变量上。返回 (配置, 提示行列表)。

    必须在建 GroupBot **之前**调用 —— 那些对象是在 __init__ 里读这些全局变量的
    （kb / notifier 都是"配了才建"），晚一步就不会生效。
    """
    cfg = load_config(path)
    notes = []
    gtxt = _read_groups_txt()
    if not cfg and not gtxt:
        return cfg, notes

    global GROUPS, AUTO_REPLY, KNOWLEDGE_FILE, DECISION_LOG
    global DIFY_BASE_URL, DIFY_DATASET_ID, DIFY_API_KEY
    global WECOM_WEBHOOK, WECOM_FIELDS, WECOM_STATUS_NEW

    from_file = False
    if cfg.get("groups"):
        GROUPS = [str(g).strip() for g in cfg["groups"] if str(g).strip()]
        from_file = True
        notes.append(f"群名 {len(GROUPS)} 个（来自 {path}）")
    if gtxt:                       # groups.txt 优先于 config.json（谁简单谁说了算）
        GROUPS = gtxt
        from_file = True
        notes.append(f"群名 {len(GROUPS)} 个（来自 {GROUPS_TXT}，一行一个）: {GROUPS}")
    # 默认：**配置里列出来的群，全都既监控又回复**（要少回哪个就显式关掉）
    #   auto_reply 里写 false 的群 > reply_groups 白名单 > 都不写=全回
    # 注意 auto_reply 是**覆盖**不是白名单：{"群B": false} 只关 群B，别的群照回。
    # （早期写成白名单，结果"只关 B"把没列出的群也一并关哑了 —— 部署时这种静默失效最难查。）
    if isinstance(cfg.get("auto_reply"), dict):
        off = {str(k).strip() for k, v in cfg["auto_reply"].items() if not v}
        AUTO_REPLY = {g: (g not in off) for g in GROUPS}
        AUTO_REPLY.update({str(k).strip(): bool(v) for k, v in cfg["auto_reply"].items()})
        notes.append(f"自动回复: 全开，只关 {sorted(off)}" if off else "自动回复: 全开")
    elif cfg.get("reply_groups") is not None:
        on = {str(g).strip() for g in cfg["reply_groups"]}
        AUTO_REPLY = {g: (g in on) for g in GROUPS}
        notes.append(f"自动回复只开这些群: {sorted(on) or '（无，全都不回）'}")
    elif cfg.get("groups") or from_file:
        AUTO_REPLY = {g: True for g in GROUPS}
        notes.append("自动回复: 全部开（列出的群都回）")
    if cfg.get("knowledge_file"):
        KNOWLEDGE_FILE = str(cfg["knowledge_file"])
    if cfg.get("decision_log"):
        DECISION_LOG = str(cfg["decision_log"])
    if cfg.get("capture"):
        notes.append(f"截图取法钉死为 {set_capture_override(cfg['capture'])}"
                     f"（排错用：bitblt / printwindow / printwindow-client / screen；"
                     f"不写=自动挑）")

    d = cfg.get("dify") or {}
    if isinstance(d, dict):
        DIFY_BASE_URL = str(d.get("base_url") or DIFY_BASE_URL)
        DIFY_DATASET_ID = str(d.get("dataset_id") or DIFY_DATASET_ID)
        DIFY_API_KEY = str(d.get("api_key") or DIFY_API_KEY)

    w = cfg.get("wecom") or {}
    if isinstance(w, dict):
        WECOM_WEBHOOK = str(w.get("webhook") or WECOM_WEBHOOK)
        if w.get("status_new"):
            WECOM_STATUS_NEW = str(w["status_new"])
        if isinstance(w.get("fields"), dict):
            WECOM_FIELDS = {**WECOM_FIELDS, **{k: str(v) for k, v in w["fields"].items()}}

    # 群名写错是部署时最常见的错（OCR 认的是会话列表里显示的名字，一字不差），启动时先提醒
    off_groups = [g for g in GROUPS if not AUTO_REPLY.get(g, False)]
    if off_groups:
        notes.append(f"[i] 这些群只监控、不自动回复: {off_groups}")
    return cfg, notes
# 注意：检索方式/权重/TopK/Rerank **不在这里配**，全部沿用你在 Dify 知识库「检索设置」
# 里配的那套（请求里不传 retrieval_model，Dify 就用知识库自己的设置）—— 只在一个地方调，
# 免得两边不一致。Score 阈值同理：没配 Rerank 就别设，会误杀。
INTENT_PROMPT = """你是微信群里的客服。下面给你一段群聊记录（每行"某某: 内容"，"我"是你自己），
最后几行是**刚来的新消息**。先判断：这批新消息是不是**需要你（客服）来处理**的，然后只输出一个 JSON：

{"act": "reply" | "ignore" | "handoff", "reply": "回复内容", "reason": "一句话理由"}

判断规则：
- act=reply：在咨询/求助我们的产品、价格、流程、怎么用、怎么报名、能不能做…… 需要给出回复。
  reply 里写回复正文：像群里随口接话，50 字以内，只依据下面的知识库，不要编造。
- act=ignore：纯闲聊、打招呼、表情、别人之间聊天、跟我们业务无关的话题 → reply 留空字符串 ""。
- act=handoff：退款/投诉/纠纷/要查具体订单、涉及隐私或钱、知识库里没有答案 → reply 留空字符串 ""。
- 拿不准就 handoff（不回），别硬答、别编。
- 只输出 JSON 本身，不要解释、不要 markdown 代码块。

知识库（回答只能依据这里，没有的就 handoff）：
{kb}
"""
# ============ 自动回复配置 ============
# 哪些群要自动回复（True=回，False/不在表里=只监控不回）。
# **配置里列出的群默认全都回**（config.json 不写 reply_groups 就是这个效果）；
# 这张表只是 config.json 不存在时的兜底，要单独关某个群就在 config.json 里配 auto_reply。
AUTO_REPLY = {
    "ai测试2群": True,
    "视频号，剪映，微企": True,
}
# 回复用的系统提示词（和 wechat_bot.py 的那份分开，改这边不影响老 bot）
REPLY_SYSTEM_PROMPT = """你是微信群里的一个人，用中文聊天。规则：
1. 给你的是一段群聊记录（每行"某某: 内容"，"我"是你自己），最后几行是刚发来的新消息。
   对方经常连着发好几条，要**整批一起理解**，然后只回**一句**
2. 像群里随口接话，不要正式、不要客套；50 字以内，一句话为主
3. 对方只是随口一说、没什么可接的，就回一句很短的反应
4. 不要自我介绍，不要提"AI/助手/模型"，不要发链接、电话、二维码
5. 看不懂或不适合接的话，就回一个很短的语气词
6. 注意别重复你自己（"我"）已经说过的话"""
MIN_REPLY_GAP = 3.0        # 同一个群两条回复之间至少隔几秒，别刷屏
DEDUPE_WINDOW = 30.0       # 同一批内容 30s 内只回一次（OCR 重复读到同一行时不重复回）
SEARCH_RETRY_GAP = 60.0    # 同一个群"搜索兜底"最多 60s 试一次（搜索要抢前台打字，别每轮都搜）
CTX_BEFORE = 4             # 给模型看的新消息前面几条旧消息（理解"在不？"这种短句用）
CTX_MAX_NEW = 8            # 一次最多把多少条新消息喂给模型

TOP_RATIO = 0.08        # 这条线以下才算会话列表（上面是搜索框/标题栏）
CHAT_BOTTOM = 0.88      # 聊天消息区下边界（下面就是输入框）
ROW_HEIGHT = 0.07       # 会话列表一行的最大高度（名字+预览），用来切行
# 聊天区里不是消息的行：输入框占位符、"发送"按钮、日期分隔条
JUNK_LINE = re.compile(
    r'^(发送|按住鼠标.*|星期[一二三四五六日天]|昨天|今天|前天|上午|下午|中午|晚上)$')


def _flat(s):
    """去掉空白，OCR 比对时用（换行、空格噪声都不算差异）"""
    return re.sub(r'\s+', '', s or '')


class _Tee:
    """把写到 stdout 的东西同时写一份进日志文件。

    为什么不用 Windows 上的管道 tee（`... | powershell Tee-Object`）：那会把输出按控制台的
    代码页重新解码，中文进日志就是乱码 —— 而这份日志正是出问题时唯一能发给别人看的东西。
    自己写一份两份都保真。
    """

    def __init__(self, stream, fh):
        self._stream, self._fh = stream, fh

    def write(self, s):
        self._stream.write(s)
        try:
            self._fh.write(s)
            self._fh.flush()
        except Exception:
            pass          # 日志写不动不能影响机器人本身
        return len(s)

    def flush(self):
        try:
            self._stream.flush()
            self._fh.flush()
        except Exception:
            pass

    def __getattr__(self, name):        # encoding / isatty 之类直接透传
        return getattr(self._stream, name)


def setup_log(path, max_mb=5):
    """开日志：输出同时进文件（默认 bot.log）。返回文件对象（关不掉就算了）。

    跑在别人机器上、没人盯着的时候，这份日志就是唯一的现场。
    超过 max_mb 就把旧的转成 .1（免得跑几个月把盘写满）。
    """
    if not path:
        return None
    try:
        if os.path.exists(path) and os.path.getsize(path) > max_mb * 1024 * 1024:
            os.replace(path, path + ".1")
        fh = open(path, "a", encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"[!] 打不开日志 {path}（继续跑，只是没日志）: {e}")
        return None
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)
    print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动（日志 {path}）=====")
    return fh


class ChatState:
    """一个群的轮询状态"""
    def __init__(self, name):
        self.name = name
        self.preview = None    # 会话列表里的预览，变了才切进去读
        self.lines = []        # 上次读到的消息正文，用于算增量
        self.visible = None    # 上次有没有在会话列表里看到（只在状态变化时提示，不刷屏）


class DifyKB:
    """Dify 知识库检索（POST /v1/datasets/{id}/retrieve）：给一段提问，返回召回片段拼成的文本。

    两条设计原则，别改：
      * **不传 retrieval_model** —— 检索方式/权重/TopK/Rerank 全用你在 Dify 知识库
        「检索设置」里配的那套。请求里不传，Dify 就按知识库自己的设置检索。
      * **出任何问题都返回 None**：没配、超时、401、"索引还没建完"、没有 embedding 模型，
        一律当这次没检索到，调用方照旧用 knowledge.md 兜底。知识库抖动不能把客服机器人
        整个搞哑 —— 它是来帮忙的，不是新的单点故障。
    """

    def __init__(self, base_url, dataset_id, api_key, timeout=DIFY_TIMEOUT):
        self.url = f"{base_url.rstrip('/')}/v1/datasets/{dataset_id}/retrieve"
        self.api_key = api_key
        self.timeout = timeout
        self._cache = {}        # 提问 -> (时间, 结果)，命中和空结果都缓存
        self.calls = 0          # 真发出去的检索次数（空结果/失败的不算）
        self.fails = 0

    def __str__(self):
        return (f"Dify 知识库检索已开（检索 {self.calls} 次"
                f"{f'，失败 {self.fails} 次' if self.fails else ''}）")

    def retrieve(self, query):
        """返回召回片段拼成的文本；没命中或失败返回 None"""
        # 接口限制 query 最长 250 字。**从末尾截断** —— 客户刚说的那句最重要，
        # 前面那些是上下文，丢了不致命；反过来把最新的话截掉就白检索了。
        query = (query or "").strip()[-DIFY_QUERY_MAX:]
        # **先洗掉反斜杠和双引号**：Dify 1.17 的 /retrieve 解析不了 JSON 里的转义，
        # 查询里只要出现这两个字符就直接 400（invalid_param: invalid Unicode escape）。
        # 实测 a\b、a"b 都拒；换行、emoji、tab 都没事。
        # 群里发文件路径、代码、带引号的话很常见，不洗掉这一批就检索不到 ——
        # 机器人会退回 knowledge.md 兜底，答得差、还容易误判成 handoff。
        # 反正只是拿去检索，少俩引号不影响召回。
        query = re.sub(r'[\\"]', ' ', query)
        if not query.strip():
            return None

        now = time.time()
        cached = self._cache.get(query)
        if cached and now - cached[0] < DIFY_CACHE_TTL:
            return cached[1]

        texts = None
        try:
            resp = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json={"query": query},
                timeout=self.timeout,
            )
            self.calls += 1
            resp.raise_for_status()
            texts = self._render(resp.json().get("records") or [])
        except Exception as e:
            self.fails += 1
            print(f"    [!] Dify 检索失败（这次不带知识库，用 knowledge.md 兜底）: {e}")

        self._cache[query] = (now, texts)
        if len(self._cache) > 500:          # 群里聊一天能攒几千条，别让它无限涨
            for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:250]:
                self._cache.pop(k, None)
        return texts

    @staticmethod
    def _render(records):
        """把召回的片段拼成给模型看的一段文本。

        问答型知识库（Excel 里"问题/答案"两列那种）片段带 answer 字段，
        要把它并进去 —— 不然模型只看到问题、看不到答案。
        """
        out = []
        for r in records:
            seg = r.get("segment") or {}
            content = (seg.get("content") or "").strip()
            answer = (seg.get("answer") or "").strip()
            if content and answer and answer not in content:
                content = f"{content}\n{answer}"
            if content:
                out.append(content)
        return "\n---\n".join(out) or None


class HandoffNotifier:
    """把「转人工」写进企业微信智能表格，给人一份能跟进的清单。

    走智能表格的**「接收外部数据」webhook**，不走应用 API：应用那条路要自建应用、
    要管理员在「协作-文档-API」里把应用加成可调用应用、还要应用是文档协作者，
    三处配置少一处就 401/无权限；webhook 只要在表格里点一下开关，密钥永远不过期。

    两条设计原则（和 DifyKB 一样，别改）：
      * **出任何问题都返回 False，不抛异常**：表格是给人看的账本，它挂了不能把
        客服机器人带停 —— 日志里永远还有 cs_decisions.jsonl 这份底账。
      * **同一批内容 10 分钟内不重复推**：同一批消息被判定两次（OCR 重复读到、
        重试链路）时，表格里只留一行，免得人看到一堆重复待办。
    """

    def __init__(self, webhook, fields, timeout=WECOM_TIMEOUT,
                 dedupe=WECOM_DEDUPE, select_fields=WECOM_SELECT_FIELDS,
                 date_fields=WECOM_DATE_FIELDS):
        self.url = webhook
        self.fields = {k: v for k, v in (fields or {}).items() if v}
        self.timeout = timeout
        self.dedupe = dedupe
        self.select_fields = set(select_fields or ())
        self.date_fields = set(date_fields or ())
        self._last = {}          # (群名, 消息) -> 上次推送时间
        self.pushed = 0
        self.fails = 0

    def __str__(self):
        return (f"转人工推送企微表格已开（已推 {self.pushed} 条"
                f"{f'，失败 {self.fails} 次' if self.fails else ''}）")

    def _cell(self, key, value):
        """按表格里那一列的类型拼单元格值。

        三种走法，对应表格里三种列：
          * 文本列：Object[]（CellTextValue）
          * 「单选」列：Object[]（Option）—— 漏掉这个区分就写不进去，接口只认 Option 的 text/style
          * 「日期」列：**直接传字符串**（"yyyy-mm-dd hh:mm:ss" 或毫秒时间戳），
            套上 CellTextValue 会被当成非法值拒掉
        """
        if key in self.date_fields:
            return value
        if key in self.select_fields:
            return [{"text": value}]
        return [{"type": "text", "text": value}]

    def push(self, group, msgs, reason, now=None):
        """写一行「待人工跟进」。成功返回 True。"""
        text = " / ".join(m.text for m in msgs)[:500]     # 单元格有长度上限，长批截断
        key = (group, text)
        now = time.time() if now is None else now
        last = self._last.get(key)
        if last and now - last < self.dedupe:
            print(f"    [i] 同样这批刚推过表格（{int(now - last)}s 前），不重复推")
            return False

        vals = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "group": group,
                "msgs": text, "reason": reason, "status": WECOM_STATUS_NEW}
        values = {self.fields[k]: self._cell(k, v)
                  for k, v in vals.items() if k in self.fields}
        if not values:
            print("    [!] WECOM_FIELDS 一个字段 ID 都没填，跳过推送")
            return False

        try:
            resp = requests.post(self.url, json={"add_records": [{"values": values}]},
                                 timeout=self.timeout)
            data = resp.json()
            if data.get("errcode") not in (0, None):
                raise RuntimeError(f"errcode={data.get('errcode')} {data.get('errmsg')}")
            self.pushed += 1
            self._last[key] = now
            print("    [OK] 已写进企微表格（待人工跟进）")
            return True
        except Exception as e:
            self.fails += 1
            print(f"    [!] 写企微表格失败（不影响回复，日志里还有记录）: {e}")
            return False


class GroupBot:
    def __init__(self, groups, interval=3.0, full_scan_every=5, search_fallback=True,
                 reply=True, auto_reply=None, dry_run=False, use_at=True, intent=INTENT_ON):
        self.targets = {name: ChatState(name) for name in groups}
        self.interval = interval
        # 每隔几轮强制把每个群都点进去读一次全文。
        # 会话列表的预览是**截断**显示的（同一人连发两条前缀相同的消息，可见文本一模一样），
        # 光靠预览指纹会漏；定期全量读兜底。设 1 = 每轮都全量读。
        self.full_scan_every = full_scan_every
        # 群滚出可视会话列表（会话列表就那么几行）时，是否退回 Ctrl+F 搜索去找。
        # 只在"定期全量读"那一轮试，不是每次都搜 —— 搜索要抢前台打字，能不用就不用。
        self.search_fallback = search_fallback
        # 自动回复：reply 总开关；auto_reply 是每个群的开关（默认用文件头的 AUTO_REPLY）
        self.reply = reply
        self.auto_reply = dict(AUTO_REPLY if auto_reply is None else auto_reply)
        self.dry_run = dry_run        # True = 只算出回复并打印，不真发（调试用）
        self.use_at = use_at          # 回复时是否在开头带上 @发送者（纯文本，不是真 @）
        # 意图判断：开着就只回"需要我们回答的"，无关闲聊不回（AI 客服的底线）
        self.intent = intent
        self._knowledge = None
        # Dify 知识库检索：配了 DIFY_* 才建，没配就是 None（decide 里按 None 走老路）
        self.kb = (DifyKB(DIFY_BASE_URL, DIFY_DATASET_ID, DIFY_API_KEY)
                   if DIFY_DATASET_ID and DIFY_API_KEY else None)
        # 转人工推送企微表格：配了 webhook 才建，没配就别推（行为和以前一样）
        self.notifier = (HandoffNotifier(WECOM_WEBHOOK, WECOM_FIELDS)
                         if WECOM_WEBHOOK else None)
        self._client_intent = None    # 意图/客服模式用的 client（提示词带知识库）
        self._client_plain = None     # --no-intent 用的 client（回一切，仅联调）
        self.gemini = GeminiClient(GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL,
                                   system_prompt=REPLY_SYSTEM_PROMPT)
        self._last_reply_at = {}      # 群名 -> 上次回复时间
        self._last_replied = {}       # 群名 -> (回复过的内容, 时间)，防重复回同一条
        self._last_search = {}        # 群名 -> 上次"搜索兜底"的时间（同一个群别每轮都去搜）
        # 下面三个是给"读不到群"时打诊断用的：光说一句"不在会话列表可视区"没法定位问题
        self._side_texts = []         # 上一次会话列表 OCR 到的文本（原样，给人看）
        self._side_ratio = None       # 量出来的侧栏宽度比
        self._side_size = None        # 截图尺寸
        self.cycle = 0
        self.ocr = RapidOCR()
        self.hwnd = find_wechat_hwnd()
        if not self.hwnd:
            raise RuntimeError("没找到微信窗口")
        self._win = auto.ControlFromHandle(self.hwnd)
        self._prev_fg = None   # 轮询周期开始时的前台窗口，周期结束还原

    # ================= 会话列表 =================
    @staticmethod
    def _name_matches(ocr_text, target):
        """OCR 出来的这一行是不是目标群名。**这里必须严**，别调松。

        会话列表里会同时存在名字很像的群 —— 实测这台机器上就有「测试群」「测试群2」「ai测试2群」
        三个。原来用 `ratio >= 0.6`，而 测试群2 和 ai测试2群 的相似度算出来是 **0.8**（只差
        一个 ai 前缀加一个 2 的位置），于是匹配到隔壁群、点开的是 测试群2 —— 标题校验正确地
        报了"点到的不是 ai测试2群"，然后每轮都退到 Ctrl+F 搜索兜底（慢，还每次抢前台打字）。
        所以：模糊匹配要"比值够高（0.9）+ 长度差不超过 1 个字"，宁可认不出（退回搜索）也别认错。
        """
        t = _flat(ocr_text)
        if len(t) < 2 or t == "搜索":
            return False
        # 只认"整名读出来了"（后面可能跟 OCR 当噪声读出来的图标，如 ①），
        # 以及"少读了一个字"（比值 ≥0.9 且长度差 ≤1）。
        # 不认"行比目标长一个字"这种情况 —— 「测试群2」对「测试群」就是它，
        # 认错了会把回复发到别的群；认不出的代价只是退回 Ctrl+F 搜索（慢一点）。
        if target in t:
            extra = t.replace(target, "", 1)
            if not re.search(r'[\w一-鿿]', extra):
                return True
        return (difflib.SequenceMatcher(None, t, target).ratio() >= 0.9
                and abs(len(t) - len(target)) <= 1)

    def _window_minimized(self):
        """微信是不是最小化/挪到屏幕外了。

        必须拦：最小化时 BitBlt 拿回来的不是黑屏，而是**最后一帧的旧画面**
        （实测：窗口矩形 -32000,-32000，截图却还是 1928x1041、亮度 214、OCR 也读得出字）。
        拿旧画面干活比读不到更危险 —— 昨天/上一条消息可能被当成"新消息"回出去。
        """
        try:
            r = win32gui.GetWindowRect(self.hwnd)
            return bool(win32gui.IsIconic(self.hwnd)) or r[0] <= -30000 or r[1] <= -30000
        except Exception:
            return False

    def check_capture(self):
        """启动自检：先截一张，把"机器人到底看不看得见"打在日志最前面。返回是否看得见。

        这一行是给**远程排查**用的：客户的第一反应是"机器人没反应"，而最省事的定位
        就是看这一行 —— 用的哪种取法、内部标准差/近单色占比多少、其它取法都是什么情况、
        用"抓屏幕"时微信有没有露在最前面。看不到这几项，就只能一遍遍来回问。
        """
        # 窗口太窄时微信会把会话列表压成一条窄栏（OCR 一个群都认不出），先提醒最大化。
        # 不替用户最大化（那也是"动他的窗口"），只把话说清楚 —— README 里也写了这一条。
        try:
            l, t, r, b = win32gui.GetWindowRect(self.hwnd)
            w, h = r - l, b - t
            if w < 1000 or h < 700:
                print(f"[!] 微信窗口只有 {w}x{h}，太窄了：会话列表会被压成窄条，机器人认不出群。"
                      "请把微信窗口**最大化**再启动（README 第 4 步也写了这一条）。")
        except Exception:
            pass
        img = capture_window(self.hwnd)
        if img is None:
            print("[截图] **四种截法全部失败**（一张都没拿到）：机器人等于瞎的，什么都不会回。"
                  "先确认微信已登录、窗口没最小化，再跑一次 诊断.bat。")
            return False
        st = content_stats(img)
        ok = capture_ok()
        print(f"[截图] 取法={capture_method() or '？'}  内部标准差 {st['sd']:.1f}  "
              f"近单色像素 {st['flat']:.2f}  边缘像素 {st['edge']:.4f}"
              f"  → {'可以干活' if ok else '**拿不到画面**'}")
        print(f"[截图] 各路情况: {capture_summary()}")
        if ok and capture_method() == "屏幕抓取":
            print("[截图] 用的是「抓屏幕」：**微信窗口必须一直露在最前面**（别让浏览器、"
                  "文件夹挡住它），否则读到的会是压在它上面的那个窗口。")
        if not ok:
            print("[截图] **机器人等于瞎的，屏幕上读到的一切都不可信**，所以它什么都不做、"
                  "什么都不回。按可能性排：① 微信最小化了；② 微信被别的窗口完全盖住；"
                  "③ 这台是远程桌面，而远程窗口被最小化或已断开（断开后 Windows 停止渲染"
                  "桌面，截出来全是空白 —— 云主机上十有八九是这个）。"
                  "处理好再启动；或跑一次 诊断.bat，把报告和那几张图发回来。")
        return ok

    def read_sidebar(self):
        """一次截图 + 一次 OCR，返回 {群名: {"preview": 预览, "click": (x, y) 窗口内偏移}}"""
        img = capture_window(self.hwnd)
        if img is None or not capture_ok():
            # 截图是空白（拿到的只有窗口底色）= 后面 OCR 出来的全是垃圾。
            # 直接当"一个群都没看到"，别拿着垃圾去点群、发消息。
            return {}
        W, H = img.size
        top = int(H * TOP_RATIO)
        sw = sidebar_ratio(img)
        left = int(W * sw)
        region = img.crop((0, top, left, H))
        up = region.resize((region.width * 2, region.height * 2), 1)   # 小字放大更好认
        result, _ = self.ocr(np.array(up))

        items = []   # (y, x中心, x右缘, text)，坐标都换算成**窗口内偏移**
        for box, text, _ in (result or []):
            text = text.strip()
            if text:
                items.append((top + sum(p[1] for p in box) / 4 / 2,
                              sum(p[0] for p in box) / 4 / 2,
                              max(p[0] for p in box) / 2,
                              text))
        items.sort()
        # 用时间列把"会话列表右边界"锚实（原因见 wechat_bot 里那段注释：像素量法会被
        # 聊天区的头像顶到右边，宽出来的那一截会把**聊天标题**框进侧栏里来）。
        anchor = sidebar_left_from_items(items, W, left)
        set_sidebar_left(anchor)
        if anchor < left:
            items = [it for it in items if it[1] < anchor]
        self._side_ratio, self._side_size = round(sw, 3), (W, H)
        self._side_texts = [t for *_, t in items][:16]

        found = {}
        # 行高不按窗口比例猜（0.07H 是在大窗上标的；客户 648 高的小窗上真实行高 63px，
        # 比例算出来只有 45px —— 预览那行就被当成"下一行会话"，指纹全是碎的）。
        # 直接从 OCR 到的 y 现量：相邻会话行（有时间的那些）的中位间距就是行高。
        row_ys = sorted(cy for cy, _cx, _r, t in items
                        if t and (_TIME_LIKE.match(t.strip()) or len(t.strip()) >= 2))
        gaps = sorted(b - a for a, b in zip(row_ys, row_ys[1:]) if 30 < b - a < 120)
        row_max = gaps[len(gaps) // 2] if gaps else H * ROW_HEIGHT
        for name in self.targets:
            for i, (cy, cx, _x2, text) in enumerate(items):
                if not self._name_matches(text, name):
                    continue
                # 这一行的"变化指纹" = 群名 + 同行内所有文本（发送者、内容、时间、未读数）。
                # 一定要把时间和未读数也算进来：会话列表里的预览是**截断**显示的，
                # 同一人连发两条前缀相同的消息，光看内容文本是同一个字符串，会漏消息；
                # 但时间/未读数变了就能发现。
                row = [text]
                for y, _, _r, nxt in items[i + 1:]:
                    if y - cy > row_max:          # 超出这一行的高度 → 已经是下一个会话
                        break
                    row.append(nxt)
                found[name] = {"preview": " ".join(row), "click": (int(cx), int(cy))}
                break
        return found

    def _explain_blind(self, name, why=""):
        """读不到某个群时，把"当时列表里到底有什么"写进日志。

        这一行是给**远程排查**用的：光说一句"不在会话列表可视区"没法定位 ——
        可能是群被新消息顶下去了、可能是 OCR 没认出这几个字、也可能是截图是黑的
        （微信最小化/被完全遮住）。把 OCR 原文和量出来的尺寸打出来，一眼就能判断。
        """
        print(f"    [诊断] [{name}] 没认出来{why}")
        print(f"    [诊断] 截图 {self._side_size}，侧栏宽度比 {self._side_ratio}，"
              f"OCR 读到 {len(self._side_texts)} 项: {self._side_texts}")
        print("    [诊断] 群名必须和列表里显示的**一字不差**（标点、空格都算）；"
              "要盯的群先在微信里置顶，窗口别最小化。")

    def switch_by_search(self, name):
        """兜底：Ctrl+F 搜索切群。**只**在会话列表里找不到该群时用。

        搜索会打字（要抢前台），所以不是每次都搜；正常情况一律点会话列表。

        两个坑（客户那台实测踩到底了）：
        * **粘贴之后不能马上回车**：搜索结果还没列出来时回车会被忽略（机器慢、或刚切过
          前台时尤其明显）。回车一旦白按，**搜索面板就留在那儿不关**，而搜索面板会把
          会话列表整个顶掉 —— 从那一刻起每一轮都是"会话列表里没有 → 再搜索 → 再失败"，
          自锁成死循环（客户那台跑了几十轮全是这两行）。所以回车要重试。
        * **失败了一定要按 Esc 收起搜索面板**，否则把后面的轮次也一起拖死。
        """
        if not self._ensure_foreground():
            return False
        print(f"    [{name}] 会话列表里没有，退回 Ctrl+F 搜索")
        send_key(ord('F'), ctrl=True)      # Ctrl+F 打开搜索
        time.sleep(0.8)
        pyperclip.copy(name)
        time.sleep(0.2)
        send_key(ord('V'), ctrl=True)      # 粘贴群名，避开输入法
        time.sleep(1.5)                    # 留时间让搜索结果列出来
        title = ""
        for attempt in (1, 2):
            send_key(win32con.VK_RETURN)
            time.sleep(1.2)
            title = read_chat_title(self.ocr, self.hwnd)
            if title_matches(title, name):
                return True
            if attempt == 1:
                print(f"    [!] 回车后没切到（标题={title!r}），再按一次回车")
        send_key(win32con.VK_ESCAPE)       # 收起搜索面板：别让它把会话列表顶掉
        time.sleep(0.5)
        print(f"    [!] 搜索也没切到 [{name}]（标题={title!r}），已按 Esc 收起搜索框")
        return False

    def switch_group(self, name, info=None, attempts=3):
        """点会话列表那一行切群，并 OCR 标题确认切对了。返回是否切到。

        三条必须知道的：
        1. **已经在目标群就别再点**：微信里**点已经打开的那个会话 = 把它收起来** ——
           聊天区变成微信大头占位、标题只剩窗口按钮 'X'。实测：第 1 次点打开（标题读到
           `X 测试群2 (8)`），第 2 次点同一行就把它关掉了。所以"标题读不出来就再点一次"
           这种兜底会自己把会话关掉，越点越乱（曾经让某个群连续 3 次"切不过去"被跳过）。
        2. **微信必须压在最上面**：微信被别的窗口（浏览器、编辑器等）盖住时，点击会被那个
           窗口收走，微信这边毫无反应（连前台窗口都不变）—— 但 BitBlt 截图读的是窗口自己的
           后台缓冲，照样能 OCR 出正确内容，很容易误判成"坐标算错了"。所以先抬前台再点。
        3. **点完要确认**：会话列表随新消息重排，"截图 → OCR 算坐标 → 点击"中间隔着几百毫秒，
           期间别的会话来了新消息目标行就挪了，会点开隔壁群（实测点 ai测试2群 开的是 视频号）。
           确认不对就重读一次列表再点。

        坐标语义：这里的 (x, y) 是**窗口内偏移**（click_at 自己换算成屏幕绝对坐标）。
        """
        # （1）已经在目标群：直接返回，绝不点下去
        if title_matches(read_chat_title(self.ocr, self.hwnd), name):
            return True
        if not self._ensure_foreground():
            print("    [!] 微信切不到前台（可能被别的窗口压着），这次先不切群")
            return False
        row = info
        for attempt in range(1, attempts + 1):
            if not row:
                return False
            click_at(self.hwnd, *row["click"])
            wrong, title = None, ""
            for _ in range(10):                   # 最多等约 4s，等标题重绘出来
                time.sleep(0.4)
                title = read_chat_title(self.ocr, self.hwnd)
                if title_matches(title, name):
                    return True
                if re.search(r'[一-鿿]', title):  # 读出来是**别的群** → 这次确实点歪了
                    wrong = title
                    break
            if wrong is None:
                # 标题里一个字的中文都没有 = 会话刚打开、还没重绘完（刚被抬到前台时尤其明显）。
                # **这时候不能靠"再点一次"来催** —— 那就是上面第 1 条的关掉会话。
                # 只能多等一会儿；还不出来就认输，留给下一轮，至少状态是干净的。
                print(f"    [!] [{name}] 标题还没读出来（OCR={title!r}），再等一会儿（不重复点击）")
                for _ in range(10):
                    time.sleep(0.4)
                    title = read_chat_title(self.ocr, self.hwnd)
                    if title_matches(title, name):
                        return True
                print(f"    [!] [{name}] 读不出标题，这一轮不读了（下一轮再来）")
                return False
            print(f"    [!] 点到的不是 [{name}]（标题={wrong!r}），重读会话列表再点一次")
            row = self.read_sidebar().get(name)
        return False

    def establish_baseline(self, name, info=None):
        """建基线：切进这个群，把当前消息正文记下来，之后只报比它新的。

        必须在开始轮询前对每个群做一次 —— 否则第一次"预览变了"时 diff_new 会把读到的
        整屏内容当基线吞掉（返回 0 条），那条新消息就永远漏了。
        """
        if not self.switch_group(name, info):
            if not (self.search_fallback and self.switch_by_search(name)):
                return False
        st = self.targets[name]
        st.lines = [m.text for m in self.read_chat()]
        return True

    # ================= 读聊天 =================
    def read_chat(self):
        """读**当前打开的**聊天的消息，返回 [Message]（cx/cy 是聊天区内坐标）"""
        img = capture_window(self.hwnd)
        if img is None or not capture_ok():
            return []      # 空白图 → 一条消息都不报（宁可漏，也不能拿垃圾 OCR 当新消息回）
        W, H = img.size
        sw = sidebar_left(img)
        # 聊天区的下边界 = 输入框的上沿（现量），量不到才退回 CHAT_BOTTOM 比例。
        # 0.88H 是在大窗上标的；小窗上聊天区更高，写死会把最后几条消息整个切掉。
        ib_top = input_box_top(img, sw + 40, default=int(H * CHAT_BOTTOM))
        chat = img.crop((sw, int(H * TOP_RATIO), W, ib_top))
        cw, ch = chat.size
        result, _ = self.ocr(np.array(chat))
        if not result:
            return []

        items = sorted((sum(p[1] for p in b) / 4, sum(p[0] for p in b) / 4, t.strip())
                       for b, t, _ in result)
        # 判"我"的**第一道关是气泡颜色**（绿=自己发的），cx 只是兜底。
        # 为什么 cx 阈值靠不住：它假设"自己的气泡都在右半边"，但气泡短的时候中心
        # 并不靠右（实测 '我连下' 中心只过半 0.56），别人的长气泡中心也能过 0.6 ——
        # 判错的代价不对称：**别人说"密码是什么"被判成"我"→ 直接被过滤、永远不回**
        # （2026-10-16 客户那台"七天无理由退换货服务是什么"就是被这么吃掉的）。
        # 绿色是微信自己消息气泡的底色（149,236,105 系），判据 g-r > 30 对白色/灰色/
        # 深底都成立，实测一行文字跨绿气泡时命中稳定。
        g = np.asarray(img.convert("RGB"))[:, :, 1].astype(int) - \
            np.asarray(img.convert("RGB"))[:, :, 0].astype(int)
        green = g > 30

        def _on_green(cx_img, cy_img):
            y = max(0, int(cy_img) - 6)
            x = max(0, int(cx_img) - 4)
            return bool(green[y:int(cy_img) + 6, x:int(cx_img) + 4].any())

        # cx 兜底阈值：按"有没有右列"现算（有右列 → 中点分；没有 → 0.62cw 全当别人）
        cxs = [cx for _cy, cx, _t in items if cx]
        if cxs:
            left_cx, right_cx = min(cxs), max(cxs)
            has_right = (cw - right_cx) < (left_cx * 0.55)
            self_cx = (left_cx + right_cx) / 2 if has_right else cw * 0.62
        else:
            self_cx = cw * 0.62
        left = [cx for _, cx, t in items
                if cx <= self_cx and t and not re.fullmatch(r'\d{1,2}:\d{2}', t)]
        nick_cx = min(left) if left else None   # 昵称顶着头像右缘，是全屏最靠左的文本

        msgs, sender = [], None
        for cy, cx, text in items:
            if not text or re.fullmatch(r'\d{1,2}:\d{2}', text) or cy < ch * 0.05:
                continue                          # 时间戳行、顶部群名/人数行
            if JUNK_LINE.match(text):
                continue                          # 输入框占位符/"发送"/星期几分隔条
            if _on_green(sw + cx, int(H * TOP_RATIO) + cy):
                msgs.append(Message("我", text, True, cx, cy))
            elif cx > self_cx:
                msgs.append(Message("我", text, True, cx, cy))
            elif nick_cx is not None and abs(cx - nick_cx) <= 20:
                sender = text                     # 这一行是昵称，下一行才是正文
            else:
                msgs.append(Message(sender or "群成员", text, False, cx, cy))
        return msgs

    def diff_new(self, st, msgs):
        """对比基线和当前这一帧，取新增的消息"""
        lines = [m.text for m in msgs]
        if not st.lines:
            st.lines = lines
            return []
        tail = st.lines[-1]
        if tail in lines:                          # 基线尾行还在 → 它后面的都是新的
            idx = len(lines) - 1 - lines[::-1].index(tail)
            new = msgs[idx + 1:]
        else:                                      # 尾行被滚走了 → 模糊找最接近的位置
            best_i, best = -1, 0.0
            for i in range(len(lines) - 1, max(len(lines) - 25, -1), -1):
                r = difflib.SequenceMatcher(None, tail, lines[i]).ratio()
                if r > best:
                    best, best_i = r, i
            new = msgs[best_i + 1:] if best > 0.5 else msgs[-3:]
        st.lines = lines
        return new

    # ================= 发送 =================
    def _ensure_foreground(self):
        """按键只送给前台窗口，所以发东西前必须确认微信在前台"""
        if win32gui.GetForegroundWindow() == self.hwnd:
            return True
        self._win.SetActive()
        time.sleep(0.3)
        if win32gui.GetForegroundWindow() == self.hwnd:
            return True
        return force_foreground(self.hwnd)

    def _restore_foreground(self):
        fg = self._prev_fg
        if fg and fg != self.hwnd:
            try:
                win32gui.SetForegroundWindow(fg)
            except Exception:
                pass

    def send_text(self, name, text, must_be_in_list=True, at=None):
        """向指定群发一条文本，可选在开头带上 @某人（纯文本）。返回是否确认发出去了。

        校验链：已经在目标群就直接发；否则切群（点会话列表/兜底搜索）并 OCR 标题确认 →
        抢前台 → 点输入框 → 粘贴 → OCR 验证进了输入框 → 才回车 → 判定是否真的发出去。
        任何一步不过就中止，不会盲发、也不会把按键打进别的窗口。
        """
        saved_fg = win32gui.GetForegroundWindow()
        self._prev_fg = saved_fg
        if at and at not in ("群成员", "我"):
            text = f"@{at} {text}"
        marker = text.strip()
        try:
            # 已经在这个群里（刚读屏时就在）就不用再切一次：少一次点会话列表 = 少一次打扰，
            # 也少一次"列表已重排"的机会
            here = title_matches(read_chat_title(self.ocr, self.hwnd), name)
            if not here:
                info = self.read_sidebar().get(name)
                if not info and must_be_in_list:
                    print(f"    [X] 会话列表里没有 [{name}]，不发送（避免发错群）")
                    return False
                if info and not self.switch_group(name, info):
                    return False
                if not info and not (self.search_fallback and self.switch_by_search(name)):
                    return False
                title = read_chat_title(self.ocr, self.hwnd)
                if not title_matches(title, name):
                    print(f"    [X] 当前聊天不是 [{name}]（标题 OCR={title!r}），中止发送")
                    return False

            if not self._ensure_foreground():
                print("    [X] 抢不到微信前台，放弃发送（避免按键打进别的窗口）")
                return False

            pyperclip.copy(text)
            time.sleep(0.2)
            pasted, y_paste = False, None
            # 点击位置不按整窗比例猜（0.80/0.92 是大窗的标定值，小窗上会点偏）：
            # 先现量输入框上沿，在**输入区内部**从上往下试几个高度。
            shot = capture_window(self.hwnd)
            W_, H_ = (shot.size if shot is not None and capture_ok() else (0, 0))
            box_top = input_box_top(shot, sidebar_left(shot) + 40, default=int(H_ * 0.64)) \
                if W_ else int(H_ * 0.64)
            click_ys = []
            if W_:
                span = H_ * 0.99 - box_top
                click_ys = [box_top + span * r for r in (0.35, 0.55, 0.18, 0.75)]
            else:
                click_ys = [H_ * r for r in INPUT_CLICK_Y_RATIOS]
            for cy in click_ys:
                if win32gui.GetForegroundWindow() != self.hwnd:
                    print("    [!] 前台已经不是微信了，中止发送")
                    return False
                rect = win32gui.GetWindowRect(self.hwnd)
                click_at(self.hwnd, (rect[2] - rect[0]) * 0.5, cy)
                time.sleep(0.3)
                send_key(ord('V'), ctrl=True)
                time.sleep(0.6)
                hit, texts, y = input_box_has(self.ocr, self.hwnd, marker)
                print(f"    点 y={int(cy)}（输入区 {int(box_top)}~{int(H_ * 0.99)}）后输入框 OCR: {texts}")
                if hit:
                    pasted, y_paste = True, y
                    break
            if not pasted:
                print("    [X] 文字没进输入框，不按回车")
                return False

            send_key(win32con.VK_RETURN)
            time.sleep(0.8)
            hit2, texts2, y2 = input_box_has(self.ocr, self.hwnd, marker)
            if looks_sent(hit2, texts2, y2, y_paste):
                print(f"    [OK] 已发送 [{name}]: {text}")
                return True
            print(f"    [X] 回车后文字还留在输入框里: {texts2}")
            return False
        finally:
            self._prev_fg = None
            if saved_fg and saved_fg != self.hwnd:
                try:
                    win32gui.SetForegroundWindow(saved_fg)
                except Exception:
                    pass

    # ================= 自动回复 =================
    @staticmethod
    def _sender_is_ok(sender):
        """昵称能不能拿来 @：读屏猜出来的昵称经常不准（'群成员'、时间、正文片段），
        猜不准就干脆不带 @，只回内容 —— 免得 @ 到不存在的名字。"""
        if not sender or sender in ("我", "群成员"):
            return False
        s = sender.strip()
        if not (2 <= len(s) <= 12):
            return False
        if re.fullmatch(r'[\d:：\s.。．、,，]+', s):      # 纯数字/时间/标点
            return False
        return not any(c in s for c in ":：")

    def _compose_query(self, new_msgs, context=None):
        """把"这次的新消息"拼成一段群聊记录给模型（前面带几条旧消息做上下文）。

        为什么要把整批一起给：别人经常连着发好几条（"在不" / "帮我看下这个" / "谢谢"），
        单看最后一条根本不知道在说什么。
        """
        lines = []
        for m in (context or [])[-CTX_BEFORE:]:
            lines.append(f"{'我' if m.is_self else m.sender}: {m.text}")
        for m in new_msgs[-CTX_MAX_NEW:]:
            lines.append(f"{'我' if m.is_self else m.sender}: {m.text}")
        return "\n".join(lines)

    def make_reply(self, name, new_msgs, context=None):
        """（--no-intent 模式）让模型针对这批新消息生成一句回复，失败返回 None"""
        return self.gemini.chat(self._compose_query(new_msgs, context),
                                user="", group_name=name)

    # ---------- 意图判断 = ----------
    def load_knowledge(self):
        """读知识库文件。客服的答案只能依据它 —— 没有它，模型只能按常识猜，等于会编。"""
        if self._knowledge is None:
            try:
                with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
                    self._knowledge = f.read().strip()[:4000]
            except FileNotFoundError:
                self._knowledge = "（还没有 knowledge.md 知识库文件）"
            except Exception as e:
                self._knowledge = f"（读知识库失败: {e}）"
        return self._knowledge

    def _intent_client(self):
        if self._client_intent is None:
            self._client_intent = GeminiClient(
                GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL,
                # 用 replace 不用 format：提示词里有 JSON 的花括号，format 会当成占位符
                system_prompt=INTENT_PROMPT.replace("{kb}", self.load_knowledge()))
        return self._client_intent

    @staticmethod
    def _parse_decision(raw):
        """从模型输出里抠出那个 JSON。抠不出来返回 None（调用方会再催一次）"""
        m = re.search(r'\{.*\}', raw or "", re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except Exception:
            return None
        act = str(d.get("act", "")).strip().lower()
        if act not in ("reply", "ignore", "handoff"):
            return None
        return {"act": act, "reply": str(d.get("reply") or "").strip(),
                "reason": str(d.get("reason") or "").strip(), "raw": raw}

    def _with_retrieved(self, query):
        """把这批消息去 Dify 检索到的片段附在提问后面。

        为什么附在**提问**里、不是塞进系统提示词：GeminiClient 是每个群第一次调用时把
        system_prompt 固化进那段历史、之后再也不更新（见 wechat_bot.GeminiClient.chat），
        所以"每次都不同"的检索结果只能走提问这一路。
        顺带的好处：系统提示词里那份 knowledge.md 保持不变，继续当"什么相关/不做什么"的
        定义，检索只负责给事实依据。
        """
        if self.kb is None:
            return query
        hits = self.kb.retrieve(query)
        if not hits:
            return query                 # 没检索到就原样走（knowledge.md 还在提示词里兜底）
        return (f"{query}\n\n"
                f"【知识库检索到的片段（回答只能依据这里，没有的就 handoff）】\n{hits}")

    def decide(self, name, new_msgs, context=None):
        """一次模型调用：判断该不该回 + 直接给出回复内容。返回 {act, reply, reason}"""
        client = self._intent_client()
        # 检索只做一次；下面"没按 JSON 回"再催一次时用的是同一个 query（片段原样复用）
        query = self._with_retrieved(self._compose_query(new_msgs, context))
        d = self._parse_decision(client.chat(query, user="", group_name=name))
        if d is None:                       # 没按 JSON 回 → 催一次
            print("    [!] 模型没按 JSON 回，催一次")
            d = self._parse_decision(
                client.chat(query + "\n（只输出那个 JSON，别的都不要）", user="", group_name=name))
        return d or {"act": "handoff", "reply": "", "reason": "模型没按 JSON 回"}

    def _notify_handoff(self, name, batch, reason):
        """转人工 → 往企微表格写一行，等人跟进（没配 webhook 就什么都不做）。

        dry-run 下不推：跟"只算不发"一个道理，调试时别往人的待办表里灌测试数据。
        """
        if not self.notifier:
            return False
        if self.dry_run:
            print("    [dry-run] 不推送到企微表格")
            return False
        return self.notifier.push(name, batch, reason)

    def _log_decision(self, name, batch, d, sent):
        """把每次判定记一行（jsonl），方便回头复盘：哪些该回没回、哪些不该回瞎回了"""
        try:
            with open(DECISION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "group": name,
                    "act": d.get("act"),
                    "reason": d.get("reason"),
                    "reply": d.get("reply"),
                    "sent": bool(sent),
                    "msgs": [f"{m.sender}: {m.text}" for m in batch],
                }, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"    [!] 写判定日志失败: {e}")

    def maybe_reply(self, name, msgs, context=None):
        """有新消息时按需回复：只回别人发的；整批一起理解、**只回一句**。

        意图判断开着时（默认）：先让模型判 act = reply / ignore / handoff，
        只有 reply 才真发；闲聊(ignore)和需要人工(handoff)都不回，只记日志。
        """
        if not (self.reply and self.auto_reply.get(name, False)):
            return False
        others = [m for m in msgs if not m.is_self]
        if not others:
            return False
        batch = others[-CTX_MAX_NEW:]
        joined = re.sub(r'\s+', '', " ".join(m.text for m in batch))
        if len(joined) < 2:                      # 整批都是表情/单字，没什么可回
            return False
        msg = others[-1]                         # @ 用最后这条的发送者

        now = time.time()
        if now - self._last_reply_at.get(name, 0) < MIN_REPLY_GAP:
            print(f"    [i] [{name}] 距上次回复不到 {MIN_REPLY_GAP:.0f}s，这次不回")
            return False
        last_text, last_at = self._last_replied.get(name, ("", 0))
        if joined == last_text and now - last_at < DEDUPE_WINDOW:
            print(f"    [i] [{name}] 同一批内容刚回过，跳过")
            return False

        decision = None
        if self.intent:
            print(f"    → 判断意图（{len(batch)} 条新消息，最后一条 {msg.sender}: {msg.text[:24]!r}）")
            decision = self.decide(name, batch, context)
            print(f"    → 判定 {decision['act']}：{decision['reason']}")
            if decision["act"] != "reply" or not decision["reply"]:
                self._log_decision(name, batch, decision, sent=False)
                if decision["act"] == "handoff":
                    print("    [i] 这条要人工处理，先不回（已记到日志里）")
                    self._notify_handoff(name, batch, decision["reason"])
                return False
            reply = decision["reply"]
        else:
            print(f"    → 调模型生成回复（{len(batch)} 条新消息，最后一条 {msg.sender}: {msg.text[:24]!r}）")
            reply = self.make_reply(name, batch, context)
            if not reply:
                print("    [X] 模型没给出回复")
                return False

        at = msg.sender if (self.use_at and self._sender_is_ok(msg.sender)) else None
        print(f"    → 回复{'（@'+at+'）' if at else ''}: {reply}")
        if self.dry_run:
            print("    [dry-run] 只算不发")
            self._last_reply_at[name] = now
            self._last_replied[name] = (joined, now)
            if decision:
                self._log_decision(name, batch, decision, sent=False)
            return False
        sent = self.send_text(name, reply, at=at)
        if sent:
            self._last_reply_at[name] = time.time()
            self._last_replied[name] = (joined, now)
        if decision:
            self._log_decision(name, batch, decision, sent=sent)
        return sent

    def _handle(self, name, msgs, on_messages=None, context=None):
        """一轮里发现新消息后的统一出口：先回调（用户自己的处理），再按配置自动回复"""
        if on_messages:
            on_messages(name, msgs)
        if self.reply:
            self.maybe_reply(name, msgs, context)

    # ================= 轮询 =================
    def poll_once(self, on_messages=None):
        """跑一轮：读一次会话列表（所有群共用这次截图/OCR）→ 需要看的群点进去读全文。

        默认只点"预览指纹变了"的群（不打扰用户）；每 full_scan_every 轮无条件全读一次兜底。
        返回 {群名: [Message]}。一轮结束把前台窗口还原。
        """
        self._prev_fg = win32gui.GetForegroundWindow()
        self.cycle += 1
        if self._window_minimized():
            if self.cycle % 10 == 1:      # 别每轮刷一遍，这条会一直成立直到窗口恢复
                print("[!] 微信窗口是最小化的：现在截到的是**过时的旧画面**，这一轮什么都别信。"
                      "把微信窗口恢复出来（点一下任务栏上的微信）就继续了。")
            self._restore_foreground()
            self._prev_fg = None
            return {}
        force = self.full_scan_every <= 1 or (self.cycle % self.full_scan_every == 0)
        out = {}
        try:
            sidebar = self.read_sidebar()
            if not self._side_texts and self.cycle % 10 == 1:
                # 整张截图 OCR 不出任何文本 = 不是"群名对不上"，是压根没读到画面
                print("[诊断] 会话列表里一个会话都没读到 —— 微信可能被最小化/完全遮住，"
                      "或者截图拿不到画面。跑一次 诊断.bat，看它存下来的那张图。")
                print(f"[诊断] 截图情况: {capture_summary()}")
            for name, st in self.targets.items():
                info = sidebar.get(name)
                if not info:
                    if st.visible is not False:
                        st.visible = False
                        print(f"[{name}] 不在会话列表可视区（滚走了/收进折叠群聊）")
                        self._explain_blind(name)
                    # 列表里看不到，就没有"预览指纹"可用，只能定期搜索进去读一次
                    if not (self.search_fallback and force):
                        continue
                    # 搜索要抢前台打字，同一个群最多每 SEARCH_RETRY_GAP 秒试一次 ——
                    # 否则日志会被"搜索→失败→再搜索"刷满，真正有用的行全被埋掉
                    if time.time() - self._last_search.get(name, 0) < SEARCH_RETRY_GAP:
                        continue
                    self._last_search[name] = time.time()
                    if not self.switch_by_search(name):
                        self._explain_blind(name, why="（搜索兜底也没切进去）")
                        continue
                    frame = self.read_chat()
                    new = self.diff_new(st, frame)
                    if new:
                        out[name] = new
                        # 前面那几条当上下文一起喂给模型（理解"在不？"这种短句）
                        self._handle(name, new, on_messages, frame[:len(frame) - len(new)])
                    continue
                if st.visible is not True:
                    st.visible = True
                    print(f"[{name}] 在会话列表里 点击点={info['click']} 指纹={info['preview']!r}")

                changed = (st.preview is None
                           or difflib.SequenceMatcher(None, info["preview"], st.preview).ratio() <= 0.85)
                if changed:
                    print(f"[{name}] 预览变化: {st.preview!r} → {info['preview']!r}")
                elif not force:
                    continue                      # 指纹没变，不用切进去
                else:
                    print(f"[{name}] 定期全量读一次（指纹没变，防止预览被截断漏消息）")
                st.preview = info["preview"]

                # 切群失败要兜底：点列表三次都点不进去时（侧栏刚被新消息重排、行挪了），
                # 再退回 Ctrl+F 搜索切一次 —— 不兜底的话，新消息来了它却"跳过本轮"，
                # 下一轮列表可能又变了，表现就是"明明检测到新消息、却永远不回"
                # （2026-10-16 客户那台：预览变化打出来了、正文永远读不到）。
                # 有 SEARCH_RETRY_GAP 限频（默认 60s/群），不会每轮都去搜索抢前台。
                if not self.switch_group(name, info):
                    if self.search_fallback and force:
                        if time.time() - self._last_search.get(name, 0) < SEARCH_RETRY_GAP:
                            print(f"[{name}] 切不过去（本轮跳过；搜索兜底 {SEARCH_RETRY_GAP:.0f}s 内不重试）")
                            continue
                        self._last_search[name] = time.time()
                        if self.switch_by_search(name):
                            frame = self.read_chat()
                            new = self.diff_new(st, frame)
                            if new:
                                out[name] = new
                                self._handle(name, new, on_messages,
                                             frame[:len(frame) - len(new)])
                            continue
                    print(f"[{name}] 切不过去，本轮跳过")
                    continue
                frame = self.read_chat()
                new = self.diff_new(st, frame)
                if new:
                    out[name] = new
                    self._handle(name, new, on_messages, frame[:len(frame) - len(new)])
        finally:
            self._restore_foreground()
            self._prev_fg = None
        return out

    def run(self, on_messages=None, duration=None):
        """先建基线（读会话列表 + 每个群切进去读一次正文），然后开始轮询"""
        self._prev_fg = win32gui.GetForegroundWindow()
        while self._window_minimized():       # 最小化时读到的是旧画面，建出来的基线是错的
            print("[!] 微信窗口是最小化的：先把它恢复出来（点一下任务栏上的微信）…")
            time.sleep(5)
        self.check_capture()                  # 先说清楚"看不看得见"，后面读不到群名时好定位
        sidebar = self.read_sidebar()
        print(f"轮询 {list(self.targets)}，每 {self.interval}s 读一次会话列表")
        for name, st in self.targets.items():
            info = sidebar.get(name)
            st.preview = info["preview"] if info else None
            st.visible = bool(info)
            if not info:
                print(f"  基线 [{name}] 不在会话列表可视区")
                self._explain_blind(name)
                continue
            if self.establish_baseline(name, info):
                print(f"  基线 [{name}] 正文 {len(st.lines)} 行 预览={info['preview']!r}")
            else:
                print(f"  基线 [{name}] 切不进去，本轮先跳过")
        self._restore_foreground()
        start = time.time()
        while True:
            try:
                self.poll_once(on_messages)
            except Exception as e:
                print(f"轮询异常: {e}")
            time.sleep(self.interval)
            if duration and time.time() - start > duration:
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=CONFIG_FILE,
                    help=f"部署配置文件（默认 {CONFIG_FILE}；群名/Dify/企微都在里面配）")
    ap.add_argument("--log", default="bot.log",
                    help="输出同时写一份到这个文件（默认 bot.log；给空字符串关掉）")
    ap.add_argument("--groups", default=None,
                    help="要监控的群名，逗号分隔（不给就用配置/文件头里的）")
    ap.add_argument("--interval", type=float, default=None)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--full-scan-every", type=int, default=None,
                    help="每隔几轮无条件把每个群读一次全文（预览被截断时的兜底）")
    ap.add_argument("--no-search-fallback", action="store_true",
                    help="群滚出会话列表时不要退回 Ctrl+F 搜索（默认会，但只在定期全量读那轮）")
    ap.add_argument("--echo", action="store_true", help="别人发言就回一句固定文案（不调模型，联调用）")
    ap.add_argument("--no-reply", action="store_true", help="不要自动回复，只监控打印")
    ap.add_argument("--reply-groups", default=None,
                    help="只有这些群自动回复（逗号分隔；默认用配置里的 AUTO_REPLY）")
    ap.add_argument("--dry-run", action="store_true", help="算出回复但只打印不发送（调试用）")
    ap.add_argument("--no-at", action="store_true", help="回复时不带 @发送者")
    ap.add_argument("--no-intent", action="store_true",
                    help="关掉意图判断：别人说什么都回（只适合联调，客服场景别关）")
    ap.add_argument("--probe", default=None, help="自测：自己先发一条，再验证能不能被发现")
    ap.add_argument("--probe-reply", default=None,
                    help="自测：把这些文本当成别人连发的新消息跑一遍回复（多条用 | 分隔）")
    ap.add_argument("--test-handoff", nargs="?", const="", default=None, metavar="文本",
                    help="自测：直接往企微表格写一行「转人工」样例，验证 webhook 通不通"
                         "（不碰窗口、不调模型；不填文本用默认样例）")
    args = ap.parse_args()

    # 日志先开：后面所有输出（含配置提示、每步判定）都留在文件里
    setup_log(args.log)
    print(f"[启动] 版本 {BUILD}")

    # 先吃配置再解析默认值：--groups 之类不给时就用配置里的，命令行给了命令行为准
    cfg, notes = apply_config(args.config)
    if args.groups is None:
        args.groups = ",".join(GROUPS)
    if args.interval is None:
        args.interval = float(cfg.get("interval") or 3.0)
    if args.full_scan_every is None:
        args.full_scan_every = int(cfg.get("full_scan_every") or 5)
    for line in notes:
        print(f"  {line}")
    if not os.path.exists(args.config) and not os.path.exists(GROUPS_TXT):
        print(f"  [i] 没找到 {args.config} / {GROUPS_TXT}，现在用的是代码里写死的群名 {GROUPS}")
        print(f"      换机器部署时请建一个 {GROUPS_TXT}：一行一个群名，"
              f"要和微信会话列表里显示的一字不差")
    groups = [g.strip() for g in args.groups.split(",") if g.strip()]

    # 自测：只验企微表格那条路（不建 GroupBot——那要求微信窗口在，调 webhook 不需要）
    if args.test_handoff is not None:
        if not WECOM_WEBHOOK:
            print("[X] WECOM_WEBHOOK 没填：先按文件头注释在企微表格里开「接收外部数据」拿地址")
            return 1
        print(f"推送目标: {WECOM_WEBHOOK.split('key=')[0]}key=***")
        print(f"字段映射: { {k: v for k, v in WECOM_FIELDS.items() if v} or '（一个都没填，写不进去）'}")
        texts = [t.strip() for t in
                 (args.test_handoff or "我要退款|订单号发你了").split("|") if t.strip()]
        fakes = [Message("测试同学", t, False, 0, 0) for t in texts]
        n = HandoffNotifier(WECOM_WEBHOOK, WECOM_FIELDS)
        ok = n.push(groups[0], fakes, "自测：验证企微表格 webhook 通不通")
        print("[OK] webhook 通了，去表格里看有没有这一行" if ok
              else "[X] 没写进去（看上面报错：401/字段 ID 不对/开关没开）")
        return 0 if ok else 1

    auto_reply = None
    if args.reply_groups:
        auto_reply = {g.strip(): True for g in args.reply_groups.split(",") if g.strip()}
    elif args.groups and args.groups.split(",")[0].strip() not in AUTO_REPLY:
        # --groups 临时指定了 AUTO_REPLY 里没有的群（联调常用），不能让它被
        # "该群没开自动回复"这条静默拦掉 —— probe 模式的回复一条都不会发，
        # 表现就是日志里什么都没有、只剩一句"回复没发出去"，极难查。
        auto_reply = {g.strip(): True for g in args.groups.split(",") if g.strip()}
    reply_on = not (args.no_reply or args.echo)      # --echo 自带回复，不要再叠加模型回复

    print(f"群: {groups}  自动回复: {'开' if reply_on else '关'}"
          f"{'（dry-run，只算不发）' if args.dry_run else ''}")
    try:
        bot = GroupBot(groups, interval=args.interval, full_scan_every=args.full_scan_every,
                       search_fallback=not args.no_search_fallback,
                       reply=reply_on, auto_reply=auto_reply,
                       dry_run=args.dry_run, use_at=not args.no_at, intent=not args.no_intent)
    except RuntimeError as e:
        print(f"[X] {e}")
        print("    请检查：① 微信是否已登录、窗口开着；")
        print("            ② 微信和 python 是不是**同一个权限级别** —— "
              "用'以管理员身份运行'打开的微信会让按键全被系统拦掉；")
        print("            ③ 微信是不是被最小化了（最小化时读不到画面）。")
        return 1
    if reply_on:
        will = [g for g in groups if bot.auto_reply.get(g)]
        print(f"  会回复的群: {will or '（无，检查 AUTO_REPLY / --reply-groups）'}")
        print(f"  意图判断: {'开（只回需要我们回答的）' if bot.intent else '关（别人说什么都回）'}")
        print(f"  知识库: {KNOWLEDGE_FILE}（{'已加载' if bot.load_knowledge() and '还没有' not in bot.load_knowledge() else '没找到，模型只能凭常识答'}）")
        print(f"  检索: {bot.kb or '没配（DIFY_DATASET_ID / DIFY_API_KEY 留空）→ 整份读上面那个文件'}")
        print(f"  转人工推送: {bot.notifier or '没配（WECOM_WEBHOOK 留空）→ 只记 cs_decisions.jsonl，没人被通知'}")

    def on_messages(name, msgs):
        print(f"=== [{name}] 新消息 {len(msgs)} 条 ===")
        for m in msgs:
            print(f"  {m}")
        if args.echo:
            other = [m.text for m in msgs if not m.is_self]
            if other:
                bot.send_text(name, f"收到: {other[-1]}")

    # 自测：把一段/多段文本当成"别人发的新消息"，跑一遍自动回复（不依赖真有人发言）
    if args.probe_reply:
        name = groups[0]
        bot._prev_fg = win32gui.GetForegroundWindow()
        context = []
        if bot.dry_run:
            # dry-run 只看模型怎么判、怎么答，不用碰窗口（调提示词时完全不打扰用户）
            print("[自测] dry-run：不切窗口、不读屏，只跑模型这一环")
        else:
            sidebar = bot.read_sidebar()
            if name in sidebar and not bot.switch_group(name, sidebar[name]):
                print(f"[X] 切不进 [{name}]，稍后再试（你正在用别的窗口时可能抢不到前台）")
                bot._restore_foreground()
                return 1
            context = bot.read_chat()[-CTX_BEFORE:]
        texts = [t.strip() for t in args.probe_reply.split("|") if t.strip()]
        fakes = [Message("测试同学", t, False, 0, 0) for t in texts]
        print(f"[自测] 把这些当成别人连发的新消息：")
        for f in fakes:
            print(f"    {f}")
        if context:
            print(f"   （上下文 {len(context)} 条：{[m.text[:12] for m in context]}）")
        ok = bot.maybe_reply(name, fakes, context)
        bot._restore_foreground()
        if bot.dry_run:
            print("[dry-run] 跑完（没真发）")
            return 0
        print("[OK] 回复链路通了" if ok else "[X] 回复没发出去（看上面日志）")
        return 0 if ok else 1

    # 自测模式：建基线 → 自己发一条 → 轮询到它为止
    if args.probe:
        name = groups[0]
        bot._prev_fg = win32gui.GetForegroundWindow()
        sidebar = bot.read_sidebar()
        if name not in sidebar:
            print(f"[X] 会话列表里没看到 [{name}]，没法自测")
            return 1
        bot.targets[name].preview = sidebar[name]["preview"]
        bot.targets[name].visible = True
        if not bot.establish_baseline(name, sidebar[name]):
            print(f"[X] 切不进 [{name}]，建不了基线")
            return 1
        print(f"[1] 基线 [{name}] 正文 {len(bot.targets[name].lines)} 行 "
              f"预览={sidebar[name]['preview']!r}")
        print(f"[2] 自己发一条: {args.probe!r}")
        if not bot.send_text(name, args.probe):
            print("[X] 发送失败")
            bot._restore_foreground()
            return 1
        head = _flat(args.probe)[:5]
        bot.full_scan_every = 1     # 自测：每轮都全量读，省得等兜底周期
        print("[3] 轮询，看能不能发现刚才那条")
        for i in range(1, 9):
            time.sleep(2)
            got = bot.poll_once(on_messages=None)
            found = [m for m in got.get(name, []) if head in _flat(m.text)]
            print(f"    第{i}轮: 新消息 {len(got.get(name, []))} 条")
            if found:
                print(f"[OK] 发现了: {found[0]!r} → 发送链路和会话列表轮询都通")
                bot._restore_foreground()
                return 0
        print("[X] 8 轮没发现，看上面每轮输出")
        bot._restore_foreground()
        return 1

    bot.run(on_messages, duration=args.duration)
    return 0


if __name__ == "__main__":
    sys.exit(main())