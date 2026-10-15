"""设备动作工具（agent 的「手」）。

在本地设备上执行：查状态、控制服务、GPIO、白名单命令。
通过函数 schema 暴露给 LLM 做 tool-calling；DRY_RUN=1 时只打印不执行。

设计原则：**白名单 + 最小权限**——语音输入是不可信来源，绝不能任意执行命令。
"""

import os
import platform
import shutil
import subprocess

DRY_RUN = os.getenv("DRY_RUN") == "1"

# 允许的服务 / 命令白名单（按需扩展）
SERVICE_ALLOW = set((os.getenv("SERVICE_ALLOW") or "agent,camera,collector,sensor").split(","))
COMMAND_ALLOW = {"uptime", "free", "df", "uname", "tegrastats", "vcgencmd"}


def _run(cmd: list[str], timeout: int = 15) -> str:
    if DRY_RUN:
        return f"[dry-run] {' '.join(cmd)}"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()[:2000] or "(no output)"
    except FileNotFoundError:
        return f"(未安装: {cmd[0]})"
    except subprocess.TimeoutExpired:
        return "(超时)"


def get_status() -> str:
    """设备概览：主机/负载/内存/磁盘/温度。"""
    parts = [f"host={platform.node()} arch={platform.machine()}", _run(["uptime"])]
    parts.append(_run(["free", "-h"]))
    parts.append(_run(["df", "-h", "/"]))
    temp = _read_temp()
    if temp:
        parts.append(f"temp={temp}")
    return "\n".join(p for p in parts if p)


def _read_temp() -> str:
    for p in ("/sys/class/thermal/thermal_zone0/temp", "/sys/devices/virtual/thermal/thermal_zone0/temp"):
        try:
            with open(p) as f:
                return f"{int(f.read().strip()) / 1000:.1f}°C"
        except Exception:
            continue
    return ""


def list_services() -> str:
    """列出受管服务及状态。"""
    out = []
    for s in sorted(SERVICE_ALLOW):
        out.append(f"{s}: {_service_state(s)}")
    return "\n".join(out)


def _service_state(name: str) -> str:
    if DRY_RUN:
        return "(dry-run)"
    r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
    return (r.stdout or r.stderr).strip() or "unknown"


def control_service(action: str, name: str) -> str:
    """启停/重启受管服务。action ∈ {start,stop,restart,status}。"""
    if name not in SERVICE_ALLOW:
        return f"拒绝：{name} 不在白名单 {sorted(SERVICE_ALLOW)}"
    if action not in ("start", "stop", "restart", "status"):
        return f"拒绝：非法动作 {action}"
    if action == "status":
        return f"{name}: {_service_state(name)}"
    if DRY_RUN:
        return f"[dry-run] systemctl {action} {name}"
    # 需要 sudo；只允许白名单服务
    r = subprocess.run(["sudo", "-n", "systemctl", action, name], capture_output=True, text=True)
    ok = r.returncode == 0
    return f"{'OK' if ok else 'FAIL'}: systemctl {action} {name} {(r.stderr or '').strip()[:200]}"


def gpio(action: str, pin: int, value: int = 0) -> str:
    """GPIO 读写（libgpiod）。action ∈ {get,set}。"""
    if action == "set":
        return _run(["gpioset", f"gpiochip0", f"{pin}={1 if value else 0}"])
    if action == "get":
        return _run(["gpioget", "gpiochip0", str(pin)])
    return f"拒绝：非法动作 {action}"


def run_command(cmd: str) -> str:
    """只允许白名单里的只读命令。"""
    base = cmd.strip().split()[0] if cmd.strip() else ""
    if base not in COMMAND_ALLOW:
        return f"拒绝：{base} 不在白名单 {sorted(COMMAND_ALLOW)}"
    if not shutil.which(base):
        return f"(未安装: {base})"
    return _run(cmd.split())


# OpenAI / DeepSeek 兼容的工具 schema
SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_status", "description": "查询设备状态：负载、内存、磁盘、温度。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_services", "description": "列出受管服务及其运行状态。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "control_service", "description": "启停或重启一个受管服务。",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["start", "stop", "restart", "status"]},
            "name": {"type": "string", "description": "服务名（须在白名单内）"}},
            "required": ["action", "name"]}}},
    {"type": "function", "function": {
        "name": "gpio", "description": "读写 GPIO 引脚（开灯/继电器等）。",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["get", "set"]},
            "pin": {"type": "integer"}, "value": {"type": "integer", "enum": [0, 1]}},
            "required": ["action", "pin"]}}},
    {"type": "function", "function": {
        "name": "run_command", "description": "执行白名单内的只读命令（uptime/free/df 等）。",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}},
                       "required": ["cmd"]}}},
]

DISPATCH = {
    "get_status": lambda **a: get_status(),
    "list_services": lambda **a: list_services(),
    "control_service": lambda **a: control_service(a["action"], a["name"]),
    "gpio": lambda **a: gpio(a["action"], a["pin"], a.get("value", 0)),
    "run_command": lambda **a: run_command(a["cmd"]),
}


def call(name: str, args: dict) -> str:
    fn = DISPATCH.get(name)
    if not fn:
        return f"未知工具: {name}"
    try:
        return str(fn(**args))
    except Exception as e:  # noqa: BLE001
        return f"工具执行失败: {e}"
