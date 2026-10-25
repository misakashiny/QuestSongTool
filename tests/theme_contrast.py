"""配色对比度检查：把"太白 / 看不清"变成可断言的数字。

为什么要有这个脚本
------------------
用户反馈过"配色不太行、太白了"。这是**主观**描述，靠肉眼争论没有结论；
但配色问题本质上是两件可以量化的事：

1. **对比度**（WCAG 相对亮度比）—— 文字/图标与底色差多少。
   算到 4.5:1 以下，任何人都看不清，跟显示器好坏无关。
2. **层次分离**（背景层两两之间的落差）—— 浅色主题下如果
   `BG_APP` / `BG_CARD` / `BG_INPUT` 全都是接近白的灰，
   界面就会糊成一片白，也就是用户说的"太白"。

所以本脚本直接对 `theme.LIGHT` / `theme.DARK` 算这批数字并施加门槛。
改配色后跑一遍，低于门槛就报错 —— 免得下次又悄悄调回一片白。

运行：
    python tests/theme_contrast.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quest_tool.theme import DARK, LIGHT, Palette, FORM_COLORS  # noqa: E402

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


# --------------------------------------------------------------------------------------
# 对比度计算
# --------------------------------------------------------------------------------------
def _linearize(channel: float) -> float:
    channel = channel / 255.0
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    """WCAG 相对亮度。"""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def contrast(a: str, b: str) -> float:
    """WCAG 对比度，1:1 ~ 21:1。"""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


#: (说明, 前景字段, 背景字段, 门槛)
#: 门槛取 WCAG 2.1 AA：正文 4.5、大字号/非文字 UI 3.0。
#: 描边类按"能不能看见边界"给 1.5，这是可用性下限而非标准值。
CONTRAST_RULES: tuple[tuple[str, str, str, float], ...] = (
    ("正文（主文字 / 窗口底）", "TEXT_PRIMARY", "BG_APP", 4.5),
    ("正文（主文字 / 卡片底）", "TEXT_PRIMARY", "BG_CARD", 4.5),
    ("次要文字 / 卡片底", "TEXT_SECONDARY", "BG_CARD", 4.5),
    ("弱文字 / 卡片底", "TEXT_MUTED", "BG_CARD", 4.5),
    ("列表行文字 / 卡片底", "ROW_TEXT", "BG_CARD", 4.5),
    ("主按钮文字 / 主色", "ACCENT_TEXT", "ACCENT", 4.5),
    ("主色 / 窗口底（焦点可见）", "ACCENT", "BG_APP", 3.0),
    ("强描边 / 卡片底", "BORDER_STRONG", "BG_CARD", 3.0),
    ("描边 / 卡片底（边界可见）", "BORDER", "BG_CARD", 1.5),
    ("选中行 / 卡片底", "BG_SELECTED", "BG_CARD", 1.5),
    # 背景层之间的分离度。1.10 是"看得出这是两块"，1.03 是"至少不是同一块"。
    ("卡片底 / 窗口底（分层可见）", "BG_CARD", "BG_APP", 1.10),
    ("输入框底 / 卡片底", "BG_INPUT", "BG_CARD", 1.10),
    ("斑马纹 / 卡片底", "BG_ZEBRA", "BG_CARD", 1.03),
)


def check_palette(palette: Palette) -> None:
    print(f"\n[{palette.name}] 对比度")
    for label, fg_field, bg_field, need in CONTRAST_RULES:
        fg = getattr(palette, fg_field)
        bg = getattr(palette, bg_field)
        value = contrast(fg, bg)
        check(
            f"{label}: {value:.2f}:1（需 {need}）",
            value >= need,
            f"{fg_field}={fg} on {bg_field}={bg}",
        )


# --------------------------------------------------------------------------------------
# "太白"的量化判据
# --------------------------------------------------------------------------------------
def check_light_not_washed_out() -> None:
    """浅色主题不能所有背景都挤在接近白的一小段里。

    这条是用户那句"太白了"的直接量化。老色板的 7 个背景层亮度落在
    0.78~1.00（顶端 22%），而且彼此差不到 1.08 —— 界面糊成一片。
    现在要求：最暗的背景层亮度 <= 0.90（也就是窗口底要**真的下得来**），
    且最亮与最暗之间对比度 >= 1.15。
    """
    print("\n[light] 层次分离（“太白”的量化判据）")
    layers = [
        ("BG_APP", LIGHT.BG_APP),
        ("BG_CARD", LIGHT.BG_CARD),
        ("BG_ELEVATED", LIGHT.BG_ELEVATED),
        ("BG_INPUT", LIGHT.BG_INPUT),
        ("BG_HOVER", LIGHT.BG_HOVER),
        ("BG_ZEBRA", LIGHT.BG_ZEBRA),
        ("BG_SELECTED", LIGHT.BG_SELECTED),
    ]
    for name, value in layers:
        print(f"    {name:<14}{value}   亮度 {luminance(value):.4f}")

    darkest_name, darkest = min(layers, key=lambda kv: luminance(kv[1]))
    brightest_name, brightest = max(layers, key=lambda kv: luminance(kv[1]))
    spread = contrast(darkest, brightest)

    check(
        f"最暗背景层（{darkest_name}）亮度 {luminance(darkest):.3f} <= 0.90",
        luminance(darkest) <= 0.90,
        "浅色主题若连窗口底都接近纯白，整个界面就会糊成一片白",
    )
    check(
        f"最亮/最暗背景层落差 {spread:.3f} >= 1.15",
        spread >= 1.15,
        f"{brightest_name} vs {darkest_name}",
    )
    # 卡片必须是白的（浅色主题的基本预期），否则是配色跑偏而不是"不白"。
    check("卡片底仍为纯白 #ffffff", LIGHT.BG_CARD.lower() == "#ffffff")


def check_accent_text_distinct_from_body_text() -> None:
    """深浅两套主题的主按钮文字色可以不同 —— 这是刻意的。

    深色主题主色偏亮（#4b8cf7），白字只有 3.28:1，压不住；
    所以深色用深色字。若哪天有人把两者改回同一个白，这条会失败。
    """
    print("\n[两套] 主按钮文字色")
    print(f"    light.ACCENT_TEXT = {LIGHT.ACCENT_TEXT}")
    print(f"    dark.ACCENT_TEXT  = {DARK.ACCENT_TEXT}")
    check(
        f"light 主按钮文字与主色对比 {contrast(LIGHT.ACCENT_TEXT, LIGHT.ACCENT):.2f} >= 4.5",
        contrast(LIGHT.ACCENT_TEXT, LIGHT.ACCENT) >= 4.5,
    )
    check(
        f"dark 主按钮文字与主色对比 {contrast(DARK.ACCENT_TEXT, DARK.ACCENT):.2f} >= 4.5",
        contrast(DARK.ACCENT_TEXT, DARK.ACCENT) >= 4.5,
    )
    # 悬停态也要够看（用户按住鼠标时字不能糊掉）
    check(
        f"dark 主按钮文字与悬停色对比 {contrast(DARK.ACCENT_TEXT, DARK.ACCENT_HOVER):.2f} >= 3.0",
        contrast(DARK.ACCENT_TEXT, DARK.ACCENT_HOVER) >= 3.0,
    )


def check_dark_layer_separation() -> None:
    print("\n[dark] 层次分离")
    check(
        f"卡片底 / 窗口底 {contrast(DARK.BG_CARD, DARK.BG_APP):.3f} >= 1.10",
        contrast(DARK.BG_CARD, DARK.BG_APP) >= 1.10,
    )
    check(
        f"悬浮层 / 卡片底 {contrast(DARK.BG_ELEVATED, DARK.BG_CARD):.3f} >= 1.05",
        contrast(DARK.BG_ELEVATED, DARK.BG_CARD) >= 1.05,
        "下拉/菜单要能浮在卡片之上",
    )


def main() -> int:
    print("=" * 74)
    print("配色对比度与层次分离检查")
    print("=" * 74)
    check_palette(LIGHT)
    check_palette(DARK)
    check_light_not_washed_out()
    check_dark_layer_separation()
    check_accent_text_distinct_from_body_text()
    for index, palette in enumerate((LIGHT, DARK)):
        for label, foreground, background, minimum in (
            ("form primary hover", palette.ACCENT_TEXT, FORM_COLORS["PRIMARY_HOVER"][index], 4.5),
            ("form menu", palette.TEXT_PRIMARY, palette.BG_INPUT, 4.5),
            ("form selected tab", palette.TEXT_PRIMARY, palette.ACCENT_SOFT, 4.5),
            ("form focus", FORM_COLORS["FOCUS"][index], palette.BG_INPUT, 3.0),
        ):
            value = contrast(foreground, background)
            check(f"{palette.name} {label}: {value:.2f} >= {minimum}", value >= minimum)

    print("\n" + "=" * 74)
    print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("未达标：")
        for label in FAILED:
            print(f"  - {label}")
        return 1
    print("全部达标 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
