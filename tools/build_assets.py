"""把 gen/ 里的绿幕原图加工成前端素材 web/assets/。

    python tools/build_assets.py

1. 场景 scene：屏幕绿幕区域检测出矩形 → 写入 manifest，绿色填成暗色防止边缘漏绿
2. 房间 room：缩放压缩
3. 立绘 sprites：
   - 绿幕抠像 + 去溢色
   - 表情差分只取"和母版不同的脸部区域"贴回母版，保证头发、身体在所有表情间像素级一致，
     切换表情/口型时整体不会抖动
   - 口型 / 眨眼只取嘴 / 眼区域的变化做成小贴片，前端叠加在任意表情上
     （表情 × 4 级口型 × 眨眼自由组合，不需要每种组合一张整图）
   - 所有立绘统一裁切框，输出 webp(带透明) + manifest.json（含贴片位置和骨骼点）
"""
import json, pathlib, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parent.parent
GEN, OUT = ROOT / "gen", ROOT / "web/assets"

EMOTIONS = ["neutral", "happy", "sad", "surprised", "thinking", "shy", "angry"]
# 表情 -> 口型 1/2/3 级（微张/半张/大张）所用的原图；0 级就是表情本身
MOUTHS = {
    "neutral":   ["neutral_m1", "talk", "neutral_m3"],
    "happy":     ["happy_m1", "happy_m2", "happy_talk"],
    "sad":       ["sad_m1", "sad_talk", "sad_m3"],
    "surprised": ["surprised_m2", "surprised_m2", "surprised_m3"],
    "thinking":  ["thinking_m1", "thinking_m2", "thinking_m2"],
    "shy":       ["shy_m1", "shy_m2", "shy_m2"],
    "angry":     ["angry_m1", "angry_m2", "angry_m3"],
}
# 表情 -> 闭眼帧；happy 本来就眯眼、surprised 睁大眼不眨
BLINKS = {"neutral": "blink", "thinking": "thinking_blink", "sad": "sad_blink",
          "shy": "shy_blink", "angry": "angry_blink"}
FACE_BOX = (280, 480, 780, 980)  # 母版 1024x1536 坐标里允许替换的区域（脸 + 汗珠/怒气符号）
MOUTH_BOX = (420, 800, 640, 990)
EYE_BOX = (290, 660, 780, 845)
# 贴片只保留和这些点相连的变化区域（去掉改图时顺带画歪的泪珠、汗珠、发丝）
MOUTH_SEEDS = [(505, 850)]
EYE_SEEDS = [(397, 772), (630, 772)]
# 骨骼点（母版坐标）：脖子转轴（≈下巴）、脸中心、头顶（不含呆毛和头饰）、躯干半宽、呆毛/耳朵的 [根部, 尖端]
RIG = {"neck": (512, 945), "face": (512, 780), "headTop": (512, 430), "torsoHalf": 225,
       "ahoge": [(530, 375), (515, 280)],
       "earL": [(225, 755), (85, 775)], "earR": [(800, 740), (950, 770)]}
SPRITE_HEIGHT = 1200             # 输出立绘高度


