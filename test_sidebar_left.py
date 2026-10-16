# -*- coding: utf-8 -*-
"""会话列表右边界（"时间列锚"）的回归测试 —— 拿客户那台机器的真实 OCR 结果当固定样本。

背景（2026-10-16 客户那台）：像素量法把会话列表右边界量成 364，而实际只到 ~322
—— 量宽了 40 多像素，于是：
  * 会话列表裁剪区把**聊天标题**那一行的字一起框进来，侧栏里凭空多出一个和群名
    一模一样的"行"（报告 §6 里 y=57 那条），机器人拿它当会话行去点（点的是标题栏）；
    客户屏幕上的日志就是这个：`预览变化: '测试群 Q搜索' → ...`（Q搜索 = 搜索框，
    和那个假行处在同一行）。
  * 聊天标题的裁剪区从 364 开始，把群名**切掉三个字**（§8 只读到 '2 (8)'），
    title_matches 永远不过 → 日志里一直刷 `点到的不是 [测试群]（标题='口 2 (8)'）`
    `切不过去，本轮跳过` → **能读、不能发**。
修法：用"时间列"锚定（微信把每行的时间右对齐到会话行右缘）。

    python test_sidebar_left.py
"""
import sys

from PIL import Image, ImageDraw

from wechat_bot import (SIDEBAR_ANCHOR_PAD, input_box_top, sidebar_left_from_items,
                        title_band_bottom, title_matches)

_fail = []


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else ' X'}] {name}  {extra}")
    if not cond:
        _fail.append(name)


# 客户那份诊断报告 §6 的原文，逐项抄下来：(y, x 中心, x 右缘, 文本)
# 右缘由"中心 + 字数 × 8px ÷ 2"估出来（'09:25' 5 个字符 ≈ 40px 宽）
CUSTOMER_ITEMS = [
    (57, 343, 364, "测试群"),            # ← 假行：其实是聊天标题被切剩的半截
    (58, 110, 131, "Q搜索"),             # ← 侧栏的搜索框
    (104, 151, 172, "测试群2"), (104, 283, 303, "09:25"),
    (124, 206, 290, "海妖：@测试同学大部分地区.."),
    (171, 168, 216, "文件传输助手"),
    (234, 147, 168, "测试群"), (234, 283, 303, "08:52"),
    (254, 200, 284, "海妖：七天无理由退换货服务"),
    (494, 140, 154, "海妖"), (494, 283, 303, "10:26"),
    (559, 141, 169, "Grey"), (559, 283, 303, "09:41"),
    (624, 189, 245, "视频号，剪映，微企"), (624, 283, 303, "08:33"),
]

# 本机那次诊断 §6 的原文（2546x1610 的窗口）
LOCAL_ITEMS = [
    (219, 291, 333, "一甜李"), (219, 563, 585, "10:00"),
    (348, 371, 460, "上海江儿童医院.."), (479, 561, 583, "10/07"),
    (609, 277, 305, "测试"), (609, 562, 584, "09/24"),
]


