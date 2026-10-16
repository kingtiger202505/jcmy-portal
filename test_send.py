"""往两个群发一条测试消息，验证 send_text 能正常工作"""
import time
from wechat_multi_monitor import WeChatMultiMonitor

groups = ["视频号，剪映，微企", "ai测试2群"]
mon = WeChatMultiMonitor(groups, interval=3)

for group in groups:
    ts = time.strftime("%H:%M:%S")
    msg = f"🤖 bot 测试消息 [{ts}]"
    print(f"发送到 [{group}]: {msg}")
    mon.send_text(group, msg)
    print(f"  完成")
    time.sleep(1)

print("全部发送完毕")
