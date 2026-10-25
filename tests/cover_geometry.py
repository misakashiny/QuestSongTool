"""封面装饰的几何检查：画布不能比封面大出一大截（"大黑边"的量化）。

## 这条测试挡的是什么

用户反馈过详情栏"预览图后面有个特别大的黑边"。原因有两层：

1. **颜色写死**：`decorate_cover` 早期把补边底色写死成深色 `(24, 26, 31)`，
   浅色主题下那块深色就特别显眼。（已修：底色由调用方按主题传入）
2. **画布比图宽**（更根本）：Beat Saber 封面是**正方形**，而 `max_size`
   是 300×186 的横长方形。`thumbnail()` 会按高度贴合缩成 186×186，
   但画布仍按 `max_size[0] + 24 = 324` 宽开 —— 于是左右各空出
   **57 px**，加上 12 px 内边距，封面两侧各挂约 **69 px** 的纯色块。

只修颜色的话，"黑边"会变成"白边"，宽度还是 69 px。所以本测试量的是
**几何**：画布宽度应该约等于封面宽度 + 2×内边距。

运行：
    python tests/cover_geometry.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from quest_tool.app_common import RIGHT_PANEL_WIDTH, decorate_cover  # noqa: E402

PAD = 12  # decorate_cover 内部的内边距
MAX_SIZE = (RIGHT_PANEL_WIDTH - 96, 186)
PASSED = 0
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ✔ {label}")
    else:
        FAILED.append(label)
        print(f"  ✘ {label}")
        if detail:
            print(f"      {detail}")


def make_image(size: tuple[int, int], color=(200, 60, 60)) -> Image.Image:
    return Image.new("RGB", size, color)


def edge_background_run(image: Image.Image, bg: tuple[int, int, int]) -> tuple[int, int]:
    """从中线往左右数：边缘连续是背景色的像素数。"""
    px = image.load()
    w, h = image.size
    mid = h // 2
    left = 0
    for x in range(w):
        if px[x, mid] == bg:
            left += 1
        else:
            break
    right = 0
    for x in range(w - 1, -1, -1):
        if px[x, mid] == bg:
            right += 1
        else:
            break
    return left, right


def check_square_cover() -> None:
    """正方形封面（Beat Saber 的真实情况）不该产生左右空白。"""
    print("\n[正方形封面 600x600]")
    out = decorate_cover(make_image((600, 600)), background="#ffffff")
    bg = out.load()[0, 0]
    left, right = edge_background_run(out, bg)
    waste_l, waste_r = max(0, left - PAD), max(0, right - PAD)
    print(f"    输出 {out.size}  边缘背景 左{left}px 右{right}px（内边距 {PAD}px 属正常）")
    check(
        f"左侧无多余空白（{waste_l}px <= 8px）",
        waste_l <= 8,
        "老代码这里是 57px：画布按 max_size 宽度开，而正方形封面缩到 186 宽",
    )
    check(f"右侧无多余空白（{waste_r}px <= 8px）", waste_r <= 8)

    # 画布宽度应约等于「封面宽 + 2*内边距」
    expected = out.size[1] - PAD * 2 + PAD * 2  # 正方形：宽 = 高
    check(
        f"画布是正方形（{out.size[0]}x{out.size[1]}）",
        abs(out.size[0] - out.size[1]) <= 2,
        f"正方形封面不该被塞进横长方形画布（期望约 {expected}）",
    )


def check_landscape_cover() -> None:
    """横长方形封面应该贴合 max_size，不产生上下空白。"""
    print("\n[横封面 800x400]")
    out = decorate_cover(make_image((800, 400)), background="#ffffff")
    bg = out.load()[0, 0]
    left, right = edge_background_run(out, bg)
    print(f"    输出 {out.size}")
    check(f"左边缘无多余空白（{max(0, left - PAD)}px <= 8px）", max(0, left - PAD) <= 8)
    check(f"右边缘无多余空白（{max(0, right - PAD)}px <= 8px）", max(0, right - PAD) <= 8)
    # 横封面按宽度贴合 max_size[0]
    check(
        f"宽度贴合上限（{out.size[0] - PAD * 2} <= {MAX_SIZE[0]}）",
        out.size[0] - PAD * 2 <= MAX_SIZE[0],
    )


def check_tall_cover() -> None:
    """竖长封面按高度贴合，不该超出 max_size。"""
    print("\n[竖封面 300x900]")
    out = decorate_cover(make_image((300, 900)), background="#ffffff")
    print(f"    输出 {out.size}")
    check(
        f"高度贴合上限（{out.size[1] - PAD * 2} <= {MAX_SIZE[1]}）",
        out.size[1] - PAD * 2 <= MAX_SIZE[1],
        "thumbnail 应该把高缩到 186 以内",
    )


def check_background_follows_theme() -> None:
    """补边底色必须跟着调用方给的色走（浅色主题下不能出现深色块）。"""
    print("\n[补边底色跟随主题]")
    light = decorate_cover(make_image((600, 600)), background="#e8ecf2")
    dark = decorate_cover(make_image((600, 600)), background="#1c1f26")
    check("传入浅色时角落像素是浅色", light.load()[0, 0] == (0xE8, 0xEC, 0xF2),
          f"实际 {light.load()[0, 0]}")
    check("传入深色时角落像素是深色", dark.load()[0, 0] == (0x1C, 0x1F, 0x26),
          f"实际 {dark.load()[0, 0]}")
    # 不传 background 时用旧默认（深色），保证向后兼容不炸
    default = decorate_cover(make_image((600, 600)))
    check("不传 background 时回落到默认深色", default.load()[0, 0] == (24, 26, 31),
          f"实际 {default.load()[0, 0]}")


def check_tiny_and_extreme() -> None:
    """极端尺寸不崩。"""
    print("\n[极端输入]")
    for size in ((1, 1), (2, 300), (300, 2), (2000, 2000)):
        try:
            out = decorate_cover(make_image(size), background="#ffffff")
            ok = out.size[0] > 0 and out.size[1] > 0
        except Exception as exc:  # pragma: no cover
            ok = False
            print(f"      {size} 抛异常: {exc}")
        check(f"{size} 能正常处理（输出 {out.size if ok else '—'}）", ok)


def check_roundtrip_from_bytes() -> None:
    """从字节解码再装饰（详情栏的真实路径）也不留空白。"""
    print("\n[从字节解码的路径]")
    buffer = io.BytesIO()
    make_image((512, 512)).save(buffer, format="PNG")
    data = buffer.getvalue()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        source = im.convert("RGB")
    out = decorate_cover(source, background="#ffffff")
    bg = out.load()[0, 0]
    left, right = edge_background_run(out, bg)
    check(f"字节路径左边缘无空白（{max(0, left - PAD)}px <= 8px）", max(0, left - PAD) <= 8)
    check(f"字节路径右边缘无空白（{max(0, right - PAD)}px <= 8px）", max(0, right - PAD) <= 8)


def main() -> int:
    print("=" * 74)
    print("封面装饰几何检查（防“大黑边”回归）")
    print("=" * 74)
    check_square_cover()
    check_landscape_cover()
    check_tall_cover()
    check_background_follows_theme()
    check_tiny_and_extreme()
    check_roundtrip_from_bytes()

    print("\n" + "=" * 74)
    print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for label in FAILED:
            print(f"  - {label}")
        return 1
    print("全部通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
