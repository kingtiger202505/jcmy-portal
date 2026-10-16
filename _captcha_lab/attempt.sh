#!/bin/bash
# 一次完整尝试：刷新 -> 装探针 -> 弹滑块 -> 抓图 -> 求解 -> 拖动 -> 读服务端判决
# 抓到的题若纹理太差（对比度 < 3x）就换一题，最多换 4 次
S=wpgm
LAB=D:/code/jcmy-portal/_captcha_lab
DATA=$LAB/data
OUT=$LAB/out

bsk reload --hard --session $S >/dev/null 2>&1
sleep 4
bsk evaluate "$(cat $LAB/hook.js)" --session $S 2>/dev/null
echo "hook: $(bsk evaluate 'window.__gt_hooked===true?"ok":"FAILED"' --session $S 2>/dev/null)"
bsk click ".geetest_radar_btn" --session $S >/dev/null 2>&1
sleep 10

DIST=""
for try in 1 2 3 4; do
  for pair in "bg.geetest_canvas_bg" "fullbg.geetest_canvas_fullbg" "slice.geetest_canvas_slice"; do
    n="${pair%%.*}"; c="${pair#*.}"
    bsk evaluate "document.querySelector('.$c').toDataURL('image/png')" --session $S > "$DATA/$n.b64" 2>/dev/null
  done
  python -I $LAB/locate.py >/dev/null 2>&1
  [ -s $OUT/answer.txt ] || { echo "  第 $try 次: 求解失败"; continue; }
  read -r GX GY PX0 PY0 CONTRAST < $OUT/answer.txt
  CONTRAST=${CONTRAST%$'\r'}
  ok=$(python -I -c "print(1 if float('$CONTRAST')>=3 else 0)" 2>/dev/null | tr -d '\r\n')
  echo "  第 $try 次抓图: 缺口 x=$GX y=$GY  对比度=${CONTRAST}x  $([ "$ok" = 1 ] && echo 合格 || echo '太平坦，换题')"
  if [ "$ok" = "1" ]; then
    DIST=$((GX - PX0))
    break
  fi
  bsk click ".geetest_refresh_1" --session $S >/dev/null 2>&1
  sleep 7
done

if [ -z "$DIST" ]; then echo "四次都没抓到有纹理的题，放弃拖动"; exit 0; fi
echo "拖动距离 $DIST px"
sed "s/__DIST__/$DIST/" $LAB/drag.js > $LAB/.drag_run.js
echo "drag: $(bsk evaluate "$(cat $LAB/.drag_run.js)" --session $S 2>/dev/null)"
sleep 5

echo "--- 极验 JSONP 回调（服务端真实回复） ---"
bsk evaluate 'JSON.stringify(window.__gt_cbs||[])' --session $S 2>/dev/null
echo "--- 页面 UI ---"
bsk evaluate 'JSON.stringify({tip:(document.querySelector(".geetest_radar_tip_content")||{}).textContent,panel:getComputedStyle(document.querySelector(".geetest_panel")||document.body).display})' --session $S 2>/dev/null