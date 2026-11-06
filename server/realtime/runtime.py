"""全双工语音聊天运行时。

浏览器 <--WebSocket PCM--> 本运行时 <--WebSocket--> Qwen-Audio-3.1-Realtime-Flash

职责：
- 把浏览器的麦克风 PCM16(16k) 原样转给模型；
- 把模型的音频 PCM16(24k) 二进制转回浏览器；
- 把模型的转写文本变成字幕事件（speech begin/text/end），让立绘和字幕动起来；
- 处理 server VAD 的 speech_started：一开口就打断（停字幕/音频、取消当前回答）；
- 处理聊天辅助 function calling：remember / set_emotion / set_motion。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time

import aiohttp

from ..protocol import PERSONA, SpeechStream
from .tools import TOOL_SCHEMAS, execute

log = logging.getLogger("yuyaya.realtime")

_CAPTION_TAG = re.compile(r"\[[^\]]{0,40}\]?")
DEFAULT_URL = ("wss://cn-beijing.maas.qianwenaiapi.com/api-ws/v1/realtime"
               "?model={model}")


class _Bridge:
    """一条浏览器连接对应一个模型连接。"""

    def __init__(self, runtime, ws, up) -> None:
        self.runtime = runtime
        self.ws = ws
        self.up = up
        self.sp = SpeechStream()
        self.turn = None
        self.saw_transcript_delta = False
        self.response_active = False
        self.pending_outputs = []
        self.caption_raw = ""
        self.user_speaking = False
        self.seen_calls: set = set()        # function_call 会被两个事件各通知一次，按 call_id 去重
        self.last_activity = 0.0
        self.echo_guard_until = 0.0     # 回答结束后再屏蔽一小段麦克风，躲开扬声器尾音
        self.replay_only = False         # say 只朗读 agent 已生成的回答，不重复记入历史
        self.loop = asyncio.get_running_loop()

    # ---------- 浏览器 -> 模型 ----------

    async def client_loop(self) -> None:
        while True:
            msg = await self.ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("bytes")
            if data:
                # 她正在说话时丢掉麦克风音频：扬声器的声音会被麦克风收回去，
                # 浏览器那点回声消除不够用，模型会把自己的话当成用户在说，
                # 于是自己跟自己聊起来（"一直在自言自语"）。
                if self.response_active or self.echo_guard_until > time.time():
                    continue
                await self.up.send_json({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(data).decode("ascii"),
                })
                continue
            text = msg.get("text")
            if not text:
                continue
            try:
                cmd = json.loads(text)
            except ValueError:
                continue
            ctype = cmd.get("type")
            if ctype == "cancel":
                await self._barge_in(send_cancel=True)
            elif ctype == "text" and cmd.get("text"):
                self.runtime.touch()
                await self._barge_in(send_cancel=True)      # 打字也算插话：先停掉正在说的
                await self._safe_send({"type": "caption_clear"})
                self.runtime._remember_turn("user", cmd["text"])
                await self.up.send_json({
                    "type": "conversation.item.create",
                    "item": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": cmd["text"]}]},
                })
                await self.up.send_json({"type": "response.create"})
            elif ctype == "say" and cmd.get("text"):
                # 回答已经由内嵌 agent 生成，这里只借用实时会话的声音和音频链路。
                self.runtime.touch()
                await self._barge_in(send_cancel=True)
                await self._safe_send({"type": "caption_clear"})
                self.replay_only = True
                spoken = str(cmd["text"])[:1500]
                await self.up.send_json({
                    "type": "conversation.item.create",
                    "item": {"type": "message", "role": "user",
                             "content": [{"type": "input_text",
                                          "text": "这是一段已经想好的回答。请保持鱼丫丫的语气，"
                                                  "把下面内容直接说给用户；不要补充、修改或解释：\n"
                                                  + spoken}]},
                })
                await self.up.send_json({"type": "response.create"})
            elif ctype == "ping":
                await self.ws.send_json({"type": "pong"})

    # ---------- 模型 -> 浏览器 / 事件总线 ----------

    async def up_loop(self) -> None:
        async for msg in self.up:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    event = json.loads(msg.data)
                except ValueError:
                    continue
                await self.on_event(event)
            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED,
                              aiohttp.WSMsgType.CLOSING):
                try:
                    await self.ws.send_json({"type": "closed"})
                except Exception:
                    pass
                return

    async def on_event(self, ev: dict) -> None:
        etype = ev.get("type", "")
        rt = self.runtime
        hub = rt.hub

        if etype == "session.updated":
            await self.ws.send_json({
                "type": "ready", "model": rt.model, "voice": rt.voice,
                "input_sample_rate": rt.input_rate, "output_sample_rate": rt.output_rate,
            })
            hub.publish({"type": "state", "state": "listening"})

        elif etype == "input_audio_buffer.speech_started":
            self.user_speaking = True
            self.last_activity = time.time()
            rt.touch()
            await self._barge_in(send_cancel=True)
            self.caption_raw = ""
            await self._safe_send({"type": "caption_clear"})
            await self._safe_send({"type": "speech_started"})
            hub.publish({"type": "state", "state": "listening"})

        elif etype == "input_audio_buffer.speech_stopped":
            self.user_speaking = False
            self.last_activity = time.time()      # 别人刚说完，汇报要等一小会儿再插
            hub.publish({"type": "state", "state": "thinking"})

        elif etype == "conversation.item.input_audio_transcription.completed":
            text = (ev.get("transcript") or "").strip()
            if text:
                hub.publish({"type": "user", "id": 0, "text": text, "source": "realtime"})
                self.runtime._remember_turn("user", text)
                await self.ws.send_json({"type": "transcript", "role": "user", "text": text})

        elif etype == "response.created":
            self.response_active = True
            self.caption_raw = ""
            await self._safe_send({"type": "caption_clear"})
            self._begin_turn()
            hub.publish({"type": "state", "state": "speaking"})

        elif etype in ("response.audio.delta", "response.audio_transcript.delta",
                       "response.text.delta", "response.audio_transcript.done",
                       "response.text.done", "response.done",
                       "response.function_call_arguments.done",
                       "response.output_item.done"):
            await self._on_response(etype, ev)

        elif etype == "error":
            err = ev.get("error") or {}
            log.warning("realtime error: %s", err)
            await self._safe_send({"type": "error", "error": err})     # 浏览器可能已经断开
            hub.publish({"type": "error", "brain": "realtime",
                         "message": str(err.get("message") or err)[:500]})

    def _begin_turn(self) -> None:
        rt = self.runtime
        self.sp = SpeechStream()
        self.saw_transcript_delta = False
        if self.turn is not None:
            return
        self.turn = rt.avatar.turns.begin("default", "realtime", kind="realtime",
                                          barge_in=False, publish_interrupt=False)
        t = self.turn
        rt.hub.publish({"type": "speech", "id": t.id, "turn": t.id, "session": "default",
                        "ch": "main", "op": "begin", "source": "realtime",
                        "queue": True}, turn=t)

    async def _on_response(self, etype: str, ev: dict) -> None:
        rt = self.runtime
        hub = rt.hub

        if etype == "response.audio.delta":
            delta = ev.get("delta")
            if delta:
                try:
                    await self.ws.send_bytes(base64.b64decode(delta))
                except Exception:
                    pass
            return

        if etype in ("response.audio_transcript.delta", "response.text.delta"):
            delta = ev.get("delta") or ""
            if delta:
                # 字幕直接跟模型的转写增量走；情绪/动作标签不显示，
                # 跨 delta 的半截标签也先藏起来，等闭合后再整段去掉。
                self.caption_raw += delta
                visible = _CAPTION_TAG.sub("", self.caption_raw)
                await self._safe_send({"type": "caption", "text": visible})
            if delta and self.turn is not None:
                self.saw_transcript_delta = True
                rt.avatar._emit(self.turn, self.sp.feed(delta), "main")
            return

        if etype in ("response.audio_transcript.done", "response.text.done"):
            text = ev.get("transcript")
            if text and self.turn is not None and not self.turn.stale and not self.replay_only:
                self.runtime._remember_turn("assistant", text)
            if text and not self.saw_transcript_delta and self.turn is not None:
                rt.avatar._emit(self.turn, self.sp.feed(text), "main")
            return

        if etype == "response.function_call_arguments.done":
            await self._run_tool(ev.get("name"), ev.get("arguments"), ev.get("call_id"))
            return

        if etype == "response.output_item.done":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                await self._run_tool(item.get("name"), item.get("arguments"),
                                     item.get("call_id"))
            return

        if etype == "response.done":
            self.response_active = False
            # 回答播完后音频还在扬声器里走，再挡一小段，别把尾音收进来
            self.echo_guard_until = time.time() + float(rt.conf.get("echo_guard_sec", 1.2))
            await self._safe_send({"type": "caption_end"})
            await self._flush_tools()      # 工具结果要等这一轮结束再交回去
            self._finish_turn()
            self.replay_only = False

    async def _run_tool(self, name: str, arguments, call_id) -> None:
        if not name:
            return
        if call_id:
            if call_id in self.seen_calls:
                return
            self.seen_calls.add(call_id)
        try:
            args = json.loads(arguments or "{}")
        except ValueError:
            args = {}
        log.info("realtime 工具调用：%s %s", name, args)
        self.runtime.hub.publish({"type": "state", "state": "thinking"})
        try:
            # codex 可能要跑几十秒，不能阻塞事件循环（否则音频/心跳都停了）
            output = await asyncio.to_thread(execute, self.runtime, name, args)
        except Exception as e:
            log.exception("工具执行失败：%s", name)
            output = json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
        # 这一轮还没结束，不能马上 response.create（平台会报
        # "Cannot create response while another response is in progress"）；
        # 先攒着，等 response.done 再一起交回去。
        self.pending_outputs.append((call_id, output))

    async def _safe_send(self, payload: dict, upstream: bool = False) -> bool:
        target = self.up if upstream else self.ws
        try:
            await target.send_json(payload)
            return True
        except Exception:
            return False

    async def _flush_tools(self) -> None:
        if not self.pending_outputs:
            return
        pending, self.pending_outputs = self.pending_outputs, []
        for call_id, output in pending:
            ok = await self._safe_send({
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id,
                         "output": output},
            }, upstream=True)
            if not ok:
                return
        await self._safe_send({"type": "response.create"}, upstream=True)

    def _finish_turn(self) -> None:
        if self.turn is None:
            return
        turn = self.turn
        rt = self.runtime
        rt.avatar._emit(turn, self.sp.flush(), "main")
        rt.hub.publish({"type": "speech", "id": turn.id, "turn": turn.id,
                        "session": "default", "ch": "main", "op": "end",
                        "interrupted": turn.stale}, turn=None if turn.stale else turn)
        rt.avatar.turns.finish(turn)
        self.turn = None
        self.sp = SpeechStream()

    def idle(self, grace: float = 0.0) -> bool:
        """现在能不能插话：用户没在说话、模型没在回答、没有待交回的工具结果。

        ``grace`` 是"刚安静下来还要再等一会儿"：用户话音刚落时立刻汇报会显得抢话，
        等一小段更像在认真听完。
        """
        if self.user_speaking or self.response_active or self.pending_outputs:
            return False
        if grace and self.last_activity and time.time() - self.last_activity < grace:
            return False
        return True

    async def inject(self, text: str, grace: float = 0.0) -> bool:
        """把一条系统通知交给模型，让它自己决定怎么说（也可以什么都不说）。"""
        if not self.idle(grace):
            return False
        ok = await self._safe_send({
            "type": "conversation.item.create",
            "item": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": text}]},
        }, upstream=True)
        if not ok:
            return False
        self.response_active = True            # 抢占：别让下一条通知叠在它前面
        return await self._safe_send({"type": "response.create"}, upstream=True)

    async def _barge_in(self, send_cancel: bool = False) -> None:
        rt = self.runtime
        interrupted = rt.avatar.turns.interrupt("default", reason="barge_in",
                                                cancel_action=False)
        if send_cancel and self.response_active:
            try:
                await self.up.send_json({"type": "response.cancel"})
            except Exception:
                pass
        self.response_active = False
        self.turn = None
        self.sp = SpeechStream()
        if interrupted:
            log.info("实时语音：用户插话，已打断")


class _Speaker:
    """给外部脚本预留的发言口：线程安全地把文本注入当前的实时会话。"""

    def __init__(self, runtime) -> None:
        self.rt = runtime

    def _bridge(self):
        return self.rt.bridges[-1] if self.rt.bridges else None

    def has_session(self) -> bool:
        return bool(self.rt.bridges)

    def idle(self) -> bool:
        b = self._bridge()
        return b is not None and b.idle()

    def speak(self, text: str, grace: float = 0.0) -> bool:
        b = self._bridge()
        if b is None:
            return False
        fut = asyncio.run_coroutine_threadsafe(b.inject(text, grace), b.loop)
        try:
            return bool(fut.result(timeout=5))
        except Exception:
            return False


class RealtimeRuntime:
    def __init__(self, hub, avatar, conf: dict | None = None) -> None:
        self.hub = hub
        self.avatar = avatar
        self.conf = conf or {}
        self.enabled = bool(self.conf.get("enabled"))
        self.model = self.conf.get("model") or "qwen-audio-3.1-realtime-flash"
        self.url = (self.conf.get("url") or DEFAULT_URL).format(model=self.model)
        self.api_key_env = self.conf.get("api_key_env") or "DASHSCOPE_API_KEY"
        self.api_key = os.environ.get(self.api_key_env, "")
        self.voice = self.conf.get("voice") or "qiaoxiaojiao_v3.1"
        self.input_rate = int(self.conf.get("input_sample_rate", 16000))
        self.output_rate = int(self.conf.get("output_sample_rate", 24000))
        self.base_instructions = self.build_instructions()
        self.turn_detection = {
            "type": "server_vad",
            "threshold": float(self.conf.get("vad_threshold", 0.5)),
            "silence_duration_ms": int(self.conf.get("silence_duration_ms", 500)),
        }
        self.enable_tools = bool(self.conf.get("tools", True))
        self.bridges: list = []
        self.speaker = _Speaker(self)

    def _persona(self) -> str:
        """当前人设：页面上改过就用改过的，没改就用内置。"""
        return self.conf.get("persona_text") or PERSONA

    def build_instructions(self) -> str:
        """人设 + 语音模式下的额外约定。人设改了要重新调这个。"""
        return (self.conf.get("instructions")
                or (self._persona() + self.VOICE_SUFFIX))

    VOICE_SUFFIX = """

