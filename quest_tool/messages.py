"""界面文案与多语言（ROADMAP C5）。

**做什么**：把所有面向用户的文案收进一张表，按语言取。这样加一门语言就是加一张字典，
而不是在 11k 行里到处找中文。

**怎么做**：`tr("key", **kwargs)` —— 键 + 具名占位符。取不到键时**原样返回键**并记一条
warning（这样界面不会因为漏翻而空白，测试也能立刻发现）。

**当前覆盖面（重要，别高估）**：只有**主界面外壳**走这里 ——
应用栏、路径区、三组操作按钮、底部状态栏、菜单栏、首次使用向导。
`ui_dialogs` / `ui_songlist` / `ui_coverwall` / 各 mixin 里的提示语**还是内联中文**。
机制已经就位，剩下的是一处一处搬（`tests/smoke_i18n.py` 会守住"已经搬过来的不许退回去"）。

**两条约定**：

1. 新增文案先往这里加键，不要在控件里直接写中文；
2. 中英两张表的**键必须完全一致**（有测试守着）—— 缺哪边都会当场红。
"""
from __future__ import annotations

from typing import Any

from .logging_setup import get_logger

logger = get_logger("i18n")

#: 支持的语言（"zh" = 简体中文，"en" = English）
LANGUAGES: tuple[str, ...] = ("zh", "en")
DEFAULT_LANGUAGE = "zh"

#: 语言下拉里显示的名字
LANGUAGE_NAMES = {"zh": "简体中文", "en": "English"}


# ======================================================================================
# 文案表
# ======================================================================================
_ZH: dict[str, str] = {
    "layout.search": "⌕ 搜索{title}…",
    "layout.size": "大小",
    "layout.selected": "已选 {count}",
    "layout.quest_missing": "Quest 缺失",
    "layout.local_missing": "本地缺失",
    "layout.app_title": "Quest 歌单对比工具",
    "layout.tagline": "Beat Saber 自定义歌曲管理",
    "layout.total": "共 {count} 首",
    "layout.preview": "试听",
    "layout.local_preview": "仅支持本地试听",
    "layout.volume": "音量",
    "toolbar.duplicates": "重复检测",
    "toolbar.import_playlist": "导入歌单",
    "toolbar.export_playlist": "导出歌单",
    "toolbar.export_report": "导出报告",
    "songlist.col.song": "歌曲名 / 艺术家",
    "songlist.col.status": "状态",
    "songlist.state.present": "已匹配",
    "songlist.state.missing": "缺失",
    'app.name': '歌单对比工具',
    'app.name_full': '🎵 Quest歌单对比工具',
    'app.title': '🎵 Quest歌单对比工具 V{version}',
    'sort.default': '默认',
    'sort.name': '名称',
    'sort.rating': '评分',
    'list.page': '第 {page}/{total} 页 · 共 {count} 首',
    'wall.status': '共 {count} 首 · 单击选中、双击查看详情、滚轮滚动',
    'app.tagline': '对比 PC 本地与 Quest 头显上的 Beat Saber 自定义歌曲',
    'app.view.list': '列表',
    'app.view.grid': '封面墙',
    'app.details.toggle': '详情栏',
    'status.ready': '就绪',
    'status.selected': '已选中 {count} 首（Ctrl+A 当前页 · Ctrl+Shift+A 全部 · Esc 取消）',
    'device.none': '● 未连接设备',
    'device.connected': '● {kind}：{path}',
    'device.badge.ok': '● 已连接',
    'device.badge.pending': '◌ 连接中…',
    'device.badge.bad': '● 连接失败',
    'hint.no_library': '点「📂 浏览」选择歌曲文件夹，或点「⚡ 载入」使用上次路径',
    'hint.missing_list': '先点「🔍 找 Quest 缺的」计算缺失歌曲',
    'hint.local_wall': '点「📂 浏览」选择歌曲文件夹\n载入后封面会显示在这里',
    'hint.quest_wall': '点「📡 检测设备」或「📂 浏览」\n载入后封面会显示在这里',
    'hint.missing_wall': '先点「🔍 Quest 缺的」对比一次\n缺失歌曲的封面会显示在这里',
    'btn.detect': '🔌 检测设备',
    'btn.wireless': '📶 无线',
    'btn.rescan': '🔄 重新扫描',
    'btn.settings': '⚙️ 设置',
    'menu.file.wireless': '无线连接 Quest…',
    'menu.action.duplicates': '查找重复歌曲（当前导入的歌单）…',
    'menu.action.duplicates.loose': '查找重复歌曲（宽松：含作者+BPM 相同）…',
    'view.switch.grid': '🖼 封面墙',
    'view.switch.list': '📋 列表',
    'ctx.copy_folder': '复制文件夹名',
    'ctx.copy_title': '复制歌曲标题',
    'ctx.open_explorer': '在资源管理器中打开',
    'ctx.delete': '删除此歌曲',
    'detail.heading': '歌曲详情',
    'detail.no_selection': '🎵\n未选择歌曲',
    'detail.difficulties': '难度',
    'detail.rating': '评分',
    'detail.rating.clear': '清除',
    'detail.comment': '评价',
    'detail.comment.edit': '✏️ 编辑',
    'detail.comment.none': '未评价',
    'detail.only_local': '仅本地歌曲支持在资源管理器中打开',
    'detail.preview.play': '▶ 试听',
    'detail.preview.stop': '■ 停止',
    'detail.preview.loading': '正在解码…',
    'detail.preview.playing': '试听中（{seconds} 秒）',
    'detail.preview.playing_whole': '试听中（整曲）',
    'detail.preview.failed': '这首歌没有可播放的音频',
    'detail.preview.unavailable': '未安装试听组件',
    'detail.preview.local_only': '设备上的歌曲请先复制到本地再试听',
    'detail.preview.pick_first': '先选一首歌',
    'label.local': '本地歌曲',
    'label.quest': 'Quest 歌曲',
    'label.missing': '缺失歌曲',
    'status.bar.ready': '就绪',
    'import.where.title': '导入到哪里？',
    'import.where.prompt': '收到 {count} 首歌曲。没有拖到具体的列表上 —— 输入 1 复制到本地歌单，输入 2 复制到 Quest。（也可以直接把歌曲拖到目标列表上，就不必输这个了）',
    'import.where.placeholder': '1 或 2',
    'import.where.invalid': '请输入 1 或 2',
    'import.confirm.title': '确认导入',
    'import.confirm.message': '把这 {count} 个文件夹复制到{target}吗？',
    'import.confirm.detail': '目标：{path}\n同名歌曲会被新内容替换。',
    'import.confirm.ok': '开始导入',
    'import.progress.title': '正在导入',
    'import.progress.message': '正在导入 {count} 个文件夹…',
    'help.title': '使用说明',
    'help.diagnostics.title': '环境自检信息',
    'path.title': '📁 歌曲路径',
    'path.subtitle': '本地磁盘 / Quest 设备（adb 或 MTP）',
    'path.local': '本地歌曲',
    'path.quest': 'Quest 歌曲',
    'path.placeholder.local': '选择本地 CustomLevels 文件夹…',
    'path.placeholder.quest': '选择 / 自动检测 Quest 上的 CustomLevels…',
    'path.browse': '📂 浏览',
    'path.load': '⚡ 载入',
    'path.recent.tip': '最近用过的路径',
    'path.recent.empty': '还没有最近路径',
    'path.recent.empty.detail': '成功载入过一次之后，这里就会记下来',
    'path.recent.clear': '清空最近路径',
    'path.recent.cleared': '已清空最近路径',
    'path.recent.used': '已切换路径',
    'group.load': '① 载入歌曲',
    'group.compare': '② 对比歌曲',
    'group.fill': '③ 补齐歌曲',
    'group.delete': '④ 删除',
    'btn.load.local': '📥 本地',
    'btn.load.quest': '📱 Quest',
    'btn.compare.quest_missing': '🔍 Quest 缺',
    'btn.compare.local_missing': '🔍 本地 缺',
    'btn.copy.to_quest': '📤 → Quest',
    'btn.copy.to_local': '📤 → 本地',
    'btn.delete.selected': '🗑 删除选中',
    'menu.file': '文件',
    'menu.file.open_local': '打开本地歌曲文件夹…',
    'menu.file.open_quest': '打开 Quest 歌曲文件夹…',
    'menu.file.rescan': '重新扫描两侧',
    'menu.file.export_csv': '导出对比报告（CSV 表格）…',
    'menu.file.export_md': '导出对比报告（Markdown）…',
    'menu.file.export_playlist': '导出 Beat Saber 歌单（.bplist）…',
    'menu.file.import_playlist': '导入 Beat Saber 歌单并高亮匹配…',
    'menu.file.logs': '打开日志目录',
    'menu.file.exit': '退出',
    'menu.action': '操作',
    'menu.action.load_local': '载入本地歌曲',
    'menu.action.load_quest': '载入 Quest 歌曲',
    'menu.action.compare_quest': '对比：Quest 缺失',
    'menu.action.compare_local': '对比：本地缺失',
    'menu.action.copy_to_quest': '复制缺失 → Quest',
    'menu.action.copy_to_local': '复制缺失 → 本地',
    'menu.action.delete': '删除选中歌曲',
    'menu.view': '外观',
    'menu.view.list': '列表视图',
    'menu.view.grid': '封面墙视图',
    'menu.view.details': '显示/隐藏详情栏',
    'menu.help': '帮助',
    'menu.help.manual': '使用说明',
    'menu.help.wizard': '首次使用向导',
    'menu.help.diagnostics': '环境自检信息',
    'wizard.title': '首次使用向导',
    'wizard.heading': '三步开始用',
    'wizard.intro': '每一步都能单独做，做完一步下面会打勾。现在不想做就点「稍后再说」，之后可以从「帮助 → 首次使用向导」再打开。',
    'wizard.step1.title': '选择本地歌曲文件夹',
    'wizard.step1.hint': '点右边按钮，选中你电脑上放 CustomLevels 的那个文件夹（里面每首歌一个子文件夹）。',
    'wizard.step2.title': '连接 Quest 并检测',
    'wizard.step2.hint': '用数据线连上头显、在头显里点「允许 USB 调试」，然后点右边按钮自动找。',
    'wizard.step3.title': '开始对比',
    'wizard.step3.hint': '两边都载入后点右边按钮，程序会算出「Quest 缺的」和「本地缺的」。',
    'wizard.step1.button': '📂 选择文件夹',
    'wizard.step2.button': '📡 检测设备',
    'wizard.step3.button': '🔍 开始对比',
    'wizard.ready': '✅ 已就绪：{path}',
    'wizard.ready.all': '两边都就绪了，点右边开始对比。',
    'wizard.later': '稍后再说',
    'wizard.start': '开始对比',
    'wizard.not_ready.title': '还差一步',
    'wizard.not_ready.body': '先完成第 1、2 步（选本地文件夹、检测 Quest），才能开始对比。',
    'settings.title': '设置',
    'settings.heading': '⚙️ 设置',
    'settings.adb': 'adb 路径',
    'settings.adb.placeholder': '留空则自动查找（PATH / SDK / 常见目录）',
    'settings.appearance': '外观主题',
    'settings.verify': '删除前二次确认（推荐）',
    'settings.poll': '启动时自动检测 Quest 设备',
    'settings.overwrite': '复制时覆盖目标上已存在的同名歌曲（关掉则跳过、不重传）',
    'settings.source': '优先连接方式',
    'settings.source.hint': 'auto = 自动挑最好的（adb → 盘符 → MTP）；选了具体方式且它可用时会直接用，不再弹选择框',
    'settings.scale': '界面缩放',
    'settings.scale.hint': 'auto = 按系统 DPI 自动；字太小/控件挤在一起时可以手动选一档（改完需重启）',
    'settings.language': '界面语言',
    'settings.language.hint': '切换后立即生效的只有主界面外壳；其余提示语仍在逐步搬进文案表',
    'settings.preview': '试听时长',
    'settings.preview.hint': '只影响试听播放多久；选「整曲」会播放完整首歌。改动立即生效，不必重启',
    'settings.open_logs': '打开日志目录',
    'settings.cancel': '取消',
    'settings.save': '保存',
    'settings.info': '配置文件：{settings}\n数据缓存：{database}\n版本：{version}',
    'app.detail_collapsed': '按 Ctrl+D 可重新展开',
    'app.drop_hint': '每个歌曲应该是 CustomLevels 下的一个子文件夹',
    'app.empty_folder': '这个文件夹里没有歌曲（每个歌曲应该是 CustomLevels 下的一个子文件夹）',
    'status.meta_done': '元数据加载完成',
    'status.load_done': '{label}加载完成：{count} 首{detail}',
    'status.cache_hits': '（缓存命中 {count} 首）',
    'status.found': '{label}：已发现 {count} 首',
    'app.drop_no_path': '先设置本地或 Quest 歌曲路径，拖进来的歌才知道要放到哪里',
    'app.pick_folder_first': '先点「📂 浏览」选择文件夹',
    'app.refreshing': '两侧歌曲列表正在刷新',
    'copy.confirm_title': '确认复制',
    'copy.need_both': '请先载入源和目标歌曲列表',
    'copy.need_missing': '先点「🔍 找 Quest 缺的」计算缺失列表',
    'copy.running': '正在复制',
    'copy.wrong_dir_detail': '继续会在错误的方向上复制文件。',
    'copy.wrong_dir_message': '缺失列表来自上一次对比，与当前复制方向不一致。',
    'copy.wrong_dir_title': '方向可能不对',
    'del.confirm_batch_title': '确认批量删除',
    'del.confirm_title': '确认删除',
    'del.device_detail': '设备上的删除没有回收站，无法恢复。建议先在电脑上保留一份。',
    'del.device_message': '你正在删除 Quest 设备上的歌曲文件夹。',
    'del.device_title': '设备删除确认',
    'del.missing_title': '删除缺失歌曲',
    'del.need_load': '请先载入该歌曲列表',
    'detail.cover_failed': '❌\n封面加载失败',
    'detail.no_cover': '🎵\n无封面图片',
    'detail.unknown': '未知',
    'device.clear_recent': '清空最近路径',
    'device.local': '本地',
    'device.mirror_first_time': '首次加载会把歌曲目录镜像到本地，请耐心等待',
    'device.not_found_title': '未检测到 Quest',
    'device.pick_local_title': '选择本地 CustomLevels 文件夹',
    'device.pick_quest_title': '选择 Quest 歌曲目录',
    'device.quest_drive': 'Quest（盘符）',
    'device.remember_hint': '成功载入过一次之后，这里就会记下来',
    'device.see_diagnostics': '点「帮助 → 环境自检信息」可查看详细诊断',
    'device.try_all': '依次尝试盘符、adb 与 MTP 三种连接方式',
    'dlg.browse_device': '🗂 浏览设备…',
    'dlg.cancelling': '正在取消…',
    'dlg.cancelling_wait': '正在取消…（等待当前步骤结束）',
    'dlg.choose_or_type': '请选择或输入一个歌曲目录。',
    'dlg.close': '关闭',
    'dlg.close_esc': '关闭 (Esc)',
    'dlg.content_empty': '内容不能为空',
    'dlg.copied': '✅ 已复制',
    'dlg.copy_clipboard': '📋 复制到剪贴板',
    'dlg.log': '日志',
    'dlg.mtp_no_drive': '（MTP 设备没有盘符，只能在这里手动把 CustomLevels 复制到本地）',
    'dlg.no_device': '未检测到任何设备位置（可点下面的「浏览设备…」自己进去找）',
    'dlg.no_explorer': '当前系统不支持打开资源管理器，请手动打开文件管理器。',
    'dlg.open_explorer': '📂 打开 Explorer 手动复制',
    'dlg.path_hint': '例如 D:\\Songs\\CustomLevels 或 adb:/sdcard/…',
    'dlg.path_label': '路径：',
    'dlg.pause': '⏸ 暂停',
    'dlg.pick_candidate': '请先选择一个候选位置，或手动输入路径。',
    'dlg.save_enter': '保存 (Ctrl+Enter)',
    'dlg.use_path': '使用此路径',
    'help.usage': '【三步用法】\n① 载入：分别选择本地与 Quest 的 CustomLevels 文件夹（Quest 点「⚡ 检测设备」可自动识别）。\n② 对比：点「🔍 Quest 缺的」找出本地有、Quest 没有的歌。\n③ 补齐：点「📤 缺失 → Quest」一键复制过去。\n\n【Quest 连接方式】\n• adb（推荐）：装 Android Platform Tools，头显开启开发者模式并允许 USB 调试；\n   速度快，支持读取/复制/删除，歌曲目录通常在 ModData/SongCore/CustomLevels。\n• MTP（免安装）：插上就能识别，程序会把歌曲目录镜像到本地再解析（较慢，且不删除设备文件）。\n• 系统自带的「选择文件夹」对话框无法选中 MTP 设备中的目录，这是 Windows 的限制。\n\n【快捷键】\nCtrl+O 打开本地文件夹 · Ctrl+Shift+O 打开 Quest 文件夹 · F5 重新扫描\nCtrl+1 对比 Quest 缺失 · Ctrl+2 对比本地缺失 · Ctrl+F 搜索 · F1 帮助 · Delete 删除选中\n\n【安全说明】\n• 复制前会校验源/目标不是同一个文件夹（避免误删源歌曲）；\n• 删除只允许在歌曲根目录的直接子目录上进行，并有二次确认；\n• 评分与评价按「位置 + 文件夹名」保存，本地与 Quest 互不影响。',
    'picker.choose_left': '选择左侧的文件夹…',
    'picker.current': '当前位置',
    'picker.double_click_hint': '提示：双击文件夹 = 直接确定',
    'picker.jump_custom': '⚡ 跳到 CustomLevels',
    'picker.nothing_selected': '尚未选择。\n\n在左侧展开设备，选中 Beat Saber 的 CustomLevels 文件夹。\n\n💡 MTP 设备没有盘符，系统自带的文件夹对话框选不中它，所以本工具自带这个浏览器。',
    'picker.path_hint': '在左侧选择，或直接粘贴路径',
    'picker.path_label': '路径：',
    'picker.reading': '⏳ 读取中…',
    'picker.refresh': '🔄 刷新',
    'picker.title': '选择歌曲文件夹',
    'report.export_local_only': '设备上的歌请先复制回本地，或改用「对比报告」导出',
    'report.export_playlist_title': '导出 Beat Saber 歌单',
    'report.export_report_title': '导出对比报告',
    'report.exporting_playlist': '正在导出歌单',
    'report.hint_copy': '点「📤 缺失 → Quest」可一键补齐',
    'report.identical': '本地与 Quest 的歌曲完全一致',
    'report.import_none_2': '可能是这些歌还没装，或 hash 不一致（谱面更新过）',
    'report.import_ok': '已在列表里高亮；没有的可以用「复制缺失」补齐',
    'report.import_playlist_title': '导入 Beat Saber 歌单',
    'report.need_compare': '先载入本地与 Quest 歌单，再点「对比」',
    'report.need_load_both': '先点「📥 载入本地」和「📱 载入Quest」',
    'report.need_load_dup': '先载入歌单，再找重复',
    'report.need_load_export': '先载入歌单，再导出',
    'report.need_load_import': '先载入歌单，再导入 .bplist',
    'songlist.col.folder': '文件夹',
    'songlist.col.rating': '评分',
    'songlist.col.title': '标题',
    'songlist.ctx.copy_folder': '复制文件夹名',
    'songlist.ctx.copy_title': '复制歌曲标题',
    'songlist.ctx.delete': '删除此歌曲',
    'songlist.ctx.open_explorer': '在资源管理器中打开',
    'songlist.search_hint': '搜索：文件夹 / 标题 / 作者（空格分隔=同时满足）',
    # 列表筛选档位（v17.7）。"已匹配/缺失"都是相对**另一侧**说的。
    'filter.all': '全部',
    'filter.matched': '已匹配',
    'filter.missing': '缺失',
    'filter.showing': '显示 {shown} / {total}',
    'wall.search_hint': '搜索：文件夹 / 标题 / 作者',
    'wireless.intro': '无线连接（同一个 Wi-Fi 下，不用插线）',
}