def green_mask(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (g > 150) & (g - np.maximum(r, b) > 90)


def chroma_key(img: Image.Image) -> Image.Image:
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    key = g - np.maximum(r, b)                      # 越绿越大
    alpha = np.clip(1.0 - (key - 30.0) / 90.0, 0, 1)
    # 去溢色：边缘半透明像素的绿色压到不超过红蓝最大值
    spill = g > np.maximum(r, b)
    a[..., 1] = np.where(spill, np.maximum(r, b), g)
    out = np.dstack([a, alpha * 255]).astype(np.uint8)
    return Image.fromarray(out)


def face_patch(base: Image.Image, var: Image.Image) -> Image.Image:
    """只把 var 中与 base 明显不同、且位于 FACE_BOX 内的区域贴到 base 上。"""
    b = np.asarray(base.convert("RGB")).astype(np.int16)
    v = np.asarray(var.convert("RGB")).astype(np.int16)
    diff = (np.abs(b - v).max(axis=2) > 40).astype(np.uint8) * 255
    box = np.zeros_like(diff)
    x0, y0, x1, y1 = FACE_BOX
    box[y0:y1, x0:x1] = 255
    m = Image.fromarray(np.minimum(diff, box))
    m = m.filter(ImageFilter.MedianFilter(5))       # 去掉零星噪点
    m = m.filter(ImageFilter.MaxFilter(41))         # 连成整块
    m = m.filter(ImageFilter.GaussianBlur(10))      # 羽化
    return Image.composite(var.convert("RGB"), base.convert("RGB"), m)


def build_scene(manifest: dict) -> None:
    img = Image.open(GEN / "scene.png").convert("RGB")
    a = np.asarray(img).copy()
    m = green_mask(a)
    # 只认"整行/整列大部分是绿色"的区域，排除背景里零星的绿色指示灯
    cols = np.where(m.sum(axis=0) > m.shape[0] * 0.2)[0]
    rows = np.where(m.sum(axis=1) > m.shape[1] * 0.2)[0]
    x0, x1, y0, y1 = int(cols.min()), int(cols.max()), int(rows.min()), int(rows.max())
    # 把绿幕及其边缘溢色填成屏幕暗色（只在屏幕矩形附近处理，不碰场景其他地方）
    grow = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(7))) > 0
    near = np.zeros_like(grow)
    near[max(y0 - 4, 0):y1 + 5, max(x0 - 4, 0):x1 + 5] = True
    a[grow & near] = (12, 16, 15)
    W, H = img.size
    Image.fromarray(a).save(OUT / "scene.jpg", quality=90)
    manifest["scene"] = {"src": "assets/scene.jpg", "width": W, "height": H,
                         "screen": {"x": x0 / W, "y": y0 / H, "w": (x1 - x0 + 1) / W, "h": (y1 - y0 + 1) / H}}
    print(f"scene  screen rect px=({x0},{y0})-({x1},{y1})")


def build_room(manifest: dict) -> None:
    img = Image.open(GEN / "room.png").convert("RGB")
    W, H = img.size
    img.resize((1280, round(1280 * H / W)), Image.LANCZOS).save(OUT / "room.jpg", quality=88)
    # 桌面上沿位置（前端用来把立绘下沿藏在桌子后面）
    manifest["room"] = {"src": "assets/room.jpg", "deskTop": 0.83}
    print("room   ok")


def region_patch(target: Image.Image, var: Image.Image, box, seeds) -> Image.Image:
    """var 相对 target 在 box 内、且与 seeds 相连的变化区域 → 带羽化 alpha 的整幅 RGBA。"""
    t = np.asarray(target.convert("RGB")).astype(np.int16)
    v = np.asarray(var.convert("RGB")).astype(np.int16)
    diff = (np.abs(t - v).max(axis=2) > 60).astype(np.uint8) * 255
    x0, y0, x1, y1 = box
    m = np.zeros_like(diff)
    m[y0:y1, x0:x1] = diff[y0:y1, x0:x1]
    m = Image.fromarray(m).filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.MaxFilter(15))
    ys, xs = np.nonzero(np.asarray(m))
    if len(xs):
        for sx, sy in seeds:  # 从离种子最近的变化像素开始泛洪，标成 128
            i = int(np.argmin((xs - sx) ** 2 + (ys - sy) ** 2))
            if np.asarray(m)[ys[i], xs[i]] == 255:
                ImageDraw.floodfill(m, (int(xs[i]), int(ys[i])), 128)
        m = m.point(lambda a: 255 if a == 128 else 0)
    m = m.filter(ImageFilter.GaussianBlur(4))
    a = np.asarray(chroma_key(var)).copy()
    a[..., 3] = (a[..., 3].astype(np.float32) * (np.asarray(m, np.float32) / 255)).astype(np.uint8)
    return Image.fromarray(a)


