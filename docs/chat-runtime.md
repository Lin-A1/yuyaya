# 聊天 Runtime

鱼丫丫现在只保留聊天能力。她没有文件、桌面、命令或后台任务工具，也不会启动 Codex、Claude Code、OpenCode 这类外部 agent。

## 目标

- 连续聊天，不是每条消息都重新开始。
- 共享短期对话、长期记忆和当前情绪。
- 支持打字、伪全双工语音和 Qwen Realtime 全双工。
- 需要表情和动作时，用文本标签或实时语音工具配合说话。

## 文本路径

```
POST /api/chat 或 speech_final
            │
            ▼
      Chat Brain (LLM)
            │
            ▼
        SpeechStream
            │
   ┌────────┼─────────┐
   ▼        ▼         ▼
 字幕      TTS       表情/动作
```

`Avatar.chat()` 只调用配置好的聊天模型。模型能看到最近对话、长期记忆和情绪说明，但没有函数调用表，也没有任何执行动作的入口。

## 语音路径

- 伪全双工：VAD → ASR → 同一个聊天模型 → 流式 TTS。
- 真全双工：Qwen Realtime 直接负责听、说和打断。
- 实时模型只保留 `remember`、`set_emotion`、`set_motion` 三个辅助工具。

## 能力边界

服务端在两层阻止越界：

1. `configure` 不再创建任何 agent 后端。
2. 人设末尾始终追加一条硬规则：只能聊天，不能读写文件、操作电脑或执行命令。

旧的 `/api/jobs`、`/api/agents`、`/api/action/*` 和 `/api/initiative/*` 已移除。旧配置升级到 v5 时会丢弃 `agents` 和 `initiative` 段。

## 外部控制

外部脚本仍可以通过 HTTP 或 MCP 驱动她说话、切表情和做动作。方向只有外部程序 → 她，她不会反过来调用外部 agent。