_EN: dict[str, str] = {
    "layout.search": "⌕ Search {title}…",
    "layout.size": "Size",
    "layout.selected": "Selected {count}",
    "layout.quest_missing": "Missing on Quest",
    "layout.local_missing": "Missing locally",
    "layout.app_title": "Quest Song Comparator",
    "layout.tagline": "Beat Saber custom song manager",
    "layout.total": "{count} songs",
    "layout.preview": "Preview",
    "layout.local_preview": "Local preview only",
    "layout.volume": "Volume",
    "toolbar.duplicates": "Duplicates",
    "toolbar.import_playlist": "Import playlist",
    "toolbar.export_playlist": "Export playlist",
    "toolbar.export_report": "Export report",
    "songlist.col.song": "Song / Artist",
    "songlist.col.status": "Status",
    "songlist.state.present": "Matched",
    "songlist.state.missing": "Missing",
    'app.name': 'Song Comparator',
    'app.name_full': '🎵 Quest Song Comparator',
    'app.title': '🎵 Quest Song Comparator V{version}',
    'sort.default': '🔢 Default',
    'sort.name': '🔤 Name',
    'sort.rating': '⭐ Rating',
    'list.page': 'Page {page}/{total} · {count} songs',
    'wall.status': '{count} songs · click to select, double-click for details, scroll to browse',
    'app.tagline': 'Compare Beat Saber custom songs between your PC and your Quest',
    'app.view.list': 'List',
    'app.view.grid': 'Covers',
    'app.details.toggle': 'Details',
    'status.ready': 'Ready',
    'status.selected': '{count} selected (Ctrl+A this page · Ctrl+Shift+A all · Esc clear)',
    'device.none': '● No device',
    'device.connected': '● {kind}: {path}',
    'device.badge.ok': '● Connected',
    'device.badge.pending': '◌ Connecting…',
    'device.badge.bad': '● Connection failed',
    'hint.no_library': 'Click "📂 Browse" to pick a song folder, or "⚡ Load" to reuse the last path',
    'hint.missing_list': 'Click "🔍 Find missing on Quest" to compute missing songs',
    'hint.local_wall': 'Click "📂 Browse" to pick a song folder\nCovers appear here once loaded',
    'hint.quest_wall': 'Click "📡 Detect device" or "📂 Browse"\nCovers appear here once loaded',
    'hint.missing_wall': 'Click "🔍 Missing on Quest" to compare\nMissing covers appear here',
    # noqa: i18n-length —— help.usage 英文版是**有意压缩**的（18 行 vs 6 行）：
    # 中文帮助把快捷键/安全说明全列出来了，英文只给三步 + 指向 README。
    # 这不是漏译，所以 smoke_i18n 的换行一致性检查对它单独放行。
    'btn.detect': '🔌 Detect',
    'btn.wireless': '📶 Wireless',
    'btn.rescan': '🔄 Rescan',
    'btn.settings': '⚙️ Settings',
    'menu.file.wireless': 'Connect wirelessly…',
    'menu.action.duplicates': 'Find duplicate songs (current library)…',
    'menu.action.duplicates.loose': 'Find duplicates (loose: same author + BPM)…',
    'view.switch.grid': '🖼 Covers',
    'view.switch.list': '📋 List',
    'ctx.copy_folder': 'Copy folder name',
    'ctx.copy_title': 'Copy song title',
    'ctx.open_explorer': 'Open in File Explorer',
    'ctx.delete': 'Delete this song',
    'detail.heading': 'Song details',
    'detail.no_selection': '🎵\nNo song selected',
    'detail.difficulties': 'Difficulties',
    'detail.rating': 'Rating',
    'detail.rating.clear': 'Clear',
    'detail.comment': 'Comment',
    'detail.comment.edit': '✏️ Edit',
    'detail.comment.none': 'No comment',
    'detail.only_local': 'Only local songs can be opened in File Explorer',
    'detail.preview.play': '▶ Preview',
    'detail.preview.stop': '■ Stop',
    'detail.preview.loading': 'Decoding…',
    'detail.preview.playing': 'Playing ({seconds}s)',
    'detail.preview.playing_whole': 'Playing (whole song)',
    'detail.preview.failed': 'No playable audio in this song',
    'detail.preview.unavailable': 'Preview component not installed',
    'detail.preview.local_only': 'Copy the song locally first to preview it',
    'detail.preview.pick_first': 'Pick a song first',
    'label.local': 'Local songs',
    'label.quest': 'Quest songs',
    'label.missing': 'Missing songs',
    'status.bar.ready': 'Ready',
    'import.where.title': 'Where should these go?',
    'import.where.prompt': "Got {count} songs. You didn't drop them onto a specific list — type 1 for Local, 2 for Quest. (Or drop them right onto the target list and skip this.)",
    'import.where.placeholder': '1 or 2',
    'import.where.invalid': 'Please type 1 or 2',
    'import.confirm.title': 'Confirm import',
    'import.confirm.message': 'Copy these {count} folders into {target}?',
    'import.confirm.detail': 'Target: {path}\nSame-named songs will be replaced.',
    'import.confirm.ok': 'Start import',
    'import.progress.title': 'Importing',
    'import.progress.message': 'Importing {count} folders…',
    'help.title': 'How to use',
    'help.diagnostics.title': 'Diagnostics',
    'path.title': '📁 Song folders',
    'path.subtitle': 'Local disk / Quest (adb or MTP)',
    'path.local': 'Local songs',
    'path.quest': 'Quest songs',
    'path.placeholder.local': 'Pick your local CustomLevels folder…',
    'path.placeholder.quest': 'Pick or auto-detect CustomLevels on the Quest…',
    'path.browse': '📂 Browse',
    'path.load': '⚡ Load',
    'path.recent.tip': 'Recently used paths',
    'path.recent.empty': 'No recent paths yet',
    'path.recent.empty.detail': 'They show up here after your first successful load',
    'path.recent.clear': 'Clear recent paths',
    'path.recent.cleared': 'Recent paths cleared',
    'path.recent.used': 'Path switched',
    'group.load': '① Load',
    'group.compare': '② Compare',
    'group.fill': '③ Fill',
    'group.delete': '④ Delete',
    'btn.load.local': '📥 Local',
    'btn.load.quest': '📱 Quest',
    'btn.compare.quest_missing': '🔍 Missing on Quest',
    'btn.compare.local_missing': '🔍 Missing locally',
    'btn.copy.to_quest': '📤 → Quest',
    'btn.copy.to_local': '📤 → Local',
    'btn.delete.selected': '🗑 Delete selected',
    'menu.file': 'File',
    'menu.file.open_local': 'Open local songs folder…',
    'menu.file.open_quest': 'Open Quest songs folder…',
    'menu.file.rescan': 'Rescan both sides',
    'menu.file.export_csv': 'Export comparison (CSV)…',
    'menu.file.export_md': 'Export comparison (Markdown)…',
    'menu.file.export_playlist': 'Export Beat Saber playlist (.bplist)…',
    'menu.file.import_playlist': 'Import playlist and highlight matches…',
    'menu.file.logs': 'Open log folder',
    'menu.file.exit': 'Exit',
    'menu.action': 'Actions',
    'menu.action.load_local': 'Load local songs',
    'menu.action.load_quest': 'Load Quest songs',
    'menu.action.compare_quest': 'Compare: missing on Quest',
    'menu.action.compare_local': 'Compare: missing locally',
    'menu.action.copy_to_quest': 'Copy missing → Quest',
    'menu.action.copy_to_local': 'Copy missing → Local',
    'menu.action.delete': 'Delete selected songs',
    'menu.view': 'View',
    'menu.view.list': 'List view',
    'menu.view.grid': 'Cover wall view',
    'menu.view.details': 'Toggle details panel',
    'menu.help': 'Help',
    'menu.help.manual': 'How to use',
    'menu.help.wizard': 'First-run wizard',
    'menu.help.diagnostics': 'Diagnostics',
    'wizard.title': 'First-run wizard',
    'wizard.heading': 'Get started in three steps',
    'wizard.intro': 'Each step stands on its own — it gets a tick when done. Not now? Hit "Later" and reopen it from Help → First-run wizard.',
    'wizard.step1.title': 'Pick your local songs folder',
    'wizard.step1.hint': 'Click the button and choose the folder holding CustomLevels (one subfolder per song).',
    'wizard.step2.title': 'Connect and detect the Quest',
    'wizard.step2.hint': 'Plug in the headset, tap "Allow USB debugging" inside it, then hit the button.',
    'wizard.step3.title': 'Compare',
    'wizard.step3.hint': "Once both sides are loaded, this tells you what's missing where.",
    'wizard.step1.button': '📂 Choose folder',
    'wizard.step2.button': '📡 Detect device',
    'wizard.step3.button': '🔍 Compare now',
    'wizard.ready': '✅ Ready: {path}',
    'wizard.ready.all': 'Both sides are ready — hit the button to compare.',
    'wizard.later': 'Later',
    'wizard.start': 'Compare',
    'wizard.not_ready.title': 'One step left',
    'wizard.not_ready.body': 'Finish steps 1 and 2 (local folder + Quest) before comparing.',
    'settings.title': 'Settings',
    'settings.heading': '⚙️ Settings',
    'settings.adb': 'adb path',
    'settings.adb.placeholder': 'Leave empty to auto-detect (PATH / SDK / common folders)',
    'settings.appearance': 'Theme',
    'settings.verify': 'Confirm before deleting (recommended)',
    'settings.poll': 'Auto-detect Quest on startup',
    'settings.overwrite': "Overwrite same-named songs on the target (off = skip, don't re-transfer)",
    'settings.source': 'Preferred connection',
    'settings.source.hint': "auto = best available (adb → drive → MTP); pick one and it's used directly when available",
    'settings.scale': 'UI scale',
    'settings.scale.hint': 'auto = follow system DPI; pick a step if text is too small or controls feel cramped (restart needed)',
    'settings.language': 'Language',
    'settings.language.hint': 'Only the main window chrome switches immediately; other messages are being moved over gradually',
    'settings.preview': 'Preview length',
    'settings.preview.hint': 'How long the preview plays; pick "whole song" to play it all. Applies immediately, no restart needed',
    'settings.open_logs': 'Open log folder',
    'settings.cancel': 'Cancel',
    'settings.save': 'Save',
    'settings.info': 'Settings: {settings}\nDatabase: {database}\nVersion: {version}',
    'app.detail_collapsed': 'Press Ctrl+D to expand again',
    'app.drop_hint': 'Each song should be a sub-folder under CustomLevels',
    'app.empty_folder': 'This folder has no songs (each song should be a sub-folder under CustomLevels)',
    'status.meta_done': 'Metadata loaded',
    'status.load_done': '{label} loaded: {count} songs{detail}',
    'status.cache_hits': ' ({count} from cache)',
    'status.found': '{label}: {count} songs found',
    'app.drop_no_path': 'Set the local or Quest song path first so dropped songs know where to go',
    'app.pick_folder_first': 'Click 「📂 Browse」 to choose a folder first',
    'app.refreshing': 'Both song lists are refreshing',
    'copy.confirm_title': 'Confirm copy',
    'copy.need_both': 'Load both the source and target song lists first',
    'copy.need_missing': 'Click 「🔍 Find missing on Quest」 first',
    'copy.running': 'Copying',
    'copy.wrong_dir_detail': 'Continuing would copy files in the wrong direction.',
    'copy.wrong_dir_message': 'The missing list comes from the previous comparison and does not match the current direction.',
    'copy.wrong_dir_title': 'The direction may be wrong',
    'del.confirm_batch_title': 'Confirm batch delete',
    'del.confirm_title': 'Confirm delete',
    'del.device_detail': 'There is no recycle bin on the device and this cannot be undone. Keep a copy on your PC first.',
    'del.device_message': 'You are about to delete song folders on the Quest.',
    'del.device_title': 'Confirm device delete',
    'del.missing_title': 'Delete missing songs',
    'del.need_load': 'Load that song list first',
    'detail.cover_failed': '❌\nFailed to load cover',
    'detail.no_cover': '🎵\nNo cover image',
    'detail.unknown': 'Unknown',
    'device.clear_recent': 'Clear recent paths',
    'device.local': 'Local',
    'device.mirror_first_time': 'The first load mirrors the song folder locally — please be patient',
    'device.not_found_title': 'No Quest detected',
    'device.pick_local_title': 'Choose the local CustomLevels folder',
    'device.pick_quest_title': 'Choose the Quest song folder',
    'device.quest_drive': 'Quest (drive letter)',
    'device.remember_hint': 'Once it loads successfully, it will be remembered here',
    'device.see_diagnostics': 'See 「Help → Diagnostics」 for details',
    'device.try_all': 'Tries drive letter, adb and MTP in turn',
    'dlg.browse_device': '🗂 Browse device…',
    'dlg.cancelling': 'Cancelling…',
    'dlg.cancelling_wait': 'Cancelling… (waiting for the current step to finish)',
    'dlg.choose_or_type': 'Please choose or type a song folder.',
    'dlg.close': 'Close',
    'dlg.close_esc': 'Close (Esc)',
    'dlg.content_empty': 'Content cannot be empty',
    'dlg.copied': '✅ Copied',
    'dlg.copy_clipboard': '📋 Copy to clipboard',
    'dlg.log': 'Log',
    'dlg.mtp_no_drive': '(MTP devices have no drive letter — copy CustomLevels here manually)',
    'dlg.no_device': 'No device location detected (use “Browse device…” below)',
    'dlg.no_explorer': 'This system cannot open Explorer — please open your file manager manually.',
    'dlg.open_explorer': '📂 Open Explorer and copy manually',
    'dlg.path_hint': 'e.g. D:\\Songs\\CustomLevels or adb:/sdcard/…',
    'dlg.path_label': 'Path:',
    'dlg.pause': '⏸ Pause',
    'dlg.pick_candidate': 'Please pick one of the candidates, or type a path.',
    'dlg.save_enter': 'Save (Ctrl+Enter)',
    'dlg.use_path': 'Use this path',
    'help.usage': '【Three-step usage】\n① Load: choose the local and Quest CustomLevels folders separately\n② Compare: click 「🔍 Find missing on Quest」 (or 「Find missing on PC」)\n③ Copy: review the total size / estimated time, then click 「📤 Missing → Quest」\n\nSafety: copying never deletes the source; deleting is guarded by path checks and always asks first.\nFull instructions: see README.md',
    'picker.choose_left': 'Choose a folder on the left…',
    'picker.current': 'Current location',
    'picker.double_click_hint': 'Tip: double-click a folder to confirm it',
    'picker.jump_custom': '⚡ Jump to CustomLevels',
    'picker.nothing_selected': "Nothing selected yet.\n\nExpand the device on the left and pick Beat Saber's CustomLevels folder.\n\n💡 MTP devices have no drive letter, so the system folder dialog cannot select them — that's why this browser exists.",
    'picker.path_hint': 'Pick on the left, or paste a path',
    'picker.path_label': 'Path:',
    'picker.reading': '⏳ Reading…',
    'picker.refresh': '🔄 Refresh',
    'picker.title': 'Choose song folder',
    'report.export_local_only': 'Device songs must be copied locally first, or use “Export comparison” instead',
    'report.export_playlist_title': 'Export Beat Saber playlist',
    'report.export_report_title': 'Export comparison report',
    'report.exporting_playlist': 'Exporting playlist',
    'report.hint_copy': 'Use 「📤 Missing → Quest」 to copy them all',
    'report.identical': 'Local and Quest libraries are identical',
    'report.import_none_2': "Either these songs aren't installed, or the hashes differ (the map was updated)",
    'report.import_ok': 'Highlighted in the list; use 「Copy missing」 to fill the gaps',
    'report.import_playlist_title': 'Import Beat Saber playlist',
    'report.need_compare': 'Load both local and Quest libraries, then click 「Compare」',
    'report.need_load_both': 'Click 「📥 Load local」 and 「📱 Load Quest」 first',
    'report.need_load_dup': 'Load a library first, then look for duplicates',
    'report.need_load_export': 'Load a library before exporting',
    'report.need_load_import': 'Load a library before importing a .bplist',
    'songlist.col.folder': 'Folder',
    'songlist.col.rating': 'Rating',
    'songlist.col.title': 'Title',
    'songlist.ctx.copy_folder': 'Copy folder name',
    'songlist.ctx.copy_title': 'Copy song title',
    'songlist.ctx.delete': 'Delete this song',
    'songlist.ctx.open_explorer': 'Open in Explorer',
    'songlist.search_hint': 'Search: folder / title / author (space = AND)',
    'filter.all': 'All',
    'filter.matched': 'On both',
    'filter.missing': 'Missing',
    'filter.showing': 'Showing {shown} / {total}',
    'wall.search_hint': 'Search: folder / title / author',
    'wireless.intro': 'Wireless connection (same Wi-Fi, no cable needed)',
}

_ZH["dl.error.history"] = "下载记录保存失败，请检查磁盘空间和目录权限；已入库的歌曲保留。"
_EN["dl.error.history"] = "Could not save download history. Check space and permissions; installed songs remain intact."

_ZH["dl.clear"] = "清除已结束记录"
_EN["dl.clear"] = "Clear finished history"

