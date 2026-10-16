# -*- coding: utf-8 -*-
"""截图"有没有内容"判定的回归测试 —— 拿客户那台机器的失败现场当固定样本。

背景（2026-10 客户那台 Windows 10 机器）：BitBlt 拿回来的是一整片窗口底色，
只有**最左一列/最右一列**有几块碎片字形。旧判定是"整张图灰度标准差 < 3.0 才算空白"，
而那张图的整图标准差被边缘碎片抬到 **16.97** —— 于是它被判成"有内容"，
机器人从此拿它当画面：会话列表 OCR 只读到 '0' 和 '选'（两个碎片），
群名一个都认不出，标题读成空，什么都不会回。

这个脚本把那张图重建成固定样本（`customer_gray_shot`），锁住现在的判定。
    python test_capture_blank.py
"""
import sys

from PIL import Image, ImageDraw, ImageStat

from wechat_bot import _looks_blank, content_stats

_fail = []


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else ' X'}] {name}  {extra}")
    if not cond:
        _fail.append(name)


def customer_gray_shot(w=896, h=648):
    """客户那台 BitBlt 拿到的图：整片灰底 + 边缘碎片。

    碎片的位置照着诊断报告第 6 节来：OCR 在会话列表区读到 '0'(y≈229) 和 '选'(y≈628)，
    x 都是 4 —— 说明字全挤在最左边一列。右边一列也有几块（贴的那张图里能看到）。
    """
    im = Image.new("RGB", (w, h), (235, 235, 237))
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in ((0, 26, 4, 40), (0, 55, 8, 70), (0, 225, 7, 240),
                           (0, 320, 5, 335), (0, 380, 4, 395), (0, 440, 5, 455),
                           (0, 515, 4, 530), (0, 585, 9, 600), (0, 618, 10, 645)):
        d.rectangle([x0, y0, x1, y1], fill=(38, 38, 38))
    for y0 in (188, 428, 548, 605):
        d.rectangle([w - 8, y0, w - 1, y0 + 14], fill=(30, 30, 30))
    d.rectangle([0, h - 26, w, h], fill=(60, 80, 140))       # 底部那条（任务栏）
    return im


def wechat_like_shot(w=896, h=648):
    """正常微信窗口的样子：灰侧栏 + 白聊天区 + 标题条 + 会话行 + 气泡。"""
    im = Image.new("RGB", (w, h), (250, 250, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, int(w * 0.33), h], fill=(238, 238, 240))
    d.rectangle([int(w * 0.33), 0, w, 90], fill=(247, 247, 247))
    for i in range(9):
        y = 60 + i * 70
        d.rectangle([40, y, 150, y + 12], fill=(60, 60, 60))
        d.rectangle([40, y + 20, 260, y + 30], fill=(150, 150, 150))
    d.rectangle([int(w * 0.33) + 20, 60, int(w * 0.33) + 200, 250],
                outline=(180, 180, 180))                     # 输入框
    for i in range(5):
        y = 140 + i * 80
        d.rectangle([320, y, 500, y + 40], fill=(255, 255, 255), outline=(200, 200, 200))
        d.rectangle([640, y + 30, 800, y + 70], fill=(149, 236, 105))
    return im


def flat(w, h, color):
    return Image.new("RGB", (w, h), color)


def main():
    print("客户那台 BitBlt 的灰底图（有边缘碎片）：")
    gray = customer_gray_shot()
    st = content_stats(gray)
    old = ImageStat.Stat(gray.convert("L")).stddev[0]
    check("整图标准差仍然 >3（旧的判定就是这么被骗过去的）", old > 3.0,
          f"整图标准差={old:.2f}")
    check("新判定：空白", _looks_blank(gray),
          f"内部sd={st['sd']:.2f} 近单色={st['flat']:.3f} 边缘={st['edge']:.4f}")

    print("\n真微信窗口的样子（本机 30 多张真截图实测：sd 38~67 / 近单色 0.44~0.63 / 边缘 0.044~0.090）：")
    win = wechat_like_shot()
    st = content_stats(win)
    check("新判定：有内容", not _looks_blank(win),
          f"内部sd={st['sd']:.2f} 近单色={st['flat']:.3f} 边缘={st['edge']:.4f}")

    print("\n其它空白样态：")
    for name, im in (("全黑（PrintWindow 失败）", flat(896, 648, (0, 0, 0))),
                     ("全白", flat(896, 648, (255, 255, 255))),
                     ("全灰", flat(896, 648, (235, 235, 237))),
                     ("极小图（不该崩）", flat(8, 8, (0, 0, 0)))):
        check(f"{name} → 空白", _looks_blank(im))

    print("\n边缘那一圈不该算数（碎片就长在那儿）：")
    # 只有边框一圈有内容、内部是空白的图：真"抓不到画面"时就是这样
    im = Image.new("RGB", (896, 648), (235, 235, 237))
    d = ImageDraw.Draw(im)
    for x in range(0, 896, 3):
        d.rectangle([x, 0, x + 1, 2], fill=(0, 0, 0))
        d.rectangle([x, 645, x + 1, 647], fill=(0, 0, 0))
    check("只有上下边框有东西 → 空白", _looks_blank(im),
          f"{content_stats(im)}")

    print()
    if _fail:
        print(f"[X] {len(_fail)} 项没过: {_fail}")
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())