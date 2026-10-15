#!/usr/bin/env python3
"""voice-mcp — 用语音控制一台边缘设备：听 → 想(工具调用) → 做 → 说。

管线固定，后端可切换（这是核心：云端先出效果，本地是护城河）：
  STT_BACKEND = text | cloud | local     （语音转文字）
  TTS_BACKEND = none | cloud | local     （文字转语音）
  LLM_BASE_URL / LLM_MODEL               （DeepSeek 云 或 本地 llama.cpp，OpenAI 兼容）

用法：
  python voice_mcp.py --text             # 打字输入（不用麦克风，先测管线）
  python voice_mcp.py                    # 按 STT_BACKEND 走麦克风
  python voice_mcp.py --self-test        # 自检：打印工具 schema 并 DRY-RUN 执行
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

import device_tools as dt

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
STT_BACKEND = os.getenv("STT_BACKEND", "text")
TTS_BACKEND = os.getenv("TTS_BACKEND", "none")
MAX_STEPS = int(os.getenv("VOICE_MAX_STEPS", "6"))
SYSTEM = os.getenv(
    "VOICE_SYSTEM",
    "你是运行在一台边缘设备上的语音助手。用户用语音下达指令，你调用工具操作这台设备。"
    "回答要简短、口语化（会被朗读出来）。需要操作设备时调用对应工具。",
)


# ------------------------------------------------------------------ STT

def _record_wav(seconds: float = 4.0) -> str:
    import numpy as np
    import sounddevice as sd
    import wave

    rate = 16000
    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16")
    sd.wait()
    path = tempfile.mktemp(suffix=".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.tobytes())
    return path


def stt_text() -> str:
    return input("🎤 (输入) > ").strip()


def stt_cloud(wav: str) -> str:
    from openai import OpenAI
    client = OpenAI(base_url=LLM_BASE_URL, api_key=os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    with open(wav, "rb") as f:
        r = client.audio.transcriptions.create(model=os.getenv("STT_MODEL", "whisper-1"), file=f)
    return (r.text or "").strip()


def stt_local(wav: str) -> str:
    bin_ = os.getenv("WHISPER_BIN", "whisper-cli")
    model = os.getenv("WHISPER_MODEL", "models/ggml-base.bin")
    r = subprocess.run([bin_, "-m", model, "-f", wav, "-nt"], capture_output=True, text=True)
    return (r.stdout or "").strip()


def listen() -> str:
    if STT_BACKEND == "text":
        return stt_text()
    wav = _record_wav(float(os.getenv("RECORD_SECONDS", "4")))
    try:
        return stt_cloud(wav) if STT_BACKEND == "cloud" else stt_local(wav)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


# ------------------------------------------------------------------ TTS

def _play(path: str) -> None:
    for player in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                   ["aplay", "-q", path], ["mpv", "--no-video", "--really-quiet", path]):
        if _which(player[0]):
            subprocess.run(player, capture_output=True)
            return


def _which(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


def speak(text: str) -> None:
    print(f"🔊 {text}")
    if TTS_BACKEND == "none":
        return
    try:
        if TTS_BACKEND == "cloud":
            from openai import OpenAI
            client = OpenAI(base_url=LLM_BASE_URL, api_key=os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"))
            out = tempfile.mktemp(suffix=".mp3")
            with client.audio.speech.with_streaming_response.create(
                model=os.getenv("TTS_MODEL", "tts-1"), voice="alloy", input=text
            ) as resp:
                resp.stream_to_file(out)
            _play(out)
        else:  # local: piper
            out = tempfile.mktemp(suffix=".wav")
            p = subprocess.run(["piper", "--model", os.getenv("PIPER_MODEL", "en_US-lessac-medium.onnx"),
                                "--output_file", out], input=text.encode(), capture_output=True)
            if p.returncode == 0:
                _play(out)
    except Exception as e:  # noqa: BLE001
        print(f"[tts warn] {e}", file=sys.stderr)


# ------------------------------------------------------------------ LLM loop

def _client():
    from openai import OpenAI
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("缺少 DEEPSEEK_API_KEY（或 OPENAI_API_KEY）")
    return OpenAI(base_url=LLM_BASE_URL, api_key=key)


def _to_dict(msg) -> dict:
    d = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    return d


def run_turn(client, history: list, user_text: str) -> str:
    history.append({"role": "user", "content": user_text})
    for _ in range(MAX_STEPS):
        resp = client.chat.completions.create(model=LLM_MODEL, messages=history, tools=dt.SCHEMAS)
        msg = resp.choices[0].message
        history.append(_to_dict(msg))
        if not msg.tool_calls:
            return msg.content or ""
        for tc in msg.tool_calls:
            args = {}
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                pass
            print(f"  ⚙ {tc.function.name}({args})")
            result = dt.call(tc.function.name, args)
            print(f"    → {result[:200]}")
            history.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    return "(达到最大步数，已停止)"


# ------------------------------------------------------------------ main

def self_test() -> None:
    print("工具 schema：", [s["function"]["name"] for s in dt.SCHEMAS])
    print("--- 状态 ---"); print(dt.call("get_status", {}))
    print("--- 服务 ---"); print(dt.call("list_services", {}))
    print("--- 非法服务(应拒绝) ---"); print(dt.call("control_service", {"action": "restart", "name": "sshd"}))
    print("--- 非法命令(应拒绝) ---"); print(dt.call("run_command", {"cmd": "rm -rf /"}))
    print("--- 白名单命令 ---"); print(dt.call("run_command", {"cmd": "uptime"}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true", help="打字输入（不用麦克风）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    global STT_BACKEND
    if args.text:
        STT_BACKEND = "text"

    client = _client()
    history = [{"role": "system", "content": SYSTEM}]
    print(f"voice-mcp 就绪（STT={STT_BACKEND} TTS={TTS_BACKEND} LLM={LLM_MODEL} via {LLM_BASE_URL}）")
    while True:
        try:
            user = listen()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user.lower() in ("exit", "quit", "退出"):
            break
        reply = run_turn(client, history, user)
        if reply:
            speak(reply)


if __name__ == "__main__":
    main()