_ZH.update({
    "dl.title": "下载歌曲",
    "dl.intro": "浏览推荐、筛选或搜索，先试听再下载；也可粘贴链接/编号或导入ZIP。关闭窗口后下载队列继续。",
    "dl.links": "链接 / 编号",
    "dl.destination": "保存位置",
    "dl.browse": "浏览",
    "dl.start": "下载并解压",
    "dl.import_zip": "导入 ZIP",
    "dl.keep_zip": "保留原 ZIP",
    "dl.column.song": "歌曲 / 编号",
    "dl.column.mapper": "谱师",
    "dl.column.state": "状态",
    "dl.column.progress": "进度",
    "dl.cancel": "取消所选",
    "dl.retry": "重试所选",
    "dl.open": "打开文件夹",
    "dl.zip_files": "歌曲压缩包",
    "dl.ready": "每首曲包单独入库，不覆盖已有歌曲。同版本默认跳过；不同版本另存。",
    "dl.added": "已加入 {count} 个任务；保存位置已固定到各自任务。",
    "dl.summary": "完成 / 已有 {done} / {total}",
    "dl.refreshed": "下载入库完成，已刷新本地歌曲",
    "dl.state.queued": "等待中",
    "dl.state.resolving": "读取曲包信息",
    "dl.state.downloading": "下载中",
    "dl.state.extracting": "解压检查中",
    "dl.state.installing": "正在入库",
    "dl.state.done": "已完成",
    "dl.state.exists": "本地已有",
    "dl.state.failed": "失败，可重试",
    "dl.state.cancelled": "已取消",
    "dl.state.interrupted": "上次未完成",
    "dl.error.input": "请输入 BeatSaver 歌曲链接或编号；支持换行或空格分隔，最多200首。",
    "dl.error.url": "下载地址不是受支持的 BeatSaver HTTPS 地址。",
    "dl.error.limit": "曲包超过文件数量、体积或解压比例限制。",
    "dl.error.incomplete": "下载不完整，请重试。",
    "dl.error.not_found": "曲包不存在或已经下架。",
    "dl.error.network": "网络请求失败，自动重试后仍未成功。",
    "dl.error.metadata": "没有可下载的已发布版本，或网站信息格式异常。",
    "dl.error.unsafe": "压缩包包含异常路径或链接，已拒绝解压。",
    "dl.error.structure": "歌曲结构不完整：需要有效Info.dat、音频和引用的谱面文件。",
    "dl.error.space": "磁盘空间不足，未入库。",
    "dl.error.zip": "ZIP不存在、已损坏或不受支持。",
    "dl.error.hash": "曲包版本校验不一致，未入库。",
    "dl.error.target": "请选择已存在的本地歌曲文件夹；不支持盘符根目录或网络设备地址。",
    "dl.error.history_full": "下载记录已达500条，请保留记录后整理历史文件。",
    "dl.error.closed": "程序正在退出，不能新增任务。",
    "dl.error.cancelled": "任务已取消；重试会从头下载。",
    "dl.error.interrupted": "上次退出时未完成，请选择重试；不会自动重新下载。",
    "dl.error.unexpected": "任务失败，详情如下。"
})
_EN.update({
    "dl.title": "Download songs",
    "dl.intro": "Browse picks, filter or search; preview before downloading. Links, IDs and ZIP imports also work. Closing this window keeps downloads running.",
    "dl.links": "Links / IDs",
    "dl.destination": "Destination",
    "dl.browse": "Browse",
    "dl.start": "Download & extract",
    "dl.import_zip": "Import ZIP",
    "dl.keep_zip": "Keep ZIP",
    "dl.column.song": "Song / ID",
    "dl.column.mapper": "Mapper",
    "dl.column.state": "Status",
    "dl.column.progress": "Progress",
    "dl.cancel": "Cancel selected",
    "dl.retry": "Retry selected",
    "dl.open": "Open folder",
    "dl.zip_files": "Song archives",
    "dl.ready": "Each map has its own folder. Existing songs are never overwritten. Identical versions are skipped.",
    "dl.added": "Added {count} tasks. Each task keeps its own destination.",
    "dl.summary": "Done / existing {done} / {total}",
    "dl.refreshed": "Download installed; local library refreshed",
    "dl.state.queued": "Queued",
    "dl.state.resolving": "Fetching metadata",
    "dl.state.downloading": "Downloading",
    "dl.state.extracting": "Extracting",
    "dl.state.installing": "Installing",
    "dl.state.done": "Completed",
    "dl.state.exists": "Already installed",
    "dl.state.failed": "Failed; retry available",
    "dl.state.cancelled": "Cancelled",
    "dl.state.interrupted": "Interrupted",
    "dl.error.input": "Enter BeatSaver map links or IDs, separated by spaces or newlines (up to 200).",
    "dl.error.url": "The download URL is not an approved BeatSaver HTTPS address.",
    "dl.error.limit": "Archive exceeds file count, size, or compression limits.",
    "dl.error.incomplete": "Download incomplete. Please retry.",
    "dl.error.not_found": "Map not found or removed.",
    "dl.error.network": "Network request failed after automatic retries.",
    "dl.error.metadata": "No published version is available, or metadata is invalid.",
    "dl.error.unsafe": "Archive contains unsafe paths or links and was rejected.",
    "dl.error.structure": "Incomplete song: valid Info.dat, audio, and referenced beatmaps are required.",
    "dl.error.space": "Not enough disk space. Nothing installed.",
    "dl.error.zip": "ZIP missing, corrupt, or unsupported.",
    "dl.error.hash": "Map version hash mismatch. Nothing installed.",
    "dl.error.target": "Choose an existing local song folder; drive roots and network locations are not supported.",
    "dl.error.history_full": "History reached 500 tasks. Archive the history file before starting a new history.",
    "dl.error.closed": "The app is closing; new tasks cannot be added.",
    "dl.error.cancelled": "Cancelled. Retry starts a fresh download.",
    "dl.error.interrupted": "Interrupted at shutdown. Retry explicitly; downloads do not resume automatically.",
    "dl.error.unexpected": "Task failed. Details below."
})

_ZH.update({
    "search.tab": "发现歌曲", "search.tasks": "下载任务",
    "search.placeholder": "输入歌名、歌手或谱师，回车搜索",
    "search.go": "搜索", "search.order.Relevance": "相关度", "search.order.Latest": "最新发布",
    "search.order.Rating": "好评优先", "search.order.Curated": "精选",
    "search.choose": "点击歌曲查看详情；勾选后可批量下载。",
    "search.previous": "上一页", "search.next": "下一页", "search.page": "第{page}页",
    "search.download": "下载已选 ({count})", "search.clear": "清空选择", "search.select_page": "选择本页",
    "search.ready": "搜索 BeatSaver；跨页保留选择，最多200首。",
    "search.loading": "正在搜索…", "search.results": "本页 {count} 首 · 跨页已选 {selected} 首",
    "search.empty": "没有找到可下载的已发布歌曲，请调整关键词。",
    "search.limit": "最多选择200首，请先下载或清空选择。",
    "search.unknown": "未知", "search.no_requirements": "网站未标注；不代表游戏兼容性已验证",
    "search.details": "{title}\n\n歌手：{artist}\n谱师：{mapper}\n编号：{map_id}\nBPM：{bpm}\n时长：{duration}\n好评率：{rating}\n版本发布：{published}\n\n难度 / 模式 / NPS\n{diffs}\n\n网站标注的扩展\n{requirements}\n\n简介\n{description}",
    "dl.error.query": "请输入1至200字的搜索关键词。"
})
_EN.update({
    "search.tab": "Discover songs", "search.tasks": "Download tasks",
    "search.placeholder": "Song, artist or mapper; press Enter to search",
    "search.go": "Search", "search.order.Relevance": "Relevance", "search.order.Latest": "Latest",
    "search.order.Rating": "Top rated", "search.order.Curated": "Curated",
    "search.choose": "Click a song for details; check songs to download together.",
    "search.previous": "Previous", "search.next": "Next", "search.page": "Page {page}",
    "search.download": "Download selected ({count})", "search.clear": "Clear", "search.select_page": "Select page",
    "search.ready": "Search BeatSaver. Keep up to 200 selections across pages.",
    "search.loading": "Searching…", "search.results": "{count} on this page · {selected} selected across pages",
    "search.empty": "No downloadable published maps found. Try another query.",
    "search.limit": "Select up to 200 songs. Download or clear the selection first.",
    "search.unknown": "Unknown", "search.no_requirements": "Not listed; game compatibility has not been verified",
    "search.details": "{title}\n\nArtist: {artist}\nMapper: {mapper}\nID: {map_id}\nBPM: {bpm}\nDuration: {duration}\nPositive votes: {rating}\nVersion published: {published}\n\nDifficulty / mode / NPS\n{diffs}\n\nExtensions listed by BeatSaver\n{requirements}\n\nDescription\n{description}",
    "dl.error.query": "Enter a search query of 1 to 200 characters."
})

_ZH.update({'search.order.Random': '随机', 'search.order.Duration': '时长', 'filter.title': '筛选', 'filter.active': '筛选 ({count})', 'filter.general': '常规与扩展', 'filter.tags': '标签', 'filter.environments': '环境', 'filter.metrics': '数值与日期', 'filter.any': '不限', 'filter.yes': '包含 / 需要', 'filter.no': '排除 / 不需要', 'filter.all': '全部', 'filter.human': '人工谱面', 'filter.ai_only': 'AI谱面', 'filter.ai': '制作方式', 'filter.leaderboard': '排名平台', 'filter.style': '谱面风格 Style', 'filter.genre': '音乐类型 Genre', 'filter.legacy': '经典环境 Legacy', 'filter.new': '新环境 New', 'filter.bounds_hint': '留空表示不限；好评率单位%，时长单位秒。日期为UTC，结束日期包含当天。扩展按网站标注筛选，不代表Quest兼容性。', 'filter.min': '最小值', 'filter.max': '最大值', 'filter.from': '起始日期 (UTC)', 'filter.to': '结束日期 (UTC)', 'filter.reset': '重置草稿', 'filter.apply': '应用筛选', 'filter.cancel': '取消', 'browse.Search': '搜索歌曲', 'browse.Curated': '精选推荐', 'browse.Week': '本周高评分', 'browse.Month': '本月高评分', 'browse.Year': '本年高评分', 'browse.Latest': '最新发布', 'browse.Verified': '认证谱师', 'browse.Ranked': '排名曲目', 'browse.Random': '随便听听', 'browse.hint': '推荐分类无需关键词；按网站精选、评分或时间排序', 'media.no_cover': '暂无封面', 'media.preview': '试听片段', 'media.missing': '无试听资源', 'media.loading': '正在加载…', 'media.failed': '试听失败', 'dl.error.filters': '筛选值无效或与推荐分类冲突；请检查数字、上下限和日期。', 'dl.error.cover': '封面无法读取。', 'dl.error.preview': '试听音频无效、无法解码或播放失败。', 'dl.error.preview_missing': '网站未提供试听片段。', 'dl.error.preview_unavailable': '试听组件不可用，请查看环境自检信息。', 'dl.error.rate_limit': '网站暂时限制请求，已按等待时间重试；请稍后重试。', 'dl.error.auth': '网站要求身份验证；本程序未提供账号登录，请在网站检查访问状态。', 'dl.error.access': '网站拒绝访问，可能是访问策略或网络限制；不会自动绕过。', 'filter.flag.curated': '精选', 'filter.flag.verified': '认证谱师', 'filter.flag.fullSpread': '完整难度分布', 'filter.flag.chroma': 'Chroma', 'filter.flag.noodle': 'Noodle Extensions', 'filter.flag.me': 'Mapping Extensions', 'filter.flag.cinema': 'Cinema', 'filter.flag.vivify': 'Vivify', 'filter.metric.Nps': 'NPS（每秒音符）', 'filter.metric.Bpm': 'BPM', 'filter.metric.Duration': '时长（秒）', 'filter.metric.Rating': '好评率（%）', 'filter.metric.BlStars': 'BeatLeader星级', 'filter.metric.SsStars': 'ScoreSaber星级', 'filter.metric.Votes': '投票数'})
_EN.update({'search.order.Random': 'Random', 'search.order.Duration': 'Duration', 'filter.title': 'Filters', 'filter.active': 'Filters ({count})', 'filter.general': 'General / requirements', 'filter.tags': 'Tags', 'filter.environments': 'Environments', 'filter.metrics': 'Ranges / dates', 'filter.any': 'Any', 'filter.yes': 'Include / required', 'filter.no': 'Exclude / not required', 'filter.all': 'All', 'filter.human': 'Human', 'filter.ai_only': 'AI', 'filter.ai': 'Map creator', 'filter.leaderboard': 'Leaderboard', 'filter.style': 'Style', 'filter.genre': 'Genre', 'filter.legacy': 'Legacy', 'filter.new': 'New', 'filter.bounds_hint': 'Blank means no bound. Rating uses %, duration uses seconds. Dates use UTC; the end date includes that day. Website extension labels do not verify Quest compatibility.', 'filter.min': 'Minimum', 'filter.max': 'Maximum', 'filter.from': 'Start date (UTC)', 'filter.to': 'End date (UTC)', 'filter.reset': 'Reset draft', 'filter.apply': 'Apply filters', 'filter.cancel': 'Cancel', 'browse.Search': 'Search songs', 'browse.Curated': 'Curated picks', 'browse.Week': 'Top this week', 'browse.Month': 'Top this month', 'browse.Year': 'Top this year', 'browse.Latest': 'Newest', 'browse.Verified': 'Verified mappers', 'browse.Ranked': 'Ranked maps', 'browse.Random': 'Random picks', 'browse.hint': 'Browse without keywords; website curation, votes and dates', 'media.no_cover': 'No cover', 'media.preview': 'Audio preview', 'media.missing': 'No preview', 'media.loading': 'Loading…', 'media.failed': 'Preview failed', 'dl.error.filters': 'Invalid filters or conflicting browse preset. Check values, bounds and dates.', 'dl.error.cover': 'Cannot read cover image.', 'dl.error.preview': 'Preview audio is invalid or could not be decoded or played.', 'dl.error.preview_missing': 'BeatSaver has not provided an audio preview.', 'dl.error.preview_unavailable': 'Audio component unavailable. Check the environment diagnostics.', 'dl.error.rate_limit': 'BeatSaver limited requests after waiting and retrying. Please try again later.', 'dl.error.auth': 'Website requires authentication. This app has no account login; check access on the website.', 'dl.error.access': 'Website refused access due to an access policy or network restriction. No bypass is attempted.', 'filter.flag.curated': 'Curated', 'filter.flag.verified': 'Verified mapper', 'filter.flag.fullSpread': 'Full spread', 'filter.flag.chroma': 'Chroma', 'filter.flag.noodle': 'Noodle Extensions', 'filter.flag.me': 'Mapping Extensions', 'filter.flag.cinema': 'Cinema', 'filter.flag.vivify': 'Vivify', 'filter.metric.Nps': 'NPS (notes / second)', 'filter.metric.Bpm': 'BPM', 'filter.metric.Duration': 'Duration (seconds)', 'filter.metric.Rating': 'Positive votes (%)', 'filter.metric.BlStars': 'BeatLeader stars', 'filter.metric.SsStars': 'ScoreSaber stars', 'filter.metric.Votes': 'Vote count'})

_ZH.update({'layout.import': '链接 / ZIP导入', 'layout.change_path': '更改位置', 'layout.shared_path': '保存位置在窗口底部统一设置；只影响之后新加入的任务。', 'layout.covers': '封面', 'layout.list': '列表', 'layout.download_one': '下载这首', 'layout.mapper': '谱师 · {mapper}', 'layout.selected': '已选 {count} 首 · 跨页保留'})
_EN.update({'layout.import': 'Links / ZIP import', 'layout.change_path': 'Change', 'layout.shared_path': 'Set the shared destination at the bottom. Only new tasks use a changed path.', 'layout.covers': 'Covers', 'layout.list': 'List', 'layout.download_one': 'Download this song', 'layout.mapper': 'Mapper · {mapper}', 'layout.selected': '{count} selected · kept across pages'})

