# -*- coding: utf-8 -*-
"""豆包订阅 OpenAI 兼容桥:把 DoubaoChatClient(订阅额度)包成 /v1/chat/completions。
用法: python aux_doubao_bridge.py [session_json] [port]
注意: 仅本机 127.0.0.1,非流式文本对话;串行锁防风控。
"""
import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from doubao2api.client import DoubaoChatClient

SESSION = sys.argv[1] if len(sys.argv) > 1 else ".doubao_session.json"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9090
THINK = int(os.environ.get("DOUBAO_SUB_THINK", "0"))  # 0=快速 1=思考 2=自动 3=专家(豆包2.1 Turbo 专家)

app = FastAPI()
_lock = asyncio.Lock()
_client: Optional[DoubaoChatClient] = None
_cid: str = ""  # 复用同一会话(风控友好): 首请求创建, 后续续用 + 每轮 click_clear_context 保证独立上下文
_calls = 0
_upstream_attempts = 0
TARGETED = os.environ.get("DOUBAO_TARGETED_ROUND") == "1"


class ChatBody(BaseModel):
    model: str = "doubao"
    messages: List[Dict[str, Any]] = []
    max_tokens: int = 700
    temperature: float = 0.3
    stream: bool = False
    need_deep_think: int = 0


@app.on_event("startup")
async def startup():
    global _client
    if TARGETED and (THINK != 3 or os.environ.get("DOUBAO_FRESH") != "1"):
        raise RuntimeError("targeted bridge requires expert mode and fresh context")
    _client = (DoubaoChatClient.from_session(SESSION, captcha_handler=None, max_captcha_retries=0)
               if TARGETED else DoubaoChatClient.from_session(SESSION))
    await _client.__aenter__()


@app.on_event("shutdown")
async def shutdown():
    if _client is not None:
        await _client.__aexit__(None, None, None)


@app.get("/health")
async def health():
    return {"ok": True, "session": os.path.basename(SESSION), "think": THINK,
            "fresh": os.environ.get("DOUBAO_FRESH") == "1", "targeted": TARGETED,
            "upstream_attempts": _upstream_attempts, "successful_calls": _calls}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "doubao", "object": "model"}]}


def _last_text(messages):
    for m in reversed(messages or []):
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            return c
        if isinstance(c, list):
            parts = [x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"]
            joined = "".join(parts).strip()
            if joined:
                return joined
    return ""


@app.post("/v1/chat/completions")
async def chat_completions(body: ChatBody):
    prompt = _last_text(body.messages)
    if not prompt:
        return JSONResponse({"error": {"message": "empty prompt"}}, status_code=400)
    text = ""
    last_err = ""
    backoff = [] if TARGETED else [20, 40, 80, 120]
    global _cid, _calls, _upstream_attempts
    for attempt in range(len(backoff) + 1):
        try:
            async with _lock:  # 串行防风控
                fresh = os.environ.get("DOUBAO_FRESH") == "1"  # 每请求新会话(避开同会话保守拒答)
                dt = int(getattr(body, "need_deep_think", 0)) or THINK
                if TARGETED:
                    if _upstream_attempts >= 8:
                        return JSONResponse({"error": {"message": "targeted budget exhausted"}}, status_code=429)
                    if dt != 3 or not fresh:
                        return JSONResponse({"error": {"message": "targeted configuration mismatch"}}, status_code=400)
                    from targeted_round import PrivacyGate
                    PrivacyGate().check(prompt)
                _upstream_attempts += 1
                r = await _client.chat_completion(
                    prompt,
                    need_deep_think=dt,
                    conversation_id="" if fresh else _cid,
                    click_clear_context=(not fresh),
                )
            text = getattr(r, "text", "") or ""
            if getattr(r, "conversation_id", None):
                _cid = r.conversation_id           # 记录会话id供后续续用
            _calls += 1
            print(f"[bridge] successful_calls={_calls} upstream_attempts={_upstream_attempts}", flush=True)
            break
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            if TARGETED:
                from targeted_round import PrivacyBlocked
                code = 400 if isinstance(e, PrivacyBlocked) else 502
                print(f"[bridge-stopped] error_class={type(e).__name__} upstream_attempts={_upstream_attempts}", flush=True)
                return JSONResponse({"error": {"message": "targeted upstream stopped"}}, status_code=code)
            last_err = msg
            is_rl = "RateLimit" in msg or "71002200" in msg or "rate" in msg.lower()
            if is_rl and attempt < len(backoff):
                print(f"[bridge-rl] attempt {attempt + 1} sleep {backoff[attempt]}s: {msg[:120]}", flush=True)
                await asyncio.sleep(backoff[attempt])
                continue
            if attempt < len(backoff):
                print(f"[bridge-retry] attempt {attempt + 1} sleep {backoff[attempt]}s: {msg[:120]}", flush=True)
                await asyncio.sleep(backoff[attempt])
                continue
    if not text:
        return JSONResponse({"error": {"message": last_err}}, status_code=502)
    return {
        "id": "chatcmpl-doubao-sub",
        "object": "chat.completion",
        "model": body.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