def main():
    from wechat_group_bot import GroupBot

    print("客户那台（像素量法给 364，实际该在 320 附近）：")
    anchor = sidebar_left_from_items(CUSTOMER_ITEMS, 896, 364)
    check("锚定出来的边界落在 305~325", 305 <= anchor <= 325, f"anchor={anchor}")
    check("比像素量法更靠左（像素量法会被头像/气泡顶到右边）", anchor < 364,
          f"{anchor} < 364")
    check("时间文本本身不会被切掉", anchor >= 303, f"anchor={anchor} ≥ 时间右缘 303")

    kept = [it for it in CUSTOMER_ITEMS if it[1] < anchor]
    kept_texts = [t for *_, t in kept]
    check("聊天标题那半截 '测试群'(y=57) 被剔掉",
          (57, 343, 364, "测试群") not in kept, f"剔除 {len(CUSTOMER_ITEMS) - len(kept)} 项")
    check("搜索框 'Q搜索' 留在里面但不会被当成群名（_name_matches 不认它）",
          "Q搜索" in kept_texts and not any(
              GroupBot._name_matches("Q搜索", g) for g in ("测试群", "测试群2")))
    check("真正的会话行都还在",
          {"测试群2", "文件传输助手", "海妖", "Grey", "视频号，剪映，微企"} <= set(kept_texts))

    # 机器人是"按 y 从上到下取第一个认得出的" —— 修完必须点到真正的会话行
    from wechat_group_bot import GroupBot
    for name, want_y in (("测试群", 234), ("测试群2", 104)):
        hit = next(((y, x) for y, x, _r, t in kept if GroupBot._name_matches(t, name)), None)
        check(f"「{name}」点到的是会话行 y={want_y}", hit and hit[0] == want_y, f"命中 {hit}")

    print("\n本机（2546 宽的窗口，像素量法给 613）：")
    anchor2 = sidebar_left_from_items(LOCAL_ITEMS, 2546, 613)
    check("锚定值在 580~610（比像素量法略紧、但不切内容）", 580 <= anchor2 <= 610,
          f"anchor={anchor2}")
    check("本机没有可剔的项（侧栏里本来就没有假行）",
          not [it for it in LOCAL_ITEMS if it[1] >= anchor2])

    print("\n量不出时间列时（列表里没有时间/日期文本）：")
    check("老实退回像素量法",
          sidebar_left_from_items([(100, 50, 60, "某个群")], 896, 364) == 364)

    print("\n异常输入不该崩：")
    for bad in ([], [(0, 0, 0, "")], [(1, 2, 3, "9:5")], [(1, 2, 3, "25:99")]):
        sidebar_left_from_items(bad, 896, 364)
    check("空/坏输入都能兜住", True, f"pad={SIDEBAR_ANCHOR_PAD}")

    # ---------------- title_matches：客户那对名字的坑 ----------------
    print("\ntitle_matches（「测试群」会命中「测试群2」的旧 bug）：")
    cases = [
        ("X 测试群2 (8)", "测试群", False, "旧代码 [:4] in 会命中，发错群"),
        ("X 测试群2 (8)", "测试群2", True, "正确命中"),
        ("X 测试群 (8)", "测试群", True, "正确命中"),
        ("X 测试群 (8)", "测试群2", False, ""),
        ("", "测试群", False, "空标题"),
        ("X 微信", "测试群", False, ""),
        ("测试群2", "测试群", False, "无括号也要卡住"),
        ("测试群", "测试群", True, "整条就是群名"),
        ("X 测试群2 (8) 某某: 你们发什么快递", "测试群2", True, "标题带首条消息"),
    ]
    for title, name, want, why in cases:
        got = title_matches(title, name)
        check(f"{title!r} vs {name!r} → {want}" + (f"（{why}）" if why else ""),
              got == want, f"got={got}")

    # ---------------- 自适应布局：同一套函数吃两种窗口尺寸 ----------------
    def synth(w, h, sb, title_bottom, input_top):
        """拼一张微信布局的仿真图：侧栏灰、聊天区白、标题深底+文字、分隔线、输入线。"""
        im = Image.new("RGB", (w, h), (250, 250, 250))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, sb, h], fill=(238, 238, 240))
        d.rectangle([sb, 0, w, 13], fill=(255, 255, 255))           # 顶部白边
        d.rectangle([sb, 13, w, int(title_bottom * 0.45)], fill=(117, 117, 122))   # 标题深底
        d.rectangle([sb + 30, int(title_bottom * 0.45), sb + 260, title_bottom - 4],
                    fill=(40, 40, 40))                               # 群名文字
        d.rectangle([sb, title_bottom - 2, w, title_bottom], fill=(240, 240, 240))  # 分隔线
        d.rectangle([sb, input_top, w, input_top + 2], fill=(221, 221, 223))       # 输入线
        return im

    print("\n自适应布局（同一套函数，两种窗口尺寸）：")
    # 大窗：本机实拍过的结构（13~77 深底、77~171 文字、171 分隔线、1310 输入线）
    big = synth(2546, 1610, 605, 171, 1310)
    check("大窗 title_band_bottom = 171", title_band_bottom(big, 615, 225) == 171,
          f"got={title_band_bottom(big, 615, 225)}")
    check("大窗 input_box_top = 1310", input_box_top(big, 645, 1030) == 1310,
          f"got={input_box_top(big, 645, 1030)}")
    # 小窗：客户那台的比例（标题条/输入线按 648 高等比缩）
    small = synth(896, 648, 315, 69, 438)
    check("小窗 title_band_bottom = 69", title_band_bottom(small, 325, 90) == 69,
          f"got={title_band_bottom(small, 325, 90)}")
    check("小窗 input_box_top = 438", input_box_top(small, 355, 414) == 438,
          f"got={input_box_top(small, 355, 414)}")
    # 全白图：量不到就退回默认值，不崩
    blank = Image.new("RGB", (896, 648), (255, 255, 255))
    check("量不到 → 退回默认值", title_band_bottom(blank, 400, 90) == 90
          and input_box_top(blank, 400, 414) == 414)

    print()
    if _fail:
        print(f"[X] {len(_fail)} 项没过: {_fail}")
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())