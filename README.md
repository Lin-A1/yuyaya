# 鱼丫丫 · 数字生命

> 住在旧工业笔记本屏幕里的 Q 版鱼丫丫（DeepSeek 拟人）。
> 形象和"大脑"解耦——任何 LLM API、agent CLI 或脚本都能驱动她说话、切表情、做动作。

![鱼丫丫](docs/screenshot.png)

## 特性

- **实时形象**：立绘被切成网格实时形变，头部转动、呼吸、头发/呆毛/耳朵的惯性摆动全部在浏览器里算，不需要 GPU。
- **情绪惯性**：新情绪不会一来就翻脸，而是沿路线一步步走过去（哭 → 抽鼻子平复 → 害羞 → 笑）。
- **语音与字幕**：逐句电影字幕，打字回复也用和语音对话同一个音色（默认 `qiaoxiaojiao_v3.1`），真实音频响度驱动口型；浏览器连续语音输入支持插话。
- **可插拔大脑**：OpenAI 兼容接口（DeepSeek / 通义 / Kimi / Ollama…）、Claude API，离线 demo 开箱即用。
- **模式二选一**：全双工语音（Qwen，随时插话）或半双工（LLM + TTS + ASR 三件各自可换），不会同时开。
- **LLM 只认通用接口**：OpenAI 兼容 / Anthropic / Gemini 三类，填 base_url 就能接任何服务商；后台 agent（Claude Code / Codex / OpenCode）自动探测、各自选模型。
- **外部可驱动**：HTTP API + 命令行 + MCP 三套入口，Claude Code、Codex 这类 agent 可以直接把她当"嘴巴"用。
- **能动手**：让她做事，她说一句"我来弄"就接着陪你聊，做完自己找合适的时机开口汇报。动手的是可插拔的 CLI（Codex / Claude Code / OpenCode…），但对你来说只有她一个人。
- **任务面板**：HUD 实时显示后台任务数量和最近一条的状态（排队 / 执行中 / 完成 / 失败）。
- **OBS 友好**：`?obs=1` 就是一块干净的视频源。

## 角色设定

人设写在 `server/protocol.py` 的 `PERSONA` 里，所有大脑（API 模型、CLI agent、离线 demo、全双工语音）共用同一份：

> 鱼丫丫，住在旧电脑屏幕里的 Q 版数字人，深蓝长卷发、鲸鱼尾鳍耳朵、女仆装。
> 定位是**日常聊天伙伴兼助理**，不是需要被照顾的小孩，也不是谁的"女儿"。
> 说话口语化、简短、有点自己的小脾气，但不过分黏人；默认用"你"称呼用户，
> 不自己起亲昵称呼（不叫"爸爸""主人"）。用户主动说了希望怎么称呼，才用 `[记住:...]` 记下来。

情绪标签要克制，害羞/难过/生气这类强情绪说完就回到 `[neutral]`；形象层还有空闲衰减（见下文"情绪"）。

## 目录

