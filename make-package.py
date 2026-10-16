# -*- coding: utf-8 -*-
"""打部署包：把要发给客户的文件收进 dist/wechat-cs-bot.zip。

为什么写成脚本而不是手工拷：这份包改过好几轮，手拷漏过 config.json、也差点把
pip 装的 .venv（几百 MB）打进包里。这里把"哪些文件进包"写死成一张表，
外加包内专用的 groups.txt / config.json（群名跟本机不一样）。

用法：python make-package.py            # 群名用下面 GROUPS
      python make-package.py 群A,群B     # 临时指定群名
"""
import json
import os
import shutil
import sys
import zipfile

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8","utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(HERE, "dist", "wechat-cs-bot")
ZIP = os.path.join(HERE, "dist", "wechat-cs-bot.zip")

# 包里都放什么（本机专用的东西一律不进：debug_*.png、cs_decisions.jsonl、bot.log、.venv……）
FILES = {
    "wechat_group_bot.py": "wechat_group_bot.py",
    "wechat_bot.py": "wechat_bot.py",
    "knowledge.md": "knowledge.md",
    "requirements.txt": "requirements.txt",
    "start.bat": "start.bat",
    "dryrun.bat": "dryrun.bat",
    "diagnose.bat": "diagnose.bat",
    "diagnose.py": "diagnose.py",
    "README.txt": "README.txt",
    "测试用例.txt": "TEST-CASES.txt",      # 包内用英文名，免得解压时中文文件名变乱码
}
GROUPS = ["测试群", "测试群2"]

GROUPS_TXT = """# ============================================================
#  要监控哪些群 —— 一行写一个群名，保存后重新启动机器人就生效。
#
#  * 群名必须和微信**会话列表里显示的**一模一样（差一个字、多个空格都找不到那个群）。
#  * 以 # 开头的行是注释，不会被当成群名。
#  * 这里列出的群**都会自动回复**（也都在监控）；
#    只想监控、不自动回复的群，去 config.json 里写 "auto_reply": {{"群名": false}}。
#  * 不确定群名怎么写？启动机器人，看黑窗口里那几行：
#    「[群名] 不在会话列表可视区」说明没认到，多半是名字对不上。
#
#  注意：列表里如果有名字很像的群（比如「测试群」和「测试群2」），
#  机器人会按**完全一致**来认，不会认错；名字写错它宁可不切，也不去挤隔壁那个群。
# ============================================================

{groups}
"""


def main():
    groups = [g.strip() for g in (sys.argv[1].split(",") if len(sys.argv) > 1 else GROUPS) if g.strip()]
    # 不删整个目录（它的父目录可能是当前工作目录，Windows 会报"设备或资源忙"），
    # 只清掉上一轮留下的同名文件；真正进包的是下面写死的这张名单。
    os.makedirs(STAGE, exist_ok=True)

    for src, dst in FILES.items():
        src_path = os.path.join(HERE, src)
        if not os.path.exists(src_path):
            print(f"[X] 少了 {src} —— 先在项目目录里把它补回来")
            return 1
        shutil.copy2(src_path, os.path.join(STAGE, dst))

    with open(os.path.join(STAGE, "groups.txt"), "w", encoding="utf-8") as f:
        f.write(GROUPS_TXT.format(groups="\n".join(groups)))

    # config.json：Dify 那几项沿用本机那份（一份来源，免得密钥要改两处），只换群名
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["groups"] = groups
    cfg.pop("reply_groups", None)          # 列出的群默认全回，不写白名单
    cfg["_说明"] = ("机器人配置。**平时只需要改 groups.txt（群名）**，这个文件里的东西一般不用动。"
                 "群名必须是会话列表里显示的那个名字（一字不差）。这里列出的群一律既监控又回复；"
                 "要单独关掉某个群的自动回复，加 auto_reply。命令行参数优先级高于本文件。")
    with open(os.path.join(STAGE, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    entries = sorted(list(FILES.values()) + ["groups.txt", "config.json"])
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for name in entries:
            z.write(os.path.join(STAGE, name), f"wechat-cs-bot/{name}")

    size = os.path.getsize(ZIP) / 1024
    print(f"包好了: {ZIP}  {size:.0f} KB")
    print(f"群名: {groups}")
    print("内容:")
    with zipfile.ZipFile(ZIP) as z:
        for i in z.infolist():
            print(f"   {i.filename:38s} {i.file_size:7d} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())