_ZH.update({'layout.columns': '列', 'layout.rows': '行 / 页', 'filter.guide.general': '保留网站英文名，并在下方解释中文含义。扩展标记仅用于筛选，不代表 Quest 已安装或兼容。', 'filter.guide.tags': '保留英文标签并附中文解释。不限 = 不参与筛选；包含 = 必须有；排除 = 不能有。多项包含条件需要同时满足。', 'filter.guide.environments': '场景指谱面使用的舞台背景，不是歌曲类型。英文名用于与网站对应，括号内为中文注释；可多选。', 'filter.guide.metrics': '按整首歌曲筛选，不按试听片段。NPS 是每秒音符数；星级来自对应排行榜，不等同游戏内难度。', 'filter.board.All': 'All（不限榜单）', 'filter.board.Ranked': 'Ranked（任一排名榜）', 'filter.board.BeatLeader': 'BeatLeader（对应排名榜）', 'filter.board.ScoreSaber': 'ScoreSaber（对应排名榜）', 'filter.tag.tech': 'Tech（技术型，复杂切块与手腕走向）', 'filter.tag.dance-style': 'Dance（舞蹈型，重视身体动作）', 'filter.tag.speed': 'Speed（速度型，侧重快速挥砍）', 'filter.tag.balanced': 'Balanced（均衡型）', 'filter.tag.challenge': 'Challenge（挑战型）', 'filter.tag.accuracy': 'Accuracy（精度型，追求准确得分）', 'filter.tag.fitness': 'Fitness（健身型，侧重运动量）', 'filter.tag.poodle': 'Poodle（特殊谱面风格名）', 'filter.tag.swing': 'Swing（摇摆乐）', 'filter.tag.nightcore': 'Nightcore（加速变调风格）', 'filter.tag.folk-acoustic': 'Folk & Acoustic（民谣与原声）', 'filter.tag.kids-family': 'Kids & Family（儿童与家庭）', 'filter.tag.ambient': 'Ambient（氛围音乐）', 'filter.tag.funk-disco': 'Funk & Disco（放克与迪斯科）', 'filter.tag.jazz': 'Jazz（爵士）', 'filter.tag.classical-orchestral': 'Classical & Orchestral（古典与管弦乐）', 'filter.tag.soul': 'Soul（灵魂乐）', 'filter.tag.speedcore': 'Speedcore（极速硬核）', 'filter.tag.punk': 'Punk（朋克）', 'filter.tag.rb': 'R&B（节奏布鲁斯）', 'filter.tag.holiday': 'Holiday（节庆音乐）', 'filter.tag.vocaloid': 'Vocaloid（虚拟歌声）', 'filter.tag.j-rock': 'J-Rock（日本摇滚）', 'filter.tag.trance': 'Trance（出神电音）', 'filter.tag.drum-and-bass': 'Drum and Bass（鼓打贝斯）', 'filter.tag.comedy-meme': 'Comedy & Meme（搞笑与网络梗）', 'filter.tag.instrumental': 'Instrumental（器乐曲）', 'filter.tag.hardcore': 'Hardcore（硬核音乐）', 'filter.tag.k-pop': 'K-Pop（韩国流行）', 'filter.tag.indie': 'Indie（独立音乐）', 'filter.tag.techno': 'Techno（科技舞曲）', 'filter.tag.house': 'House（浩室音乐）', 'filter.tag.video-game-soundtrack': 'Video Game（游戏配乐）', 'filter.tag.tv-movie-soundtrack': 'TV & Film（影视配乐）', 'filter.tag.alternative': 'Alternative（另类音乐）', 'filter.tag.dubstep': 'Dubstep（回响贝斯）', 'filter.tag.metal': 'Metal（金属）', 'filter.tag.anime': 'Anime（动漫音乐）', 'filter.tag.hip-hop-rap': 'Hip Hop & Rap（嘻哈与说唱）', 'filter.tag.j-pop': 'J-Pop（日本流行）', 'filter.tag.dance': 'Dance（舞曲音乐类型）', 'filter.tag.rock': 'Rock（摇滚）', 'filter.tag.pop': 'Pop（流行）', 'filter.tag.electronic': 'Electronic（电子音乐）', 'filter.tag.ai': 'AI（人工智能相关标签）', 'filter.env.DefaultEnvironment': 'Default（默认场景）', 'filter.env.TriangleEnvironment': 'Triangle（三角场景）', 'filter.env.NiceEnvironment': 'Nice（Nice 场景名）', 'filter.env.BigMirrorEnvironment': 'Big Mirror（大镜面场景）', 'filter.env.KDAEnvironment': 'KDA（K/DA 主题）', 'filter.env.MonstercatEnvironment': 'Monstercat（Monstercat 主题）', 'filter.env.CrabRaveEnvironment': 'Crab Rave（螃蟹舞主题）', 'filter.env.DragonsEnvironment': 'Dragons（梦龙乐队主题）', 'filter.env.OriginsEnvironment': 'Origins（起源场景）', 'filter.env.PanicEnvironment': 'Panic（Panic! 乐队主题）', 'filter.env.RocketEnvironment': 'Rocket（火箭主题）', 'filter.env.GreenDayEnvironment': 'Green Day（绿日乐队主题）', 'filter.env.GreenDayGrenadeEnvironment': 'Green Day Grenade（绿日手榴弹主题）', 'filter.env.TimbalandEnvironment': 'Timbaland（Timbaland 主题）', 'filter.env.FitBeatEnvironment': 'Fitbeat（健身节拍主题）', 'filter.env.LinkinParkEnvironment': 'Linkin Park（林肯公园主题）', 'filter.env.BTSEnvironment': 'BTS（防弹少年团主题）', 'filter.env.KaleidoscopeEnvironment': 'Kaleidoscope（万花筒场景）', 'filter.env.InterscopeEnvironment': 'Interscope（同名音乐包主题场景）', 'filter.env.SkrillexEnvironment': 'Skrillex（同名音乐包主题场景）', 'filter.env.BillieEnvironment': 'Billie（同名音乐包主题场景）', 'filter.env.HalloweenEnvironment': 'Halloween（万圣节主题）', 'filter.env.GagaEnvironment': 'Gaga（同名音乐包主题场景）', 'filter.env.Halloween2Environment': 'Spoooky（万圣节场景）', 'filter.env.GlassDesertEnvironment': 'Glass Desert（玻璃沙漠）', 'filter.env.WeaveEnvironment': 'Weave（交织场景）', 'filter.env.PyroEnvironment': 'Pyro（火焰场景）', 'filter.env.EDMEnvironment': 'EDM（电子舞曲主题）', 'filter.env.TheSecondEnvironment': 'The Second（第二场景）', 'filter.env.LizzoEnvironment': 'Lizzo（同名音乐包主题场景）', 'filter.env.TheWeekndEnvironment': 'The Weeknd（同名音乐包主题场景）', 'filter.env.RockMixtapeEnvironment': 'Rock Mixtape（摇滚混音主题）', 'filter.env.Dragons2Environment': 'Dragons 2（梦龙乐队场景 2）', 'filter.env.Panic2Environment': 'Panic 2（Panic! 场景 2）', 'filter.env.QueenEnvironment': 'Queen（皇后乐队主题）', 'filter.env.LinkinPark2Environment': 'Linkin Park 2（林肯公园场景 2）', 'filter.env.TheRollingStonesEnvironment': 'The Rolling Stones（滚石乐队主题）', 'filter.env.LatticeEnvironment': 'Lattice（格栅场景）', 'filter.env.DaftPunkEnvironment': 'Daft Punk（蠢朋克主题）', 'filter.env.HipHopEnvironment': 'HipHop（嘻哈场景）', 'filter.env.ColliderEnvironment': 'Collider（对撞机场景）', 'filter.env.BritneyEnvironment': 'Britney（同名音乐包主题场景）', 'filter.env.Monstercat2Environment': 'Monstercat 2（同名音乐包主题场景）', 'filter.env.MetallicaEnvironment': 'Metallica（金属乐队主题）', 'filter.env.GridEnvironment': 'Cube（方块场景）', 'filter.env.ColdplayEnvironment': 'Coldplay（酷玩乐队主题）', 'filter.env.ProdigyEnvironment': 'Prodigy（The Prodigy 乐队主题）', 'filter.mode.Standard': 'Standard（双手标准模式）', 'filter.mode.OneSaber': 'OneSaber（单手模式）', 'filter.mode.NoArrows': 'NoArrows（无方向箭头）', 'filter.mode.90Degree': '90Degree（90 度转向模式）', 'filter.mode.360Degree': '360Degree（360 度转向模式）', 'filter.mode.Lightshow': 'Lightshow（灯光展示）', 'filter.mode.Lawless': 'Lawless（特殊自由谱面分类）', 'filter.mode.Legacy': 'Legacy（旧版谱面分类）', 'filter.explain.curated': 'Curated · 精选\n由网站精选收录的谱面', 'filter.explain.verified': 'Verified mapper · 认证谱师\n谱师账号具有网站认证标记', 'filter.explain.fullSpread': 'Full spread · 完整难度分布\n包含从简单到高难的完整难度分布', 'filter.explain.chroma': 'Chroma · 灯光与颜色扩展\n筛选带 Chroma 标记的谱面', 'filter.explain.noodle': 'Noodle Extensions · 轨道动画扩展\n筛选带 Noodle 标记的谱面', 'filter.explain.me': 'Mapping Extensions · 谱面扩展\n允许扩展网格等谱面机制', 'filter.explain.cinema': 'Cinema · 视频扩展\n谱面包含背景视频扩展标记', 'filter.explain.vivify': 'Vivify · 自定义视觉扩展\n谱面包含自定义视觉资源标记', 'filter.metric.Nps': 'NPS（每秒音符数）', 'filter.metric.Bpm': 'BPM（每分钟节拍数）', 'filter.metric.BlStars': 'BeatLeader（难度星级）', 'filter.metric.SsStars': 'ScoreSaber（难度星级）'})
_EN.update({'layout.columns': 'Cols', 'layout.rows': 'Rows / page', 'filter.guide.general': 'Official English names are kept. Notes explain what each filter means. Extension labels do not verify Quest compatibility.', 'filter.guide.tags': 'Any ignores the tag, Include requires it, Exclude rejects it. Included tags are combined with AND.', 'filter.guide.environments': 'Environments are map stage backgrounds, not music genres. Multiple selections are allowed.', 'filter.guide.metrics': 'Ranges describe the full song, not its preview. NPS means notes per second. Leaderboard stars are not in-game difficulty labels.', 'filter.board.All': 'All (any leaderboard)', 'filter.board.Ranked': 'Ranked (any ranked board)', 'filter.board.BeatLeader': 'BeatLeader', 'filter.board.ScoreSaber': 'ScoreSaber', 'filter.tag.tech': 'Tech', 'filter.tag.dance-style': 'Dance', 'filter.tag.speed': 'Speed', 'filter.tag.balanced': 'Balanced', 'filter.tag.challenge': 'Challenge', 'filter.tag.accuracy': 'Accuracy', 'filter.tag.fitness': 'Fitness', 'filter.tag.poodle': 'Poodle', 'filter.tag.swing': 'Swing', 'filter.tag.nightcore': 'Nightcore', 'filter.tag.folk-acoustic': 'Folk & Acoustic', 'filter.tag.kids-family': 'Kids & Family', 'filter.tag.ambient': 'Ambient', 'filter.tag.funk-disco': 'Funk & Disco', 'filter.tag.jazz': 'Jazz', 'filter.tag.classical-orchestral': 'Classical & Orchestral', 'filter.tag.soul': 'Soul', 'filter.tag.speedcore': 'Speedcore', 'filter.tag.punk': 'Punk', 'filter.tag.rb': 'R&B', 'filter.tag.holiday': 'Holiday', 'filter.tag.vocaloid': 'Vocaloid', 'filter.tag.j-rock': 'J-Rock', 'filter.tag.trance': 'Trance', 'filter.tag.drum-and-bass': 'Drum and Bass', 'filter.tag.comedy-meme': 'Comedy & Meme', 'filter.tag.instrumental': 'Instrumental', 'filter.tag.hardcore': 'Hardcore', 'filter.tag.k-pop': 'K-Pop', 'filter.tag.indie': 'Indie', 'filter.tag.techno': 'Techno', 'filter.tag.house': 'House', 'filter.tag.video-game-soundtrack': 'Video Game', 'filter.tag.tv-movie-soundtrack': 'TV & Film', 'filter.tag.alternative': 'Alternative', 'filter.tag.dubstep': 'Dubstep', 'filter.tag.metal': 'Metal', 'filter.tag.anime': 'Anime', 'filter.tag.hip-hop-rap': 'Hip Hop & Rap', 'filter.tag.j-pop': 'J-Pop', 'filter.tag.dance': 'Dance', 'filter.tag.rock': 'Rock', 'filter.tag.pop': 'Pop', 'filter.tag.electronic': 'Electronic', 'filter.tag.ai': 'AI', 'filter.env.DefaultEnvironment': 'Default', 'filter.env.TriangleEnvironment': 'Triangle', 'filter.env.NiceEnvironment': 'Nice', 'filter.env.BigMirrorEnvironment': 'Big Mirror', 'filter.env.KDAEnvironment': 'KDA', 'filter.env.MonstercatEnvironment': 'Monstercat', 'filter.env.CrabRaveEnvironment': 'Crab Rave', 'filter.env.DragonsEnvironment': 'Dragons', 'filter.env.OriginsEnvironment': 'Origins', 'filter.env.PanicEnvironment': 'Panic', 'filter.env.RocketEnvironment': 'Rocket', 'filter.env.GreenDayEnvironment': 'Green Day', 'filter.env.GreenDayGrenadeEnvironment': 'Green Day Grenade', 'filter.env.TimbalandEnvironment': 'Timbaland', 'filter.env.FitBeatEnvironment': 'Fitbeat', 'filter.env.LinkinParkEnvironment': 'Linkin Park', 'filter.env.BTSEnvironment': 'BTS', 'filter.env.KaleidoscopeEnvironment': 'Kaleidoscope', 'filter.env.InterscopeEnvironment': 'Interscope', 'filter.env.SkrillexEnvironment': 'Skrillex', 'filter.env.BillieEnvironment': 'Billie', 'filter.env.HalloweenEnvironment': 'Halloween', 'filter.env.GagaEnvironment': 'Gaga', 'filter.env.Halloween2Environment': 'Spoooky', 'filter.env.GlassDesertEnvironment': 'Glass Desert', 'filter.env.WeaveEnvironment': 'Weave', 'filter.env.PyroEnvironment': 'Pyro', 'filter.env.EDMEnvironment': 'EDM', 'filter.env.TheSecondEnvironment': 'The Second', 'filter.env.LizzoEnvironment': 'Lizzo', 'filter.env.TheWeekndEnvironment': 'The Weeknd', 'filter.env.RockMixtapeEnvironment': 'Rock Mixtape', 'filter.env.Dragons2Environment': 'Dragons 2', 'filter.env.Panic2Environment': 'Panic 2', 'filter.env.QueenEnvironment': 'Queen', 'filter.env.LinkinPark2Environment': 'Linkin Park 2', 'filter.env.TheRollingStonesEnvironment': 'The Rolling Stones', 'filter.env.LatticeEnvironment': 'Lattice', 'filter.env.DaftPunkEnvironment': 'Daft Punk', 'filter.env.HipHopEnvironment': 'HipHop', 'filter.env.ColliderEnvironment': 'Collider', 'filter.env.BritneyEnvironment': 'Britney', 'filter.env.Monstercat2Environment': 'Monstercat 2', 'filter.env.MetallicaEnvironment': 'Metallica', 'filter.env.GridEnvironment': 'Cube', 'filter.env.ColdplayEnvironment': 'Coldplay', 'filter.env.ProdigyEnvironment': 'Prodigy', 'filter.mode.Standard': 'Standard', 'filter.mode.OneSaber': 'OneSaber', 'filter.mode.NoArrows': 'NoArrows', 'filter.mode.90Degree': '90Degree', 'filter.mode.360Degree': '360Degree', 'filter.mode.Lightshow': 'Lightshow', 'filter.mode.Lawless': 'Lawless', 'filter.mode.Legacy': 'Legacy', 'filter.explain.curated': 'Curated', 'filter.explain.verified': 'Verified mapper', 'filter.explain.fullSpread': 'Full spread', 'filter.explain.chroma': 'Chroma', 'filter.explain.noodle': 'Noodle Extensions', 'filter.explain.me': 'Mapping Extensions', 'filter.explain.cinema': 'Cinema', 'filter.explain.vivify': 'Vivify'})

_ZH.update({'search.details_compact': '时长：{duration} · BPM：{bpm}\n好评率：{rating}\n谱师：{mapper}\n编号：{map_id}\n版本发布：{published}\n\n难度 / 模式 / NPS\n{diffs}\n\n网站标注的扩展\n{requirements}\n\n简介\n{description}'})
_EN.update({'search.details_compact': 'Duration: {duration} · BPM: {bpm}\nPositive votes: {rating}\nMapper: {mapper}\nID: {map_id}\nVersion published: {published}\n\nDifficulty / mode / NPS\n{diffs}\n\nExtensions listed by BeatSaver\n{requirements}\n\nDescription\n{description}'})

CATALOGS: dict[str, dict[str, str]] = {"zh": _ZH, "en": _EN}

#: 当前语言（进程级）。UI 单线程，不需要锁。
_current = DEFAULT_LANGUAGE


def set_language(language: str) -> str:
    """切换语言，返回**实际**生效的语言（非法值回落默认）。"""
    global _current
    _current = language if language in CATALOGS else DEFAULT_LANGUAGE
    return _current


def current_language() -> str:
    return _current


def tr(key: str, **kwargs: Any) -> str:
    """取文案。取不到就**原样返回键**并记 warning —— 界面不会空白，测试也能立刻发现。"""
    catalog = CATALOGS.get(_current, _ZH)
    text = catalog.get(key)
    if text is None:
        text = _ZH.get(key)
        if text is None:
            logger.warning("文案表里没有这个键：%s", key)
            return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError) as exc:  # 占位符写错了
            logger.warning("文案 %s 的占位符不匹配：%s", key, exc)
            return text
    return text


def catalog(language: str) -> dict[str, str]:
    """取某语言的整张表（测试用来核对两边键一致）。"""
    return dict(CATALOGS.get(language, {}))

_ZH.update({"filter.modes": "游戏模式"})
_EN.update({"filter.modes": "Game modes"})

_ZH.update({
    "inspector.title": "控件详细属性（只读）", "inspector.copy": "复制完整属性",
    "inspector.copied": "已复制", "inspector.hint": "Tab / F2 切换详情 · Ctrl+Alt+D 完整属性",
    "inspector.manager": "布局方式", "inspector.layout": "布局参数", "inspector.parent": "父控件",
    "inspector.class": "Python 类型", "inspector.options": "外观属性", "inspector.empty": "请先指向一个控件",
})
_EN.update({
    "inspector.title": "Widget properties (read-only)", "inspector.copy": "Copy all properties",
    "inspector.copied": "Copied", "inspector.hint": "Tab/F2: compact or detailed | Ctrl+Alt+D: all properties",
    "inspector.manager": "Layout", "inspector.layout": "Layout options", "inspector.parent": "Parent",
    "inspector.class": "Python class", "inspector.options": "Style options", "inspector.empty": "Hover a widget first",
})

_ZH.update({
    'qt.title': 'Quest 歌曲工具 · Qt 界面预览 M1',
    'qt.heading': '界面基础预览',
    'qt.scope': '本阶段用于验证窗口与基础控件。歌曲功能将在后续阶段接入。',
    'qt.appearance': '外观与基础控件',
    'qt.language': '语言',
    'qt.theme': '主题',
    'qt.light': '浅色',
    'qt.dark': '深色',
    'qt.input': '输入示例',
    'qt.option': '选项示例',
    'qt.disabled': '禁用状态',
    'qt.placeholder': '输入一些文字，测试中英文显示',
    'qt.check': '启用示例选项',
    'qt.unavailable': '暂不可用',
    'qt.ready': '可测试输入、选择、键盘焦点和窗口缩放。',
    'qt.applied': '示例状态已应用。',
    'qt.apply': '应用示例',
    'qt.reset': '重置示例',
    'qt.close': '关闭',
    'qt.status': '基础预览 · 正式歌曲数据未接入',
    'qt.data_conflict': '预览目录包含正式数据文件，请选择另一个父目录。',
    'qt.already_running': '该预览目录已被另一个实例使用。',
    'qt.error': 'Qt 预览错误',
    'qt.error_hint': '操作遇到错误，详情已记录到预览日志。',
    'qt.cli': '独立 Qt 界面预览',
    'qt.cli_data': '预览数据父目录；自动创建独立子目录',
})

_EN.update({
    'qt.title': 'Quest Song Tool · Qt Preview M1',
    'qt.heading': 'Interface foundation',
    'qt.scope': 'This preview validates windows and controls. Song features will follow in later stages.',
    'qt.appearance': 'Appearance and controls',
    'qt.language': 'Language',
    'qt.theme': 'Theme',
    'qt.light': 'Light',
    'qt.dark': 'Dark',
    'qt.input': 'Sample input',
    'qt.option': 'Sample option',
    'qt.disabled': 'Disabled state',
    'qt.placeholder': 'Enter text to check Chinese and English rendering',
    'qt.check': 'Enable sample option',
    'qt.unavailable': 'Unavailable',
    'qt.ready': 'Try text entry, selection, keyboard focus and window resizing.',
    'qt.applied': 'Sample state applied.',
    'qt.apply': 'Apply example',
    'qt.reset': 'Reset example',
    'qt.close': 'Close',
    'qt.status': 'Foundation preview · Song data is not connected',
    'qt.data_conflict': 'Preview folder contains production data files. Choose another parent folder.',
    'qt.already_running': 'Another instance is using this preview folder.',
    'qt.error': 'Qt preview error',
    'qt.error_hint': 'An operation failed. Details were recorded in the preview log.',
    'qt.cli': 'Independent Qt interface preview',
    'qt.cli_data': 'Parent directory for the dedicated preview subfolder',
})

_ZH.update({
    'qt.dl.title': 'Quest 歌曲工具 · 下载歌曲 · Qt 预览 M5B',
    'qt.dl.results_empty': '歌曲将在这里显示\n选择分类，浏览歌曲封面或列表。',
    'qt.dl.tasks_empty': '暂无下载任务',
    'qt.dl.import_empty': '通过链接、歌曲编号或 ZIP 文件导入歌曲。',
    'qt.dl.choose': '选择一首歌曲',
    'qt.dl.detail_hint': '歌曲名称、歌手、谱师、时长和评分将在这里显示。',
    'qt.dl.cover': '歌曲封面',
    'qt.dl.destination': '选择歌曲保存位置',
    'qt.dl.selected': '已选 0 首 · 跨页保留',
    'qt.dl.download': '下载已选 (0)',
    'qt.dl.preview': '试听片段',
    'qt.dl.position': '试听进度',
    'qt.dl.time': '00:00 / 00:00',
    'qt.dl.page': '第 1 页',
    'qt.dl.note': '布局预览 · 歌曲功能正在迁移',
    'qt.dl.unavailable': '当前预览暂不可用',
    'qt.dl.components': '打开基础控件预览',
    'qt.dl.filter': '筛选',
    'qt.dl.browse': '更改位置',
})

_EN.update({
    'qt.dl.title': 'Quest Song Tool · Downloads · Qt Preview M5B',
    'qt.dl.results_empty': 'Songs will appear here\nChoose a category to browse covers or a list.',
    'qt.dl.tasks_empty': 'No download tasks',
    'qt.dl.import_empty': 'Import songs using links, map IDs or ZIP files.',
    'qt.dl.choose': 'Select a song',
    'qt.dl.detail_hint': 'Song title, artist, mapper, duration and rating will appear here.',
    'qt.dl.cover': 'Song cover',
    'qt.dl.destination': 'Choose where to save songs',
    'qt.dl.selected': '0 selected · Kept across pages',
    'qt.dl.download': 'Download selected (0)',
    'qt.dl.preview': 'Preview',
    'qt.dl.position': 'Preview position',
    'qt.dl.time': '00:00 / 00:00',
    'qt.dl.page': 'Page 1',
    'qt.dl.note': 'Layout preview · Song features are being migrated',
    'qt.dl.unavailable': 'Not available in this preview',
    'qt.dl.components': 'Open the foundation controls preview',
    'qt.dl.filter': 'Filters',
    'qt.dl.browse': 'Change location',
})