（你现在是语音输出：方括号里的情绪/动作标签只作为文字标记，不要念出来。）

你只负责聊天。不能读写文件、操作电脑、执行命令或调用外部 agent。
如果用户要求这些能力，就自然说明当前只能陪聊，不要假装已经开始或已经完成。
可以长期记住用户的喜好，也可以用表情和动作配合说话。"""

    def touch(self) -> None:
        init = getattr(self.avatar, "initiative", None)
        if init is not None:
            init.touch()

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.key)

    @property
    def key(self) -> str:
        """配置里直接带的 key 优先；没有就读环境变量（老配置）。"""
        return self.conf.get("api_key") or os.environ.get(self.api_key_env, "")

    def info(self) -> dict:
        return {
            "enabled": self.available,
            "provider": "qwen",
            "model": self.model,
            "voice": self.voice,
            "input_sample_rate": self.input_rate,
            "output_sample_rate": self.output_rate,
            "tools": self.enable_tools,
        }

    def _instructions(self) -> str:
        """连接时把长期记忆和最近几轮对话塞进 instructions。

        模型自己的 session 只在一条 WebSocket 里有效；断开重连、或从文字聊天
        切过来时，靠这里把同一份 Session 的记忆重新喂给它。
        """
        parts = [self.base_instructions]
        session = self.avatar.session
        try:
            ctx = session.context()
        except Exception:
            ctx = ""
        if ctx:
            parts.append(ctx)
        try:
            history = session.messages()[-6:]
        except Exception:
            history = []
        if history:
            nl = chr(10)
            lines = []
            for m in history:
                who = "用户" if m.get("role") == "user" else "你"
                text = (m.get("content") or "").replace(nl, " ")[:100]
                lines.append(f"{who}：{text}")
            parts.append("（最近的对话：" + nl + nl.join(lines) + "）")
        return (chr(10) * 2).join(parts)

    def _remember_turn(self, role: str, text: str) -> None:
        text = " ".join((text or "").split()).strip()
        if not text:
            return
        session = self.avatar.session
        try:
            last = session.turns[-1] if session.turns else None
            if last and last.get("role") == role and last.get("content") == text:
                return
            session.add_turn(role, text)
            session.save()
        except Exception:
            log.debug("记录实时对话失败", exc_info=True)

    def session_update(self) -> dict:
        session = {
            "modalities": ["text", "audio"],
            "voice": self.voice,
            "instructions": self._instructions(),
            "turn_detection": self.turn_detection,
        }
        if self.enable_tools:
            session["tools"] = TOOL_SCHEMAS
            session["tool_choice"] = "auto"
        return {"type": "session.update", "session": session}

    async def handle(self, ws) -> None:
        await ws.accept()
        if not self.available:
            reason = ("realtime 未启用" if not self.enabled
                      else f"缺少环境变量 {self.api_key_env}")
            await ws.send_json({"type": "error", "error": reason})
            await ws.close(code=1011)
            return
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=15)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.ws_connect(
                        self.url, headers={"Authorization": f"Bearer {self.key}"},
                        heartbeat=20, max_msg_size=0) as up:
                    await up.send_json(self.session_update())
                    bridge = _Bridge(self, ws, up)
                    self.bridges.append(bridge)
                    try:
                        await asyncio.gather(bridge.client_loop(), bridge.up_loop())
                    finally:
                        if bridge in self.bridges:
                            self.bridges.remove(bridge)
        except aiohttp.ClientError as e:
            log.warning("连接实时模型失败：%s", e)
            try:
                await ws.send_json({"type": "error", "error": f"连接实时模型失败：{e}"})
            except Exception:
                pass
        except Exception:
            log.exception("实时语音运行时异常")
        finally:
            try:
                await ws.close()
            except Exception:
                pass
