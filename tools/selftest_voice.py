"""伪全双工语音流水线的自检：切句 / VAD / 音频转换 / 说话器 / WebSocket 往返 / 插话 / 回声 / 合并。

全部用假的 ASR 和假的 TTS（正弦波），不联网、不花钱、不依赖声卡。
"""
from __future__ import annotations

import asyncio
import math
import struct
import threading
import time

RATE_IN, RATE_OUT = 16000, 24000


def tone(ms: int, amp: float = 0.2, rate: int = RATE_IN, hz: float = 220.0) -> bytes:
    n = int(rate * ms / 1000)
    return b"".join(struct.pack("<h", int(32767 * amp * math.sin(2 * math.pi * hz * i / rate)))
                    for i in range(n))


def quiet(ms: int, rate: int = RATE_IN) -> bytes:
    n = int(rate * ms / 1000)
    # 极轻的环境噪声（幅度 ~0.002）
    return b"".join(struct.pack("<h", int(65 * math.sin(i * 1.7))) for i in range(n))


def feed_all(ep, pcm: bytes, strict: bool = False):
    events = []
    step = 640                                  # 20ms
    for i in range(0, len(pcm), step):
        events += ep.feed(pcm[i:i + step], strict=strict)
    return events


def voice_units(check) -> None:
    from server.voice import Endpointer, split_sentences
    from server.voice import audio as va

    # ---------------- 切句
    s = split_sentences("好的。我来看看，这个问题有点意思！你觉得呢？")
    check("切句：按句末标点", s == ["好的。我来看看，这个问题有点意思！", "你觉得呢？"]
          or s == ["好的。", "我来看看，这个问题有点意思！", "你觉得呢？"], str(s))
    s = split_sentences("嗯。我觉得可以。")
    check("切句：碎片并进下一句", s == ["嗯。我觉得可以。"], str(s))
    long = "这是一段很长很长的话，" * 8
    s = split_sentences(long, soft_max=30)
    check("切句：太长的在逗号处再切，每段都有内容",
          len(s) > 1 and all(len(x) <= 40 for x in s), str([len(x) for x in s]))
    check("切句：纯标点不成句", split_sentences("……！？") == [], str(split_sentences("……！？")))

    # ---------------- VAD
    ep = Endpointer()
    ev = feed_all(ep, quiet(600) + tone(900) + quiet(1000))
    kinds = [e[0] for e in ev]
    check("VAD：一句话 = 一次 start + 一次 end", kinds == ["start", "end"], str(kinds))
    pcm = next(e for e in ev if e[0] == "end")[1]
    check("VAD：带预录且不截尾（0.9s 话音 + 前后留白）",
          0.9 * RATE_IN * 2 <= len(pcm) <= 1.9 * RATE_IN * 2, f"{len(pcm) / 2 / RATE_IN:.2f}s")

    ep = Endpointer()
    ev = feed_all(ep, quiet(600) + tone(60) + quiet(1000))
    check("VAD：60ms 的敲击声不算开口", [e[0] for e in ev] == [], str(ev))

    ep = Endpointer()
    ev = feed_all(ep, quiet(400) + tone(180) + quiet(1000))
    check("VAD：开口 180ms 但不足 250ms → 丢弃当噪声",
          [e[0] for e in ev] == ["start", "drop"], str([e[0] for e in ev]))

    ep = Endpointer()
    ev = feed_all(ep, quiet(400) + tone(200) + quiet(1000), strict=True)
    check("VAD：她在说话时（strict）200ms 的声音不会触发插话",
          [e[0] for e in ev] == [], str([e[0] for e in ev]))
    ep = Endpointer()
    ev = feed_all(ep, quiet(400) + tone(700) + quiet(1000), strict=True)
    check("VAD：她在说话时，持续 700ms 的发声才算插话",
          [e[0] for e in ev] == ["start", "end"], str([e[0] for e in ev]))

    ep = Endpointer()
    ev = feed_all(ep, tone(500) + quiet(300) + tone(500) + quiet(1000))
    check("VAD：话中 300ms 的停顿不会把一句话切成两句",
          [e[0] for e in ev] == ["start", "end"], str([e[0] for e in ev]))

    ep = Endpointer()
    feed_all(ep, quiet(5000))
    check("VAD：噪声底自适应，纯环境噪声不触发", not ep.in_speech and ep.floor < 0.01,
          f"floor={ep.floor:.4f}")

    # 分片喂：任意切法结果一致（流式场景里包的大小不固定）
    ep = Endpointer()
    sig = quiet(500) + tone(800) + quiet(900)
    ev = []
    for i in range(0, len(sig), 1000):          # 1000 字节：不是 20ms 的整数倍
        ev += ep.feed(sig[i:i + 1000])
    check("VAD：包大小不整齐也能正确切句", [e[0] for e in ev] == ["start", "end"],
          str([e[0] for e in ev]))

    # ---------------- 音频转换
    wav16 = va.pcm_to_wav(tone(500, rate=16000), 16000)
    pcm24 = va.wav_to_pcm(wav16, 24000)
    check("音频：16k WAV 重采样到 24k，时长不变",
          abs(len(pcm24) / 2 / 24000 - 0.5) < 0.02, f"{len(pcm24) / 2 / 24000:.3f}s")
    bogus = bytearray(va.pcm_to_wav(tone(300, rate=24000), 24000))
    struct.pack_into("<I", bogus, 4, 0x7FFFFFBF)
    struct.pack_into("<I", bogus, 40, 0x7FFFFFBF)       # 流式占位头
    check("音频：头里长度是占位值的 WAV 也能读",
          len(va.wav_to_pcm(bytes(bogus), 24000)) == 24000 * 2 * 3 // 10, "")
    check("音频：分块按偶数字节对齐",
          all(len(c) % 2 == 0 for c in va.chunk_pcm(tone(750, rate=24000), 200, 24000)), "")


def voice_suite(client, check) -> None:
    from server.voice import session as vsession

    pipe = client.app.state.pipeline
    saved = (pipe.enabled, pipe.transcribe, pipe.synth)

    said: list[str] = []
    spoken_pcm = tone(1500, rate=RATE_OUT, hz=300)         # 每句假音频 1.5 秒

    def fake_synth(text: str) -> bytes:
        time.sleep(0.25)
        said.append(text)
        return spoken_pcm

    heard = {"text": "你好呀今天天气怎么样"}
    client.post("/api/settings", json={"llm": {"type": "demo"}})    # 离线演示大脑：回答稳定、不联网
    pipe.enabled = lambda: (True, "")
    pipe.transcribe = lambda pcm: heard["text"]
    pipe.synth = fake_synth

    try:
        r = client.get("/api/voice/config").json()
        check("语音配置带 pipeline 段（采样率 + 回声闸门参数）",
              {"enabled", "reason", "input_sample_rate", "output_sample_rate",
               "echo_gate", "echo_ratio"} <= set(r.get("pipeline", {})), str(r.get("pipeline")))

        box: list = []
        stop = threading.Event()

        with client.websocket_connect("/ws/voice") as ws:
            def reader():
                while not stop.is_set():
                    try:
                        m = ws.receive()
                    except Exception:
                        return
                    if m.get("type") == "websocket.disconnect":
                        return
                    if m.get("bytes") is not None:
                        box.append(("audio", len(m["bytes"])))
                    elif m.get("text"):
                        import json
                        box.append(("json", json.loads(m["text"])))

            t = threading.Thread(target=reader, daemon=True)
            t.start()

            def send(pcm: bytes):
                for i in range(0, len(pcm), 640):
                    ws.send_bytes(pcm[i:i + 640])

            def wait_for(pred, timeout=8.0):
                end = time.time() + timeout
                while time.time() < end:
                    if pred():
                        return True
                    time.sleep(0.05)
                return False

            def jtypes():
                return [m["type"] for k, m in box if k == "json"]

            check("连上后先收到 ready（pipeline 模式 + 采样率）",
                  wait_for(lambda: "ready" in jtypes())
                  and next(m for k, m in box if k == "json" and m["type"] == "ready")["mode"]
                  == "pipeline", str(jtypes()))

            # ---- 一句话 → 识别 → 回答 → 音频
            send(quiet(500) + tone(900) + quiet(900))
            check("说完一句话 → 收到识别文字",
                  wait_for(lambda: any(k == "json" and m["type"] == "transcript"
                                       and m["text"] == heard["text"] for k, m in box)),
                  str(jtypes()))
            check("回答被念出来：字幕先于音频、音频随后、最后收口",
                  wait_for(lambda: "caption_end" in jtypes() and any(k == "audio" for k, _ in box),
                           timeout=15)
                  and jtypes().index("caption_clear") < jtypes().index("caption")
                  < jtypes().index("caption_end"),
                  str(jtypes())[:200])
            check("音频是按 24k PCM16 切块发的（每块 ≤ 200ms）",
                  all(n <= 24000 * 2 * 0.2 + 2 and n % 2 == 0 for k, n in box if k == "audio"),
                  str([n for k, n in box if k == "audio"][:5]))
            check("对话进了记忆（语音输入走的是统一入口）",
                  any(h["content"] == heard["text"] for h in client.app.state.avatar.session.messages()
                      if h["role"] == "user"), "")

            # ---- 回声：识别结果就是她刚说过的话 → 丢掉，不能自己回答自己
            n_before = jtypes().count("transcript")
            sp = client.app.state.pipeline.current.speaker
            echo_text = (sp.recent[-1][1] if sp.recent else "").strip()
            heard["text"] = echo_text or "余丫丫你又来啦"
            send(quiet(300) + tone(900) + quiet(900))
            time.sleep(1.5)
            check("识别结果是她刚说过的话 → 当回声丢弃",
                  bool(echo_text) and jtypes().count("transcript") == n_before,
                  f"echo={echo_text!r}")

            # ---- 插话：她在说长话时开口
            del box[:]
            heard["text"] = "等一下我想问别的"
            said.clear()
            client.post("/api/say", json={"text": "第一句话在这里。第二句话在这里。第三句话在这里。"
                                                  "第四句话在这里。", "interrupt": True})
            check("她开始说（收到字幕和音频）",
                  wait_for(lambda: "caption" in jtypes() and any(k == "audio" for k, _ in box)),
                  str(jtypes()))
            before_caps = jtypes().count("caption")
            send(quiet(100) + tone(900))
            check("她在说话时开口 → 立刻收到 speech_started（让前端停音频）",
                  wait_for(lambda: "speech_started" in jtypes(), timeout=3), str(jtypes()))
            time.sleep(1.2)
            caps_after = jtypes().count("caption")
            check("插话后，没合成完的后几句不再发出（不会冒旧台词）",
                  caps_after - before_caps <= 1 and len(said) < 4,
                  f"caption {before_caps}->{caps_after}, synth {len(said)}/4")
            send(quiet(900))            # 把这句话收尾，让 VAD 回到空闲

            # ---- 取消指令
            ws.send_text('{"type":"cancel"}')
            check("cancel 指令 → 收到 flush", wait_for(lambda: "flush" in jtypes(), timeout=3),
                  str(jtypes()))
            stop.set()

        # ---- 补问合并（不依赖时序，直接测规则）
        s = vsession._Session(pipe, None)
        s.last_text, s.last_at, s.answered = "我想问一下", time.monotonic(), False
        check("补问合并：她还没出声就接着说 → 并成一句",
              s._merge("明天天气怎么样") == "我想问一下，明天天气怎么样", s._merge("x"))
        s.answered = True
        check("补问合并：她已经开口回答了 → 不合并", s._merge("明天天气怎么样") == "明天天气怎么样", "")
        s.answered, s.last_at = False, time.monotonic() - 10
        check("补问合并：隔太久 → 不合并", s._merge("明天天气怎么样") == "明天天气怎么样", "")
        s.last_text, s.last_at = "我想问。", time.monotonic()
        check("补问合并：上一段已有标点就不再加逗号",
              s._merge("明天呢") == "我想问。明天呢", "")
    finally:
        pipe.enabled, pipe.transcribe, pipe.synth = saved