_ZH.update({
    "qt.dl.note": "示例数据预览 · 搜索、下载与试听尚未接入；封面为程序绘制的示例图",
    "qt.songs.art": "示例封面",
    "qt.songs.mapper": "谱师 · {name}",
    "qt.songs.selected": "已选 {count} 首 · 跨页保留",
    "qt.songs.page": "第 {page} / {total} 页",
    "qt.songs.browser": "示例歌曲：方向键浏览，空格勾选，Tab切换区域",
    "qt.songs.details": "{artist}\n\n谱师：{mapper}\n时长：{duration} · BPM：{bpm}\n好评率：{rating}\n\n以上为固定示例数据",
})
_EN.update({
    "qt.dl.note": "Sample data preview · Search, downloads and audio are not connected; covers are generated samples",
    "qt.songs.art": "Sample cover",
    "qt.songs.mapper": "Mapper · {name}",
    "qt.songs.selected": "{count} selected · Kept across pages",
    "qt.songs.page": "Page {page} / {total}",
    "qt.songs.browser": "Sample songs: arrow keys to browse, Space to check, Tab to change area",
    "qt.songs.details": "{artist}\n\nMapper: {mapper}\nDuration: {duration} · BPM: {bpm}\nRating: {rating}\n\nFixed sample data",
})


_ZH.update({
    "qt.cli_samples": "使用离线示例，不连接网站",
    "qt.search.sort": "搜索排序",
    "qt.search.cancel": "取消请求",
    "qt.search.retry": "重试",
    "qt.search.cancelled": "请求已取消，迟到结果不会更新界面。",
    "qt.search.ready": "结果来自 BeatSaver · 封面与试听将在后续阶段接入",
    "qt.search.note": "BeatSaver 歌曲与封面 · 支持完整筛选；下载和试听将在后续接入",
    "qt.cover.loading": "正在加载封面…",
    "qt.search.cover": "封面待接入",
    "qt.search.browser": "BeatSaver 歌曲：方向键浏览，空格勾选，Tab切换区域",
    "qt.search.details": "{artist}\n\n谱师：{mapper}\n时长：{duration} · BPM：{bpm}\n好评率：{rating}\n编号：{map_id}\n版本发布：{published}\n\n难度：\n{diffs}\n\n扩展：{requirements}\n\n{description}",
})
_EN.update({
    "qt.cli_samples": "Use offline samples without connecting to the website",
    "qt.search.sort": "Search order",
    "qt.search.cancel": "Cancel request",
    "qt.search.retry": "Retry",
    "qt.search.cancelled": "Request cancelled. Late results will be ignored.",
    "qt.search.ready": "Results from BeatSaver · Covers and audio will follow in a later stage",
    "qt.search.note": "BeatSaver songs and covers · Full filters enabled; downloads and audio will follow",
    "qt.cover.loading": "Loading cover…",
    "qt.search.cover": "Cover pending",
    "qt.search.browser": "BeatSaver songs: arrow keys to browse, Space to check, Tab to change area",
    "qt.search.details": "{artist}\n\nMapper: {mapper}\nDuration: {duration} · BPM: {bpm}\nRating: {rating}\nMap ID: {map_id}\nPublished: {published}\n\nDifficulties:\n{diffs}\n\nRequirements: {requirements}\n\n{description}",
})

_ZH.update({"qt.preview.stop": "停止试听", "qt.search.ready": "BeatSaver 实时结果 · 支持封面和试听", "qt.search.note": "BeatSaver 歌曲、封面与试听 · 下载功能将在后续接入"})
_EN.update({"qt.preview.stop": "Stop preview", "qt.search.ready": "BeatSaver results · Covers and audio enabled", "qt.search.note": "BeatSaver songs, covers and audio · Downloads will follow"})

_ZH.update({"qt.queue.detail": "保存位置 / 错误", "qt.queue.title": "下载任务", "qt.queue.sandbox": "Qt试点：歌曲仅写入独立沙箱 {path}。正式路径、导入和历史清理将在M5B接入。", "qt.search.note": "BeatSaver 搜索、封面、试听与沙箱下载 · 导入及正式路径待M5B"})
_EN.update({"qt.queue.detail": "Location / error", "qt.queue.title": "Download tasks", "qt.queue.sandbox": "Qt preview: songs stay in the isolated sandbox {path}. Library paths, import and history cleanup follow in M5B.", "qt.search.note": "BeatSaver search, covers, audio and sandbox downloads · Import and library paths follow in M5B"})

_ZH.update({"qt.import.hint": "粘贴 BeatSaver 链接或歌曲编号，每行一首；ZIP导入使用同样的校验与查重规则。", "qt.import.placeholder": "551d8\nhttps://beatsaver.com/maps/551d8", "qt.local.author": "艺术家", "qt.local.duration": "时长", "qt.local.refresh": "刷新本地歌曲", "qt.local.loading": "正在读取当前目录…", "qt.local.failed": "读取失败，请检查当前目录后重试。", "qt.local.count": "当前目录：{count} 个歌曲条目", "qt.queue.sandbox": "保存到：{path}。更改位置只影响新任务；重试需先选择任务原目录。", "qt.search.note": "BeatSaver 搜索、封面、试听、下载与导入 · 用户选择目录后写入"})
_EN.update({"qt.import.hint": "Paste BeatSaver links or map IDs, one per line. ZIP imports use the same validation and duplicate checks.", "qt.import.placeholder": "551d8\nhttps://beatsaver.com/maps/551d8", "qt.local.author": "Artist", "qt.local.duration": "Duration", "qt.local.refresh": "Refresh local songs", "qt.local.loading": "Reading current folder…", "qt.local.failed": "Cannot read folder. Check the location and retry.", "qt.local.count": "Current folder: {count} song entries", "qt.queue.sandbox": "Save to: {path}. Changes affect new jobs only; select the original folder before retrying a job.", "qt.search.note": "BeatSaver search, covers, audio, downloads and import · Writes to your selected folder"})


_ZH.update({'qt.library.title': 'Quest 歌曲工具 · 本地曲库', 'qt.library.local': '本地歌曲', 'qt.library.quest': 'Quest 歌曲', 'qt.library.missing': '缺失歌曲', 'qt.library.details': '歌曲详情', 'qt.library.later_detail': '详情、本地试听与评分将在 M6B 接入。', 'qt.library.later_device': '设备及对比数据尚未接入；此区域不表示曲库为空。', 'qt.library.scope': 'M6A1 只读曲库预览 · 当前支持本地列表；封面墙及对比视图待 M6A2，设备连接待 M7。', 'qt.library.choose': '请选择歌曲目录；不会自动读取旧版路径。', 'qt.library.search': '搜索歌曲、艺术家或文件夹', 'qt.library.select': '勾选可见歌曲', 'qt.library.count': '共 {count} 首 · 可见 {visible} 首 · 已勾选 {selected} 首', 'qt.library.cli': '打开只读本地曲库主窗口预览', 'qt.library.cli_conflict': '--library 不能与 --samples 或 --components 同时使用。', 'qt.library.download': '下载歌曲'})
_EN.update({'qt.library.title': 'Quest Songs · Local library', 'qt.library.local': 'Local songs', 'qt.library.quest': 'Quest songs', 'qt.library.missing': 'Missing songs', 'qt.library.details': 'Song details', 'qt.library.later_detail': 'Details, local audio and ratings follow in M6B.', 'qt.library.later_device': 'Device and comparison data are not connected. This does not mean the library is empty.', 'qt.library.scope': 'M6A1 read-only preview · Local list available; covers and comparison views follow in M6A2, devices in M7.', 'qt.library.choose': 'Choose a song folder. Old application paths are not loaded automatically.', 'qt.library.search': 'Search songs, artists or folders', 'qt.library.select': 'Check visible songs', 'qt.library.count': '{count} songs · {visible} visible · {selected} checked', 'qt.library.cli': 'Open the read-only local library window', 'qt.library.cli_conflict': '--library cannot be combined with --samples or --components.', 'qt.library.download': 'Download songs'})

_ZH.update({'qt.library.scope': 'M6A2 只读曲库预览 · 列表/封面及快照对比；真实设备待 M7，详情与本地试听待 M6B。', 'qt.library.filter.all': '全部', 'qt.library.filter.matched': '已匹配', 'qt.library.filter.missing': '对侧缺失', 'qt.library.missing.quest': 'Quest 缺失 · 来源本地', 'qt.library.missing.local': '本地缺失 · 来源 Quest', 'qt.library.snapshot': '只读快照：{source} · {count} 首', 'qt.library.need_both': '需要有效的本地曲库和 Quest 快照才能判断缺失；设备尚未接入。', 'qt.library.difference': '{count} 首 · 实际来源：{source}'})
_EN.update({'qt.library.scope': 'M6A2 read-only preview · Lists, covers and snapshot comparison; devices follow in M7, details/local audio in M6B.', 'qt.library.filter.all': 'All', 'qt.library.filter.matched': 'Matched', 'qt.library.filter.missing': 'Missing on other side', 'qt.library.missing.quest': 'Missing on Quest · From local', 'qt.library.missing.local': 'Missing locally · From Quest', 'qt.library.snapshot': 'Read-only snapshot: {source} · {count} songs', 'qt.library.need_both': 'A valid local library and Quest snapshot are required to compare. Devices are not connected.', 'qt.library.difference': '{count} songs · Actual source: {source}'})

_ZH.update({"qt.library.song": "歌曲"})
_EN.update({"qt.library.song": "Song"})

_ZH.update({'qt.detail.choose': '选择歌曲查看详情', 'qt.detail.source': '来源', 'qt.detail.folder': '文件夹', 'qt.detail.bpm': 'BPM', 'qt.detail.duration': '时长', 'qt.detail.version': '格式版本', 'qt.detail.size': '大小', 'qt.detail.difficulties': '难度', 'qt.detail.loading': '正在读取歌曲详情…', 'qt.detail.failed': '读取失败，请重试或重新选择歌曲。', 'qt.detail.incomplete': '元数据不完整，部分信息未知。', 'qt.detail.snapshot': '只读 Quest 快照；未提供的详情显示未知。', 'qt.detail.ready': '歌曲详情已载入', 'qt.detail.retry': '重试读取', 'qt.detail.next': '本地试听、评分与相关设置将在后续 M6B 批次接入。', 'qt.library.scope': 'M6B1 只读曲库预览 · 详情、列表/封面与快照对比；本地试听/评分待后续，设备待 M7。'})
_EN.update({'qt.detail.choose': 'Select a song to see details', 'qt.detail.source': 'Source', 'qt.detail.folder': 'Folder', 'qt.detail.bpm': 'BPM', 'qt.detail.duration': 'Duration', 'qt.detail.version': 'Format version', 'qt.detail.size': 'Size', 'qt.detail.difficulties': 'Difficulties', 'qt.detail.loading': 'Reading song details…', 'qt.detail.failed': 'Cannot read details. Retry or select another song.', 'qt.detail.incomplete': 'Metadata is incomplete; some details are unknown.', 'qt.detail.snapshot': 'Read-only Quest snapshot. Unavailable details remain unknown.', 'qt.detail.ready': 'Song details loaded', 'qt.detail.retry': 'Retry details', 'qt.detail.next': 'Local audio, ratings and related settings follow in later M6B batches.', 'qt.library.scope': 'M6B1 read-only preview · Details, lists/covers and snapshots; local audio/ratings follow later, devices in M7.'})

_ZH.update({'qt.local_audio.play': '试听本地歌曲', 'qt.local_audio.choose': '请选择本地歌曲试听', 'qt.local_audio.failed': '无法试听，请检查音频文件和输出设备。', 'qt.local_audio.full': '整曲', 'qt.local_audio.seconds': '{seconds} 秒', 'qt.local_audio.volume': '音量', 'qt.local_audio.seek': '试听进度', 'qt.local_audio.length': '试听长度', 'qt.detail.next': '评分和相关设置将在后续批次接入。', 'qt.library.scope': 'M6B2 曲库预览 · 本地详情与试听；评分待后续，设备待 M7。'})
_EN.update({'qt.local_audio.play': 'Preview local song', 'qt.local_audio.choose': 'Select a local song to preview', 'qt.local_audio.failed': 'Cannot preview. Check the audio file and output device.', 'qt.local_audio.full': 'Full song', 'qt.local_audio.seconds': '{seconds} seconds', 'qt.local_audio.volume': 'Volume', 'qt.local_audio.seek': 'Preview position', 'qt.local_audio.length': 'Preview length', 'qt.detail.next': 'Ratings and related settings follow in a later batch.', 'qt.library.scope': 'M6B2 library preview · Local details and audio; ratings follow later, devices in M7.'})

_ZH.update({'qt.userdata.rating': '本地评分', 'qt.userdata.none': '未评分', 'qt.userdata.clear_hint': '选择未评分可清除评分，不影响评价。', 'qt.userdata.edit': '编辑评价', 'qt.userdata.save': '保存（Ctrl+Enter）', 'qt.userdata.cancel': '取消', 'qt.userdata.empty': '未评价', 'qt.userdata.unavailable': '仅支持本地歌曲；Quest快照不可编辑。', 'qt.userdata.pending': '正在读取或保存…', 'qt.userdata.failed': '读写失败；已提交的修改可重试。', 'qt.userdata.ready': '评分与评价保存在独立Qt预览数据库。', 'qt.userdata.retry': '重试', 'qt.userdata.save_failed': '{song}：保存失败，请回到该歌曲重试。', 'qt.detail.next': '评分和试听偏好仅写入独立Qt预览目录。', 'qt.library.scope': 'M6B3 曲库预览 · 详情、试听、评分评价；真实设备待 M7。'})
_EN.update({'qt.userdata.rating': 'Local rating', 'qt.userdata.none': 'Not rated', 'qt.userdata.clear_hint': 'Choose Not rated to clear the rating and keep the comment.', 'qt.userdata.edit': 'Edit comment', 'qt.userdata.save': 'Save (Ctrl+Enter)', 'qt.userdata.cancel': 'Cancel', 'qt.userdata.empty': 'No comment', 'qt.userdata.unavailable': 'Local songs only. Quest snapshots cannot be edited.', 'qt.userdata.pending': 'Reading or saving…', 'qt.userdata.failed': 'Read/write failed. Retry submitted changes.', 'qt.userdata.ready': 'Ratings and comments use the separate Qt preview database.', 'qt.userdata.retry': 'Retry', 'qt.userdata.save_failed': '{song}: save failed. Return to this song to retry.', 'qt.detail.next': 'Ratings and audio preferences use the separate Qt preview folder.', 'qt.library.scope': 'M6B3 library preview · Details, audio, ratings/comments; devices follow in M7.'})

_ZH.update({'qt.ratings.heading': '评分', 'qt.ratings.sort': '本地评分排序', 'qt.ratings.sort.title': '按歌曲名称', 'qt.ratings.sort.high': '评分从高到低', 'qt.ratings.sort.low': '评分从低到高', 'qt.ratings.loading': '正在读取本地评分…', 'qt.ratings.failed': '评分读取失败，未读取项保持未知。', 'qt.ratings.ready': '本地评分已载入', 'qt.ratings.idle': '本地评分未载入', 'qt.ratings.retry': '重试评分', 'qt.library.scope': 'M6C1 曲库预览 · 本地评分列表、封面与排序；设备待 M7。'})
_EN.update({'qt.ratings.heading': 'Rating', 'qt.ratings.sort': 'Local rating order', 'qt.ratings.sort.title': 'Song name', 'qt.ratings.sort.high': 'Highest rating first', 'qt.ratings.sort.low': 'Lowest rating first', 'qt.ratings.loading': 'Reading local ratings…', 'qt.ratings.failed': 'Cannot read ratings. Unread values remain unknown.', 'qt.ratings.ready': 'Local ratings loaded', 'qt.ratings.idle': 'Local ratings not loaded', 'qt.ratings.retry': 'Retry ratings', 'qt.library.scope': 'M6C1 library preview · Local ratings in lists, covers and sorting; devices follow in M7.'})

_ZH.update({'qt.ratings.sort.artist':'按艺术家','qt.ratings.sort.duration':'按时长'})
_EN.update({'qt.ratings.sort.artist':'Artist','qt.ratings.sort.duration':'Duration'})

_ZH.update({'qt.ratings.unknown_mark':'?'})
_EN.update({'qt.ratings.unknown_mark':'?'})


_ZH.update({'qt.actions.visible':'勾选当前可视区域 (Ctrl+A)', 'qt.actions.all':'勾选筛选结果全部 (Ctrl+Shift+A)', 'qt.actions.clear':'清空本栏勾选及行选择 (Esc)', 'qt.actions.open_failed':'无法打开本地歌曲目录。', 'qt.library.scope':'M6C2 曲库预览 · 右键复制与本地目录；Ctrl+D 显隐详情，Ctrl+1/2 切换比较方向；设备待 M7。'})
_EN.update({'qt.actions.visible':'Check visible viewport (Ctrl+A)', 'qt.actions.all':'Check all filtered results (Ctrl+Shift+A)', 'qt.actions.clear':'Clear checks and row selection (Esc)', 'qt.actions.open_failed':'Could not open the local song folder.', 'qt.library.scope':'M6C2 library preview · Copy and local folders; Ctrl+D toggles details, Ctrl+1/2 switches comparison; devices follow in M7.'})


_ZH.update({'qt.device.pick':'选择已验证的候选设备', 'qt.device.list':'刷新候选', 'qt.device.read':'读取快照', 'qt.device.clear':'取消 / 清除快照', 'qt.device.excluded':'已排除 {count} 个无效、离线或非Quest候选', 'qt.device.state.disabled':'离线适配阶段：真实设备提供者尚未接入。', 'qt.device.state.idle':'尚未请求候选设备。', 'qt.device.state.discovering':'正在读取候选设备…', 'qt.device.state.choose':'请选择候选，再读取快照。', 'qt.device.state.loading':'正在读取所选设备的只读快照…', 'qt.device.state.ready':'只读快照已就绪。', 'qt.device.state.empty':'读取成功：该位置没有歌曲。', 'qt.device.state.none':'没有可用候选；尚未读取曲库。', 'qt.device.state.disconnected':'提供者报告设备已断开，旧快照已清除。', 'qt.device.state.rejected':'设备身份或快照数据未通过校验。', 'qt.device.state.failed':'读取失败；可重新刷新候选或读取。', 'qt.device.state.cancelled':'请求已取消，快照已清除。', 'qt.library.scope':'M7A1 离线只读适配 · 真实设备尚未接入；保留本地浏览、评分及试听。'})
_EN.update({'qt.device.pick':'Select a verified candidate', 'qt.device.list':'Refresh list', 'qt.device.read':'Read snapshot', 'qt.device.clear':'Cancel / clear snapshot', 'qt.device.excluded':'Excluded {count} invalid, offline or non-Quest candidates', 'qt.device.state.disabled':'Offline adapter stage: no real device provider is connected.', 'qt.device.state.idle':'No candidate request has been made.', 'qt.device.state.discovering':'Reading device candidates…', 'qt.device.state.choose':'Select a candidate, then read its snapshot.', 'qt.device.state.loading':'Reading the selected device snapshot…', 'qt.device.state.ready':'Read-only snapshot is ready.', 'qt.device.state.empty':'Read succeeded: no songs at this location.', 'qt.device.state.none':'No usable candidates; no library has been read.', 'qt.device.state.disconnected':'Provider reported a disconnect. Previous snapshot cleared.', 'qt.device.state.rejected':'Device identity or snapshot validation failed.', 'qt.device.state.failed':'Read failed. Refresh the list or retry reading.', 'qt.device.state.cancelled':'Request cancelled and snapshot cleared.', 'qt.library.scope':'M7A1 offline read-only adapter · Real devices remain disconnected; local browsing, ratings and preview retained.'})

