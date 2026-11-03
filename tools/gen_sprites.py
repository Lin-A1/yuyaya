"""从母版立绘批量生成表情 / 口型 / 眨眼差分（APINebula 改图接口）。

用法:
    set APINEBULA_API_KEY=sk-...
    python tools/gen_sprites.py                 # 生成全部缺失的帧
    python tools/gen_sprites.py happy sad_m1    # 只（重新）生成指定帧

输出到 gen/char_<name>.png（绿幕），再用 tools/build_assets.py 抠像入库。

三类帧:
    表情帧   以母版为底图改整张脸
    口型帧   <表情>_m1/_m2/_m3：以该表情帧为底图只改嘴（m0 就是表情帧本身，闭嘴）
    眨眼帧   <表情>_blink：以该表情帧为底图只改眼睛
build_assets.py 只会取各自负责的区域（脸 / 嘴 / 眼）贴回去，所以底图以外的细微变化不会带进成品。
"""
import os, subprocess, sys, pathlib
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = pathlib.Path.home() / ".claude/skills/apinebula-imagegen/scripts/apinebula_image.py"

KEEP_FACE = ("严格保持这张图的角色、画风、姿势、构图、角色在画面中的位置和大小、头发、耳朵、头饰、服装、双手、"
             "纯绿色 #00FF00 背景全部完全不变，像素级对齐，只修改面部表情（眼睛、眉毛、嘴巴、脸颊）：")
KEEP_MOUTH = ("严格保持这张图的一切完全不变（角色、画风、构图、位置大小、眼睛、眉毛、脸颊红晕、泪痕、汗珠、符号、"
              "头发、服装、纯绿色背景），像素级对齐，只修改嘴巴的张开程度，嘴巴位置和大小比例保持Q版风格：")
KEEP_EYES = ("严格保持这张图的一切完全不变（角色、画风、构图、位置大小、嘴巴、眉毛、脸颊红晕、泪痕、汗珠、符号、"
             "头发、服装、纯绿色背景），像素级对齐，只修改眼睛：")

