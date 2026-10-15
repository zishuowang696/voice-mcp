# voice-mcp

> 用**语音控制一台边缘设备**：`听 → 想(工具调用) → 做 → 说`。管线固定，**STT / LLM / TTS 三个后端可切换（云端 ↔ 本地）**。
> By [embedai.com.cn](https://embedai.com.cn)

云端能快速出效果，但**本地/离线才是护城河**——同一套代码，把三个后端从 `cloud` 换成 `local`，就能**断网运行、数据不出设备、低延迟**。

```
麦克风 → STT → LLM(工具调用) → 设备工具 → TTS → 喇叭
              └── cloud: DeepSeek | local: llama.cpp (OpenAI 兼容) ──┘
```

## 为什么不是"又一个语音助手"
- 目标是**控制真设备**（GPIO / 服务 / 遥测），不是闲聊；
- 架构上**离线可跑**（换后端即可），面向现场/边缘；
- **安全**：语音是不可信输入 → 工具全走**白名单 + 最小权限**（`DRY_RUN`、命令/服务白名单）。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env          # 填 DEEPSEEK_API_KEY

# 1) 自检（不需要 key / 麦克风）
python3 voice_mcp.py --self-test

# 2) 打字模式跑通管线（不用麦克风）
STT_BACKEND=text python3 voice_mcp.py --text
> 这台设备怎么样？        # → 调用 get_status
> 重启采集服务            # → 调用 control_service
> 把灯打开                # → 调用 gpio

# 3) 接麦克风（云端）
STT_BACKEND=cloud TTS_BACKEND=cloud python3 voice_mcp.py
```

## 设备工具（agent 的「手」）
| 工具 | 作用 |
|---|---|
| `get_status` | 主机/负载/内存/磁盘/温度 |
| `list_services` / `control_service` | 查看、启停/重启**白名单**服务 |
| `gpio` | 读写 GPIO（开灯/继电器） |
| `run_command` | 仅白名单只读命令 |

配置：`SERVICE_ALLOW`（服务白名单）、`COMMAND_ALLOW`（命令白名单，见 `device_tools.py`）、`DRY_RUN=1`。

## 后端切换
| 后端 | STT | LLM | TTS |
|---|---|---|---|
| cloud | OpenAI 兼容转写 | DeepSeek / 任意 OpenAI 兼容 | OpenAI 兼容 TTS |
| local | whisper.cpp（`whisper-cli`） | llama.cpp（`llama-server`） | piper |

切本地只改 `.env`：
```
STT_BACKEND=local
LLM_BASE_URL=http://localhost:8080/v1
LLM_MODEL=local
TTS_BACKEND=local
```

## 路线图
- [x] 管线 + 可切后端 + 设备工具（白名单）
- [ ] 唤醒词（openWakeWord / Porcupine）
- [ ] 全本地跑通（whisper.cpp + llama.cpp + piper）并实测**端到端延迟**
- [ ] Jetson：TensorRT-LLM / Riva，目标 **< 1s**
- [ ] 更多设备工具（串口 / Modbus / 摄像头）

## 许可
MIT