_ZH.update({'qt.device.clear_short':'清除'})
_EN.update({'qt.device.clear_short':'Clear'})


_ZH.update({'qt.device.cli_adb':'为曲库窗口启用ADB只读提供者；仍需手动刷新和选择读取', 'qt.device.cli_adb_path':'ADB可执行文件路径（只与--quest-adb一起使用）', 'qt.device.cli_conflict':'--quest-adb需要--library；--adb-path需要--quest-adb。', 'qt.device.state.disabled':'未启用设备提供者；使用--library --quest-adb可启用ADB只读。', 'qt.library.scope':'M7A2C 曲库预览 · ADB分批只读需显式启用；不含无线连接、复制或删除。'})
_EN.update({'qt.device.cli_adb':'Enable the read-only ADB provider for the library; refresh and read remain manual', 'qt.device.cli_adb_path':'ADB executable path (requires --quest-adb)', 'qt.device.cli_conflict':'--quest-adb requires --library; --adb-path requires --quest-adb.', 'qt.device.state.disabled':'No provider enabled; use --library --quest-adb for read-only ADB.', 'qt.library.scope':'M7A2C library preview · Explicit batched read-only ADB opt-in; no wireless connection, copy or deletion.'})

_ZH.update({
    'qt.sample.duration':'可选：缺少歌曲时长时，每首最多读取192 KiB音频头尾，不拉取整曲。',
    'qt.sample.cli':'Quest 只读采样：默认仅显示计划，--execute 才访问设备；结果不是完整曲库。',
    'qt.sample.execute':'实际读取明确目标，最多16个歌曲文件夹；两路对照，不写入Quest。',
    'qt.device.cancel_short':'取消',
    'qt.device.state.choose':'找到 {count} 个设备／目录候选，请明确选择后读取；不会自动选择。',
    'qt.device.state.none':'未找到可用候选。请连接并唤醒 Quest，允许 USB 调试后刷新。',
    'qt.device.state.unauthorized':'等待 USB 调试授权。请戴上头显允许此计算机，再刷新候选。',
    'qt.device.state.offline':'ADB 设备离线。请唤醒头显并检查 USB 连接，再刷新候选。',
    'qt.device.state.adb_path':'无法执行 ADB。请检查已安装的 ADB 路径，使用 --adb-path 指定后重新打开预览。',
    'qt.device.state.roots':'Quest 身份已验证，但未找到支持的歌曲目录。请确认歌曲已安装及目录访问权限，再刷新。',
    'qt.device.state.timeout':'读取超时。请检查连接后重试；取消不会安装迟到的快照。',
    'qt.device.state.command':'ADB 读取命令失败。请检查 USB 调试、目录访问权限及连接，再刷新或重试。',
    'qt.device.state.disconnected':'设备连接已失效，旧快照已清除。请重新连接并刷新候选。',
})
_EN.update({
    'qt.sample.duration':'Optional: read at most 192 KiB of audio parts per missing duration; no full song download.',
    'qt.sample.cli':'Quest read-only sample: plan only unless --execute is supplied; not a complete library.',
    'qt.sample.execute':'Read the explicit target, up to 16 song folders twice for comparison; no Quest writes.',
    'qt.device.cancel_short':'Cancel',
    'qt.device.state.choose':'Found {count} device / folder candidates. Select one explicitly before reading; none is selected automatically.',
    'qt.device.state.none':'No usable candidates. Connect and wake Quest, allow USB debugging, then refresh.',
    'qt.device.state.unauthorized':'USB debugging approval required. Allow this computer in the headset, then refresh.',
    'qt.device.state.offline':'ADB device is offline. Wake the headset and check USB, then refresh.',
    'qt.device.state.adb_path':'Cannot run ADB. Check the installed executable and reopen the preview with --adb-path.',
    'qt.device.state.roots':'Quest identity verified, but no supported song folder found. Check installed songs and folder access, then refresh.',
    'qt.device.state.timeout':'Read timed out. Check the connection and retry. Cancelled requests cannot install late snapshots.',
    'qt.device.state.command':'ADB read command failed. Check USB debugging, folder access and connection, then refresh or retry.',
    'qt.device.state.disconnected':'Connection lost; previous snapshot cleared. Reconnect and refresh the candidates.',
})


_ZH.update({
    'qt.transfer.title':'复制任务 · 离线适配',
    'qt.transfer.offline':'M7B1 离线试点：仅供注入模拟后端验证；尚未连接真实设备复制入口。已有歌曲跳过，不覆盖。',
    'qt.transfer.plan':'方向：{direction} · {count} 首\n本地：{local}\n设备：{device}\n歌曲目录：{root}',
    'qt.transfer.to_quest':'本地 → Quest', 'qt.transfer.to_local':'Quest → 本地',
    'qt.transfer.ready':'检查上述方向、设备与目录后启动模拟任务。',
    'qt.transfer.start':'启动模拟任务', 'qt.transfer.pause':'暂停', 'qt.transfer.resume':'继续',
    'qt.transfer.cancel':'取消任务', 'qt.transfer.running':'任务继续执行。',
    'qt.transfer.paused':'已请求暂停：当前歌曲完成后停下；等待期间仍可取消。',
    'qt.transfer.song':'正在处理：{folder}', 'qt.transfer.done':'完成',
    'qt.transfer.cancelled':'已取消', 'qt.transfer.blocked':'已被护栏阻止',
    'qt.transfer.result':'{state}：成功 {ok} · 跳过 {skipped} · 失败 {failed}',
})
_EN.update({
    'qt.transfer.title':'Transfer tasks · Offline adapter',
    'qt.transfer.offline':'M7B1 offline pilot: injected simulated backend only; no real device copy entrypoint. Existing songs are skipped, never overwritten.',
    'qt.transfer.plan':'Direction: {direction} · {count} songs\nLocal: {local}\nDevice: {device}\nSong root: {root}',
    'qt.transfer.to_quest':'Local → Quest', 'qt.transfer.to_local':'Quest → Local',
    'qt.transfer.ready':'Check direction, device and paths before starting the simulation.',
    'qt.transfer.start':'Start simulation', 'qt.transfer.pause':'Pause', 'qt.transfer.resume':'Resume',
    'qt.transfer.cancel':'Cancel task', 'qt.transfer.running':'Task resumed.',
    'qt.transfer.paused':'Pause requested: the current song finishes first. Cancellation remains available while waiting.',
    'qt.transfer.song':'Processing: {folder}', 'qt.transfer.done':'Finished',
    'qt.transfer.cancelled':'Cancelled', 'qt.transfer.blocked':'Blocked by safety checks',
    'qt.transfer.result':'{state}: copied {ok} · skipped {skipped} · failed {failed}',
})

_ZH.update({
    'qt.transfer.pull_title':'从 Quest 导入到本地',
    'qt.transfer.pull_scope':'只读取 Quest。已有同名歌曲跳过，不覆盖；取消在当前歌曲处理完成后生效。',
    'qt.transfer.pull_ready':'请核对来源设备、歌曲目录及本地目标，确认后开始导入。',
    'qt.transfer.pull_start':'确认并导入',
    'qt.transfer.select_quest':'勾选可见 Quest 歌曲',
    'qt.transfer.clear_quest':'清空 Quest 勾选',
    'qt.transfer.pull_selected':'导入勾选 → 本地',
    'qt.transfer.pull_missing':'导入勾选的本地缺失',
    'qt.transfer.pull_hint':'已勾选 {count} 首可读取歌曲；需先载入两侧曲库。',
    'qt.transfer.pull_library_scope':'M7B2A 曲库预览 · 已启用 Quest → 本地导入；不向 Quest 写入或删除。',
    'qt.transfer.cli_pull':'启用 Quest → 本地导入；需 --library --quest-adb，仍须手动选设备、载入并确认。',
    'qt.transfer.cli_conflict':'--quest-pull / --quest-push 需要同时使用 --library 和 --quest-adb。',
})
_EN.update({
    'qt.transfer.pull_title':'Import from Quest to local',
    'qt.transfer.pull_scope':'Quest is read-only. Existing songs are skipped, never overwritten. Cancel takes effect after the current song finishes.',
    'qt.transfer.pull_ready':'Check the source device, song root and local destination, then confirm the import.',
    'qt.transfer.pull_start':'Confirm and import',
    'qt.transfer.select_quest':'Check visible Quest songs',
    'qt.transfer.clear_quest':'Clear Quest checks',
    'qt.transfer.pull_selected':'Import checked → local',
    'qt.transfer.pull_missing':'Import checked local missing',
    'qt.transfer.pull_hint':'{count} readable songs checked; load both libraries first.',
    'qt.transfer.pull_library_scope':'M7B2A library preview · Quest → local import enabled; no Quest writes or deletion.',
    'qt.transfer.cli_pull':'Enable Quest → local import; requires --library --quest-adb and manual device selection, loading and confirmation.',
    'qt.transfer.cli_conflict':'--quest-pull / --quest-push require both --library and --quest-adb.',
})

_ZH.update({
    'qt.transfer.cli_push':'启用本地→Quest推送；需--library --quest-adb，手动勾选并确认后才执行，默认跳过已有歌曲。',
    'qt.transfer.push_title':'推送歌曲到 Quest',
    'qt.transfer.push_scope':'将写入所选 Quest 歌曲目录。已有同名歌曲跳过，不覆盖；取消在当前歌曲完成后生效。',
    'qt.transfer.push_ready':'请核对本地来源、Quest 设备与目标目录，确认后开始推送。',
    'qt.transfer.push_start':'确认并推送',
    'qt.transfer.push_selected':'本地勾选 → Quest',
    'qt.transfer.push_missing':'补齐勾选的 Quest 缺失',
    'qt.transfer.push_hint':'推送后需手动重读 Quest；不会自动覆盖或删除已有歌曲。',
    'qt.transfer.push_library_scope':'M7B2B 传输预览 · 推送需明确勾选及确认；无删除与自动覆盖。',
})
_EN.update({
    'qt.transfer.cli_push':'Enable local→Quest push; requires --library --quest-adb and explicit selection/confirmation. Existing songs are skipped.',
    'qt.transfer.push_title':'Push songs to Quest',
    'qt.transfer.push_scope':'Writes to the selected Quest song root. Existing songs are skipped, never overwritten. Cancel takes effect after the current song.',
    'qt.transfer.push_ready':'Check the local source, Quest device and target root before confirming the push.',
    'qt.transfer.push_start':'Confirm and push',
    'qt.transfer.push_selected':'Local checked → Quest',
    'qt.transfer.push_missing':'Fill checked Quest missing',
    'qt.transfer.push_hint':'Read Quest again after pushing. No automatic overwrite or deletion.',
    'qt.transfer.push_library_scope':'M7B2B transfer preview · Push requires explicit selection and confirmation; no deletion or automatic overwrite.',
})


# M7B2C: read-only transaction inspection and explicit failed-item retry.
_ZH.update({'qt.transfer.history': '传输事务记录', 'qt.transfer.history_hint': '仅查看本机日志，不能证明设备当前状态。不会恢复、清理、删除或重放事务。原始字段名用于排查。', 'qt.transfer.history_state': '状态', 'qt.transfer.history_folder': '歌曲目录', 'qt.transfer.history_id': '事务文件', 'qt.transfer.history_refresh': '刷新本机日志', 'qt.transfer.history_failed': '无法读取日志目录。未访问设备或修改任何记录。', 'qt.transfer.history_invalid': '记录不可用：格式、身份或路径未通过校验。', 'qt.transfer.history_limited': '显示范围受限：仅检查前1000个目录项中的最多100条JSON记录。', 'qt.transfer.history_review': '需核查 / 状态未确认', 'qt.transfer.history_backup': '已提交，备份保留', 'qt.transfer.history_committed': '日志记为已提交', 'qt.transfer.retry': '重新确认失败项', 'qt.transfer.retry_blocked': '设备或目录状态已改变。请先重新读取同一设备快照并确认本地目录，再重试。', 'qt.transfer.retry_ready': '已准备本次明确失败的歌曲，请再次确认并开始。已有目标会跳过；不补跑中断后未确认的项目，也不清理旧暂存或备份。'})
_EN.update({'qt.transfer.history': 'Transfer journals', 'qt.transfer.history_hint': 'Local logs only; they do not prove current device state. No restore, cleanup, deletion or replay. Raw field names are diagnostic.', 'qt.transfer.history_state': 'Status', 'qt.transfer.history_folder': 'Song folder', 'qt.transfer.history_id': 'Transaction file', 'qt.transfer.history_refresh': 'Refresh local logs', 'qt.transfer.history_failed': 'Cannot read the journal directory. No device access or record changes.', 'qt.transfer.history_invalid': 'Unavailable record: format, identity or path validation failed.', 'qt.transfer.history_limited': 'Limited view: at most 100 JSON records from the first 1000 directory entries.', 'qt.transfer.history_review': 'Review / state unconfirmed', 'qt.transfer.history_backup': 'Committed, backup retained', 'qt.transfer.history_committed': 'Recorded as committed', 'qt.transfer.retry': 'Review failed songs', 'qt.transfer.retry_blocked': 'Device or directory state changed. Re-read the same device snapshot and confirm the local folder before retrying.', 'qt.transfer.retry_ready': 'Explicitly failed songs are ready for confirmation. Start again to retry. Existing targets are skipped; uncertain interrupted items are excluded and old staging/backups remain.'})

_ZH.update({"qt.transfer.history_hint":"本机日志仅供排查，远端现状未确认；不会自动恢复、清理或重放。", "qt.transfer.history_unavailable":"记录不可用"})
_EN.update({"qt.transfer.history_hint":"Local logs only; device state is unconfirmed. No restore, cleanup or replay.", "qt.transfer.history_unavailable":"Unavailable record"})


# M8A1: report labels passed as plain data into the shared renderer.
_ZH.update({'report.column.status': '状态', 'report.column.folder': '文件夹', 'report.column.title': '标题', 'report.column.author': '作者', 'report.column.bpm': 'BPM', 'report.column.duration': '时长', 'report.column.difficulties': '难度', 'report.column.rating': '评分', 'report.document_title': 'Quest 歌单对比报告', 'report.generated_line': '- 生成时间：{value}', 'report.local_line': '- 本地路径：`{value}`', 'report.quest_line': '- Quest 路径：`{value}`', 'report.summary_line': '- 合计 {total} 首：两边都有 {both}、仅本地 {local}、仅设备 {quest}', 'report.unset': '（未设置）', 'report.state.both': '两边都有', 'report.state.local': '仅本地', 'report.state.quest': '仅设备'})
_EN.update({'report.column.status': 'Status', 'report.column.folder': 'Folder', 'report.column.title': 'Title', 'report.column.author': 'Artist', 'report.column.bpm': 'BPM', 'report.column.duration': 'Duration', 'report.column.difficulties': 'Difficulties', 'report.column.rating': 'Rating', 'report.document_title': 'Quest song comparison report', 'report.generated_line': '- Generated: {value}', 'report.local_line': '- Local path: `{value}`', 'report.quest_line': '- Quest path: `{value}`', 'report.summary_line': '- Total {total}: on both {both}, local only {local}, Quest only {quest}', 'report.unset': '(not set)', 'report.state.both': 'On both', 'report.state.local': 'Local only', 'report.state.quest': 'Quest only'})


# Quest read: invalid byte names never become replacement-character paths.
_ZH['qt.device.state.partial'] = '已读取 {songs} 首；另有 {count} 个目录名称无法解码。快照不完整，已停用完整对比、报告及传输；设备文件未修改。'
_EN['qt.device.state.partial'] = 'Read {songs} songs; {count} directory names could not be decoded. Snapshot incomplete: comparison, reports and transfers disabled. Device files unchanged.'

# M8A2: snapshot report export.
_ZH.update({'qt.library.local_select': '勾选本地可见歌曲', 'qt.library.local_clear': '清空本地选择'})
_EN.update({'qt.library.local_select': 'Check visible local songs', 'qt.library.local_clear': 'Clear local checks'})
_ZH.update({'qt.library.direction.quest': 'Quest 缺失', 'qt.library.direction.local': '本地缺失'})
_EN.update({'qt.library.direction.quest': 'Missing on Quest', 'qt.library.direction.local': 'Missing locally'})
_ZH['qt.library.filter.missing'] = '对侧缺失'
_EN['qt.library.filter.missing'] = 'Only here'

# Read-only hints for empty library viewports.
_ZH.update({
    'qt.help.open':'操作帮助', 'qt.help.title':'操作帮助与快捷键', 'qt.help.close':'关闭',
    'qt.help.intro':'快捷键的作用取决于当前操作区域。使用歌曲区域快捷键前，请先点击列表或封面。',
    'qt.help.general':'常用操作', 'qt.help.results':'歌曲区域（先点击列表或封面）',
    'qt.help.choose':'选择本地歌曲目录', 'qt.help.refresh':'刷新当前本地曲库',
    'qt.help.search':'将焦点移到搜索框', 'qt.help.details':'显示或隐藏歌曲详情',
    'qt.help.quest':'切换到“Quest 缺失”方向', 'qt.help.local':'切换到“本地缺失”方向',
    'qt.help.show':'打开操作帮助', 'qt.help.visible':'勾选当前屏幕内的歌曲',
    'qt.help.all':'勾选当前搜索／筛选结果中的全部歌曲',
    'qt.help.copy':'复制当前栏已勾选歌曲的文件夹名；没有勾选时复制所选行',
    'qt.help.menu':'打开当前歌曲的右键菜单', 'qt.help.clear':'清空当前曲库栏的全部勾选',
    'qt.help.selection_note':'“勾选本地可见歌曲”按钮会勾选当前搜索／筛选结果中的全部本地歌曲，与 Ctrl+A 的屏幕内范围不同。清空勾选不会删除歌曲。',
    'qt.help.search_note':'搜索框中的 Esc 用于清空搜索词；帮助窗口中的 Esc 用于关闭帮助。右键菜单还可单独复制歌曲标题。',
    'qt.help.snapshot_note':'缺失对比需要两侧数据就绪；Quest 快照不完整时，缺失对比、报告和传输仍不可用。',
    'qt.help.select_button':'勾选当前搜索／筛选结果中的全部本地歌曲，保留已有勾选。',
    'qt.help.clear_button':'清空本地歌曲的全部勾选，不删除歌曲。',
    'qt.help.direction_tip':'切换缺失对比方向（Ctrl+1 / Ctrl+2），需要完整的两侧数据。',
    'qt.help.view_tip':'本地列表与封面共用勾选；切换视图不会清空选择。',
})
_EN.update({
    'qt.help.open':'Help', 'qt.help.title':'Help and keyboard shortcuts', 'qt.help.close':'Close',
    'qt.help.intro':'Shortcuts depend on the active area. Click a list or cover before using song-area shortcuts.',
    'qt.help.general':'Common actions', 'qt.help.results':'Song area (click a list or cover first)',
    'qt.help.choose':'Choose a local song folder', 'qt.help.refresh':'Refresh the current local library',
    'qt.help.search':'Focus the search box', 'qt.help.details':'Show or hide song details',
    'qt.help.quest':'Switch to Missing on Quest', 'qt.help.local':'Switch to Missing locally',
    'qt.help.show':'Open operation help', 'qt.help.visible':'Check songs currently on screen',
    'qt.help.all':'Check every song in the current search/filter results',
    'qt.help.copy':'Copy folder names checked in this pane; if none are checked, copy selected rows',
    'qt.help.menu':'Open the current song context menu', 'qt.help.clear':'Clear every check in the current song pane',
    'qt.help.selection_note':'The Check visible local songs button checks every local song in the current search/filter results, unlike the on-screen scope of Ctrl+A. Clearing checks does not delete songs.',
    'qt.help.search_note':'Esc clears text in the search box and closes this help window. The context menu can also copy a song title.',
    'qt.help.snapshot_note':'Comparison requires both libraries. An incomplete Quest snapshot still disables missing-song comparison, reports and transfers.',
    'qt.help.select_button':'Check all local songs in the current search/filter results and keep existing checks.',
    'qt.help.clear_button':'Clear every local song check without deleting songs.',
    'qt.help.direction_tip':'Change comparison direction (Ctrl+1 / Ctrl+2). Complete data from both sides is required.',
    'qt.help.view_tip':'The local list and covers share checks. Switching views keeps the selection.',
})
_ZH.update({'qt.library.later_device':'尚未载入 Quest 快照；读取后显示歌曲。',
            'qt.library.need_both':'载入本地歌曲和完整 Quest 快照后可进行对比。'})