# name: (底图, 提示词前缀, 描述)
FRAMES = {
    # ---- 表情 ----
    "talk":       ("base", KEEP_FACE, "正在说话，嘴巴自然张开成小小的椭圆形，能看到一点口腔，眼睛和眉毛与原图相同。"),
    "blink":      ("base", KEEP_FACE, "轻轻闭上双眼（眨眼瞬间），闭眼是两条柔和向下弯的睫毛线，嘴巴与原图相同。"),
    "happy":      ("base", KEEP_FACE, "开心的笑，眼睛弯成 ^ ^ 形状，嘴巴闭合微笑上扬，脸颊红晕更明显。"),
    "happy_talk": ("base", KEEP_FACE, "开心地笑着说话，眼睛弯成 ^ ^ 形状，嘴巴张开大笑，脸颊红晕更明显。"),
    "sad":        ("base", KEEP_FACE, "委屈地哭，眉毛皱成八字，眼睛泪汪汪、眼角挂着泪珠并流下两道泪痕，嘴巴撇着紧闭发抖。"),
    "sad_talk":   ("base", KEEP_FACE, "委屈地边哭边说话，眉毛皱成八字，眼睛泪汪汪并流下两道泪痕，嘴巴张开呜咽。"),
    "surprised":  ("base", KEEP_FACE, "惊讶，眼睛睁得很圆很大、瞳孔缩小，眉毛高高扬起，嘴巴张成小小的 O 形。"),
    "thinking":   ("base", KEEP_FACE, "正在思考：两只眼睛的黑眼珠（虹膜和瞳孔）明显移到眼眶的左上角，眼白露在右下方，"
                                      "眉毛一高一低微微皱起，嘴巴抿成一条小小的横线，额头侧边一滴蓝色小汗珠。"),
    "shy":        ("base", KEEP_FACE, "害羞，脸颊大面积通红，眼睛向一侧看、半垂着，嘴巴是小小的波浪形，头顶冒一点点害羞的线条。"),
    "angry":      ("base", KEEP_FACE, "生气鼓脸，眉毛向中间压低，眼睛半眯瞪着，脸颊鼓起来泛红，嘴巴嘟起成一个小三角，额头一个生气的十字符号。"),

    # ---- 口型：m1 微张 / m2 半张 / m3 大张 ----
    "neutral_m1":   ("base", KEEP_MOUTH, "嘴巴微微张开一条小缝，像说话时轻轻发音，能看到一点点口腔。"),
    "neutral_m3":   ("base", KEEP_MOUTH, "嘴巴张得很大，像大声说\"啊\"，嘴巴是圆润的大椭圆，能看到口腔和舌头。"),
    "happy_m1":     ("happy", KEEP_MOUTH, "开心地微笑着、嘴巴微微张开一条小缝，嘴角上扬。"),
    "happy_m2":     ("happy", KEEP_MOUTH, "开心地笑着说话，嘴巴张开成上扬的半月形，能看到口腔，嘴角上扬。"),
    "sad_m1":       ("sad", KEEP_MOUTH, "委屈地撇着嘴、嘴巴微微张开一条发抖的小缝。"),
    "sad_m3":       ("sad", KEEP_MOUTH, "哇地大哭，嘴巴张得很大成向下撇的大椭圆形，能看到口腔。"),
    "surprised_m2": ("surprised", KEEP_MOUTH, "嘴巴张成更大的圆 O 形，惊讶地说话。"),
    "surprised_m3": ("surprised", KEEP_MOUTH, "嘴巴张得非常大成竖着的大椭圆，像惊叫\"啊！\"，能看到口腔。"),
    "thinking_m1":  ("thinking", KEEP_MOUTH, "嘴巴微微张开一条小缝，像在小声嘀咕。"),
    "thinking_m2":  ("thinking", KEEP_MOUTH, "嘴巴张开成小小的椭圆，像在说\"嗯……\"。"),
    "shy_m1":       ("shy", KEEP_MOUTH, "害羞地小声说话，波浪形的嘴微微张开一条小缝。"),
    "shy_m2":       ("shy", KEEP_MOUTH, "害羞地说话，嘴巴张开成小小的椭圆，嘴角有点紧张。"),
    "angry_m1":     ("angry", KEEP_MOUTH, "嘟着的嘴微微张开，像在小声嘟囔抱怨。"),
    "angry_m2":     ("angry", KEEP_MOUTH, "生气地说话，嘴巴张开成方方的小口，能看到一点牙齿。"),
    "angry_m3":     ("angry", KEEP_MOUTH, "生气地大声喊，嘴巴张得很大，嘴角向下，能看到牙齿和口腔。"),

    # ---- 眨眼 ----
    "sad_blink":      ("sad", KEEP_EYES, "眨眼瞬间：两只眼睛轻轻闭上，闭眼是两条向下弯的睫毛线，眼角仍然挂着泪珠。"),
    "shy_blink":      ("shy", KEEP_EYES, "眨眼瞬间：两只眼睛轻轻闭上，闭眼是两条柔和向下弯的睫毛线。"),
    "angry_blink":    ("angry", KEEP_EYES, "眨眼瞬间：两只眼睛用力闭上，闭眼是两条略微向下压的睫毛线。"),
    "thinking_blink": ("thinking", KEEP_EYES, "眨眼瞬间：两只眼睛轻轻闭上，闭眼是两条柔和向下弯的睫毛线。"),
}


def source(name: str) -> pathlib.Path:
    base = FRAMES[name][0]
    return ROOT / ("gen/char_base.png" if base == "base" else f"gen/char_{base}.png")


def gen(name: str) -> str:
    out = ROOT / f"gen/char_{name}.png"
    _, keep, desc = FRAMES[name]
    cmd = [sys.executable, str(SCRIPT), "edit", "--image", str(source(name)),
           "--size", "1024x1536", "--quality", "high", "--out", str(out),
           "--prompt", keep + desc + "不要任何文字。"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    ok = out.exists() and r.returncode == 0
    return f"{'OK  ' if ok else 'FAIL'} {name} {'' if ok else (r.stdout + r.stderr)[-300:]}"


def main():
    if not os.environ.get("APINEBULA_API_KEY"):
        sys.exit("请先设置环境变量 APINEBULA_API_KEY")
    names = sys.argv[1:] or [n for n in FRAMES if not (ROOT / f"gen/char_{n}.png").exists()]
    unknown = [n for n in names if n not in FRAMES]
    if unknown:
        sys.exit(f"未知帧: {unknown}")
    missing = [n for n in names if not source(n).exists()]
    if missing:
        sys.exit(f"这些帧的底图还没生成，请先生成底图: {[(n, source(n).name) for n in missing]}")
    with ThreadPoolExecutor(max_workers=6) as pool:
        for line in pool.map(gen, names):
            print(line, flush=True)


if __name__ == "__main__":
    main()
