# -*- coding: utf-8 -*-
"""豆包订阅 QR 登录:二维码存 PNG + 轮询 + 落盘 session JSON。
用法: python aux_doubao_login.py <session_json_path> <qr_png_path>
"""
import json
import sys
import time
import os

from doubao2api.qr_login import QRLogin, QRStatus

session_path = sys.argv[1] if len(sys.argv) > 1 else ".doubao_session.json"
qr_png_path = sys.argv[2] if len(sys.argv) > 2 else "doubao_qr.png"

login = QRLogin()
done = {}


def on_status(st, msg):
    if st == QRStatus.FETCHING_QR and msg == "qr_ready" and login.qrcode_data:
        with open(qr_png_path, "wb") as f:
            f.write(login.qrcode_data)
        print(f"[qr-saved] {qr_png_path} bytes={len(login.qrcode_data)}", flush=True)
    print(f"[status] {st}: {msg}", flush=True)


def on_done(res):
    done["res"] = res
    # 由主循环统一处理


login.start(on_status=on_status, on_done=on_done)

# 等待: qr 落盘(2s内) 或 结束
deadline = time.time() + 150
while time.time() < deadline:
    if os.path.exists(qr_png_path) and os.path.getsize(qr_png_path) > 0:
        print("[qr-file-ok]", flush=True)
        break
    time.sleep(0.5)

# 轮询登录结果
while time.time() < deadline:
    if not login.is_running and not done:
        # 线程结束但回调可能还没跑? on_done 在线程尾部同步调用,先给 0.3s
        time.sleep(0.3)
    if done:
        break
    time.sleep(1.0)

if not done:
    print("[timeout] QR 未在 150s 内完成登录", flush=True)
    sys.exit(2)

res = done["res"]
print(f"[login-status] {res.status}", flush=True)
if not res.cookies.get("sessionid"):
    print(f"[error] {res.error or 'no sessionid'}", flush=True)
    sys.exit(3)

payload = {
    "cookies": res.cookies,
    "params": res.device_params or {},
}
with open(session_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
print(f"[session-saved] {session_path} sessionid_len={len(res.sessionid)}", flush=True)
print("[SUCCESS]", flush=True)
