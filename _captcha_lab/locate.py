"""用带 alpha 掩码的模板匹配定位缺口 —— 量化判据，不靠肉眼。

思路：拼图块的像素内容 = 背景图在缺口处的原始像素。
      把拼图块（非透明部分作掩码）拿去和 fullbg 做 TM_SQDIFF，
      差值最小的地方就是它从哪儿抠下来的。
顺便也对 bg 做一遍，用来确认哪个 canvas 才是"完整背景"。
"""
import base64
import io
import json
import os

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out")


def load(name):
    raw = open(os.path.join(DATA, name), encoding="utf-8").read().strip()
    if raw.startswith('"'):
        raw = json.loads(raw)
    if raw.startswith("data:image"):
        raw = raw.split(",", 1)[1]
    arr = np.array(Image.open(io.BytesIO(base64.b64decode(raw))))
    if arr.shape[2] == 4:
        return arr[:, :, :3][:, :, ::-1].copy(), arr[:, :, 3].copy()
    return arr[:, :, ::-1].copy(), None


full, _ = load("fullbg.b64")
bg, _ = load("bg.b64")
sl_rgb, sl_a = load("slice.b64")
H, W = full.shape[:2]

cols = np.where(sl_a.max(axis=0) > 0)[0]
rows = np.where(sl_a.max(axis=1) > 0)[0]
px0, py0 = int(cols[0]), int(rows[0])
pw, ph = int(cols[-1]) - px0 + 1, int(rows[-1]) - py0 + 1

piece = sl_rgb[py0:py0 + ph, px0:px0 + pw]
alpha = sl_a[py0:py0 + ph, px0:px0 + pw]
mask = (alpha > 200).astype(np.uint8) * 255          # 只信实心像素，避开羽化边
m3 = np.repeat(mask[:, :, None], 3, axis=2).astype(np.float32)
n = max(1, int((mask > 0).sum()))
print(f"拼图块 {pw}x{ph} @slice({px0},{py0})  实心像素 {n}")

for label, scene in (("fullbg", full), ("bg", bg)):
    res = cv2.matchTemplate(scene.astype(np.float32), piece.astype(np.float32),
                            cv2.TM_SQDIFF, mask=m3)
    res = res / (255.0 * 255.0 * n)                   # 归一成"平均每像素平方差"
    flat = res.ravel()
    order = np.argsort(flat)[:5]
    print(f"\n== 以 {label} 为底图，差值最小的 5 个位置（值越小越像）")
    for i in order:
        y, x = np.unravel_index(i, res.shape)
        print(f"   x={x:3d} y={y:3d}   cost={flat[i]:8.2f}")
    y, x = np.unravel_index(order[0], res.shape)
    if label == "fullbg":
        best = (int(x), int(y))
        best_cost = float(flat[order[0]])
        col = res[y, :]
        print(f"   在 y={y} 这行的代价曲线（每 10px 采样）:")
        print("   " + " ".join(f"{xx}:{col[xx]:.0f}" for xx in range(0, W - pw, 10)))
    else:
        print(f"   -> {label} 最优 x={x}（若这个值远大于 fullbg 的，说明 {label} 是被抠过的图）")

gx, gy = best
print(f"\n【结论】缺口位置 x={gx} y={gy}")
print(f"        拼图块起点 x0={px0}  ->  滑块需拖动 ≈ {gx - px0}px")

# 落一份纯数字结果，方便脚本解析（控制台中文会乱码）
contrast = float(np.median(flat)) / max(best_cost, 1e-6)
print(f"        置信度对比度 = {contrast:.1f}x  (>=3 才算可信)")
with open(os.path.join(OUT, "answer.txt"), "w", encoding="utf-8") as f:
    f.write(f"{gx} {gy} {px0} {py0} {contrast:.2f}\n")

# 出对比图：左右并排，左=原位，右=贴到缺口
left = full.copy()
roi = left[gy:gy + ph, gx:gx + pw]
a = alpha[:, :, None].astype(np.float32) / 255.0
roi[:] = (piece.astype(np.float32) * a + roi.astype(np.float32) * (1 - a)).astype(np.uint8)

x0c, x1c = max(0, gx - 50), min(W, gx + pw + 50)
y0c, y1c = max(0, gy - 25), min(H, gy + ph + 25)
crop = left[y0c:y1c, x0c:x1c]
cv2.imwrite(os.path.join(OUT, "match_fit.png"),
            cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_LANCZOS4))
print(f"  对比图 -> out/match_fit.png   (成本 {res[gy, gx]:.2f})")