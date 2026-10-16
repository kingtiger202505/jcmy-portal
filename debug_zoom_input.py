# -*- coding: utf-8 -*-
"""对 step5 截图的底部区域做 2x 放大 OCR，带位置打印，看粘贴的文字到底进没进输入框"""
import io
import sys

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

img = Image.open("debug_flow_step5_after_paste.png")
W, H = img.size
print(f"截图 {W}x{H}")

ocr = RapidOCR()
# 底部区域：聊天区末尾 + 输入框 + 工具栏，放大 2 倍 OCR
bottom = img.crop((int(W * 0.25), int(H * 0.84), W, H))
bottom = bottom.resize((bottom.width * 2, bottom.height * 2), Image.LANCZOS)
result, _ = ocr(np.array(bottom))
if result:
    bh = bottom.height
    items = sorted(result, key=lambda l: sum(p[1] for p in l[0]) / 4)
    for box, text, score in items:
        cy = sum(p[1] for p in box) / 4 / bh
        cx = sum(p[0] for p in box) / 4 / bottom.width
        # 换算回整图比例：0.84H + cy*0.16H
        full_y = 0.84 + cy * 0.16
        print(f"  y={full_y:.3f} x={cx:.3f} ({score:.2f}) {text!r}")
else:
    print("无 OCR 结果")