_EN.update({'qt.library.later_device':'Quest snapshot not loaded. Read the library to show songs.',
            'qt.library.need_both':'Load local songs and a complete Quest snapshot to compare.'})
_ZH.update({
    'qt.empty.local':'尚未载入本地歌曲', 'qt.empty.local_hint':'点击“{browse}”选择歌曲目录。',
    'qt.empty.failed':'歌曲读取失败', 'qt.empty.failed_hint':'点击“{refresh}”重试，或选择其他目录。',
    'qt.empty.quest':'尚未读取 Quest 歌曲', 'qt.empty.quest_hint':'读取设备歌曲后，结果会显示在这里。',
    'qt.empty.none':'曲库为空', 'qt.empty.none_hint':'当前载入结果为 0 首。',
    'qt.empty.filtered':'没有匹配的歌曲', 'qt.empty.filtered_hint':'调整搜索词或筛选条件。',
    'qt.empty.both':'尚无法对比', 'qt.empty.both_hint':'缺失对比需要本地歌曲和 Quest 快照。',
    'qt.empty.partial':'快照不完整', 'qt.empty.partial_hint':'当前已停用缺失对比，请查看设备读取提示。',
    'qt.empty.complete':'当前方向没有缺失歌曲', 'qt.empty.complete_hint':'可切换对比方向查看另一侧。',
})
_EN.update({
    'qt.empty.local':'Local songs not loaded', 'qt.empty.local_hint':'Choose {browse} to select a song folder.',
    'qt.empty.failed':'Cannot read songs', 'qt.empty.failed_hint':'Choose {refresh} to retry, or select another folder.',
    'qt.empty.quest':'Quest songs not loaded', 'qt.empty.quest_hint':'Songs appear here after reading the device library.',
    'qt.empty.none':'Library is empty', 'qt.empty.none_hint':'The loaded result contains 0 songs.',
    'qt.empty.filtered':'No matching songs', 'qt.empty.filtered_hint':'Adjust the search text or filters.',
    'qt.empty.both':'Comparison unavailable', 'qt.empty.both_hint':'Load local songs and a Quest snapshot to compare.',
    'qt.empty.partial':'Incomplete snapshot', 'qt.empty.partial_hint':'Missing-song comparison is disabled. Check the device status.',
    'qt.empty.complete':'None missing in this direction', 'qt.empty.complete_hint':'Switch direction to check the other side.',
})
_ZH.update({'qt.report.export': '导出报告', 'qt.report.csv': 'CSV 表格', 'qt.report.md': 'Markdown 报告', 'qt.report.hint': '导出两侧已载入的全部歌曲，不受搜索、筛选和勾选限制。使用新版保存的本地评分；Quest评分及快照未提供的BPM/难度显示未知。', 'qt.report.need': '报告：需载入两侧及评分', 'qt.report.ready': '报告：可导出', 'qt.report.writing': '报告：正在保存', 'qt.report.saved': '报告已保存', 'qt.report.failed': '报告保存失败', 'qt.report.cancelled': '报告任务已取消', 'qt.report.snapshot_saved': '已保存此前的快照', 'qt.report.destination_error': '请选择歌曲库和程序数据目录之外的CSV或Markdown文件，使用已有的普通文件夹。', 'qt.report.write_error': '文件未保存，请检查目录权限或文件是否被占用；具体原因见日志。'})
_EN.update({'qt.report.export': 'Export report', 'qt.report.csv': 'CSV table', 'qt.report.md': 'Markdown report', 'qt.report.hint': 'Export all loaded songs on both sides, regardless of search, filters or checks. Uses local ratings saved in this version; Quest ratings and snapshot fields not supplied remain unknown.', 'qt.report.need': 'Report: load both + ratings', 'qt.report.ready': 'Report: ready', 'qt.report.writing': 'Report: saving', 'qt.report.saved': 'Report saved', 'qt.report.failed': 'Report save failed', 'qt.report.cancelled': 'Report cancelled', 'qt.report.snapshot_saved': 'Earlier snapshot saved', 'qt.report.destination_error': 'Choose a CSV or Markdown file in an existing folder outside the song library and application data.', 'qt.report.write_error': 'The report was not saved. Check folder permissions or whether the file is in use; see the log for details.'})


# Download-page cache feedback; no background widget access.
_ZH.update({
    'qt.search.refresh_page': '刷新本页',
    'qt.search.refresh_hint': '刷新当前页（F5），跳过列表缓存；保留已提交条件和勾选，不提交搜索框草稿。',
    'qt.search.next_planned': '当前页已加载 · 即将预加载下一页列表',
    'qt.search.next_loading': '正在后台准备下一页列表 · 可继续浏览',
    'qt.search.next_ready': '下一页列表已准备 · 封面按需继续加载',
    'qt.search.cached': '当前页来自缓存',
})
_EN.update({
    'qt.search.refresh_page': 'Refresh page',
    'qt.search.refresh_hint': 'Refresh this page (F5), bypassing the list cache. Keep committed filters and checks; do not submit the search draft.',
    'qt.search.next_planned': 'Page loaded · Next-page list will preload shortly',
    'qt.search.next_loading': 'Preparing the next-page list in the background · Keep browsing',
    'qt.search.next_ready': 'Next-page list ready · Covers continue loading as needed',
    'qt.search.cached': 'Current page loaded from cache',
})


_ZH.update({
 'qt.selection.title':'已选歌曲', 'qt.selection.cover':'封面', 'qt.selection.song':'歌曲',
 'qt.selection.artist':'艺术家', 'qt.selection.status':'状态记录',
 'qt.selection.same_version':'同版本下载记录', 'qt.selection.other_version':'其他版本下载记录',
 'qt.selection.last_state':'最近任务：{state}', 'qt.selection.unverified':'未核验安装状态',
 'qt.selection.history_hint':'状态来自当前目录的队列、历史及本地核验快照；只有“已核验”标签有本次文件检查依据。文件变动后请在选项菜单重新核验；不按歌名推断安装，不自动处理歌曲。',
 'qt.selection.summary':'跨页已选 {count} 首 · 搜索仅影响显示',
 'qt.selection.download':'下载全部已选 ({count})',
 'qt.selection.target':'保存位置：{path}', 'qt.selection.no_queue':'尚未启用下载队列',
 'qt.selection.search':'搜索已选歌曲、艺术家或状态',
 'qt.selection.hint':'下载按钮提交全部已选歌曲，包括搜索后隐藏的项目。移除仅取消勾选，不删除文件。封面复用已有缓存。',
 'qt.selection.remove':'移除选中项', 'qt.selection.remove_hint':'只从已选清单中移除，不删除歌曲文件。',
 'qt.selection.close':'关闭', 'qt.selection.open_hint':'查看跨页已选清单，可试听、移除并统一下载。',
})
_EN.update({
 'qt.selection.title':'Selected songs', 'qt.selection.cover':'Cover', 'qt.selection.song':'Song',
 'qt.selection.artist':'Artist', 'qt.selection.status':'Status record',
 'qt.selection.same_version':'Same-version download record', 'qt.selection.other_version':'Other-version download record',
 'qt.selection.last_state':'Last job: {state}', 'qt.selection.unverified':'Installation not verified',
 'qt.selection.history_hint':'Status uses this folder’s queue, history and local verification snapshot. Only verified labels have file-check evidence. Recheck from Options after files change. Titles do not establish installation; no automatic song changes.',
 'qt.selection.summary':'{count} selected across pages · Search affects display only',
 'qt.selection.download':'Download all ({count})',
 'qt.selection.target':'Save to: {path}', 'qt.selection.no_queue':'Download queue not enabled',
 'qt.selection.search':'Search selected songs, artists or status',
 'qt.selection.hint':'Download submits all selected songs, including filtered-out items. Removing only unchecks items; files are kept. Covers use the existing cache.',
 'qt.selection.remove':'Remove rows', 'qt.selection.remove_hint':'Remove from the selection only; keep song files.',
 'qt.selection.close':'Close', 'qt.selection.open_hint':'Review songs selected across pages, preview, remove and download together.',
})

_ZH.update({'qt.selection.open_count':'已选清单 ({count})'})
_EN.update({'qt.selection.open_count':'Selected ({count})'})

_ZH.update({
 'qt.cover_disk.clear':'清理磁盘封面缓存', 'qt.cover_disk.busy':'正在清理封面缓存…',
 'qt.cover_disk.cleared':'已清理 {count} 个缓存文件（可再次清理）',
 'qt.cover_disk.failed':'缓存清理失败，请检查目录权限后重试',
 'qt.cover_disk.hint':'只清理新版专用磁盘封面缓存，当前显示和内存中的图片保留；不删除歌曲。新请求可重新缓存。默认上限128MB/2000张，30天过期；不可用时回退联网。',
})
_EN.update({
 'qt.cover_disk.clear':'Clear disk cover cache', 'qt.cover_disk.busy':'Clearing cover cache…',
 'qt.cover_disk.cleared':'Cleared {count} cache files (click to clear again)',
 'qt.cover_disk.failed':'Cache clear failed; check folder permissions and retry',
 'qt.cover_disk.hint':'Clear only the dedicated Qt disk cover cache. Displayed/in-memory pictures stay; song files are kept. New requests may cache again. Limit: 128MB / 2000 images; expires after 30 days. Network loading remains available if caching fails.',
})

_ZH.update({
 'qt.results.summary':'队列汇总（含历史及各保存目录）· 共 {total} 项\n已下载 {done} · 已存在 {exists} · 失败 {failed} · 中断 {interrupted} · 已取消 {cancelled} · 进行中 {active}',
 'qt.results.filter_label':'按任务状态查看', 'qt.results.filter.all':'全部状态',
 'qt.results.filter.done':'已下载', 'qt.results.filter.exists':'已存在（跳过）',
 'qt.results.filter.attention':'失败 / 中断', 'qt.results.filter.cancelled':'已取消',
 'qt.results.filter.active':'进行中', 'qt.results.visible':'显示 {count} / {total} 项',
 'qt.results.source':'歌曲来源 / 编号', 'qt.results.job':'任务ID',
 'qt.results.copy':'复制当前结果', 'qt.results.copied':'已复制当前显示结果。',
 'qt.results.clear_hint':'清理全部终态历史记录，包括当前筛选隐藏的记录；不删除歌曲文件。',
 'qt.results.copy_hint':'复制当前状态筛选下的任务、状态、进度、详情及保存目录。不会复制歌曲文件。',
 'qt.results.export_hint':'下载队列结果（含历史；当前状态筛选）。记录不代表文件现在仍在；“已存在”为任务当时跳过。重试需选中任务，并使用其原保存目录。',
})
_EN.update({
 'qt.results.summary':'Queue summary (including history and all destinations) · {total} jobs\nDownloaded {done} · Already existed {exists} · Failed {failed} · Interrupted {interrupted} · Cancelled {cancelled} · Active {active}',
 'qt.results.filter_label':'View jobs by state', 'qt.results.filter.all':'All states',
 'qt.results.filter.done':'Downloaded', 'qt.results.filter.exists':'Already existed (skipped)',
 'qt.results.filter.attention':'Failed / interrupted', 'qt.results.filter.cancelled':'Cancelled',
 'qt.results.filter.active':'Active', 'qt.results.visible':'Showing {count} / {total} jobs',
 'qt.results.source':'Song source / ID', 'qt.results.job':'Job ID',
 'qt.results.copy':'Copy visible results', 'qt.results.copied':'Visible results copied.',
 'qt.results.clear_hint':'Clear all terminal history, including rows hidden by the current filter. Song files are kept.',
 'qt.results.copy_hint':'Copy jobs, states, progress, details and destinations for the current state filter. Song files are not copied.',
 'qt.results.export_hint':'Download queue results (including history; current state filter). Records do not confirm files still exist. Already existed means skipped at that time. Select jobs to retry and use their original destination.',
})

_ZH.update({'qt.selection.preview':'试听 / 停止', 'qt.selection.preview_hint':'播放时再次点击同一首可停止；进度和音量使用主窗口底部播放器调整。'})
_EN.update({'qt.selection.preview':'Preview / stop', 'qt.selection.preview_hint':'Click the same playing song again to stop. Adjust position and volume in the main window player.'})

_ZH.update({
 'qt.filters.summary':'当前筛选 ({count})', 'qt.filters.remove':'移除：{condition}',
 'qt.filters.none':'未添加个人筛选条件', 'qt.filters.clear':'清空条件',
 'qt.filters.category_hint':'推荐分类自带的时间、精选等规则仍有效。点击“当前筛选”逐项移除；数值上下限作为一组移除。',
 'qt.filters.presets':'个人预设', 'qt.filters.name':'新预设名称（最多40字）',
 'qt.filters.preset_hint':'只保存已应用的筛选条件，不保存搜索词、分类、排序或页码。载入会按当前搜索框和分类重新查询；冲突时保留原条件。最多20份；重名不覆盖，可先删除旧预设。',
 'qt.filters.save':'保存当前条件', 'qt.filters.load':'载入并筛选', 'qt.filters.delete':'删除选中预设',
 'qt.filters.close':'关闭', 'qt.filters.saved':'预设已保存。',
 'qt.filters.storage_error':'预设文件无法读取或保存，原文件保留。请检查文件权限或格式。',
 'qt.filters.save_error':'保存失败：请使用新的有效名称（1–40字），最多20份；也请检查文件权限。',
 'qt.filters.conflict':'此预设与当前推荐分类冲突，未改变条件。请关闭窗口，切换分类后重试。',
})
_EN.update({
 'qt.filters.summary':'Active filters ({count})', 'qt.filters.remove':'Remove: {condition}',
 'qt.filters.none':'No personal filter conditions', 'qt.filters.clear':'Clear filters',
 'qt.filters.category_hint':'Browse category rules, including date and curation, still apply. Use Active filters to remove a condition; numeric bounds are removed together.',
 'qt.filters.presets':'My presets', 'qt.filters.name':'New preset name (up to 40 characters)',
 'qt.filters.preset_hint':'Save applied filters only, without query, category, order or page. Loading searches using the current query field and category; conflicts keep existing filters. Up to 20 presets; names are not overwritten. Delete an old preset first to reuse its name.',
 'qt.filters.save':'Save current filters', 'qt.filters.load':'Load and search', 'qt.filters.delete':'Delete selected preset',
 'qt.filters.close':'Close', 'qt.filters.saved':'Preset saved.',
 'qt.filters.storage_error':'Cannot read or save presets. The original file is kept. Check permissions and file format.',
 'qt.filters.save_error':'Cannot save. Use a new valid name (1–40 characters), keep at most 20 presets, and check permissions.',
 'qt.filters.conflict':'This preset conflicts with the current browse category. Filters are unchanged. Close this window, change category and try again.',
})


_ZH.update({
 'qt.verify.action':'重新扫描本地歌曲', 'qt.verify.busy':'正在识别本地歌曲…',
 'qt.verify.same':'本地同版本 · 已核验', 'qt.verify.other':'本地其他版本 · 已核验',
 'qt.verify.missing':'记录对应目录已缺失', 'qt.verify.changed':'本地版本与记录不符',
 'qt.verify.unknown':'本地记录未能核验',
 'qt.verify.hint':'仅核验当前保存目录中下载记录指向的歌曲路径；无记录歌曲保持未知，不按歌名判断。结果为读取时快照（最近：{time}），文件变动后请在选项菜单重新核验。版本按原谱面哈希比较，不验证音频内容，也不自动下载、覆盖或删除。',
})
_EN.update({
 'qt.verify.action':'Rescan local songs', 'qt.verify.busy':'Identifying local songs…',
 'qt.verify.same':'Local same version · verified', 'qt.verify.other':'Local other version · verified',
 'qt.verify.missing':'Recorded folder is missing', 'qt.verify.changed':'Local version differs from record',
 'qt.verify.unknown':'Local record could not be verified',
 'qt.verify.hint':'Checks only song paths named by download records in the current destination. Songs without records remain unknown; titles are not identities. Results are read-time snapshots (last: {time}); recheck from Options after files change. Versions use the existing map hash, not audio-content verification. No automatic download, overwrite or deletion.',
})


_ZH.update({
 'qt.snapshot.now_short':'刚刚', 'qt.snapshot.minutes_short':'{count}分前',
 'qt.snapshot.cached_short':'快照', 'qt.snapshot.old_short':'建议刷新', 'qt.snapshot.partial_short':'不完整',
 'qt.snapshot.compact':'{time} · {age} · {state}',
 'qt.snapshot.just_now':'不足1分钟', 'qt.snapshot.minutes':'{count}分钟前',
 'qt.snapshot.cached':'只读快照 · 未监测连接',
 'qt.snapshot.old':'建议重新读取',
 'qt.snapshot.partial':'快照不完整',
 'qt.snapshot.notice':'读取 {time} · {age}\n{state}',
 'qt.snapshot.hint':'读取完成：{time}。时长按单调时钟计算，约15分钟后建议重新读取。此快照不代表设备当前仍在线，不会持续监测设备或自动刷新。需要更新时请使用设备区的“{action}”；重新读取前会清除旧快照。不完整快照仍受原对比、报告及传输限制。',
})
_EN.update({
 'qt.snapshot.now_short':'now', 'qt.snapshot.minutes_short':'{count}m',
 'qt.snapshot.cached_short':'snapshot', 'qt.snapshot.old_short':'re-read', 'qt.snapshot.partial_short':'partial',
 'qt.snapshot.compact':'{time} · {age} · {state}',
 'qt.snapshot.just_now':'less than 1 minute ago', 'qt.snapshot.minutes':'{count} minutes ago',
 'qt.snapshot.cached':'Snapshot · not live',
 'qt.snapshot.old':'Refresh suggested',
 'qt.snapshot.partial':'Incomplete snapshot',
 'qt.snapshot.notice':'Read {time} · {age}\n{state}',
 'qt.snapshot.hint':'Read completed: {time}. Elapsed time uses a monotonic clock; a new read is suggested after about 15 minutes. This snapshot does not confirm the device is still online. There is no continuous device monitoring or automatic refresh. Use “{action}” in the device area to update; starting a new read clears the old snapshot. Existing comparison, report and transfer restrictions still apply to incomplete snapshots.',
})


