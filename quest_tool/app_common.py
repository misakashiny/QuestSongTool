"""主界面与各 mixin 共用的小工具。

为什么要单独一个模块：v15.2 把 2400 多行的 `QuestComparatorApp` 拆成了 5 个 mixin
（`app_copy` / `app_delete` / `app_device` / `app_detail` / `app_report`）。
mixin 里**不能反向 import `app.py`**（会形成循环依赖），所以这些原本放在 `app.py`
模块级的小工具必须挪出来共用。

这里只放"纯函数 + 共享常量"，不放任何需要 `self` 的东西。
"""
from __future__ import annotations

from dataclasses import dataclass

from .database import MetaRow
from .songs import SongMeta

#: 右侧详情栏固定宽度。
#: 显式限宽很关键：列表再宽也不会把详情栏挤出屏幕（早期就是这样被挤没的）。
RIGHT_PANEL_WIDTH = 340


@dataclass(frozen=True)
class ColumnIdentity:
    """一列的"身份"：图标 + 语义色 + 标题（m05627「UI 的区分不够明显」）。

    ## 为什么要有这个（而不是各处各写一遍）

    三个列表（本地 / Quest / 缺失）原来只靠标题文字和一个 3px 竖条区分，
    用户在手机上扫一眼分不出"这列是干什么的"。加深区分度要同时动三处：
    列表标题栏、封面墙标题栏、空状态提示 —— 各写一遍必然漂移
    （本项目已多次出现"同一份东西两处定义、只改了一处"）。

    所以**只在这里定义一次**，`ui_songlist` / `ui_coverwall` / `app` 都从这里取。

    ## `icon` 用 emoji 而不是图标文件

    本项目不带图标资源（打包也要跟着进 `datas`），而 emoji 在 Windows 上
    由 Segoe UI Emoji 渲染，零成本、且能直接进标题文字。
    """

    #: 语义色名，必须存在于 `theme.Palette` / `theme.T`
    accent: str
    #: 标题前的图标，一眼区分三列
    icon: str
    #: 这一列是"本地有 / 设备有 / 缺的"哪一类，供提示文案区分
    hint: str = ""


#: 三列的固定身份。**顺序即界面上从左到右的顺序**（local / quest / missing）。
COLUMN_IDENTITIES: dict[str, ColumnIdentity] = {
    # 本地曲库：蓝（主色），🎵
    "local": ColumnIdentity(accent="ACCENT", icon="💻"),
    # Quest 设备：紫（INFO），🥽 —— 用头显意象，和"本地电脑"区分开
    "quest": ColumnIdentity(accent="INFO", icon="🥽"),
    # 缺失（本地有、设备没有）：橙（WARNING），⚠️
    "missing": ColumnIdentity(accent="WARNING", icon="⚠️"),
}


def column_identity(kind: str) -> ColumnIdentity:
    """取某一列的身份；未知 kind 回落到本地那套（宁可显示成蓝的也不要崩）。"""
    return COLUMN_IDENTITIES.get(kind, COLUMN_IDENTITIES["local"])


def meta_row(meta: SongMeta) -> MetaRow:
    """SongMeta -> 数据库行（写缓存用）。"""
    return MetaRow(
        folder_name=meta.folder_name,
        mtime=meta.mtime,
        title=meta.title,
        subtitle=meta.subtitle,
        author=meta.author,
        version=meta.version,
        bpm=meta.bpm,
        duration=meta.duration,
        difficulties=meta.difficulty_text if meta.difficulties else "",
        cover_file=meta.cover_file,
    )


def meta_from_row(folder: str, row: MetaRow) -> SongMeta:
    """数据库行 -> SongMeta（读缓存用）。"""
    return SongMeta(
        folder_name=folder,
        mtime=row.mtime,
        title=row.title,
        subtitle=row.subtitle,
        author=row.author,
        version=row.version,
        bpm=row.bpm,
        duration=row.duration,
        difficulties=tuple(row.difficulty_list),
        cover_file=row.cover_file,
    )


def parse_meta(data: dict | None, folder: str, mtime: float, cover: str) -> SongMeta:
    """把 info.dat 解析结果包成 SongMeta（延迟导入 songs，避免顶层依赖变重）。"""
    from .songs import parse_info_file

    return parse_info_file(data, folder, mtime, cover)


def shorten_path(path: str, limit: int = 68) -> str:
    """把长路径压短给菜单用：保留尾部（最有辨识度的是最后几级目录）。"""
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1):]


def decorate_cover(
    image,
    max_size: tuple[int, int] = (RIGHT_PANEL_WIDTH - 96, 186),
    background: str | None = None,
):
    """把封面缩放到容器内，并加上圆角 + 柔和阴影，返回可直接交给 CTkImage 的图。

    tkinter/ctk 都不支持图片圆角与阴影，所以这里用 PIL 直接画进一张带内边距的
    半透明画布，再合成到面板底色上（CTkImage 需要不透明图，故用底色合成）。
    结果是"一次性算完、缓存复用"，不会影响切换歌曲的流畅度。

    ## 画布尺寸要跟着图片走，不能固定成横长方形（v17.1 修）

    这里原先固定开一张 `max_size[0] + 24` 宽的画布（默认 300+24 = 324 宽），
    再 `thumbnail()` 缩放。而 Beat Saber 封面是**正方形**（300×300），
    `thumbnail((300, 186))` 会按高度贴合 → 缩成 186×186，
    于是画布左右各空出 **(324-24-186)/2 ≈ 57 px**，加上左右 12 px 内边距，
    整个封面两侧各挂着约 **69 px** 的纯色块。

    这就是用户说的"预览图后面特别大的黑边"：黑不是因为颜色写错了（v17.1 之前
    也确实写死过深色，那是最初的一半原因），而是**容器比图宽出一大截**。

    修法：先按 max_size 缩放，再用**缩放后的实际尺寸**决定画布大小 —— 画布永远
    只比图各大出一圈内边距，多余的色块从根上不存在。
    """
    from PIL import Image, ImageDraw, ImageFilter

    fill = _parse_rgb(background) if background else (24, 26, 31)

    pad = 12  # 四周内边距，留给阴影
    thumb = image.copy()
    thumb.thumbnail(max_size, Image.Resampling.LANCZOS)
    width, height = thumb.size
    radius = 10

    # 画布按**缩放后的实际尺寸**开，而不是按 max_size —— 见上面 docstring。
    canvas_size = (width + pad * 2, height + pad * 2)
    padded = Image.new("RGBA", canvas_size, (0, 0, 0, 0))

    # 圆角遮罩
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)

    offset = (pad, pad)
    # 阴影层：把圆角矩形涂黑 → 高斯模糊 → 降低透明度
    shadow = Image.new("RGBA", padded.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (offset[0] + 2, offset[1] + 3, offset[0] + width + 2, offset[1] + height + 3),
        radius=radius, fill=(0, 0, 0, 110),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(5))

    canvas = Image.new("RGBA", padded.size, (0, 0, 0, 0))
    canvas.alpha_composite(shadow)
    layer = thumb.convert("RGBA")
    layer.putalpha(mask)
    canvas.alpha_composite(layer, offset)

    # 合成到纯色底（CTkImage 不透明，避免深色主题下出现黑边）
    background_image = Image.new("RGB", canvas.size, fill)
    background_image.paste(canvas, mask=canvas.split()[3])
    return background_image


def _parse_rgb(value: str) -> tuple[int, int, int]:
    """把 `#rrggbb` 解析成 RGB 三元组；解析不了就回落到深色面板底色。"""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return (24, 26, 31)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return (24, 26, 31)