def build_sprites(manifest: dict) -> None:
    base_raw = Image.open(GEN / "char_base.png").convert("RGB")
    raws = {"neutral": base_raw}
    fulls = {}
    for name in EMOTIONS:
        src = GEN / ("char_base.png" if name == "neutral" else f"char_{name}.png")
        if not src.exists():
            print(f"skip   {name}（缺少 {src.name}）")
            continue
        if name != "neutral":
            raws[name] = Image.open(src).convert("RGB")
        img = base_raw if name == "neutral" else face_patch(base_raw, raws[name])
        fulls[name] = chroma_key(img)

    # 统一裁切框：所有表情 alpha 的并集
    union = np.zeros(base_raw.size[::-1], bool)
    for f in fulls.values():
        union |= np.asarray(f)[..., 3] > 8
    ys, xs = np.where(union)
    pad = 8
    crop = (max(int(xs.min()) - pad, 0), max(int(ys.min()) - pad, 0),
            min(int(xs.max()) + pad + 1, base_raw.size[0]), int(ys.max()) + 1)
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    size = (round(cw * SPRITE_HEIGHT / ch), SPRITE_HEIGHT)
    k = SPRITE_HEIGHT / ch

    def to_sprite(img: Image.Image) -> Image.Image:
        return img.crop(crop).resize(size, Image.LANCZOS)

    def save_patch(img: Image.Image, name: str) -> "dict | None":
        """裁到 alpha 包围盒，保存为小图，返回它在立绘中的位置（像素）。"""
        s = to_sprite(img)
        bb = s.getchannel("A").point(lambda v: 255 if v > 4 else 0).getbbox()
        if not bb:
            print(f"warn   {name} 没有检测到变化，跳过")
            return None
        s.crop(bb).save(OUT / f"sprites/{name}.webp", quality=92, method=6)
        return {"src": f"assets/sprites/{name}.webp", "x": bb[0], "y": bb[1], "w": bb[2] - bb[0], "h": bb[3] - bb[1]}

    for old in (OUT / "sprites").glob("*.webp"):
        old.unlink()
    (OUT / "sprites").mkdir(parents=True, exist_ok=True)
    emotions = {}
    for name, f in fulls.items():
        to_sprite(f).save(OUT / f"sprites/{name}.webp", quality=92, method=6)
        mouths, done = [], {}
        for frame in MOUTHS.get(name, []):
            p = GEN / f"char_{frame}.png"
            if frame not in done and p.exists():
                done[frame] = save_patch(region_patch(raws[name], Image.open(p), MOUTH_BOX, MOUTH_SEEDS),
                                         f"{name}__mouth{len(mouths) + 1}")
            mouths.append(done.get(frame) or (mouths[-1] if mouths else None))
        blink = None
        if BLINKS.get(name) and (GEN / f"char_{BLINKS[name]}.png").exists():
            blink = save_patch(region_patch(raws[name], Image.open(GEN / f"char_{BLINKS[name]}.png"), EYE_BOX,
                                            EYE_SEEDS), f"{name}__blink")
        emotions[name] = {"src": f"assets/sprites/{name}.webp", "mouth": [m for m in mouths if m], "blink": blink}
        print(f"sprite {name:9s} mouth×{len(emotions[name]['mouth'])} blink={'Y' if blink else '-'}")

    def pt(x, y):  # 母版坐标 → 立绘像素
        return [round((x - crop[0]) * k, 1), round((y - crop[1]) * k, 1)]

    manifest["sprites"] = {
        "width": size[0], "height": size[1],
        "emotions": emotions,
        # 网格形变用的骨骼点（立绘像素坐标），见 web/js/rig.js
        "rig": {
            "neck": pt(*RIG["neck"]), "face": pt(*RIG["face"]), "headTop": pt(*RIG["headTop"]),
            "torsoHalf": round(RIG["torsoHalf"] * k, 1),
            "ahoge": [pt(*RIG["ahoge"][0]), pt(*RIG["ahoge"][1])],
            "earL": [pt(*RIG["earL"][0]), pt(*RIG["earL"][1])],
            "earR": [pt(*RIG["earR"][0]), pt(*RIG["earR"][1])],
        },
    }
    print(f"sprites crop={crop} -> {size}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if not Image.registered_extensions().get(".webp"):
        sys.exit("当前 Pillow 不支持 webp，请升级 Pillow")
    manifest = {}
    build_scene(manifest)
    build_room(manifest)
    build_sprites(manifest)
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote web/assets/manifest.json")


if __name__ == "__main__":
    main()