_ZH.update({
 'qt.inventory.continuation':'读取预算不足且已取得进展时，自动复用缓存补验，最多3轮，每轮预留读取上限256 MiB；无进展即停止。强制重扫只在首轮清缓存，仍不完整的项目保持未知，可取消。',
 'qt.inventory.passes':'已扫描 {count}/{maximum} 轮',
 'qt.inventory.cancel':'取消本地扫描', 'qt.inventory.loading':'本地识别：扫描中…',
 'qt.inventory.cancelled':'本地识别：已取消，可重新扫描', 'qt.inventory.unknown':'本地识别：尚无有效扫描结果',
 'qt.inventory.summary':'已检查 {checked} 个目录 · 有效 {valid} 首 · {state}',
 'qt.inventory.complete':'本次扫描完成', 'qt.inventory.partial':'部分结果，未识别项保持未知',
 'qt.verify.hint':'扫描当前保存目录的非隐藏直接子目录，按原谱面哈希识别精确版本；不按歌名或文件夹名推断其他版本。结果为读取快照（最近：{time}），未匹配不代表歌曲不存在。自动刷新可复用文件指纹未变的结果，手动重新扫描会重算；可取消。存在数量/读取预算，部分结果明确提示。只检查音频文件存在性，不校验其内容，不自动下载、覆盖或删除。',
})
_EN.update({
 'qt.inventory.continuation':'If the read budget is exhausted after making progress, reuse cached results for up to 3 passes, reserving at most 256 MiB per pass. Stop on no progress. A forced rescan clears the cache only on the first pass. Unresolved items remain unknown; cancellation is available.',
 'qt.inventory.passes':'Scanned {count}/{maximum} passes',
 'qt.inventory.cancel':'Cancel local scan', 'qt.inventory.loading':'Local identification: scanning…',
 'qt.inventory.cancelled':'Local identification cancelled; rescan available', 'qt.inventory.unknown':'Local identification: no valid scan result yet',
 'qt.inventory.summary':'Checked {checked} folders · {valid} valid songs · {state}',
 'qt.inventory.complete':'Scan completed', 'qt.inventory.partial':'Partial results; unidentified songs remain unknown',
 'qt.verify.hint':'Scans non-hidden direct child folders in the current destination. Exact versions use the existing map hash; titles and folder names do not establish other-version identity. Results are read-time snapshots (last: {time}); no match does not prove absence. Automatic refresh may reuse unchanged file fingerprints; manual rescan recalculates. Scans can be cancelled and have count/read budgets; partial results are explicit. Audio file presence, not its contents, is checked. No automatic download, overwrite or deletion.',
})


_ZH.update({
 'qt.inventory.badge_busy':'本地识别中 · 可在选项中取消',
 'qt.inventory.badge_cancelled':'本地识别已取消 · 选项中可重扫',
 'qt.inventory.badge_failed':'本地识别失败 · 请检查保存目录',
 'qt.inventory.badge_partial':'本地已识别 {count} 首 · 部分结果',
 'qt.inventory.badge_done':'本地已识别 {count} 首 · {time}',
 'qt.inventory.badge_unknown':'本地识别结果尚未知',
 'qt.inventory.failed':'本地识别失败，未识别项保持未知',
 'qt.inventory.scope':'当前保存目录：{path}。未匹配不等于未安装；其他目录与未识别项不作判断。可在选项中重新扫描，取消只停止版本识别，不取消下载或现有列表刷新。',
})
_EN.update({
 'qt.inventory.badge_busy':'Identifying local songs · cancel in Options',
 'qt.inventory.badge_cancelled':'Local identification cancelled · rescan in Options',
 'qt.inventory.badge_failed':'Local identification failed · check destination',
 'qt.inventory.badge_partial':'{count} local songs identified · partial results',
 'qt.inventory.badge_done':'{count} local songs identified · {time}',
 'qt.inventory.badge_unknown':'Local identification is not available yet',
 'qt.inventory.failed':'Local identification failed; unidentified songs remain unknown',
 'qt.inventory.scope':'Current destination: {path}. No match does not mean not installed; other folders and unidentified songs remain unknown. Rescan from Options. Cancel stops version identification only, not downloads or the existing local-list refresh.',
})


_ZH.update({
 'qt.browse_pref.saved':'启动分类：{category}',
 'qt.browse_pref.save':'将当前分类设为启动分类',
 'qt.browse_pref.reset':'启动分类恢复精选推荐',
 'qt.browse_pref.error':'浏览偏好未能读取或保存；原文件保留',
 'qt.browse_pref.hint':'仅保存下次打开的推荐分类，不立即切页。搜索歌曲和随便听听不可设为启动分类；不保存搜索词、个人筛选、排序或勾选。配置异常时使用精选推荐且不覆盖原文件。',
})
_EN.update({
 'qt.browse_pref.saved':'Startup category: {category}',
 'qt.browse_pref.save':'Use current category at startup',
 'qt.browse_pref.reset':'Use curated picks at startup',
 'qt.browse_pref.error':'Browse preference could not be read or saved; original file retained',
 'qt.browse_pref.hint':'Saves only the recommendation category for the next window; does not switch this page. Search and Random cannot be startup categories. Queries, personal filters, sort and selections are not saved. Invalid preferences fall back to Curated without overwriting the file.',
})


_ZH.update({
 'qt.favorites.search':'搜索收藏歌曲…',
 'qt.favorites.toggle':'收藏当前歌曲（勾选表示已收藏）',
 'qt.favorites.open':'收藏清单（{count}）', 'qt.favorites.title':'歌曲收藏清单',
 'qt.favorites.summary':'共收藏 {count} 首 · 收藏与下载勾选分开保存',
 'qt.favorites.select':'加入已选（{count}）', 'qt.favorites.selected':'已将所选 {count} 首加入跨页已选清单。',
 'qt.favorites.selection_limit':'加入后会超过200首勾选上限，请先减少已选歌曲。',
 'qt.favorites.remove':'取消收藏', 'qt.favorites.remove_hint':'只移除所选收藏，不取消下载勾选，不删除歌曲。',
 'qt.favorites.select_hint':'仅把当前选中的收藏加入跨页已选；不会自动下载。',
 'qt.favorites.error':'收藏读取或保存失败（原文件保留，最多200首）',
 'qt.favorites.hint':'最多200首，文件不超过2MiB。保存收藏时的歌曲信息，下载仍按编号获取当前发布版本。封面复用已有缓存，试听使用底部共享播放器；加入已选只处理当前选中项。收藏不代表已安装，不自动下载、覆盖或删除歌曲。',
})
_EN.update({
 'qt.favorites.search':'Search favorite songs…',
 'qt.favorites.toggle':'Favorite current song (checked when saved)',
 'qt.favorites.open':'Favorites ({count})', 'qt.favorites.title':'Favorite songs',
 'qt.favorites.summary':'{count} favorites · Stored separately from download selections',
 'qt.favorites.select':'Add to selection ({count})', 'qt.favorites.selected':'Added the selected {count} songs to the cross-page selection.',
 'qt.favorites.selection_limit':'This would exceed 200 selected songs. Reduce the current selection first.',
 'qt.favorites.remove':'Remove favorites', 'qt.favorites.remove_hint':'Removes selected bookmarks only; download selections and song files stay intact.',
 'qt.favorites.select_hint':'Adds only selected favorites to the cross-page selection; does not start downloads.',
 'qt.favorites.error':'Favorites could not be read or saved (file retained; maximum 200)',
 'qt.favorites.hint':'Maximum 200 songs and a 2 MiB file. Metadata is a saved snapshot; downloads resolve the current published version by map ID. Covers reuse existing cache; preview uses the shared bottom player. Add to selection affects only selected rows. Favorites do not establish installation or automatically download, overwrite or delete songs.',
})

_ZH.update({'qt.ui.favorite':'收藏', 'qt.ui.favorited':'已收藏',
            'qt.ui.snapshot_count':'{count} 首 · 只读快照'})
_EN.update({'qt.ui.favorite':'Favorite', 'qt.ui.favorited':'Saved',
            'qt.ui.snapshot_count':'{count} songs · Read-only snapshot'})

_ZH.update({
 'qt.device.directory.songcore':'SongCore 普通歌曲',
 'qt.device.directory.songloader':'SongLoader 兼容目录',
 'qt.device.directory.wip':'WIP 测试歌曲',
 'qt.device.directory.songcore_hint':'SongCore 的普通自定义歌曲目录。目录存在不代表游戏正在使用它；请按安装的模组选择。',
 'qt.device.directory.songloader_hint':'SongLoader 使用的兼容目录。可能保留旧歌曲；不会自动合并、移动或删除。',
 'qt.device.directory.wip_hint':'制作中谱面和测试歌曲目录。普通歌曲通常放在 CustomLevels；本工具不会自动切换目录。',
})
_ZH.update({
 'qt.device.media.lazy':'资料已就绪；封面和缺失时长按浏览位置补充',
 'qt.library.artist_bpm':'{artist} · {bpm} BPM',
 'qt.device.media.loading':'正在补充当前歌曲的封面和时长…',
 'qt.device.media.changed':'部分歌曲资料已变化，请重新读取曲库',
 'qt.device.media.failed':'部分封面或时长未能补充，可重新读取重试',
})
_EN.update({
 'qt.device.media.lazy':'Metadata ready; covers and missing durations load as you browse',
 'qt.library.artist_bpm':'{artist} · {bpm} BPM',
 'qt.device.media.loading':'Loading covers and durations for the current songs…',
 'qt.device.media.changed':'Some song metadata changed. Read the library again.',
 'qt.device.media.failed':'Some covers or durations could not load. Read again to retry.',
})
_EN.update({
 'qt.device.directory.songcore':'SongCore songs',
 'qt.device.directory.songloader':'SongLoader compatibility folder',
 'qt.device.directory.wip':'WIP test maps',
 'qt.device.directory.songcore_hint':'Regular custom songs for SongCore. Folder existence does not prove it is active in the game; choose for your installed mod.',
 'qt.device.directory.songloader_hint':'Compatibility folder used by SongLoader. It may contain older songs; nothing is automatically merged, moved or deleted.',
 'qt.device.directory.wip_hint':'Work-in-progress and test maps. Regular songs usually belong in CustomLevels; this tool does not switch folders automatically.',
})

_ZH.update({
 'qt.wireless.title':'无线连接与歌曲传输', 'qt.wireless.open':'无线连接',
 'qt.wireless.method':'连接方式', 'qt.wireless.mode.usb':'USB 转无线（首次使用推荐）',
 'qt.wireless.mode.connect':'已有地址，重新连接', 'qt.wireless.mode.pair':'配对码连接（设备支持时）',
 'qt.wireless.intro':'电脑与 Quest 连接同一局域网，保持头显唤醒并解锁。选择方式后点击连接；打开窗口不会自动连接。',
 'qt.wireless.hint.usb':'1. 用 USB 数据线连接 Quest，并在头显内允许 USB 调试。\n2. 检查并选中你的 Quest；IP 留空时自动读取。\n3. 点击开启无线并连接。确认连接成功后再拔线；重启头显后可能需要重新开启。',
 'qt.wireless.hint.connect':'填写 Quest 当前的 Wi-Fi IPv4 地址和连接端口。USB 转无线通常使用 5555；无线调试页面的连接端口以设备显示为准。',
 'qt.wireless.hint.pair':'仅在头显提供“无线调试 / 使用配对码配对”时使用。填写该页的 IP、配对端口及六位码，并单独填写无线调试主页面的连接端口；两个端口通常不同。',
 'qt.wireless.device':'USB Quest', 'qt.wireless.scan':'检查 USB 设备', 'qt.wireless.host':'Quest IP 地址',
 'qt.wireless.port':'连接端口', 'qt.wireless.pair_port':'配对端口', 'qt.wireless.code':'六位配对码',
 'qt.wireless.run.usb':'开启无线并连接', 'qt.wireless.run.connect':'连接 Quest', 'qt.wireless.run.pair':'配对并连接',
 'qt.wireless.cancel':'停止后续步骤', 'qt.wireless.close':'返回曲库', 'qt.wireless.transfers':'显示歌曲传输按钮（本次运行）',
 'qt.wireless.transfer_hint':'连接成功后返回曲库，选择歌曲目录并读取。显示传输按钮后，可勾选歌曲向 Quest 传入或导出到本地；每次传输仍需确认来源、目录和数量。已有歌曲默认跳过，目录读取不完整时禁止传输。',
 'qt.wireless.wait':'请等待当前连接或歌曲传输操作结束。',
 'qt.wireless.state.wireless_input':'请选择连接方式。', 'qt.wireless.state.wireless_address':'请输入有效的设备 IPv4 地址；USB 模式可留空自动读取。',
 'qt.wireless.state.wireless_port':'端口必须是 1 至 65535 的整数。', 'qt.wireless.state.wireless_code':'请输入有效的六位配对码。',
 'qt.wireless.state.wireless_usb':'请检查 USB 设备并选择已授权的 Quest。',
})
_EN.update({
 'qt.wireless.title':'Wireless connection and song transfers', 'qt.wireless.open':'Wireless',
 'qt.wireless.method':'Connection method', 'qt.wireless.mode.usb':'USB to Wi-Fi (recommended for first use)',
 'qt.wireless.mode.connect':'Reconnect using an address', 'qt.wireless.mode.pair':'Pairing code (when supported)',
 'qt.wireless.intro':'Connect the PC and Quest to the same LAN. Keep the headset awake and unlocked. Choose a method and click Connect; opening this window does not connect.',
 'qt.wireless.hint.usb':'1. Connect Quest by USB and allow USB debugging inside the headset.\n2. Check and select your Quest. Leave IP blank for automatic detection.\n3. Click Enable Wi-Fi and connect. Unplug only after success; a headset restart may require setup again.',
 'qt.wireless.hint.connect':'Enter the current Quest Wi-Fi IPv4 address and connection port. USB to Wi-Fi usually uses 5555; for wireless debugging use the connection port shown on the device.',
 'qt.wireless.hint.pair':'Use only when the headset offers Wireless debugging / Pair with pairing code. Enter its IP, pairing port and six-digit code, plus the connection port from the main wireless debugging screen. The ports usually differ.',
 'qt.wireless.device':'USB Quest', 'qt.wireless.scan':'Check USB devices', 'qt.wireless.host':'Quest IP address',
 'qt.wireless.port':'Connection port', 'qt.wireless.pair_port':'Pairing port', 'qt.wireless.code':'Six-digit pairing code',
 'qt.wireless.run.usb':'Enable Wi-Fi and connect', 'qt.wireless.run.connect':'Connect Quest', 'qt.wireless.run.pair':'Pair and connect',
 'qt.wireless.cancel':'Stop remaining steps', 'qt.wireless.close':'Return to library', 'qt.wireless.transfers':'Show song transfer buttons (this session)',
 'qt.wireless.transfer_hint':'After connecting, return to the library, choose a song folder and read it. Enable the transfer buttons to send checked songs to Quest or import them locally. Each transfer still confirms source, folder and count. Existing songs are skipped by default; incomplete directory snapshots block transfers.',
 'qt.wireless.wait':'Wait for the current connection or song transfer to finish.',
 'qt.wireless.state.wireless_input':'Choose a connection method.', 'qt.wireless.state.wireless_address':'Enter a valid device IPv4 address, or leave it blank for automatic USB detection.',
 'qt.wireless.state.wireless_port':'Port must be an integer from 1 to 65535.', 'qt.wireless.state.wireless_code':'Enter a valid six-digit pairing code.',
 'qt.wireless.state.wireless_usb':'Check USB devices and select an authorized Quest.',
})

_ZH.update({
 'qt.wireless.state.scanning':'正在检查 USB Quest…',
 'qt.wireless.state.working':'正在连接并核对 Quest 身份，请保持头显唤醒…',
 'qt.wireless.state.cancelling':'正在停止后续步骤，等待当前命令结束…',
 'qt.wireless.state.cancelled':'已停止后续步骤。已开启的无线调试或已完成的配对不会自动撤销。',
 'qt.wireless.state.usb_ready':'已找到 USB Quest。确认设备后点击“开启无线并连接”。',
 'qt.wireless.state.no_usb':'未找到已授权的 USB Quest。请检查数据线、开机解锁和头显内的 USB 调试授权。',
 'qt.wireless.state.connected':'已连接并核验 Quest：{address}。可拔掉 USB；返回曲库后选择目录并点击读取。',
 'qt.wireless.state.transfers':'歌曲传输按钮已显示。返回曲库，读取本地及完整的 Quest 曲库后勾选歌曲。',
 'qt.wireless.state.wireless_pair':'配对失败。检查配对端口和六位码是否过期；连接端口与配对端口需分别填写。',
 'qt.wireless.state.wireless_tcpip':'开启无线调试失败。请重新连接并授权 USB Quest，再重试。',
 'qt.wireless.state.wireless_connect':'未能连接。检查同一局域网、IP 和连接端口，确认头显唤醒；已配对不等于已连接。',
 'qt.wireless.state.wireless_command':'设备命令未完成。检查 USB / 网络连接后重试；已完成的步骤不会自动撤销。',
 'qt.wireless.state.device_rejected':'设备身份核验失败或已变化，请重新检查设备。未接收该设备为歌曲来源。',
 'qt.wireless.state.device_adb_path':'无法使用 ADB，请检查程序配置的 ADB 路径。',
 'qt.wireless.state.device_timeout':'连接超时，请确认头显已唤醒且网络可达后重试。',
 'qt.wireless.state.failed':'操作未完成，请检查设备连接与授权后重试。',
})
_EN.update({
 'qt.wireless.state.scanning':'Checking USB Quest devices…',
 'qt.wireless.state.working':'Connecting and verifying Quest identity. Keep the headset awake…',
 'qt.wireless.state.cancelling':'Stopping remaining steps; waiting for the current command…',
 'qt.wireless.state.cancelled':'Remaining steps stopped. Wireless debugging or pairing already completed is not automatically undone.',
 'qt.wireless.state.usb_ready':'USB Quest found. Confirm the device, then click Enable Wi-Fi and connect.',
 'qt.wireless.state.no_usb':'No authorized USB Quest found. Check the data cable, unlock the headset and allow USB debugging inside it.',
 'qt.wireless.state.connected':'Quest connected and verified: {address}. You can unplug USB. Return to the library, choose a folder and click Read.',
 'qt.wireless.state.transfers':'Song transfer buttons are visible. Return to the library, read the local and complete Quest libraries, then check songs.',
 'qt.wireless.state.wireless_pair':'Pairing failed. Check the pairing port and code expiry. Enter the connection and pairing ports separately.',
 'qt.wireless.state.wireless_tcpip':'Could not enable wireless debugging. Reconnect and authorize the USB Quest, then retry.',
 'qt.wireless.state.wireless_connect':'Could not connect. Check the LAN, IP and connection port and keep the headset awake. Paired does not mean connected.',
 'qt.wireless.state.wireless_command':'Device command did not complete. Check USB / network and retry. Completed steps are not automatically undone.',
 'qt.wireless.state.device_rejected':'Device identity verification failed or changed. Check the device again; it was not accepted as a song source.',
 'qt.wireless.state.device_adb_path':'ADB is unavailable. Check the configured ADB path.',
 'qt.wireless.state.device_timeout':'Connection timed out. Keep the headset awake and check its network, then retry.',
 'qt.wireless.state.failed':'The operation did not complete. Check the connection and device authorization, then retry.',
})
