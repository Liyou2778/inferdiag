"""本地大模型对话终端（vLLM / OpenAI 兼容）。

用法：
    uv run python scripts/chat_local.py                       # 自动发现模型，连本机 8000
    uv run python scripts/chat_local.py --url http://localhost:8000/v1/chat/completions --model <名>
退出：输入 exit 或 Ctrl+C。
"""

from __future__ import annotations

import argparse
import json
import urllib.request


def discover(base: str) -> str:
    """从 /v1/models 找出服务模型名。"""
    with urllib.request.urlopen(base + "/v1/models", timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["data"][0]["id"]


def chat_once(url: str, model: str, content: str, max_tokens: int = 300) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Talk to your local LLM (vLLM/OpenAI-compatible)")
    ap.add_argument("--url", default="http://localhost:8000/v1/chat/completions")
    ap.add_argument("--model", default=None, help="默认从 /v1/models 自动发现")
    ap.add_argument("--max-tokens", type=int, default=300)
    args = ap.parse_args()

    # 服务器根地址：去掉 "/v1/chat/completions" 尾巴
    if "/v1/chat/completions" in args.url:
        server = args.url.split("/v1/chat/completions", 1)[0]
    elif "/chat/completions" in args.url:
        server = args.url.split("/chat/completions", 1)[0]
    else:
        server = args.url.rstrip("/")
    if args.model:
        model = args.model
        print(f"模型（手动指定）: {model}")
    else:
        model = discover(server)  # discover 内部拼 /v1/models
        print(f"模型（自动发现）: {model}")

    print("本地模型对话就绪（输入 exit 退出 / Ctrl+C 中断）")
    while True:
        try:
            user = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user.lower() in {"exit", "quit", "退出"}:
            break
        try:
            reply = chat_once(args.url, model, user, args.max_tokens)
            print(f"模型: {reply}\n")
        except Exception as exc:  # noqa: BLE001
            print(f"[错误] {exc}")


if __name__ == "__main__":
    main()