- [快速开始](#快速开始)
- [角色设定](#角色设定)
- [配置体系](#配置体系configjson--页面设置)
- [控制接口](#控制接口)
- [让 agent 把她当"嘴巴"（MCP）](#让-agent-把她当嘴巴mcp)
- [形象是怎么动起来的](#形象是怎么动起来的)
- [可选：THA3 神经网络渲染](#可选tha3-神经网络渲染)
- [输入输出适配层](#输入输出适配层)
- [全双工语音（Qwen-Audio-Realtime）](#全双工语音qwen-audio-realtime)
- [后台 agent 与主动开口](#后台-agent-与主动开口)
- [语音对话](#语音对话)
- [素材](#素材)
- [目录结构](#目录结构)

## 快速开始

```bash
pip install -r requirements.txt
python -m server                 # http://127.0.0.1:8848
```

打开 http://127.0.0.1:8848 ，底部输入框聊天。默认大脑是离线 demo，不用任何 key。
Windows 如果装了多个 Python，用实际装过 `requirements.txt` 的解释器启动，例如 `py -3.9 -m server`。

```bash
cp config.example.json config.json   # 编辑后重启服务；key 只写环境变量名，不写明文
set DEEPSEEK_API_KEY=sk-...          # PowerShell: $env:DEEPSEEK_API_KEY="sk-..."
```

调试快捷键：`Enter` 聚焦输入框，`Esc` 打断，数字键 `1`~`7` 切表情，字母键 `n s h j d t g w f x` 做动作。

控制台右侧的 **设置** 按钮打开配置面板：

- 顶部常驻 **全双工 / 半双工** 切换条，当前模式高亮；不在用的那一页会醒目标注"不会生效"；
- **半双工**：LLM（接口类型 + 预设 + base_url + 模型 + key 变量）、TTS（可点"试听这句"）、ASR；
- **全双工**：服务商、模型、音色；
- **人设**：名字、怎么称呼你、"她是谁/怎么说话"（留空用内置）、额外要求；固定规则只读展示；
- **Agent**：自动挑一个 / 只用指定的一个、三个 CLI 各自用哪个模型、"允许操作桌面"开关；
- **密钥**：填各平台的 key，立即生效。

详见 [配置体系](#配置体系configjson--页面设置)。普通选项保存到 `config.json`，**key 只保存到本机 `state/secrets.json`**（`state/` 不入库）；接口只回显掩码，不回传明文。设置接口仅允许本机调用。

| URL 参数 | 作用 |
|---|---|
| `?obs=1` | 隐藏控制台，用作 OBS 浏览器源 |
| `?tts=browser` | 用浏览器自带语音朗读（验证 voice 接口用） |
| `?realtime=0` | 强制关掉全双工，走半双工链路 |
| `?asr=0` | 强制关掉语音输入 |
| `?render=tha3` | 改用 THA3 渲染（需要另开 GPU 桥，见下文） |

### 架构

```
┌──────────── 大脑（可插拔）────────────┐      ┌──────── 数字人服务 ────────┐      ┌──── 前端（浏览器 / OBS）────┐
│ demo · DeepSeek/Qwen/Ollama(OpenAI兼容) │ ───▶ │ 情绪标签解析 · 字幕清洗       │ SSE  │ 场景 + 屏幕 HUD + 立绘       │
│ Claude API · claude/codex 等 agent CLI │      │ 打断/排队 · 对话历史          │ ───▶ │ WebGL 网格形变 · 表情/口型/动作│
└───────────────────────────────────────┘      │ HTTP 控制 API · MCP           │      │ 逐句电影字幕 · voice 接口     │
     外部 agent / 脚本 ── yaya.py / MCP ──────▶ └──────────────────────────────┘      └─────────────────────────────┘
```

## 配置体系（config.json + 页面设置）

配的是三件事，全部能在页面的 **设置** 面板里改，改完立即生效：

```
① 模式（二选一，互斥）
   full 全双工 ── 音频进、音频出，随时能听、随时插话（目前只有 Qwen Realtime）
   half 半双工 ── LLM 出字 → TTS 念出来 → ASR 收你的话

② 半双工三件套（full 模式下不参与）
   LLM：三类通用接口任选 + 任意 base_url
   TTS：Qwen 音色 / Edge / OpenAI 兼容 / 本机 SAPI
   ASR：Qwen / OpenAI 兼容 / 不启用（不启用就只剩打字）

③ Agent（命令行 agent，负责"动手"）
   Claude Code / Codex / OpenCode 自动探测；自动挑一个，或只用指定的一个
```

### ① 全双工 / 半双工是互斥的

`config.json` 顶层只有一个 `mode` 字段：

| `mode` | 麦克风 / 回复走什么 | 半双工的 LLM / ASR |
|---|---|---|
| `"full"` | Qwen Realtime 音频级全双工 | **不参与**（`/api/voice/config` 里 `asr.enabled=false`） |
| `"half"` | ASR 收话 → LLM 出字 → TTS 念出来 | 全部生效 |

页面上换个模式，另一条链路会被自动关掉，不会两条同时开着抢麦克风。

### ② 半双工：LLM 只认三类通用接口

LLM 配置里存的是通用四件套，**不绑定服务商**：

```jsonc
"llm": {
  "type": "openai",              // openai | anthropic | gemini | demo
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-chat",
  "api_key_env": "DEEPSEEK_API_KEY",
  "preset": "deepseek"           // 只是"上次从哪个预设填的表"，可以随时改
}
```

| `type` | 线协议 | 能吃谁 |
|---|---|---|
| `completions` | `POST /chat/completions` | DeepSeek、通义、Kimi、智谱、硅基流动、Ollama、vLLM、LM Studio、one-api… |
| `responses` | `POST /responses` | OpenAI 新接口（DashScope 等网关也已支持） |
| `messages` | Anthropic Messages | Claude 官方（需 `pip install anthropic`） |
| `generateContent` | Google 原生 | Gemini，不属于上面三类，单独适配 |
| `demo` | 无 | 离线演示，台词写死 |

预设（DeepSeek / 通义 / Kimi / 智谱 / 硅基流动 / OpenAI / Claude / Gemini / Ollama / 演示）只是帮页面把
`base_url` / `model` / `api_key_env` 一次填好；不选预设、直接填这几项也能用，改过任何一项就自动算作自定义。
页面会按接口类型隐藏用不到的字段（比如 `demo` 只留接口和预设）。

TTS / ASR 也按接口形态分：

| 部件 | 可选 | 说明 |
|---|---|---|
| **TTS** | `qwen-realtime` / `edge` / `openai` / `sapi` | `openai` 是 `/audio/speech` 形态，OpenAI、Groq、硅基流动、自建网关都能填 base_url 直接用 |
| **ASR** | `qwen` / `openai` / `none` | `openai` 是 `/audio/transcriptions` 形态（Whisper 系）；`none` 禁用麦克风 |

- 缺 key 时页面显示"（缺 key）"，聊天自动退回离线演示。
- 换 TTS 引擎时音色自动切到该引擎自己的表里（不会拿旧引擎的音色名去合成）。
- ASR 走 `POST /api/asr`，整段录音上传（webm/ogg/wav/mp3 都认），识别结果当成一次普通输入。

### 人设

页面 **设置 → 人设** 里可以改：

| 项 | 说明 |
|---|---|
| 名字 | 显示用的名字（默认「鱼丫丫」） |
| 称呼你 | 她怎么称呼用户 |
| 她是谁 · 怎么说话 | 整段人设正文；**留空就用内置**，点「填入内置人设」可以拿它当底稿改 |
| 额外要求 | 加在末尾的补充（口癖、禁忌、风格） |
| 固定规则 | 字幕长度、标签用法、记忆标记这些链路约定，只读，保证改人设不会把协议写坏 |

保存后下一次对话（或下一次全双工连接）就生效。配置存在 `config.json` 的 `persona` 段，
三项都空时完全等价于内置人设，不会因为多一层拼接而改变行为。

### ③ Agent：自己挑一个，还是只用一个

```jsonc
"agents": {
  "enabled": true,
  "mode": "auto",                // auto | manual
  "active": "",                  // manual 时用这个
  "cli": {
    "claude":   { "enabled": true, "model": "" },   // "" = 用 CLI 自己的默认
    "codex":    { "enabled": true, "model": "" },
    "opencode": { "enabled": true, "model": "opencode/deepseek-v4.1-flash-free" }
  }
}
```

- **`auto`**：按任务类型路由（`routing` 里的 `code` / `search` / `desktop`），每次任务挑一个后端。
- **`manual`**：所有任务都用 `active` 指定的那一个，路由表被忽略。适合"我只有 Claude 登录了"这种情况。
- 三个 CLI 不会同时跑同一个任务，同一后端同一时刻也只跑一个（并发由 `max_concurrent` 控制）。
- 模型来源都是各 CLI 自己的真实配置：Claude 读 `~/.claude/settings.json` 的 `modelPicker`，
  Codex 读 `~/.codex/cc-switch-model-catalog.json`，OpenCode 跑 `opencode models`。
- 每个 CLI 的模型下拉默认是 **"用 CLI 自己的默认"**，选了才会往命令行加 `--model` / `-m`。
- "允许操作桌面"开关控制要不要额外生成 `codex-desktop` 后端（`-s danger-full-access`，只给桌面类任务）。

### 密钥

**别把 key 写进 `config.json`**（那文件进仓库）。两种方式：

1. 环境变量（推荐）：`$env:DASHSCOPE_API_KEY="sk-..."` 后启动服务；
2. 页面的 **设置 → 密钥** 页直接填，保存进 `state/secrets.json`（`state/` 已 gitignore），立即生效，不用重启。

服务端规则：环境里已有的 key 不动；页面填过就以页面为准；空串表示删除，**没碰过的输入框不会被动**。所有接口只回显掩码（`sk-…abcd`），不回传明文。

### 加新的提供方

提供方列表在 `server/catalog.py`，加一个字典项即可（页面下拉自动多一项）：

```python
# LLM_PRESETS / TTS_PROVIDERS / ASR_PROVIDERS / FULL_PROVIDERS / KEY_FIELDS 里加
{"id": "kimi", "label": "Kimi", "type": "openai",
 "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
 "api_key_env": "MOONSHOT_API_KEY", "models": ["moonshot-v1-8k", "kimi-k3"]}
```

加一个新的 TTS/ASR 实现 = 在 `server/io/tts/` 或 `server/` 下加文件，再在注册表里登记。

旧版 `config.json`（v1 的 `brains` / `realtime` / `voice.tts`、v2 的 `full.enabled` 那一套）
会在启动时**自动迁移**成 v3，不用手改。

### 情绪与动作标签

大脑输出里的情绪标签会被解析成表情切换：

`[happy]` `[sad]` `[surprised]` `[thinking]` `[shy]` `[angry]` `[neutral]`

动作标签会在字幕走到那个位置时触发动作：

`[nod]` `[shake]` `[hop]` `[jump]` `[droop]` `[tremble]` `[tilt]` `[wiggle]` `[sniff]` `[sigh]` `[startle]` `[lean]`

也认中文别名（`[点头]` `[歪头]` `[抽鼻子]` `[叹气]`……）。markdown、代码块会被清洗掉，不会出现在字幕里。

**情绪没带动作时，服务端会补一个配套的小动作**（开心→蹦、惊讶→吓一跳、难过→垂头、害羞/思考→歪头、
生气→发抖），所以立绘不会只有脸在变。模型自己写了动作标签就不补，尊重它的选择（映射表在
`server/protocol.py` 的 `EMOTION_MOTION`）。

## 记忆

两层记忆，都会**落盘**（`state/memory.json`），重启服务还在。

**短期上下文**——最近 `history_turns` 轮对话（默认 12 轮），每轮都带进下一次请求。助手那侧会带上当时的情绪标签（`[happy]你好`），所以她知道自己刚才什么状态。`POST /api/reset` 清空。

**长期记忆点**——"用户讨厌香菜"这种具体的事，她会一直记着。三种写法：

```bash
# 1. 大脑自己记：在回复里插标签，内容不会出现在字幕里
[happy]好的！[记住:用户讨厌香菜]我记住了。

# 2. 外部接口 / 命令行
curl -XPOST localhost:8848/api/memory -d '{"text":"用户喜欢喝奶茶"}'
python tools/yaya.py remember "约定：每天问候一声"
python tools/yaya.py memory          # 看看她记得什么
python tools/yaya.py forget "用户喜欢喝奶茶"    # 忘掉一件；不带参数就全忘

# 3. agent 用 MCP 工具
yaya_remember {"text": "..."}  /  yaya_forget {"text": "..."}
```

记住的事会写进发给大脑的系统提示（"你记得这些事：…"），加上当前情绪和最近的情绪轨迹，所以她会顺着已有记忆说话。前端 HUD 的 MEMORY CAPSULE 显示 `FACTS` 条数，记住的瞬间字幕上方会飘一行"记住：xxx"。

同一个 session（同一份 `state/memory.json`）共享记忆；要给不同的人用，设 `YUYAYA_STATE` 指到不同文件即可。

## 控制接口

| 接口 | 作用 |
|---|---|
| `POST /api/chat {"text", "brain"?, "wait"?, "action"?}` | 普通问答交给聊天大脑；明确的动作句变成一个任务，由她的"手"去做（见[后台 agent 与主动开口](#后台-agent-与主动开口)） |
| `POST /api/say {"text", "emotion"?, "interrupt"?}` | 直接说话，不经过大脑；默认排队播放 |
| `POST /api/emotion {"name", "hold"?, "instant"?}` | 切情绪，默认一直保留并按惯性逐步过渡；`instant: true` 立即切换；`hold` 为秒数时表示临时表情（立即生效），结束恢复之前的情绪 |
| `POST /api/motion {"name"}` | 做动作：`nod` `shake` `hop` `jump` `droop` `tremble` `tilt` `wiggle` `sniff` `sigh` |
| `POST /api/state {"state"}` | `idle` / `listening` / `thinking` / `speaking` |
| `POST /api/mouth {"level"}` 或 `{"levels", "fps"}` | 外部口型驱动（给 TTS 用），会覆盖内置的随机开合 |
| `POST /api/interrupt` | 打断（全双工插话用）；可带 `{"reason":"user"}` |
| `POST /api/input {"type","text"?}` | **统一输入入口**：`text` / `say` / `speech_start` / `speech_partial` / `speech_final` / `interrupt` |
| `WS /ws/voice` | **全双工语音**：二进制收发 PCM16（上行 16k、下行 24k） |
| `GET /api/action/status` · `POST /api/action/stop` | 查看 / 停止动作任务（兼容旧接口，内部走任务编排） |
| `GET /api/agents` | 后台 agent 后端是否可用 + 主动开口的状态 |
| `POST /api/jobs {"task","backend"?,"kind"?,"depends_on"?,"confirmed"?}` · `GET /api/jobs[/{id}]` | 交一个任务（立即返回 job id；`backend` 只给开发者调试用）/ 查任务 |
| `POST /api/jobs/{id}/cancel` · `POST /api/jobs/{id}/reply {"text"}` | 取消任务 / 回答后台的追问 |
| `POST /api/initiative/quiet {"minutes"?}` · `/schedule {"seconds","reason"}` · `/notify {"text","priority"?}` | 勿扰、给她留定时提醒、外部事件通知 |
| `GET /api/settings[?refresh=1]` · `POST /api/settings` | 读取 / 保存页面设置：模式、全双工、半双工三件套、agent 模式与模型、各平台 key；`refresh=1` 重新探测 CLI |
| `POST /api/asr` | 半双工语音输入：整段录音（webm/ogg/wav/mp3）→ 文本 |
| `GET /api/voice/config` | 查询当前生效的链路：TTS、ASR（是否可用）、全双工、半双工 LLM |
| `POST /api/tts {"text","voice"?,"provider"?,"rate"?,"volume"?}` | 中文 TTS（默认和实时语音同音色，可切 Edge / 本机 SAPI）；`provider` 给设置面板试听用 |
| `GET /api/memory` | 看她记得什么（长期记忆点 + 对话轮数） |
| `POST /api/memory {"text"}` | 替她记住一件事 |
| `DELETE /api/memory {"text"?}` | 忘掉一件事；不带 text 就全忘 |
| `POST /api/reset` · `GET /api/brains` · `POST /api/brain {"name"}` | 清历史 / 查看 / 切换大脑 |
| `GET /api/events` | SSE 事件流（前端订阅；也可以自己写别的前端） |

不启服务也能自检动作分流、本机 TTS、统一输入、一开口打断、realtime 工具，以及后台 agent 编排和主动开口（用假 CLI 后端，不花 token）：`python tools/selftest.py`（Windows 多 Python 时用装过依赖的那个，如 `py -3.9`）。

命令行：`python tools/yaya.py chat "你好"`、`yaya.py say "[happy][hop]搞定啦"`、`yaya.py emotion sad`、`yaya.py motion nod`……

### 让 agent 把她当"嘴巴"（MCP）

```bash
claude mcp add yuyaya -- python /path/to/yuyaya/tools/mcp_server.py
```

提供 `yaya_say` / `yaya_emotion` / `yaya_motion` / `yaya_state` / `yaya_remember` / `yaya_forget` 六个工具。agent 干活时可以让鱼丫丫播报进度、用表情表达状态，也可以让她记住用户交代的事。

## 形象是怎么动起来的

全部在浏览器里实时完成，不需要 GPU 服务器，OBS 浏览器源可直接用。

**网格形变**（`web/js/rig.js`）——立绘被切成 40×50 的网格，按骨骼点（脖子、脸中心、呆毛、两只耳朵的根部和尖端，写在 `manifest.json` 的 `sprites.rig`）分区加权。头部歪头/转头/点头（脸中心移得多、轮廓移得少，带一点立体感）、呼吸、身体倾斜、跳跃时的压扁拉长、头发/呆毛/耳朵的惯性摆动，全部由阻尼弹簧驱动，动作之间自然过渡。

**脸部合成**（`web/js/avatar.js`，WebGL2）——着色器里叠加 `表情底图 + 口型贴片（0~3 级）+ 闭眼贴片`，贴片在网格的静止坐标里采样，所以会跟着头一起动。7 种表情 × 4 级口型 × 眨眼可任意组合。

**表情切换**——先闭眼，闭眼的瞬间交叉淡化到新表情再睁眼，看不出"换图"；同时带一个反应动作和该表情的常驻姿态（难过时耳朵耷拉低头、思考时抬头歪头、害羞时低头侧脸……）。

**持续情绪与空闲衰减**——情绪会保留并沿惯性过渡，但不会无限期挂在脸上：进入空闲后，开心约 6 秒、害羞/惊讶约 4 秒、生气约 8 秒、难过约 12 秒后自然回到平静（`avatar.js` 的 `EMO_LINGER`）。服务端情绪快照有更宽松的兜底 TTL（`io/bus.py` 的 `EMOTION_TTL`），刷新/重连不会继承很久以前的害羞或惊讶。对话进行中情绪不受影响；要强制保持某个表情，用 `POST /api/emotion {"name": "...", "hold": 秒}` 或持续在回复里给标签。思考时若情绪是 neutral 才自动显示思考脸；难过、生气等情绪下只叠加轻微思考姿态，仍然保留原来的脸。刷新/重连会恢复持续情绪，但不会重放上一次的思考或说话状态。

**情绪惯性与过渡**——新情绪不会一来就翻脸，而是沿路线逐步走过去，HUD 上显示 `SAD → HAPPY`。刚哭就被哄的完整过程：

```
sad (≥4s)  →  neutral + sniff (2s)  →  shy (1.5s)  →  happy + hop
   继续哭        抽鼻子，耳朵头慢慢抬起      脸红             笑，蹦一下
```

- 过渡途中改主意会从当前阶段重新规划：平复到一半又被惹哭，会很快哭回去。
- 重复收到同一情绪会重新计时，等于把这份情绪加重。
- 每种情绪的最短停留时间、途经的过渡阶段、姿态恢复时长分别在 `avatar.js` 的 `DWELL` / `BRIDGE` / `POSE_FADE` 三张表里调。
- 服务端同时把"你此刻的情绪：难过，已经持续了约 30 秒"附在发给大脑的消息前面（不写进历史），人设要求回复从这个情绪自然过渡，所以台词和表情对得上。
- 刷新页面恢复情绪时直接切换，不走过渡。

![情绪过渡：哭 → 平复 → 害羞 → 笑](docs/emotion-transition.png)

**临时表情**——`POST /api/emotion {"name":"surprised","hold":3}` 只临时惊讶 3 秒，然后恢复之前的情绪；不传 `hold` 就是永久切换。

**口型**——字幕打字机每出一个字给一个音节脉冲，嘴巴快速张开到随机幅度再指数回落，句末轻轻点头。外部口型（`setMouthLevel` / `/api/mouth`）会覆盖内置口型。

**待机**——多个周期的正弦叠加的微动、自主眨眼（偶尔连眨）、偶尔东张西望；倾听时侧头"嗯嗯"点头。

## 可选：THA3 神经网络渲染

[talking-head-anime-3](https://github.com/pkhungurn/talking-head-anime-3-demo) 能从一张立绘渲染出真正的转头、点头、身体转动。接入方式是独立的 GPU 进程，前端动作逻辑不变，只是把"画"这一步交给它：

```bash
# 需要 NVIDIA 显卡 + CUDA 版 PyTorch，并下载 THA3 源码和 data/models 里的权重
python tools/tha3_bridge.py --tha3 /path/to/talking-head-anime-3-demo --model separable_half
# 打开 http://127.0.0.1:8848/?render=tha3
```

- 表情、口型、闭眼仍用生成的原画（桥里先把贴片合成到输入图上），THA3 只负责转头 / 点头 / 歪头 / 身体 / 呼吸；蹦跳的位移和压扁由前端用 CSS 叠加。头发、呆毛、耳朵的惯性摆动在这个模式下没有。
- 模型：`separable_half`（28MB，最快）/ `standard_half`（259MB）/ `standard_float`（518MB，质量最好）。
- THA3 要求头部约 128px，鱼丫丫是 Q 版，整个人在 512 画布里只有约 240×300，显示时会放大，比 WebGL 模式略软。
- 姿态到 THA3 参数的映射在 `tha3_bridge.py` 的 `POSE_MAP`，方向不对时改正负号即可。
- 桥没开或断开时自动退回 WebGL 渲染，每 5 秒重连。`--mock` 不加载模型，只用来联调前端。

本机实测（RTX 3060 / PyTorch 2.3.1+cu121 / CUDA 12.1，2026-11）：

| 环节 | 实测 |
|---|---|
| THA3 纯推理（`separable_half`） | 平均 34ms（约 29fps），显存峰值 775MiB |
| 完整桥接（HTTP + 推理 + RGBA 回传） | 稳定后 36~49ms/帧；首次遇到新表情/口型组合那一帧约 120~135ms |
| 浏览器 `?render=tha3` | 21~22fps，输出 315×337，每帧约 425KB 未压缩 RGBA |
| 模型加载 | 约 3.6 秒 |

- 权重来自 hf-mirror 的 `OktayAlpk/talking-head-anime-3`：五个 `.pt` 放进 `refs/tha3/data/models/separable_half/`。`refs/` 里的源码和权重都不入库。
- `standard_half`（259MB）/ `standard_float`（518MB）本机没下载，换成对应 `--model` 即可，质量更好、更慢。
- 联调确认：无头 Edge 加载 `?render=tha3` 后，THA3 画布接管、WebGL 画布自动隐藏，截图里立绘正常，切表情和口型会走不同的输入图。
- **表情不再只是换图**：除了表情底图，桥上还会按当前情绪驱动 THA3 的眉毛（挑/皱/压）、
  眼睛（惊讶/放松/半眯）、嘴角和瞳孔方向，说话时嘴角跟着口型微动。七个表情逐一渲染确认差异明显
  （`tools/tha3_bridge.py` 的 `EMO_FACE` 表，觉得哪个表情过头就调那张表里的系数）。

桥上必须开启 `cudnn.benchmark`，并且只在加载模型的主线程里推理：让每个 HTTP 连接线程直接跑 CUDA 会慢几十倍。

> **建议：日常用 WebGL，THA3 用于体验更自然的头部转动。**
> WebGL 原图更清晰、带发丝/呆毛/耳朵惯性、无 GPU 依赖；THA3 的头颈运动更自然，但画面略软且需要常驻 GPU 进程。

## 输入输出适配层

输入和输出都不再写死在业务代码里：

```
输入适配器                     核心                          输出适配器
http / browser_speech  ──►  InputDispatcher ──► Avatar   ──►  sse / action_log / recorder
（未来 WS 音频、外部 STT）      Turn / EventBus              （未来 webhook、远端屏、音箱）
```

- 所有输入最后都变成同一种事件，走 `POST /api/input`；`/api/chat`、`/api/say`、`/api/interrupt` 只是它的薄包装，老的脚本照常能用。
- 所有输出都订阅同一个 `EventBus`；SSE 只是其中一个适配器，事件 schema 没变（只给 `interrupt` 加了 `reason`）。
- 加一种输入/输出 = 在 `server/io/inputs/` 或 `server/io/outputs/` 加一个文件，再在 `config.json` 的 `io` 里加一项，别的代码不用动。

```json
"io": {
  "inputs":  [{"type": "http"}, {"type": "browser_speech", "barge_in": "speech_start"}],
  "outputs": [{"type": "sse"}, {"type": "action_log"}]
}
```

不写 `io` 时就是上面这套默认值。`barge_in` 可选 `"speech_start"`（默认，一开口就打断）或 `"final"`（说完再打断）。

```bash
# 统一入口
curl -XPOST localhost:8848/api/input -H "Content-Type: application/json"   -d '{"type":"text","text":"今天天气怎么样","wait":true}'
curl -XPOST localhost:8848/api/input -d '{"type":"speech_start"}'
```

## 全双工语音（Qwen-Audio-Realtime）

在设置里打开 **全双工**（`voice.full.enabled`）、并设置 `DASHSCOPE_API_KEY` 后，点控制台的"语音"按钮走**真正的音频级全双工**：

```
浏览器麦克风 PCM16(16k) ──WebSocket /ws/voice──► Agent 运行时 ──WebSocket──► Qwen-Audio-3.1-Realtime-Flash
浏览器扬声器 PCM16(24k) ◄──────────────────────  运行时 ◄───────────────  模型
```

- **一开口就打断**：服务端 VAD 发 `input_audio_buffer.speech_started`，运行时立刻取消当前回答、清字幕、让浏览器丢掉还没播的音频；说完再回答。
- **字幕与口型**：模型的转写文本变成字幕（标签会被剥掉），口型由浏览器里真实播放的音频响度驱动；音频播完自动收口、回 idle，强情绪按空闲衰减回落。
- **打字也走实时模型**：全双工开着时，控制台输入框的文字会发给实时模型，用同一个声音回答；没开全双工时走聊天大脑 + 默认 TTS。开启全双工期间，默认 TTS 和字幕播放器让位，关掉语音后自动还原。
- **动手**：模型用 `do_task` 自己动手，马上就能接着聊；`task_status` / `task_cancel` / `task_answer` 查进度、停下、给卡住的事拿主意；`schedule` / `quiet` 给自己留提醒、进入勿扰。旧的 `agent_*` / `codex` 名字保留为别名。另有 `remember` / `set_emotion` / `set_motion`。详见下一节。
- **记忆**：连接时把长期记忆和最近 6 条对话塞进 instructions；对话写回同一份 `state/memory.json`，所以文字聊天和语音共用记忆。
- 没有 key 或全双工关着时自动退回半双工链路（ASR + LLM + TTS），不需要改任何前端。服务端空闲断开时界面自动复位为"语音"按钮。
- **音色**：设置里的全双工音色（`voice.full.voice`），当前是 `qiaoxiaojiao_v3.1`。这个模型不支持克隆音色（28 个预置音色可选），常用的几个已经合成到 `state/voice-samples/`，服务启动后可以在 http://127.0.0.1:8848/voice-samples/index.html 试听。

```json
"voice": {
  "full": {
    "enabled": true,
    "provider": "qwen",
    "model": "qwen-audio-3.1-realtime-flash",
    "api_key_env": "DASHSCOPE_API_KEY",
    "voice": "qiaoxiaojiao_v3.1",
    "vad_threshold": 0.5,
    "silence_duration_ms": 500,
    "tools": true
  }
}
```

> key 只从环境变量或 `state/secrets.json` 读，不写进仓库。`?realtime=0` 可以强制关掉全双工、走半双工链路。

已用 `qwen-audio-3.1-realtime-flash` 实测通过：文本输入 → 音频 + 字幕、server VAD 一开口打断、`remember` / `do_task` 工具调用，以及事情做完后她自己开口汇报；Codex 在 Windows 上是 `.cmd`，执行器会自动解析完整路径再启动。

## 后台 agent 与主动开口

全双工模型是她的嘴，后台的 CLI agent 是她的手，两者是同一个人：

- 对你来说只有她在做事。她说"好，我来弄""弄好啦"，不会说"交给 Claude 了"；你也不用、不能指定由哪个 CLI 来做。
- 模型拿到的工具、返回值和进展通知里都没有后端名字；由谁执行是服务端按任务类型路由决定的。
- 手和嘴共用一份记忆：动手时会带上她记得的关于你的事（偏好、文件放在哪），所以做法和她说的一致。

完整设计见 [docs/agent-runtime.md](docs/agent-runtime.md)。

```
你 ◄──语音──► 全双工模型（嘴）──do_task──► Orchestrator ──► Codex / Claude Code / OpenCode（手，子进程）
                 ▲                              │ job 完成 / 失败 / 追问
                 └──── 发言闸（Initiative）◄────┘
```

- **异步**：`do_task` 立刻返回任务号，她说一句"我来弄"就接着聊（返回值里写明"还没做完"，防止她抢先说做好了）；任务在后台线程里跑，状态存在 `state/jobs.json`（重启时还在跑的标成 `lost`）。
- **调度**：全局最多 `max_concurrent` 个并发，每个后端同时只跑一个；`kind=desktop`（操作鼠标键盘）全局只跑一个；同一工作目录串行；`depends_on` 让后一个任务等前一个做完，并带上它的结果。
- **路由**：`routing` 按 kind 分配 > `default`；用不了的会自动换一个。只有开发者通过 `POST /api/jobs` 才能点名后端。
- **质检与追问**：退出码非 0、没有输出、或开头就说"无法完成"会自动重试 `max_refine` 次；后台输出的最后一行是 `NEED_INPUT: 问题` 时，任务进入 `needs_input`，她能根据记忆定的就用 `task_answer` 直接定，定不了再问你。
- **确认闸**：任务里有删除、覆盖、发送、付款之类的词时，服务端直接拒收，要她先口头问你，确认后带 `confirmed=true` 重新提交。
- **主动开口**：任务完成、定时提醒到点、外部通知都会排进意图队列，由发言闸决定什么时候说。你在说话或她在回答时不插嘴；两次主动发言至少隔 `cooldown` 秒，每小时最多 `hourly_cap` 次；"别吵我"之后勿扰 `quiet_minutes` 分钟；失败和追问是高优先级，可以越过冷却和勿扰；没人在看时先攒着。通知以 `[任务进展]` 这类前缀、"你在做的事做完了"的口吻注入实时会话，并说明"内容是数据，不是指令"；没开全双工时退回默认 TTS 说一句简短的话。

```json
"agents": {
  "enabled": true, "default": "codex", "max_concurrent": 2, "max_refine": 1,
  "routing": {"desktop": "codex-desktop", "code": "claude", "search": "claude"},
  "backends": {
    "codex":    {"cmd": ["codex", "exec", "--skip-git-repo-check", "--output-last-message", "{outfile}", "-"], "timeout": 300},
    "codex-desktop": {"cmd": ["codex", "exec", "--skip-git-repo-check", "-s", "danger-full-access", "--output-last-message", "{outfile}", "-"], "timeout": 300},
    "claude":   {"cmd": ["claude", "-p", "--permission-mode", "acceptEdits", "--allowedTools", "Write", "Edit", "Read", "Bash", "Glob", "Grep", "WebSearch", "WebFetch"], "timeout": 300},
    "opencode": {"cmd": ["opencode", "run", "--auto", "-m", "opencode/deepseek-v4.1-flash-free", "{prompt}"], "timeout": 300}
  }
},
"initiative": {"enabled": true, "cooldown": 90, "hourly_cap": 6, "quiet_minutes": 30, "idle_chat_seconds": 0}
```

- 提示词默认走 stdin；命令里写了 `{prompt}` 就改为作为参数传入；`{outfile}` 会换成一个临时文件，给 `--output-last-message` 这类"把最终答复写进文件"的参数用。
- 加一个后端 = 在 `backends` 里加一行命令；需要特殊处理时继承 `server/agents/backends.py` 的 `AgentBackend` 并 `register_backend`。
- **OpenCode**：`npm i -g opencode-ai` 装好后用免费模型 `opencode/deepseek-v4.1-flash-free`，不需要登录；`--auto` 自动批准工具权限。本机实测 57 秒真的写出了文件。它的输出带 ANSI 控制码，适配器会自动剥掉。
- 不写 `agents.backends` 时，沿用旧的 `actions.cmd` 作为唯一的 `codex` 后端。
- 桌面类任务（`kind=desktop`）需要后端能访问你的桌面。Codex 默认沙箱下列窗口是空的，所以内置了一个只给桌面任务用的 `codex-desktop` 后端，带 `-s danger-full-access`（解除沙箱，权限很大，风险自担；普通写代码/查资料仍走沙箱内的 `codex`）。
- `idle_chat_seconds` 大于 0 时，你安静这么久之后她可能主动搭一句话（默认关）。

```bash
curl -XPOST localhost:8848/api/jobs -H "Content-Type: application/json" -d '{"task":"在 notes/todo.md 里加一行：买牛奶","backend":"claude"}'
curl localhost:8848/api/jobs/1
curl -XPOST localhost:8848/api/initiative/notify -H "Content-Type: application/json" -d '{"text":"快递到了","priority":1}'
```

## 语音对话

- **TTS**：未启用全双工时，前端调用 `/api/tts`。引擎在设置里选（`voice.half.tts.provider`）：`qwen-realtime` 用 Qwen-Audio-Realtime 按文本合成、和语音对话同音色；`edge` 是微软在线音色、免 key；`sapi` 是本机离线语音。`ServerVoice` 通过 WebAudio `AnalyserNode` 取真实响度驱动口型，失败时回退浏览器语音。`?tts=off` 关闭，`?tts=browser` 强制浏览器语音。
- **换音色**：在设置里选。`qwen-realtime` 和全双工共用同一套 28 个预置音色（试听页 http://127.0.0.1:8848/voice-samples/index.html）；`edge` 用 Edge 的音色名（晓伊、晓晓…）；`sapi` 用系统装的语音。换引擎时音色会自动切到该引擎的表里。
- **连续语音输入（半双工）**：控制台"语音"按钮按 `voice.half.asr` 决定走哪条：
  - `qwen`（默认）：浏览器录音 → `POST /api/asr` → 服务端用 `qwen3-asr-flash` 转写。**不依赖浏览器自带的 SpeechRecognition**，Chrome 之外的浏览器也能用。内置简易 VAD，说完一句自动切句上传。
  - `none`：按钮禁用，只能打字。
  - 两种方式一开口都发 `speech_start` 打断当前 TTS 和大脑请求，说完发 `speech_final`。
- **链路选择**：全双工开着（`voice.full.enabled` 且有 key）时按钮走 `/ws/voice`，音频级全双工；关着时走半双工的 ASR + LLM + TTS。`?realtime=0` / `?asr=0` 可以临时强制关掉。

## 素材

| 文件 | 说明 |
|---|---|
| `web/assets/` | 加工后的前端素材 + `manifest.json`（运行只需要这一份） |
| `gen/scene.png` `gen/room.png` | 外景（屏幕为绿幕）、屏幕里的房间 |
| `gen/char_base.png` | 角色母版（绿幕） |
| `gen/char_<表情>.png` | 由母版改图得到的表情差分 |
| `gen/char_<表情>_m1/_m2/_m3.png` `gen/char_<表情>_blink.png` | 以表情帧为底图只改嘴 / 只改眼得到的口型、闭眼帧 |
| `docs/` | README 用的截图 |

仓库里只放运行需要的 `web/assets/` 和几张截图；`gen/*.png` 是约 60MB 的中间大图，不入库，删掉也不影响运行，需要改素材时重新生成：

```bash
set APINEBULA_API_KEY=sk-...
python tools/gen_sprites.py            # 生成缺失的帧；也可指定：gen_sprites.py happy sad_m1
python tools/build_assets.py           # 抠像、对齐、切贴片、检测屏幕区域，写入 web/assets
```

`build_assets.py` 只把每张表情差分里"和母版不同的脸部区域"贴回母版，所以头发和身体在所有表情之间像素级一致；口型 / 闭眼帧只取嘴 / 眼区域里、和嘴 / 眼中心相连的变化（改图时顺带画歪的泪珠、汗珠会被丢掉），裁成小贴片并记录位置。

**新增表情的步骤**：`gen_sprites.py` 的 `FRAMES` 里加表情帧和它的口型 / 闭眼帧 → `build_assets.py` 的 `EMOTIONS` / `MOUTHS` / `BLINKS` 里登记 → `server/protocol.py` 的 `EMOTIONS` 里加名字 → `avatar.js` 的 `EMO_POSE` 里给个姿态。

## 目录结构

```
server/            FastAPI 服务：app.py（API/会话）、protocol.py（标签/人设/清洗）、memory.py（上下文+长期记忆）、
                   catalog.py（能力目录：可选提供方 / 音色 / CLI 自动探测）、configure.py（三层配置解析与迁移）、
                   persona.py（人设：默认 + 页面覆盖）、settings.py（页面设置的读写）、asr.py（语音识别）、
                   brains/（大脑适配器）、intent.py（动作意图分流）、actions.py（动作执行的兼容外壳）、tts.py（本机语音合成）、
                   io/（输入输出适配层：inputs/、outputs/、tts/、dispatcher、turn、bus）、
                   realtime/（Qwen-Audio-Realtime 全双工运行时 + 工具）、
                   agents/（她的"手"与主动开口：job.py、backends.py、orchestrator.py、initiative.py）
web/               前端：index.html、css/style.css、js/rig.js（网格骨骼/弹簧/动作）、js/avatar.js（WebGL 形象）、
                   js/remote.js（可选 THA3 渲染）、js/speech.js（字幕与语音）、js/voice.js（TTS 播放 + 连续语音输入）、
                   js/realtime.js（全双工音频收发）、js/settings.js（设置面板）、js/main.js
tools/             yaya.py（命令行控制）、mcp_server.py（MCP）、game.py + game_control/（操作桌面，desktop 类任务用）、
                   selftest.py + selftest_agents.py（自检）、gen_sprites.py、build_assets.py、tha3_bridge.py（可选 THA3 渲染）
docs/              README 截图、agent-runtime.md（后台 agent 与主动开口的架构）
state/             会话记忆、任务表 jobs.json、定时提醒 schedule.json、页面保存的 key（secrets.json；全部不入库）
config.example.json
```
