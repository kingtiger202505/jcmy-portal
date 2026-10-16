"""拿极验静态服务器上的原图算标准答案。
用法: solve_pair.py <fullbg.jpg 完整背景> <slice.png 拼图块>
"""
import sys

import cv2
import numpy as np

full_p, slice_p = sys.argv[1], sys.argv[2]
full = cv2.imread(full_p)
sl = cv2.imread(slice_p, cv2.IMREAD_UNCHANGED)
print(f"背景 {full.shape}  拼图块 {sl.shape}")
assert sl.shape[2] == 4, "拼图块需要 alpha 通道"

a = sl[:, :, 3]
cols = np.where(a.max(axis=0) > 0)[0]
rows = np.where(a.max(axis=1) > 0)[0]
px0, py0 = int(cols[0]), int(rows[0])
pw, ph = int(cols[-1]) - px0 + 1, int(rows[-1]) - py0 + 1
print(f"拼图块 bbox @({px0},{py0}) {pw}x{ph}")

piece = sl[py0:py0 + ph, px0:px0 + pw, :3].copy()
alpha = a[py0:py0 + ph, px0:px0 + pw]
mask = (alpha > 200).astype(np.float32)
piece[alpha < 200] = 0
m3 = np.repeat(mask[:, :, None], 3, axis=2)
n = max(1, int(mask.sum()))

res = cv2.matchTemplate(full.astype(np.float32), piece.astype(np.float32),
                        cv2.TM_SQDIFF, mask=m3) / (255.0 * 255.0 * n)

flat = res.ravel()
order = np.argsort(flat)[:5]
print("\n差值最小的 5 个位置:")
for i in order:
    y, x = np.unravel_index(i, res.shape)
    print(f"  x={x:3d} y={y:3d}  cost={flat[i]:9.2f}")

y, x = np.unravel_index(order[0], res.shape)
best_cost = flat[order[0]]
worst = np.median(flat)
print(f"\n【标准答案】x={x} y={y}")
print(f"  谷底 cost={best_cost:.0f}  中位数 cost={worst:.0f}  对比度={worst / max(best_cost, 1e-6):.1f}x")
if worst / max(best_cost, 1e-6) < 3:
    print("  !! 对比度偏低，这个匹配不可信")