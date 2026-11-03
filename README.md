# 大肥鱼 · 数字生命

> 住在旧工业笔记本屏幕里的 Q 版大肥鱼（DeepSeek 拟人）。
> 形象和"大脑"解耦——任何 LLM API、agent CLI 或脚本都能驱动她说话、切表情、做动作。

![大肥鱼](docs/screenshot.png)

## 特性

- **实时形象**：立绘被切成网格实时形变，头部转动、呼吸、头发/呆毛/耳朵的惯性摆动全部在浏览器里算，不需要 GPU。
- **情绪惯性**：新情绪不会一来就翻脸，而是沿路线一步步走过去（哭 → 抽鼻子平复 → 害羞 → 笑）。
- **口型与字幕**：逐句电影字幕，嘴型跟着每个音节开合；接口留好了，随时能接 TTS / ASR。
- **可插拔大脑**：OpenAI 兼容接口（DeepSeek / 通义 / Kimi / Ollama…）、Claude API、任意命令行 agent，离线 demo 开箱即用。
- **外部可驱动**：HTTP API + 命令行 + MCP 三套入口，Claude Code、Codex 这类 agent 可以直接把她当"嘴巴"用。
- **OBS 友好**：`?obs=1` 就是一块干净的视频源。

## 目录

- [快速开始](#快速开始)
- [大脑配置](#大脑配置configjson)
- [控制接口](#控制接口)
- [让 agent 把她当"嘴巴"（MCP）](#让-agent-把她当嘴巴mcp)
- [形象是怎么动起来的](#形象是怎么动起来的)
- [可选：THA3 神经网络渲染](#可选tha3-神经网络渲染)
- [接 TTS / ASR / 全双工](#接-tts--asr--全双工)
- [素材](#素材)
- [目录结构](#目录结构)

## 快速开始

```bash
pip install -r requirements.txt
python -m server                 # http://127.0.0.1:8848
```

打开 http://127.0.0.1:8848 ，底部输入框聊天。默认大脑是离线 demo，不用任何 key。

```bash
cp config.example.json config.json   # 编辑后重启服务；key 只写环境变量名，不写明文
set DEEPSEEK_API_KEY=sk-...          # PowerShell: $env:DEEPSEEK_API_KEY="sk-..."
```

调试快捷键：`Enter` 聚焦输入框，`Esc` 打断，数字键 `1`~`7` 切表情，字母键 `n s h j d t g w f x` 做动作。

| URL 参数 | 作用 |
|---|---|
| `?obs=1` | 隐藏控制台，用作 OBS 浏览器源 |
| `?tts=browser` | 用浏览器自带语音朗读（验证 voice 接口用，正式 TTS 见下文） |
| `?render=tha3` | 改用 THA3 渲染（需要另开 GPU 桥，见下文） |

### 架构

```
┌──────────── 大脑（可插拔）────────────┐      ┌──────── 数字人服务 ────────┐      ┌──── 前端（浏览器 / OBS）────┐
│ demo · DeepSeek/Qwen/Ollama(OpenAI兼容) │ ───▶ │ 情绪标签解析 · 字幕清洗       │ SSE  │ 场景 + 屏幕 HUD + 立绘       │
│ Claude API · claude/codex 等 agent CLI │      │ 打断/排队 · 对话历史          │ ───▶ │ WebGL 网格形变 · 表情/口型/动作│
└───────────────────────────────────────┘      │ HTTP 控制 API · MCP           │      │ 逐句电影字幕 · voice 接口     │
     外部 agent / 脚本 ── yaya.py / MCP ──────▶ └──────────────────────────────┘      └─────────────────────────────┘
```

## 大脑配置（config.json）

| type | 说明 | 关键字段 |
|---|---|---|
| `demo` | 离线脚本台词，调形象用 | — |
| `openai` | 任何 OpenAI 兼容接口：DeepSeek、通义、Kimi、智谱、Ollama、vLLM… | `base_url` `model` `api_key_env` `extra` |
| `anthropic` | Claude（官方 SDK，需 `pip install anthropic`），默认开启拒答时服务端自动回退 | `model` `effort` `fallbacks` |
| `cli` | 任何命令行 agent。人设和历史拼进 prompt，经 stdin 传入 | `cmd` `stdin` `timeout` `cwd` `persona` |

`cli` 的 `cmd` 支持占位符 `{prompt}`（`stdin=false` 时替换为完整提示词）和 `{outfile}`（以临时文件内容作为回复，适合 stdout 夹杂日志的 CLI，如 codex）。已实测 `claude -p`（约 7 秒）和 `codex exec`（约 45 秒）。打断时会杀掉整棵子进程树。

### 情绪与动作标签

大脑输出里的情绪标签会被解析成表情切换：

`[happy]` `[sad]` `[surprised]` `[thinking]` `[shy]` `[angry]` `[neutral]`

动作标签会在字幕走到那个位置时触发动作：

`[nod]` `[shake]` `[hop]` `[jump]` `[droop]` `[tremble]` `[tilt]` `[wiggle]` `[sniff]` `[sigh]`

也认中文别名（`[点头]` `[歪头]` `[抽鼻子]` `[叹气]`……）。markdown、代码块会被清洗掉，不会出现在字幕里。

## 记忆

两层记忆，都会**落盘**（`state/memory.json`），重启服务还在。

**短期上下文**——最近 `history_turns` 轮对话（默认 12 轮），每轮都带进下一次请求。助手那侧会带上当时的情绪标签（`[happy]你好`），所以她知道自己刚才什么状态。`POST /api/reset` 清空。

**长期记忆点**——"爸爸讨厌香菜"这种具体的事，她会一直记着。三种写法：

```bash
# 1. 大脑自己记：在回复里插标签，内容不会出现在字幕里
[happy]好的！[记住:爸爸讨厌香菜]我记住了。

# 2. 外部接口 / 命令行
curl -XPOST localhost:8848/api/memory -d '{"text":"爸爸喜欢喝奶茶"}'
python tools/yaya.py remember "约定：每天要问候爸爸"
python tools/yaya.py memory          # 看看她记得什么
python tools/yaya.py forget "爸爸喜欢喝奶茶"    # 忘掉一件；不带参数就全忘

# 3. agent 用 MCP 工具
yaya_remember {"text": "..."}  /  yaya_forget {"text": "..."}
```

记住的事会写进发给大脑的系统提示（"你记得这些事：…"），加上当前情绪和最近的情绪轨迹，所以她会顺着已有记忆说话。前端 HUD 的 MEMORY CAPSULE 显示 `FACTS` 条数，记住的瞬间字幕上方会飘一行"记住：xxx"。

同一个 session（同一份 `state/memory.json`）共享记忆；要给不同的人用，设 `YUYAYA_STATE` 指到不同文件即可。

## 控制接口

| 接口 | 作用 |
|---|---|
| `POST /api/chat {"text", "brain"?, "wait"?}` | 交给大脑回复；新的 chat 会打断正在说的话 |
| `POST /api/say {"text", "emotion"?, "interrupt"?}` | 直接说话，不经过大脑；默认排队播放 |
| `POST /api/emotion {"name", "hold"?, "instant"?}` | 切情绪，默认一直保留并按惯性逐步过渡；`instant: true` 立即切换；`hold` 为秒数时表示临时表情（立即生效），结束恢复之前的情绪 |
| `POST /api/motion {"name"}` | 做动作：`nod` `shake` `hop` `jump` `droop` `tremble` `tilt` `wiggle` `sniff` `sigh` |
| `POST /api/state {"state"}` | `idle` / `listening` / `thinking` / `speaking` |
| `POST /api/mouth {"level"}` 或 `{"levels", "fps"}` | 外部口型驱动（给 TTS 用），会覆盖内置的随机开合 |
| `POST /api/interrupt` | 打断（全双工插话用） |
| `GET /api/memory` | 看她记得什么（长期记忆点 + 对话轮数） |
| `POST /api/memory {"text"}` | 替她记住一件事 |
| `DELETE /api/memory {"text"?}` | 忘掉一件事；不带 text 就全忘 |
| `POST /api/reset` · `GET /api/brains` · `POST /api/brain {"name"}` | 清历史 / 查看 / 切换大脑 |
| `GET /api/events` | SSE 事件流（前端订阅；也可以自己写别的前端） |

命令行：`python tools/yaya.py chat "你好"`、`yaya.py say "[happy][hop]搞定啦"`、`yaya.py emotion sad`、`yaya.py motion nod`……

### 让 agent 把她当"嘴巴"（MCP）

```bash
claude mcp add yuyaya -- python /path/to/yuyaya/tools/mcp_server.py
```

提供 `yaya_say` / `yaya_emotion` / `yaya_motion` / `yaya_state` / `yaya_remember` / `yaya_forget` 六个工具。agent 干活时可以让大肥鱼播报进度、用表情表达状态，也可以让她记住用户交代的事。

## 形象是怎么动起来的

全部在浏览器里实时完成，不需要 GPU 服务器，OBS 浏览器源可直接用。

**网格形变**（`web/js/rig.js`）——立绘被切成 40×50 的网格，按骨骼点（脖子、脸中心、呆毛、两只耳朵的根部和尖端，写在 `manifest.json` 的 `sprites.rig`）分区加权。头部歪头/转头/点头（脸中心移得多、轮廓移得少，带一点立体感）、呼吸、身体倾斜、跳跃时的压扁拉长、头发/呆毛/耳朵的惯性摆动，全部由阻尼弹簧驱动，动作之间自然过渡。

**脸部合成**（`web/js/avatar.js`，WebGL2）——着色器里叠加 `表情底图 + 口型贴片（0~3 级）+ 闭眼贴片`，贴片在网格的静止坐标里采样，所以会跟着头一起动。7 种表情 × 4 级口型 × 眨眼可任意组合。

**表情切换**——先闭眼，闭眼的瞬间交叉淡化到新表情再睁眼，看不出"换图"；同时带一个反应动作和该表情的常驻姿态（难过时耳朵耷拉低头、思考时抬头歪头、害羞时低头侧脸……）。

**持续情绪**——情绪默认永久保留，不会被空闲、说话或思考自动清掉；只有新情绪标签、再次调用情绪 API 或显式 `[neutral]` 才会改变它。思考时若情绪是 neutral 才自动显示思考脸；难过、生气等情绪下只叠加轻微思考姿态，仍然保留原来的脸。刷新/重连会恢复持续情绪，但不会重放上一次的思考或说话状态。

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
- THA3 要求头部约 128px，大肥鱼是 Q 版，整个人在 512 画布里只有约 240×300，显示时会放大，比 WebGL 模式略软。
- 姿态到 THA3 参数的映射在 `tha3_bridge.py` 的 `POSE_MAP`，方向不对时改正负号即可。
- 桥没开或断开时自动退回 WebGL 渲染，每 5 秒重连。`--mock` 不加载模型，只用来联调前端。

RTX 3060 实测（PyTorch 2.3.1 + CUDA 12.1）：

| 模型 | 纯推理 | PyTorch 显存峰值 |
|---|---:|---:|
| `separable_half` | 22.5ms / 44.5fps | 775MiB |
| `standard_half` | 30.3ms / 33.0fps | 472MiB |
| `standard_float` | 44.5ms / 22.5fps | 1094MiB |

默认 `separable_half` 走完整桥接链路（HTTP + 推理 + 回传）预热后约 **28fps**，浏览器实测 **27~29fps**，运行中 GPU 总占用约 1.85GiB（含桌面等其它进程）。首次遇到某个表情/口型组合时要合成输入图，之后有缓存。

桥上必须开启 `cudnn.benchmark`，并且只在加载模型的主线程里推理：让每个 HTTP 连接线程直接跑 CUDA 会慢几十倍。

> **建议：日常用 WebGL，THA3 用于体验更自然的头部转动。**
> WebGL 原图更清晰、带发丝/呆毛/耳朵惯性、无 GPU 依赖；THA3 的头颈运动更自然，但画面略软且需要常驻 GPU 进程。

## 接 TTS / ASR / 全双工

接口已经预留好了，不需要改动形象层。

- **TTS**：前端 `SpeechPlayer` 接受 `voice = { speak(text): Promise, cancel() }`，每句话交给 voice 朗读，字幕和语音同步推进。在 `speak()` 里用 WebAudio `AnalyserNode` 取响度调 `avatar.setMouthLevel(x)`（0~1，自动量化成 4 级口型）就是真口型。参考 `web/js/speech.js` 里的 `BrowserVoice`。服务端 TTS 也可以直接 `POST /api/mouth {"levels": [...], "fps": 60}`。
- **ASR**：识别开始 `POST /api/state {"state":"listening"}`（她会轻微摇晃、HUD 显示 LISTENING），识别完 `POST /api/chat`。
- **全双工 / 插话**：检测到用户开口就 `POST /api/interrupt`。服务端会取消大脑请求、杀掉 CLI 子进程，前端立即停止字幕和口型。

## 素材

| 文件 | 说明 |
|---|---|
| `web/assets/` | 加工后的前端素材 + `manifest.json`（运行只需要这一份） |
| `gen/scene.png` `gen/room.png` | 外景（屏幕为绿幕）、屏幕里的房间 |
| `gen/char_base.png` | 角色母版（绿幕） |
| `gen/char_<表情>.png` | 由母版改图得到的表情差分 |
| `gen/char_<表情>_m1/_m2/_m3.png` `gen/char_<表情>_blink.png` | 以表情帧为底图只改嘴 / 只改眼得到的口型、闭眼帧 |
| `docs/` | README 用的截图 |

仓库里只放运行需要的 `web/assets/` 和几张截图；`gen/*.png` 是约 100MB 的中间大图，不入库，删掉也不影响运行，需要改素材时重新生成：

```bash
set APINEBULA_API_KEY=sk-...
python tools/gen_sprites.py            # 生成缺失的帧；也可指定：gen_sprites.py happy sad_m1
python tools/build_assets.py           # 抠像、对齐、切贴片、检测屏幕区域，写入 web/assets
```

`build_assets.py` 只把每张表情差分里"和母版不同的脸部区域"贴回母版，所以头发和身体在所有表情之间像素级一致；口型 / 闭眼帧只取嘴 / 眼区域里、和嘴 / 眼中心相连的变化（改图时顺带画歪的泪珠、汗珠会被丢掉），裁成小贴片并记录位置。

**新增表情的步骤**：`gen_sprites.py` 的 `FRAMES` 里加表情帧和它的口型 / 闭眼帧 → `build_assets.py` 的 `EMOTIONS` / `MOUTHS` / `BLINKS` 里登记 → `server/protocol.py` 的 `EMOTIONS` 里加名字 → `avatar.js` 的 `EMO_POSE` 里给个姿态。

## 目录结构

```
server/            FastAPI 服务：app.py（总线/会话/API）、protocol.py（标签/人设/清洗）、memory.py（上下文+长期记忆）、
                   brains/（适配器）
web/               前端：index.html、css/style.css、js/rig.js（网格骨骼/弹簧/动作）、js/avatar.js（WebGL 形象）、
                   js/remote.js（可选 THA3 渲染）、js/speech.js（字幕与语音）、js/main.js
tools/             yaya.py（命令行控制）、mcp_server.py（MCP）、gen_sprites.py、build_assets.py、tha3_bridge.py（可选 THA3 渲染）
docs/              README 截图
state/             会话记忆文件（不入库）
config.example.json
```
