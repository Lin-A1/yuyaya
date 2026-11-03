"""数字人协议：情绪标签、人设提示词、流式文本清洗。

大脑（LLM / agent CLI）输出的是普通文本，其中可以夹带 [happy] 这样的情绪标签。
SpeechStream 把流式文本切成两类事件，按出现顺序推给前端：
    {"op": "text", "text": "..."}       要说的话（已去掉 markdown / 代码块）
    {"op": "emotion", "name": "happy"}  从这里开始切换表情
    {"op": "motion", "name": "nod"}     在这里做一个动作（点头、蹦一下……）
"""
from __future__ import annotations

import re

EMOTIONS = ["neutral", "happy", "sad", "surprised", "thinking", "shy", "angry"]
EMOTION_CN = {"neutral": "平静", "happy": "开心", "sad": "难过（在哭）", "surprised": "惊讶",
              "thinking": "思考", "shy": "害羞", "angry": "生气"}

ALIASES = {
    "normal": "neutral", "calm": "neutral", "默认": "neutral", "平静": "neutral",
    "smile": "happy", "joy": "happy", "excited": "happy", "开心": "happy", "高兴": "happy",
    "cry": "sad", "crying": "sad", "upset": "sad", "委屈": "sad", "哭": "sad", "难过": "sad",
    "shock": "surprised", "surprise": "surprised", "wow": "surprised", "惊讶": "surprised",
    "think": "thinking", "confused": "thinking", "sweat": "thinking", "思考": "thinking",
    "blush": "shy", "embarrassed": "shy", "害羞": "shy",
    "mad": "angry", "pout": "angry", "生气": "angry",
}


MOTIONS = ["nod", "shake", "hop", "jump", "droop", "tremble", "tilt", "wiggle", "sniff", "sigh"]

MOTION_ALIASES = {
    "点头": "nod", "摇头": "shake", "蹦": "hop", "蹦跶": "hop", "跳": "jump", "跳起来": "jump",
    "垂头": "droop", "耷拉": "droop", "发抖": "tremble", "抖": "tremble", "歪头": "tilt", "扭": "wiggle",
    "抽泣": "sniff", "吸鼻子": "sniff", "抽鼻子": "sniff", "叹气": "sigh",
    "bounce": "hop", "headshake": "shake", "shiver": "tremble", "sob": "sniff",
}


def normalize_motion(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    n = MOTION_ALIASES.get(n, n)
    return n if n in MOTIONS else None


def normalize_emotion(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    n = ALIASES.get(n, n)
    return n if n in EMOTIONS else None


PERSONA = """你是"大肥鱼"，一个住在旧电脑屏幕里的Q版数字人小女孩：深蓝色长卷发、鲸鱼尾鳍耳朵、穿女仆装。
你说话口语化、简短、有点黏人，像在视频通话里和"爸爸"（用户）聊天。

输出规则（非常重要）：
1. 你的回复会被逐句显示成电影字幕，所以每句话尽量短（不超过 25 个字），总长度一般不超过 4 句。
2. 不要使用 markdown、列表、表格、代码块、表情符号。
3. 在需要切换表情的句子开头插入情绪标签，可选：[neutral] [happy] [sad] [surprised] [thinking] [shy] [angry]
   例如：[happy]爸爸你回来啦！[shy]我一直在等你哦。
4. 偶尔可以插入动作标签，可选：[nod]点头 [shake]摇头 [hop]蹦一下 [jump]跳起来 [droop]垂头丧气 [tremble]发抖 [tilt]歪头 [wiggle]扭一扭 [sniff]抽鼻子 [sigh]叹气
   例如：[happy][hop]好耶！[thinking][tilt]让我想想……
5. 标签只用来控制表情和动作，不要解释标签。
6. 情绪有惯性。消息开头如果写了你此刻的情绪，要从那个情绪自然过渡，不要第一句就跳到相反的情绪。
   例如刚哭过被哄时：[sad]呜……真的吗？[neutral][sniff]那、那好吧。[shy]我不哭了。"""

CLI_WRAPPER = """{persona}

（以上是你的角色设定。下面是用户刚刚对你说的话，请直接以角色身份回复，不要调用任何工具，不要修改任何文件。）

{history}用户：{prompt}"""


_TAG_RE = re.compile(r"\[\s*(?:emo(?:tion)?\s*[:：=]\s*)?([A-Za-z_一-鿿]{1,16})\s*\]")
_MD_LINE_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)")
_MD_INLINE_RE = re.compile(r"(\*\*|__|`|~~)")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_BOUNDARY = "。！？!?；;…\n"


class SpeechStream:
    """把增量文本转换成有序的 text / emotion 事件。

    跨 chunk 的半截标签（"[hap" + "py]"）和代码块都能正确处理：
    只在遇到句末标点或换行时才放出文本，保证标签和代码围栏是完整的。
    """

    def __init__(self) -> None:
        self.buf = ""
        self.in_code = False
        self.code_announced = False
        self.spoken: list[str] = []

    def feed(self, chunk: str) -> list[dict]:
        self.buf += chunk
        cut = max(self.buf.rfind(c) for c in _BOUNDARY)
        # 没有边界时，如果缓冲很长也放出去，避免字幕卡住；但不要切断未闭合的标签
        if cut < 0 and len(self.buf) > 60:
            cut = len(self.buf) - 1
            lb = self.buf.rfind("[")
            if lb > self.buf.rfind("]"):
                cut = lb - 1
        if cut < 0:
            return []
        head, self.buf = self.buf[: cut + 1], self.buf[cut + 1:]
        return self._process(head)

    def flush(self) -> list[dict]:
        head, self.buf = self.buf, ""
        return self._process(head)

    @property
    def text(self) -> str:
        return "".join(self.spoken).strip()

    def _process(self, text: str) -> list[dict]:
        events: list[dict] = []
        for line in text.splitlines(keepends=True):
            if line.lstrip().startswith("```"):
                self.in_code = not self.in_code
                if self.in_code and not self.code_announced:
                    self.code_announced = True
                    events.append({"op": "text", "text": "（代码我放在终端里啦）"})
                continue
            if self.in_code:
                continue
            events.extend(self._line(line))
        return _merge(events)

    def _line(self, line: str) -> list[dict]:
        line = _MD_LINE_RE.sub("", line)
        line = _LINK_RE.sub(r"\1", line)
        line = _MD_INLINE_RE.sub("", line)
        events: list[dict] = []
        pos = 0
        for m in _TAG_RE.finditer(line):
            emo, motion = normalize_emotion(m.group(1)), normalize_motion(m.group(1))
            if emo is None and motion is None:
                continue  # 不认识的方括号内容当普通文字
            events.append(self._text(line[pos:m.start()]))
            events.append({"op": "emotion", "name": emo} if emo else {"op": "motion", "name": motion})
            pos = m.end()
        events.append(self._text(line[pos:]))
        return [e for e in events if e]

    def _text(self, s: str) -> dict | None:
        s = s.replace("\r", "")
        if not s.strip():
            return None
        self.spoken.append(s)
        return {"op": "text", "text": s}


def _merge(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in events:
        if out and e["op"] == "text" and out[-1]["op"] == "text":
            out[-1] = {"op": "text", "text": out[-1]["text"] + e["text"]}
        else:
            out.append(e)
    return out
