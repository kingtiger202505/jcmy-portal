#!/usr/bin/env bash
# ===========================================================================
#  在 8.140.227.42 上装 ollama + 拉 bge-m3（中文向量化模型）
#
#  用法：整段粘进那台机器的终端（阿里云 Workbench 网页终端也行），或者
#        wget -O install-ollama.sh <本文件> && bash install-ollama.sh
#
#  为什么装它：给 Dify 知识库做中文 embedding。bge-m3 = 1024 维、8K 上下文、
#  中英文都强；自己跑不花 API 钱，数据也不出机器。
#
#  留意两点：
#   * bge-m3 加载后要占 1.5~2GB 内存，机器内存不够会 OOM（脚本第一步会打出来）
#   * 全程不走 github：官方安装脚本的二进制是 ollama.com 自己的 CDN
# ===========================================================================
set -euo pipefail

echo "===== 0. 机器情况 ====="
uname -m; echo "CPU 核数: $(nproc)"; free -m | head -2; df -h / | tail -1
echo "（bge-m3 要 1.5~2GB 内存；下面 Available 那一列不够 2GB 的话，建议先升配）"

echo
echo "===== 1. 装 ollama ====="
if command -v ollama >/dev/null 2>&1; then
  echo "已装：$(ollama --version 2>&1 | head -1)"
else
  if curl -fsSL --connect-timeout 20 -o /tmp/ollama_install.sh https://ollama.com/install.sh; then
    sh /tmp/ollama_install.sh
  else
    echo "[X] 连不上 ollama.com/install.sh。可选："
    echo "    1) 换个时段/换台能出网的机器下好二进制再传过来："
    echo "       https://ollama.com/download/ollama-linux-amd64.tgz"
    echo "       解到 /usr/local（tar -C /usr/local -xzf ollama-linux-amd64.tgz），"
    echo "       再手写 /etc/systemd/system/ollama.service 起服务"
    echo "    2) 检查这台机器的出网策略（是不是只能在安全组里放行过白名单）"
    exit 1
  fi
fi
systemctl enable --now ollama 2>/dev/null || true
sleep 2
ollama --version

echo
echo "===== 2. 拉 bge-m3（约 1.2GB，走 registry.ollama.ai）====="
ollama pull bge-m3
ollama list

echo
echo "===== 3. 验证接口能出向量 ====="
curl -s http://127.0.0.1:11434/api/embed \
  -d '{"model":"bge-m3","input":"发什么快递？几天能到？"}' | head -c 240; echo
echo "（上面应该返回 embedding 数组，长度 1024。报错 = 没起来，看 journalctl -u ollama -n 50）"

echo
echo "===== 4. 要让 Dify（115.29.198.190）调得到，还得三步 ====="
cat <<'TIP'
  A. 让 ollama 监听所有网卡（默认只听 127.0.0.1）：
       systemctl edit ollama
     在打开的编辑器里填：
       [Service]
       Environment="OLLAMA_HOST=0.0.0.0:11434"
     保存后：systemctl daemon-reload && systemctl restart ollama
     验证：ss -lntp | grep 11434   应显示 0.0.0.0:11434

  B. 阿里云安全组放行 11434 —— **只放 Dify 那台的 IP**，别开 0.0.0.0/0
     （ollama 没有任何鉴权，全网开放等于谁都能白嫖你的机器）

  C. Dify 里配：设置 → 模型供应商 → Ollama → Base URL 填 http://8.140.227.42:11434
     然后知识库的 embedding 模型选 bge-m3

  注意：换 embedding 模型 = 换向量空间，Dify 里那份知识库必须**重新索引**
  （删掉文档重传，或者用「重新索引」），旧向量不能用。
TIP