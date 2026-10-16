"""从落盘的 canvas dataURL 还原图片并求滑块缺口 x。

两条互相独立的路子：
  A. 差值法      —— fullbg 与 bg 相减。bg 是抠掉拼图块后的图，差值直接框出缺口。
  B. 模板匹配法  —— 从 slice 里抠出拼图块（靠 alpha），用 Canny 边缘做 matchTemplate
                    在全背景图里找最匹配的位置。
两者对上了才敢说"解出来了"。

输出 4 张图到 out/ 供肉眼复核。
"""
import base64
import io
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)


def load_b64(name):
    raw = open(os.path.join(DATA, name), encoding="utf-8").read().strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = json.loads(raw)
    if raw.startswith("data:image"):
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw)


def unpack(name, data):
    """dataURL -> (BGR 数组, alpha 或 None)"""
    p = os.path.join(OUT, name + ".png")
    with open(p, "wb") as f:
        f.write(data)
    im = Image.open(io.BytesIO(data))
    arr = np.array(im)
    if arr.ndim == 3 and arr.shape[2] == 4:
        return arr[:, :, :3][:, :, ::-1].copy(), arr[:, :, 3].copy(), im.size
    return arr[:, :, ::-1].copy(), None, im.size


print("[1] 还原图片")
full, _, full_sz = unpack("fullbg", load_b64("fullbg.b64"))
bg, _, bg_sz = unpack("bg", load_b64("bg.b64"))
slice_rgb, slice_a, sl_sz = unpack("slice", load_b64("slice.b64"))
H, W = full.shape[:2]
for n, sz in (("fullbg", full_sz), ("bg", bg_sz), ("slice", sl_sz)):
    print(f"  {n}: {sz[0]}x{sz[1]}")
assert full_sz == bg_sz == sl_sz, "三个 canvas 尺寸不一致"

# ---- 拼图块在 slice 里的位置 ----
cols = np.where(slice_a.max(axis=0) > 0)[0]
rows = np.where(slice_a.max(axis=1) > 0)[0]
px0, px1 = int(cols[0]), int(cols[-1])
py0, py1 = int(rows[0]), int(rows[-1])
pw, ph = px1 - px0 + 1, py1 - py0 + 1
print(f"  拼图块 bbox: x={px0} y={py0} {pw}x{ph}")

# ---- 方法 A：差值法 ----
print("\n[A] 差值法 absdiff(fullbg, bg)")
diff = cv2.absdiff(full, bg)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
_, th = cv2.threshold(gray, 25, 255, cv2.THRESH_BINARY)
th = cv2.medianBlur(th, 5)
cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
cands = []
for c in cnts:
    x, y, w, h = cv2.boundingRect(c)
    if 30 <= w <= 90 and 30 <= h <= 90 and cv2.contourArea(c) > 500:
        cands.append((x, y, w, h))
cands.sort(key=lambda t: t[0])
for x, y, w, h in cands:
    print(f"  候选 x={x:3d} y={y:3d} {w}x{h}{'   <- 左侧原位，忽略' if x < 30 else ''}")
gaps = [c for c in cands if c[0] >= 30]
A = None
if gaps:
    gx, gy, gw, gh = max(gaps, key=lambda t: t[2] * t[3])
    A = (gx, gy, gw, gh)
    print(f"  -> 缺口 x={gx} y={gy} {gw}x{gh}")

# ---- 方法 B：边缘模板匹配 ----
print("\n[B] Canny 边缘 + matchTemplate")
piece = slice_rgb[py0:py0 + ph, px0:px0 + pw]
piece_mask = slice_a[py0:py0 + ph, px0:px0 + pw]
piece[piece_mask == 0] = 255  # 透明处填白，避免边缘算子吃到乱边
pe = cv2.Canny(piece, 100, 200)
fe = cv2.Canny(full, 100, 200)
res = cv2.matchTemplate(fe, pe, cv2.TM_CCOEFF_NORMED)
res[:, 0:max(1, px0 + 8)] = -1.0  # 屏蔽拼图块自身的起始位置
_, maxv, _, maxloc = cv2.minMaxLoc(res)
B = (maxloc[0], maxloc[1], pw, ph)
print(f"  -> 缺口 x={B[0]} y={B[1]} 置信度={maxv:.4f}")

# ---- 汇总 ----
print("\n[汇总]")
print(f"  A 差值法   x = {A[0] if A else None}")
print(f"  B 模板匹配 x = {B[0]}")
drag = None
if A:
    if abs(A[0] - B[0]) <= 8:
        print(f"  两法一致（差 {abs(A[0] - B[0])}px）-> 判定缺口 x = {A[0]}")
        drag = A[0]
    else:
        print(f"  两法不一致（差 {abs(A[0] - B[0])}px），需人工看图定夺")
    print(f"  拼图块起点 x0={px0} -> 需要拖动的水平距离 ≈ {A[0] - px0}px")

# ---- 出图 ----
vis = full.copy()
if A:
    cv2.rectangle(vis, (A[0], A[1]), (A[0] + A[2], A[1] + A[3]), (0, 0, 255), 2)
cv2.rectangle(vis, (B[0], B[1]), (B[0] + B[2], B[1] + B[3]), (255, 0, 0), 1)
cv2.putText(vis, f"A={A[0] if A else '-'} B={B[0]}", (4, 14),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)
cv2.imwrite(os.path.join(OUT, "annotated.png"), vis)

def composite(x, y):
    """把拼图块按 alpha 贴到 (x, y)，返回贴好的整图"""
    tgt = full.copy()
    if y + ph > H or x + pw > W:
        return None
    roi = tgt[y:y + ph, x:x + pw]
    if roi.shape[:2] != (ph, pw):
        return None
    m = piece_mask[:, :, None].astype(np.float32) / 255.0
    roi[:] = (piece.astype(np.float32) * m + roi.astype(np.float32) * (1 - m)).astype(np.uint8)
    return tgt


# 拼图块在 slice 里画在 (px0, py0)，是"零位移"参考点。
# y 一律用 py0 —— A 的 diff 框含极验那条竖直阴影带，y 不准；这里只比 x。
CANDS = {"A_diff": (A[0], py0) if A else None, "B_tmpl": (B[0], py0)}
print("\n[出图] 把拼图块贴回去比对")
for name, xy in CANDS.items():
    if not xy:
        continue
    tgt = composite(*xy)
    if tgt is None:
        print(f"  {name}: x={xy[0]} 越界，跳过")
        continue
    x0c, x1c = max(0, xy[0] - 45), min(W, xy[0] + pw + 45)
    cv2.imwrite(os.path.join(OUT, f"fit_{name}.png"),
                cv2.resize(tgt[:, x0c:x1c], None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
    print(f"  fit_{name}.png  贴图位置 x={xy[0]} y={xy[1]}")

cv2.imwrite(os.path.join(OUT, "annotated.png"), vis)
print("  annotated.png  标注图")