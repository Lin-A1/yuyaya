"""人设：默认设定 + 用户覆盖。

人设分两段：
- **identity**（她是谁、怎么说话、什么脾气）—— 可以在设置页里改；
- **rules**（字幕长度、标签用法、记忆标记这些协议约束）—— 跟着代码走，改坏了整个链路就不对劲，
  所以只在「高级」里以只读方式展示。

``config.json`` 的 ``persona`` 段：

```jsonc
"persona": {
  "name": "鱼丫丫",
  "identity": "",          // 空 = 用内置默认
  "address": "你",          // 她怎么称呼用户
  "extra": ""              // 附加要求（说话风格、口癖…）
}
```

最终拼给模型的提示词 = identity + rules + address/extra。
"""
from __future__ import annotations

from .protocol import PERSONA

# 内置的默认人设（原来的 PERSONA 整段）
DEFAULT_IDENTITY = PERSONA

DEFAULTS = {
    "name": "鱼丫丫",
    "identity": "",       # 空表示用 DEFAULT_IDENTITY
    "address": "你",
    "extra": "",
}

CHAT_ONLY_BOUNDARY = """【当前能力边界（优先于以上所有设定）】
你只能聊天和陪伴，不能读写文件、操作电脑、执行命令或调用外部智能体。
用户让你做这些事时，直接说明现在只能陪聊；不要说“我去弄”，也不要假装已经开始或完成。"""


def effective(persona: dict | None) -> str:
    """把 persona 配置拼成实际发给模型的提示词。

    什么都没改（identity 和 extra 都是空）时原样返回内置 PERSONA，
    保证和以前完全一致，不会因为多一层拼接而改变模型行为。
    """
    p = {**DEFAULTS, **(persona or {})}
    identity = (p.get("identity") or "").strip()
    extra = (p.get("extra") or "").strip()
    address = (p.get("address") or "").strip()
    if not identity and not extra and (not address or address == DEFAULTS["address"]):
        return DEFAULT_IDENTITY

    base = identity or DEFAULT_IDENTITY
    parts = [base, CHAT_ONLY_BOUNDARY]
    if address and address != DEFAULTS["address"]:
        parts.append(f"称呼用户为「{address}」。")
    if extra:
        parts.append(f"\n【额外要求】\n{extra}")
    return "\n".join(parts)


def snapshot(persona: dict | None) -> dict:
    """设置页展示用。"""
    p = {**DEFAULTS, **(persona or {})}
    return {
        "name": p.get("name") or DEFAULTS["name"],
        "identity": p.get("identity") or "",
        "address": p.get("address") or DEFAULTS["address"],
        "extra": p.get("extra") or "",
        "default_identity": DEFAULT_IDENTITY,
        "customized": bool((p.get("identity") or "").strip() or (p.get("extra") or "").strip()),
    }


def apply_name(path: str, name: str) -> str:
    """把目录/日志里出现的默认名字换成用户起的名字。"""
    if not name or name == DEFAULTS["name"]:
        return path
    return path.replace(DEFAULTS["name"], name)
