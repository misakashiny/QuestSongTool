"""
Quest歌单对比工具 - 增强版
功能：对比PC本地和Quest设备上的Beat Saber自定义歌曲
作者：基于您的需求进行开发
版本：13.5
修复：
1. 修复takefocus参数问题（customtkinter不支持）
2. 修复字符串转义警告
3. 修复焦点设置问题
"""

import customtkinter as ctk
from tkinter import filedialog, StringVar, messagebox
import os
import json
import sqlite3
import threading
import queue
import shutil
from datetime import datetime
import sys
import traceback
import logging

# ================ 全局配置 ================
APP_NAME = "🎵 Quest歌单对比工具 V13.5"  # 应用程序名称
WINDOW_SIZE = "1500x950"  # 窗口初始大小
MIN_SIZE = (1300, 850)  # 窗口最小尺寸
FONT_FAMILY = "Microsoft YaHei"  # 字体设置
COVER_SIZE = (300, 250)  # 封面固定尺寸

# ================ 日志配置 ================
def setup_logging():
    """配置日志系统"""
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, f"song_comparator_{datetime.now().strftime('%Y%m%d')}.log")

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

    return logging.getLogger(__name__)

logger = setup_logging()

# ================ 自定义列表框类 ================
class SelectableListBox(ctk.CTkScrollableFrame):
    """支持高亮选中、方向键导航和复制到剪贴板的列表框，带排序功能"""

    def __init__(self, master, title="", width=300, height=500, on_sort_changed=None, **kwargs):
        """
        初始化列表框

        参数:
            master: 父窗口
            title: 列表框标题
            width: 宽度
            height: 高度
            on_sort_changed: 排序方式改变回调函数
        """
        super().__init__(master, width=width, height=height, **kwargs)

        self.title = title
        self.width = width
        self.height = height
        self.songs = []  # 存储所有歌曲
        self.original_songs = []  # 原始歌曲列表（不排序）
        self.filtered_songs = []  # 存储过滤后的歌曲
        self.selected_index = -1  # 当前选中的索引
        self.on_select_callback = None  # 选中回调函数
        self.on_delete_callback = None  # 删除回调函数
        self.on_sort_changed = on_sort_changed  # 排序方式改变回调
        self.item_widgets = []  # 存储列表项控件
        self.parent_app = master.winfo_toplevel()  # 获取主应用窗口
        self.highlighted_items = set()  # 存储需要高亮的项目索引

        # 排序相关
        self.sort_mode = "default"  # 当前排序模式：default, name, rating
        self.sort_reverse = False   # 排序顺序：False为正序，True为反序
        self.song_ratings = {}  # 歌曲评分缓存
        self.song_names = {}  # 歌曲名称缓存

        # 创建界面
        self._create_widgets()
        self._bind_events()

    def _create_widgets(self):
        """创建列表框的UI控件"""
        # 标题框架
        self.title_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.title_frame.grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="ew")

        # 标题标签
        self.title_label = ctk.CTkLabel(
            self.title_frame,
            text=self.title,
            font=(FONT_FAMILY, 14, "bold"),
            anchor="w"
        )
        self.title_label.pack(side="left", fill="x", expand=True)

        # 排序按钮框架
        self.sort_frame = ctk.CTkFrame(self.title_frame, fg_color="transparent")
        self.sort_frame.pack(side="right", padx=(5, 0))

        # 默认排序按钮
        self.default_sort_btn = ctk.CTkButton(
            self.sort_frame,
            text="🔢",
            width=30,
            height=25,
            font=(FONT_FAMILY, 12),
            fg_color="#3498db" if self.sort_mode == "default" else ("gray75", "gray35"),
            hover_color="#2980b9"
        )
        self.default_sort_btn.pack(side="left", padx=(0, 3))

        # 绑定左右键事件
        self.default_sort_btn.bind("<Button-1>", lambda e: self.set_sort_mode("default", reverse=False))
        self.default_sort_btn.bind("<Button-3>", lambda e: self.set_sort_mode("default", reverse=True))

        # 名称排序按钮
        self.name_sort_btn = ctk.CTkButton(
            self.sort_frame,
            text="🔤",
            width=30,
            height=25,
            font=(FONT_FAMILY, 12),
            fg_color="#3498db" if self.sort_mode == "name" else ("gray75", "gray35"),
            hover_color="#2980b9"
        )
        self.name_sort_btn.pack(side="left", padx=(0, 3))

        # 绑定左右键事件
        self.name_sort_btn.bind("<Button-1>", lambda e: self.set_sort_mode("name", reverse=False))
        self.name_sort_btn.bind("<Button-3>", lambda e: self.set_sort_mode("name", reverse=True))

        # 评分排序按钮
        self.rating_sort_btn = ctk.CTkButton(
            self.sort_frame,
            text="⭐",
            width=30,
            height=25,
            font=(FONT_FAMILY, 12),
            fg_color="#3498db" if self.sort_mode == "rating" else ("gray75", "gray35"),
            hover_color="#2980b9"
        )
        self.rating_sort_btn.pack(side="left")

        # 绑定左右键事件
        self.rating_sort_btn.bind("<Button-1>", lambda e: self.set_sort_mode("rating", reverse=False))
        self.rating_sort_btn.bind("<Button-3>", lambda e: self.set_sort_mode("rating", reverse=True))

        # 搜索框
        self.search_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.search_frame.grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew")

        self.search_var = StringVar()
        self.search_entry = ctk.CTkEntry(
            self.search_frame,
            placeholder_text="搜索歌曲...",
            textvariable=self.search_var,
            width=self.width - 70
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

        # 内容容器
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.grid(row=2, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")

        # 设置网格权重
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

    def _bind_events(self):
        """绑定事件处理函数"""
        # 搜索框文本变化时过滤歌曲
        self.search_var.trace("w", lambda *args: self._filter_songs())

        # 绑定键盘事件到列表框本身
        self.bind("<KeyPress-Up>", self._on_key_up)
        self.bind("<KeyPress-Down>", self._on_key_down)
        self.bind("<Return>", self._on_key_enter)
        self.bind("<Delete>", self._on_key_delete)

        # 同时绑定到内容框架，确保点击也能响应
        self.content_frame.bind("<KeyPress-Up>", self._on_key_up)
        self.content_frame.bind("<KeyPress-Down>", self._on_key_down)
        self.content_frame.bind("<Return>", self._on_key_enter)
        self.content_frame.bind("<Delete>", self._on_key_delete)

    def _on_key_up(self, event):
        """
        处理上方向键事件

        参数:
            event: 键盘事件
        返回:
            "break" 阻止事件传播
        """
        if self.item_widgets:
            new_index = self.selected_index - 1 if self.selected_index > 0 else 0
            self._select_item(new_index)
            self._trigger_selection()
        return "break"  # 阻止默认行为

    def _on_key_down(self, event):
        """
        处理下方向键事件

        参数:
            event: 键盘事件
        返回:
            "break" 阻止事件传播
        """
        if self.item_widgets:
            new_index = self.selected_index + 1 if self.selected_index < len(self.item_widgets) - 1 else len(self.item_widgets) - 1
            self._select_item(new_index)
            self._trigger_selection()
        return "break"  # 阻止默认行为

    def _on_key_enter(self, event):
        """
        处理回车键事件

        参数:
            event: 键盘事件
        返回:
            "break" 阻止事件传播
        """
        if self.selected_index >= 0:
            self._trigger_selection()
        return "break"

    def _on_key_delete(self, event):
        """
        处理Delete键事件

        参数:
            event: 键盘事件
        返回:
            "break" 阻止事件传播
        """
        if self.selected_index >= 0:
            self._trigger_delete()
        return "break"

    def set_songs(self, songs, song_ratings=None, song_names=None):
        """
        设置歌曲列表

        参数:
            songs: 歌曲列表
            song_ratings: 歌曲评分字典
            song_names: 歌曲名称字典
        """
        self.original_songs = songs.copy()
        self.songs = songs.copy()

        # 更新缓存
        if song_ratings:
            self.song_ratings = song_ratings
        if song_names:
            self.song_names = song_names

        # 应用当前排序
        self._apply_sort()

        self.filtered_songs = self.songs.copy()
        self.selected_index = -1
        self._display_songs()

        # 设置焦点，确保键盘事件能正常工作
        self.focus_set()

    def _display_songs(self):
        """显示歌曲列表"""
        # 清除现有控件
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        self.item_widgets = []

        # 创建新的项目
        for i, song in enumerate(self.filtered_songs):
            self._create_song_item(i, song)

        # 如果有项目，设置焦点并选中第一个
        if self.filtered_songs:
            self._select_item(0)

    def _create_song_item(self, index, song):
        """
        创建单个歌曲项

        参数:
            index: 歌曲索引
            song: 歌曲名称
        """
        # 创建框架
        item_frame = ctk.CTkFrame(
            self.content_frame,
            height=32,
            corner_radius=5,
            fg_color=("gray90", "gray25")
        )
        item_frame.grid(row=index, column=0, padx=5, pady=2, sticky="ew")
        item_frame.grid_propagate(False)

        # 如果该项目需要高亮，设置为红色
        if index in self.highlighted_items:
            item_frame.configure(fg_color=("red", "darkred"))
            item_frame.is_highlighted = True
        else:
            item_frame.is_highlighted = False

        # 歌曲名称标签
        song_label = ctk.CTkLabel(
            item_frame,
            text=song,
            font=(FONT_FAMILY, 12),
            anchor="w"
        )
        song_label.pack(side="left", padx=10, fill="both", expand=True)

        # 绑定事件 - 让每个项目都能接收点击事件并传递焦点
        for widget in [item_frame, song_label]:
            widget.bind("<Button-1>", lambda e, idx=index: self._on_item_click(idx))
            widget.bind("<FocusIn>", lambda e: self.focus_set())

        # 存储引用
        item_frame.index = index
        item_frame.song = song
        self.item_widgets.append(item_frame)

    def _on_item_click(self, index):
        """
        处理项目点击事件

        参数:
            index: 点击的索引
        """
        self._select_item(index)
        self._trigger_selection()

    def _select_item(self, index):
        """
        选中指定项

        参数:
            index: 要选中的索引
        """
        # 清除之前的高亮
        for widget in self.item_widgets:
            if hasattr(widget, 'is_selected'):
                # 如果是高亮项目，保持红色
                if hasattr(widget, 'is_highlighted') and widget.is_highlighted:
                    widget.configure(fg_color=("red", "darkred"))
                else:
                    widget.configure(fg_color=("gray90", "gray25"))
                widget.is_selected = False

        # 高亮当前项
        if 0 <= index < len(self.item_widgets):
            # 如果是高亮项目，选中时稍微变亮
            if hasattr(self.item_widgets[index], 'is_highlighted') and self.item_widgets[index].is_highlighted:
                self.item_widgets[index].configure(fg_color=("#FF6666", "#990000"))
            else:
                self.item_widgets[index].configure(fg_color=("gray75", "gray50"))
            self.item_widgets[index].is_selected = True
            self.selected_index = index

            # 复制到剪贴板
            selected_song = self.filtered_songs[index]
            self.parent_app.clipboard_clear()
            self.parent_app.clipboard_append(selected_song)

    def _trigger_selection(self):
        """触发选择事件"""
        if self.selected_index >= 0 and self.on_select_callback:
            selected_song = self.filtered_songs[self.selected_index]
            self.on_select_callback(selected_song)

    def _trigger_delete(self):
        """触发删除事件"""
        if self.selected_index >= 0 and self.on_delete_callback:
            selected_song = self.filtered_songs[self.selected_index]
            self.on_delete_callback(selected_song)

    def _filter_songs(self):
        """根据搜索文本过滤歌曲列表"""
        search_text = self.search_var.get().lower()
        if not search_text:
            self.filtered_songs = self.songs.copy()
        else:
            self.filtered_songs = [song for song in self.songs if search_text in song.lower()]

        self._display_songs()

    def _apply_sort(self):
        """根据当前排序模式对歌曲进行排序"""
        if self.sort_mode == "default":
            # 默认排序：使用原始顺序
            self.songs = self.original_songs.copy()
            if self.sort_reverse:
                self.songs.reverse()
        elif self.sort_mode == "name":
            # 按歌曲名称排序（从文件夹名中提取）
            self.songs.sort(key=self._extract_song_name, reverse=self.sort_reverse)
        elif self.sort_mode == "rating":
            # 按评分排序
            self.songs.sort(key=self._get_song_rating, reverse=not self.sort_reverse)  # 评分高在前

    def _extract_song_name(self, folder_name):
        """
        从文件夹名称中提取歌曲名称

        参数:
            folder_name: 文件夹名称

        返回:
            提取的歌曲名称
        """
        # 如果有缓存的歌曲名称，使用缓存
        if folder_name in self.song_names:
            return self.song_names[folder_name].lower()

        # 尝试从文件夹名中提取歌曲名
        import re

        # 尝试匹配括号内的内容
        match = re.search(r'\(([^)]+)\)', folder_name)
        if match:
            # 如果有括号，取括号内的内容
            song_name = match.group(1).strip()
        else:
            # 如果没有括号，尝试用"-"分割
            parts = folder_name.split('-')
            if len(parts) > 1:
                song_name = parts[0].strip()
            else:
                song_name = folder_name.strip()

        # 缓存结果
        self.song_names[folder_name] = song_name
        return song_name.lower()

    def _get_song_rating(self, folder_name):
        """
        获取歌曲评分

        参数:
            folder_name: 文件夹名称

        返回:
            歌曲评分，默认为0
        """
        return self.song_ratings.get(folder_name, 0)

    def set_on_select(self, callback):
        """
        设置选择回调函数

        参数:
            callback: 回调函数
        """
        self.on_select_callback = callback

    def set_on_delete(self, callback):
        """
        设置删除回调函数

        参数:
            callback: 回调函数
        """
        self.on_delete_callback = callback

    def set_sort_mode(self, mode, reverse=False):
        """
        设置排序模式

        参数:
            mode: 排序模式，可选值：default, name, rating
            reverse: 是否反序排序
        """
        # 如果是相同的模式和顺序，只切换反序
        if mode == self.sort_mode:
            self.sort_reverse = reverse
        else:
            self.sort_mode = mode
            self.sort_reverse = reverse

        # 更新按钮颜色和文本
        self._update_sort_buttons()

        # 应用排序
        self._apply_sort()
        self.filtered_songs = self.songs.copy()
        self._display_songs()

        # 调用排序改变回调
        if self.on_sort_changed:
            self.on_sort_changed(mode, reverse)

    def _update_sort_buttons(self):
        """更新排序按钮的显示"""
        # 重置所有按钮颜色
        self.default_sort_btn.configure(fg_color=("gray75", "gray35"))
        self.name_sort_btn.configure(fg_color=("gray75", "gray35"))
        self.rating_sort_btn.configure(fg_color=("gray75", "gray35"))

        # 设置当前选中按钮的颜色
        if self.sort_mode == "default":
            self.default_sort_btn.configure(fg_color="#3498db")
            # 添加方向指示
            arrow = "↓" if self.sort_reverse else "↑"
            self.default_sort_btn.configure(text=f"🔢{arrow}")
        else:
            self.default_sort_btn.configure(text="🔢")

        if self.sort_mode == "name":
            self.name_sort_btn.configure(fg_color="#3498db")
            arrow = "↓" if self.sort_reverse else "↑"
            self.name_sort_btn.configure(text=f"🔤{arrow}")
        else:
            self.name_sort_btn.configure(text="🔤")

        if self.sort_mode == "rating":
            self.rating_sort_btn.configure(fg_color="#3498db")
            arrow = "↓" if self.sort_reverse else "↑"
            self.rating_sort_btn.configure(text=f"⭐{arrow}")
        else:
            self.rating_sort_btn.configure(text="⭐")

    def highlight_items(self, indices):
        """
        高亮指定索引的项目

        参数:
            indices: 需要高亮的索引列表
        """
        self.highlighted_items = set(indices)
        self._update_highlighted_items()

    def clear_highlights(self):
        """清除所有高亮"""
        self.highlighted_items.clear()
        self._update_highlighted_items()

    def _update_highlighted_items(self):
        """更新高亮状态"""
        for i, widget in enumerate(self.item_widgets):
            if i in self.highlighted_items:
                widget.configure(fg_color=("red", "darkred"))
                widget.is_highlighted = True
            else:
                widget.is_highlighted = False
                # 如果是选中状态，保持选中颜色
                if hasattr(widget, 'is_selected') and widget.is_selected:
                    widget.configure(fg_color=("gray75", "gray50"))
                else:
                    widget.configure(fg_color=("gray90", "gray25"))

# ================ 数据库管理器 ================
class DatabaseManager:
    """数据库管理类，用于缓存歌曲信息和用户评分"""

    def __init__(self):
        """初始化数据库管理器"""
        self.db_path = "songs_cache.db"
        self._init_database()

    def _init_database(self):
        """初始化数据库表结构"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 创建歌曲信息表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS songs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_name TEXT UNIQUE,
                    title TEXT,
                    subtitle TEXT,
                    author TEXT,
                    version TEXT,
                    bpm REAL,
                    difficulties TEXT,
                    path TEXT,
                    cover_path TEXT,
                    last_modified TIMESTAMP,
                    file_mtime REAL,
                    rating INTEGER DEFAULT 0,
                    comment TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 检查并添加缺失的字段（如果表已存在但字段缺失）
            cursor.execute("PRAGMA table_info(songs)")
            columns = [column[1] for column in cursor.fetchall()]

            # 检查并添加rating字段
            if 'rating' not in columns:
                cursor.execute('ALTER TABLE songs ADD COLUMN rating INTEGER DEFAULT 0')
                logger.info("已添加rating字段到数据库")

            # 检查并添加comment字段
            if 'comment' not in columns:
                cursor.execute('ALTER TABLE songs ADD COLUMN comment TEXT DEFAULT ""')
                logger.info("已添加comment字段到数据库")

            conn.commit()
            conn.close()
            logger.info("数据库初始化成功")
        except sqlite3.Error as e:
            logger.error(f"数据库初始化错误: {e}")

    def get_song_info(self, folder_name):
        """
        从数据库获取歌曲信息

        参数:
            folder_name: 文件夹名称

        返回:
            歌曲信息字典或None
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                SELECT title, subtitle, author, version, bpm, difficulties, 
                       cover_path, rating, comment
                FROM songs WHERE folder_name = ?
            ''', (folder_name,))

            result = cursor.fetchone()
            conn.close()

            if result:
                # 从数据库获取难度信息（字符串格式）
                difficulties_str = result[5] or ""
                # 将字符串转换为列表（用逗号分隔）
                difficulties_list = []
                if difficulties_str and difficulties_str != "未知" and difficulties_str != "无":
                    difficulties_list = [d.strip() for d in difficulties_str.split(',') if d.strip()]

                return {
                    'title': result[0] or "",
                    'subtitle': result[1] or "",
                    'author': result[2] or "",
                    'version': result[3] or "",
                    'bpm': result[4] or "",
                    'difficulties': difficulties_str,  # 字符串格式
                    'difficulties_list': difficulties_list,  # 列表格式
                    'cover_path': result[6] or "",
                    'rating': result[7] or 0,
                    'comment': result[8] or ""
                }
        except sqlite3.Error as e:
            logger.error(f"获取歌曲信息错误: {e}")
        return None

    def save_song_info(self, folder_name, song_info, folder_path, cover_path, file_mtime):
        """
        保存歌曲信息到数据库

        参数:
            folder_name: 文件夹名称
            song_info: 歌曲信息字典
            folder_path: 文件夹路径
            cover_path: 封面路径
            file_mtime: 文件修改时间

        返回:
            成功返回True，失败返回False
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 从歌曲信息中获取难度（字符串格式）
            difficulties_str = song_info.get('difficulties', '')
            # 确保难度是字符串格式
            if isinstance(difficulties_str, list):
                difficulties_str = ', '.join(difficulties_str)

            # 获取已有的评分和评价
            existing_rating = 0
            existing_comment = ""
            try:
                cursor.execute('SELECT rating, comment FROM songs WHERE folder_name = ?', (folder_name,))
                existing_data = cursor.fetchone()
                if existing_data:
                    existing_rating = existing_data[0] or 0
                    existing_comment = existing_data[1] or ""
            except sqlite3.Error:
                pass  # 如果字段不存在，使用默认值

            cursor.execute('''
                INSERT OR REPLACE INTO songs 
                (folder_name, title, subtitle, author, version, bpm, difficulties, 
                 path, cover_path, last_modified, file_mtime, rating, comment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                folder_name,
                song_info.get('title', ''),
                song_info.get('subtitle', ''),
                song_info.get('author', ''),
                song_info.get('version', ''),
                song_info.get('bpm', 0),
                difficulties_str,
                folder_path,
                cover_path,
                datetime.now(),
                file_mtime,
                existing_rating,
                existing_comment
            ))

            conn.commit()
            conn.close()
            logger.debug(f"保存歌曲信息: {folder_name}")
            return True
        except sqlite3.Error as e:
            logger.error(f"保存歌曲信息错误: {e}")
            return False

    def update_song_rating(self, folder_name, rating):
        """
        更新歌曲评分

        参数:
            folder_name: 文件夹名称
            rating: 评分(0-5)

        返回:
            成功返回True，失败返回False
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE songs SET rating = ?, last_modified = ?
                WHERE folder_name = ?
            ''', (rating, datetime.now(), folder_name))

            conn.commit()
            conn.close()
            logger.info(f"更新评分: {folder_name} -> {rating}星")
            return True
        except sqlite3.Error as e:
            logger.error(f"更新评分错误: {e}")
            return False

    def update_song_comment(self, folder_name, comment):
        """
        更新歌曲评价

        参数:
            folder_name: 文件夹名称
            comment: 评价内容

        返回:
            成功返回True，失败返回False
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE songs SET comment = ?, last_modified = ?
                WHERE folder_name = ?
            ''', (comment, datetime.now(), folder_name))

            conn.commit()
            conn.close()
            logger.info(f"更新评价: {folder_name} -> {comment[:20]}...")
            return True
        except sqlite3.Error as e:
            logger.error(f"更新评价错误: {e}")
            return False

    def get_all_song_ratings(self):
        """
        获取所有歌曲的评分

        返回:
            歌曲评分字典 {folder_name: rating}
        """
        ratings = {}
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('SELECT folder_name, rating FROM songs')

            for row in cursor.fetchall():
                ratings[row[0]] = row[1] or 0

            conn.close()
            logger.debug(f"获取到 {len(ratings)} 首歌曲的评分")
        except sqlite3.Error as e:
            logger.error(f"获取歌曲评分错误: {e}")

        return ratings

    def get_all_song_names(self):
        """
        获取所有歌曲的名称（标题）

        返回:
            歌曲名称字典 {folder_name: song_title}
        """
        song_names = {}
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('SELECT folder_name, title FROM songs')

            for row in cursor.fetchall():
                song_names[row[0]] = row[1] or ""

            conn.close()
            logger.debug(f"获取到 {len(song_names)} 首歌曲的名称")
        except sqlite3.Error as e:
            logger.error(f"获取歌曲名称错误: {e}")

        return song_names

# ================ 歌曲管理器 ================
class SongManager:
    """歌曲管理类，负责加载和解析歌曲信息"""

    def __init__(self, db_manager, progress_callback=None):
        """
        初始化歌曲管理器

        参数:
            db_manager: 数据库管理器实例
            progress_callback: 进度回调函数
        """
        self.db_manager = db_manager
        self.progress_callback = progress_callback

    def load_songs_from_path(self, path):
        """
        从指定路径加载歌曲文件夹

        参数:
            path: 歌曲文件夹路径

        返回:
            歌曲文件夹名称列表
        """
        if not os.path.exists(path):
            logger.warning(f"路径不存在: {path}")
            return []

        folders = []
        try:
            items = os.listdir(path)
            total = len(items)

            for i, item in enumerate(items):
                item_path = os.path.join(path, item)

                if os.path.isdir(item_path) and not item.startswith('.'):
                    folders.append(item)

                # 更新进度
                if self.progress_callback:
                    progress = int((i + 1) / total * 100) if total > 0 else 100
                    self.progress_callback(f"扫描: {item[:20]}...", progress)

        except OSError as e:
            logger.error(f"加载歌曲错误: {e}")
            return []

        # 按字母排序
        folders.sort(key=str.lower)
        logger.info(f"从路径 {path} 加载了 {len(folders)} 首歌曲")
        return folders

    def get_song_info(self, folder_path):
        """
        获取歌曲详细信息

        参数:
            folder_path: 歌曲文件夹路径

        返回:
            歌曲信息字典
        """
        info_files = ["info.dat", "Info.dat", "INFO.DAT"]

        for info_file in info_files:
            file_path = os.path.join(folder_path, info_file)
            if os.path.exists(file_path):
                try:
                    # 获取文件修改时间
                    file_mtime = os.path.getmtime(file_path)

                    # 从数据库获取缓存
                    folder_name = os.path.basename(folder_path)
                    cached_info = self.db_manager.get_song_info(folder_name)

                    # 重新解析以确保信息完整
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    # 解析信息
                    info = self._parse_song_info(data, folder_name)

                    # 解析难度信息
                    difficulties_list = self._parse_difficulties(data)
                    # 将列表转换为字符串，用于数据库存储
                    difficulties_str = ', '.join(difficulties_list) if difficulties_list else '未知'
                    info['difficulties'] = difficulties_str  # 字符串格式，用于数据库
                    info['difficulties_list'] = difficulties_list  # 列表格式，用于UI显示

                    # 获取封面
                    cover_path = self._find_cover_image(folder_path)

                    # 保存到数据库（传递字符串格式的难度）
                    self.db_manager.save_song_info(
                        folder_name, info, folder_path, cover_path, file_mtime
                    )

                    # 获取评分和评价
                    if cached_info:
                        info['rating'] = cached_info.get('rating', 0)
                        info['comment'] = cached_info.get('comment', '')
                    else:
                        info['rating'] = 0
                        info['comment'] = ''

                    info['cover_path'] = cover_path
                    logger.debug(f"成功解析歌曲信息: {folder_name}")
                    return info

                except (OSError, json.JSONDecodeError) as e:
                    logger.error(f"读取歌曲信息错误 {file_path}: {e}")
                    # 返回基本信息
                    folder_name = os.path.basename(folder_path)
                    info = {
                        'title': folder_name,
                        'subtitle': '',
                        'author': '未知作者',
                        'version': '未知',
                        'bpm': '未知',
                        'difficulties': '未知',
                        'difficulties_list': [],
                        'cover_path': None,
                        'rating': 0,
                        'comment': ''
                    }
                    return info

        # 如果没有找到info文件，返回基本信息
        folder_name = os.path.basename(folder_path)
        logger.warning(f"未找到info文件: {folder_path}")
        return {
            'title': folder_name,
            'subtitle': '',
            'author': '未知作者',
            'version': '未知',
            'bpm': '未知',
            'difficulties': '未知',
            'difficulties_list': [],
            'cover_path': None,
            'rating': 0,
            'comment': ''
        }

    def _parse_song_info(self, data, folder_name):
        """
        解析歌曲信息

        参数:
            data: JSON数据
            folder_name: 文件夹名称

        返回:
            解析后的歌曲信息字典
        """
        info = {}

        # 版本
        if "_version" in data:
            info["version"] = str(data["_version"])
        elif "version" in data:
            info["version"] = str(data["version"])
        else:
            info["version"] = "未知"

        # 标题
        if "_songName" in data:
            info["title"] = data["_songName"]
        elif "title" in data.get("song", {}):
            info["title"] = data["song"]["title"]
        elif "title" in data:
            info["title"] = data["title"]
        else:
            info["title"] = folder_name

        # 副标题
        if "_songSubName" in data:
            info["subtitle"] = data["_songSubName"]
        elif "subTitle" in data.get("song", {}):
            info["subtitle"] = data["song"]["subTitle"]
        elif "subTitle" in data:
            info["subtitle"] = data["subTitle"]
        else:
            info["subtitle"] = ""

        # 作者
        if "_songAuthorName" in data:
            info["author"] = data["_songAuthorName"]
        elif "author" in data.get("song", {}):
            info["author"] = data["song"]["author"]
        elif "author" in data:
            info["author"] = data["author"]
        else:
            info["author"] = "未知作者"

        # BPM
        bpm_value = 0
        if "_beatsPerMinute" in data:
            bpm_value = data["_beatsPerMinute"]
        elif "bpm" in data.get("audio", {}):
            bpm_value = data["audio"]["bpm"]
        elif "bpm" in data:
            bpm_value = data["bpm"]
        info["bpm"] = str(bpm_value) if bpm_value else "未知"

        return info

    def _parse_difficulties(self, data):
        """
        解析难度信息

        参数:
            data: JSON数据

        返回:
            难度列表
        """
        difficulties = []

        # 尝试从不同位置获取难度信息
        difficulty_sources = [
            ("_difficultyBeatmapSets", "_difficultyBeatmaps", "_difficulty"),
            ("difficultyBeatmapSets", "difficultyBeatmaps", "difficulty"),
            ("_difficultyBeatmaps", None, "_difficulty"),
            ("difficultyBeatmaps", None, "difficulty")
        ]

        for source_key, inner_key, diff_key in difficulty_sources:
            if source_key in data:
                if inner_key:
                    # 嵌套结构
                    for beatmap_set in data[source_key]:
                        if inner_key in beatmap_set:
                            for beatmap in beatmap_set[inner_key]:
                                if diff_key in beatmap:
                                    difficulty = beatmap[diff_key]
                                    if difficulty not in difficulties:
                                        difficulties.append(difficulty)
                else:
                    # 直接结构
                    for beatmap in data[source_key]:
                        if diff_key in beatmap:
                            difficulty = beatmap[diff_key]
                            if difficulty not in difficulties:
                                difficulties.append(difficulty)

        # 排序难度（如果找到）
        difficulty_order = ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"]
        sorted_difficulties = []
        for diff in difficulty_order:
            if diff in difficulties:
                sorted_difficulties.append(diff)

        # 添加其他未在预设顺序中的难度
        for diff in difficulties:
            if diff not in sorted_difficulties:
                sorted_difficulties.append(diff)

        return sorted_difficulties

    def _find_cover_image(self, folder_path):
        """
        查找封面图片

        参数:
            folder_path: 文件夹路径

        返回:
            封面图片路径或None
        """
        extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']

        # 检查cover文件
        for ext in extensions:
            cover_path = os.path.join(folder_path, f"cover{ext}")
            if os.path.exists(cover_path):
                return cover_path

            cover_path = os.path.join(folder_path, f"Cover{ext}")
            if os.path.exists(cover_path):
                return cover_path

            cover_path = os.path.join(folder_path, f"COVER{ext}")
            if os.path.exists(cover_path):
                return cover_path

        # 查找任何图片文件
        try:
            for file in os.listdir(folder_path):
                file_lower = file.lower()
                if any(file_lower.endswith(ext) for ext in extensions):
                    return os.path.join(folder_path, file)
        except OSError:
            pass

        return None

# ================ 主应用程序 ================
class SongComparatorApp:
    """主应用程序类，负责协调所有组件"""

    def __init__(self):
        """初始化应用程序"""
        logger.info("=" * 60)
        logger.info(f"启动 {APP_NAME}")
        logger.info("=" * 60)

        try:
            # 初始化数据库和歌曲管理器
            self.db_manager = DatabaseManager()
            self.song_manager = SongManager(self.db_manager)

            # 存储数据
            self.local_songs = []  # 本地歌曲列表
            self.quest_songs = []  # Quest歌曲列表
            self.missing_songs = []  # 缺失歌曲列表（双向对比结果）

            # 歌曲评分和名称缓存
            self.song_ratings = {}  # {folder_name: rating}
            self.song_names = {}    # {folder_name: song_title}

            # 当前选中的歌曲信息
            self.current_song_info = {}
            self.current_selected_list = None  # 当前选中的列表类型
            self.current_selected_song = None  # 当前选中的歌曲

            # 创建主窗口
            self.root = ctk.CTk()
            self.setup_window()

            # 设置变量
            self.local_path_var = StringVar()  # 本地路径变量
            self.quest_path_var = StringVar()  # Quest路径变量

            # 进度队列（用于线程间通信）
            self.progress_queue = queue.Queue()

            # 创建UI
            self.create_widgets()

            # 启动队列检查
            self.check_progress_queue()

            # 加载最近使用的路径
            self.load_recent_paths()

            # 绑定全局Delete键
            self.root.bind("<Delete>", self._on_delete_key)

            logger.info("应用程序初始化完成")

        except Exception as e:
            logger.critical(f"应用程序初始化失败: {e}", exc_info=True)
            # 直接使用messagebox显示错误，因为show_error方法可能还未定义
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("启动错误", f"应用程序启动失败:\n{str(e)}")
            raise

    def setup_window(self):
        """设置主窗口属性"""
        self.root.title(APP_NAME)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(*MIN_SIZE)

        # 设置网格权重
        self.root.grid_columnconfigure(0, weight=3)
        self.root.grid_columnconfigure(1, weight=2)
        self.root.grid_rowconfigure(0, weight=1)

    def create_widgets(self):
        """创建所有UI控件"""
        # 左侧框架（歌曲列表区域）
        self.left_frame = ctk.CTkFrame(self.root, corner_radius=15)
        self.left_frame.grid(row=0, column=0, padx=(15, 5), pady=15, sticky="nsew")
        self.left_frame.grid_columnconfigure(0, weight=1)
        self.left_frame.grid_rowconfigure(1, weight=1)

        # 右侧框架（歌曲信息区域）
        self.right_frame = ctk.CTkFrame(self.root, corner_radius=15)
        self.right_frame.grid(row=0, column=1, padx=(5, 15), pady=15, sticky="nsew")
        self.right_frame.grid_columnconfigure(0, weight=1)
        self.right_frame.grid_rowconfigure(0, weight=1)
        self.right_frame.grid_rowconfigure(1, weight=1)

        # 创建各部分
        self.create_path_section()  # 路径设置部分
        self.create_song_lists_section()  # 歌曲列表部分
        self.create_buttons_section()  # 按钮部分
        self.create_song_info_section()  # 歌曲信息部分
        self.create_progress_info_section()  # 进度信息部分

    def create_path_section(self):
        """创建路径选择部分"""
        frame = ctk.CTkFrame(self.left_frame, corner_radius=10)
        frame.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")

        # 标题
        ctk.CTkLabel(frame, text="📁 路径设置", font=(FONT_FAMILY, 16, "bold")).pack(anchor="w", pady=(10, 5), padx=15)

        # 本地路径
        local_frame = ctk.CTkFrame(frame, fg_color="transparent")
        local_frame.pack(fill="x", padx=15, pady=5)

        ctk.CTkLabel(local_frame, text="本地歌曲:", width=80, font=(FONT_FAMILY, 12)).pack(side="left")

        local_entry = ctk.CTkEntry(
            local_frame,
            textvariable=self.local_path_var,
            placeholder_text="选择本地歌曲文件夹...",
            font=(FONT_FAMILY, 11)
        )
        local_entry.pack(side="left", fill="x", expand=True, padx=(5, 5))

        ctk.CTkButton(
            local_frame,
            text="📂",
            width=40,
            font=(FONT_FAMILY, 12),
            command=lambda: self.select_folder(self.local_path_var, "本地")
        ).pack(side="right")

        # Quest路径
        quest_frame = ctk.CTkFrame(frame, fg_color="transparent")
        quest_frame.pack(fill="x", padx=15, pady=(5, 10))

        ctk.CTkLabel(quest_frame, text="Quest歌曲:", width=80, font=(FONT_FAMILY, 12)).pack(side="left")

        quest_entry = ctk.CTkEntry(
            quest_frame,
            textvariable=self.quest_path_var,
            placeholder_text="选择Quest歌曲文件夹...",
            font=(FONT_FAMILY, 11)
        )
        quest_entry.pack(side="left", fill="x", expand=True, padx=(5, 5))

        ctk.CTkButton(
            quest_frame,
            text="📂",
            width=40,
            font=(FONT_FAMILY, 12),
            command=lambda: self.select_folder(self.quest_path_var, "Quest")
        ).pack(side="right")

    def create_song_lists_section(self):
        """创建歌曲列表部分"""
        frame = ctk.CTkFrame(self.left_frame, corner_radius=10)
        frame.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(2, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        # 本地歌曲列表
        self.local_list = SelectableListBox(
            frame,
            title="📂 本地歌曲",
            width=300,
            height=450,
            on_sort_changed=lambda mode, reverse: self.on_sort_changed("local", mode, reverse)
        )
        self.local_list.grid(row=0, column=0, padx=5, pady=10, sticky="nsew")
        self.local_list.set_on_select(lambda song: self.on_song_selected(song, "local"))
        self.local_list.set_on_delete(lambda song: self.delete_song(song, "local"))

        # Quest歌曲列表
        self.quest_list = SelectableListBox(
            frame,
            title="🎮 Quest歌曲",
            width=300,
            height=450,
            on_sort_changed=lambda mode, reverse: self.on_sort_changed("quest", mode, reverse)
        )
        self.quest_list.grid(row=0, column=1, padx=5, pady=10, sticky="nsew")
        self.quest_list.set_on_select(lambda song: self.on_song_selected(song, "quest"))
        self.quest_list.set_on_delete(lambda song: self.delete_song(song, "quest"))

        # 缺失歌曲列表
        self.missing_list = SelectableListBox(
            frame,
            title="✨ 缺失歌曲",
            width=300,
            height=450,
            on_sort_changed=lambda mode, reverse: self.on_sort_changed("missing", mode, reverse)
        )
        self.missing_list.grid(row=0, column=2, padx=5, pady=10, sticky="nsew")
        self.missing_list.set_on_select(lambda song: self.on_song_selected(song, "missing"))
        self.missing_list.set_on_delete(lambda song: self.delete_song(song, "missing"))

    def create_buttons_section(self):
        """创建操作按钮部分"""
        frame = ctk.CTkFrame(
            self.left_frame,
            corner_radius=10,
            fg_color="transparent"
        )
        frame.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="ew")

        # 第一行按钮框架
        row1_frame = ctk.CTkFrame(frame, fg_color="transparent")
        row1_frame.pack(fill="x", padx=5, pady=(5, 5))

        # 第一行按钮布局（使用grid实现精确对齐）
        # 📥 载入本地歌曲 (列0)
        ctk.CTkButton(
            row1_frame,
            text="📥 载入本地歌曲",
            width=160,
            height=35,
            font=(FONT_FAMILY, 12),
            command=lambda: self.load_songs_in_thread("local")
        ).grid(row=0, column=0, padx=(0, 8))

        # 📱 载入Quest歌曲 (列1)
        ctk.CTkButton(
            row1_frame,
            text="📱 载入Quest歌曲",
            width=160,
            height=35,
            font=(FONT_FAMILY, 12),
            command=lambda: self.load_songs_in_thread("quest")
        ).grid(row=0, column=1, padx=(0, 8))

        # 🔍 对比Quest缺失歌曲 (列2)
        ctk.CTkButton(
            row1_frame,
            text="🔍 对比Quest缺失歌曲",
            width=170,
            height=35,
            font=(FONT_FAMILY, 12),
            fg_color="#2ecc71",
            hover_color="#27ae60",
            command=self.compare_quest_missing_songs
        ).grid(row=0, column=2, padx=(0, 8))

        # 📤缺失歌曲复制到Quest (列3)
        ctk.CTkButton(
            row1_frame,
            text="📤缺失歌曲复制到Quest",
            width=190,
            height=35,
            font=(FONT_FAMILY, 12),
            fg_color="#3498db",
            hover_color="#2980b9",
            command=self.copy_missing_to_quest
        ).grid(row=0, column=3, padx=(0, 8))

        # ❓ 帮助 (列4)
        ctk.CTkButton(
            row1_frame,
            text="❓ 帮助",
            width=160,
            height=35,
            font=(FONT_FAMILY, 12),
            fg_color="#9b59b6",
            hover_color="#8e44ad",
            command=self.open_help
        ).grid(row=0, column=4, padx=(0, 8))

        # ⚙️ 设置 (列5，右对齐)
        ctk.CTkButton(
            row1_frame,
            text="⚙️ 设置",
            width=160,
            height=35,
            font=(FONT_FAMILY, 12),
            fg_color="#2c3e50",
            hover_color="#1a252f",
            command=self.open_settings
        ).grid(row=0, column=5, padx=(0, 0))

        # 配置第一行grid权重
        for i in range(6):
            row1_frame.grid_columnconfigure(i, weight=1 if i == 5 else 0)  # 最后一列权重为1，实现右对齐

        # 第二行按钮框架
        row2_frame = ctk.CTkFrame(frame, fg_color="transparent")
        row2_frame.pack(fill="x", padx=5, pady=(5, 5))

        # 第二行按钮布局
        # 🗑️ 清空列表 (列0)
        ctk.CTkButton(
            row2_frame,
            text="🗑️ 清空列表",
            width=160,
            height=35,
            font=(FONT_FAMILY, 12),
            fg_color="#7f8c8d",
            hover_color="#616a6b",
            command=self.clear_lists
        ).grid(row=0, column=0, padx=(0, 8))

        # 🗑️ 删除歌曲 (列1)
        ctk.CTkButton(
            row2_frame,
            text="🗑️ 删除歌曲",
            width=160,
            height=35,
            font=(FONT_FAMILY, 12),
            fg_color="#e74c3c",
            hover_color="#c0392b",
            command=self.delete_selected_song
        ).grid(row=0, column=1, padx=(0, 8))

        # 🔍 对比本地缺失歌曲 (列2)
        ctk.CTkButton(
            row2_frame,
            text="🔍 对比本地缺失歌曲",
            width=170,
            height=35,
            font=(FONT_FAMILY, 12),
            fg_color="#f39c12",
            hover_color="#e67e22",
            command=self.compare_local_missing_songs
        ).grid(row=0, column=2, padx=(0, 8))

        # 📤缺失歌曲复制到本地 (列3)
        ctk.CTkButton(
            row2_frame,
            text="📤缺失歌曲复制到本地",
            width=190,
            height=35,
            font=(FONT_FAMILY, 12),
            fg_color="#e67e22",
            hover_color="#d35400",
            command=self.copy_missing_to_local
        ).grid(row=0, column=3, padx=(0, 8))

        # ❌ 退出程序 (列4，右对齐)
        ctk.CTkButton(
            row2_frame,
            text="❌ 退出程序",
            width=160,
            height=35,
            font=(FONT_FAMILY, 12),
            fg_color="#e74c3c",
            hover_color="#c0392b",
            command=self.safe_quit
        ).grid(row=0, column=4, padx=(0, 0))

        # 配置第二行grid权重
        for i in range(5):
            row2_frame.grid_columnconfigure(i, weight=1 if i == 4 else 0)  # 最后一列权重为1，实现右对齐

    def create_song_info_section(self):
        """创建歌曲信息显示部分 - 修复难度显示跳动问题"""
        # 封面区域
        self.cover_frame = ctk.CTkFrame(self.right_frame, corner_radius=10)
        self.cover_frame.grid(row=0, column=0, padx=15, pady=(15, 10), sticky="nsew")
        self.cover_frame.grid_propagate(True)

        # 标题
        ctk.CTkLabel(
            self.cover_frame,
            text="🎨 歌曲封面",
            font=(FONT_FAMILY, 16, "bold")
        ).pack(pady=(15, 10))

        # 封面容器 - 固定尺寸
        cover_container = ctk.CTkFrame(self.cover_frame, fg_color="transparent")
        cover_container.pack(fill="both", expand=True, padx=20, pady=5)

        # 固定尺寸的封面标签
        self.cover_label = ctk.CTkLabel(
            cover_container,
            text="🎵\n选择歌曲查看封面",
            font=(FONT_FAMILY, 18),
            justify="center",
            width=COVER_SIZE[0],
            height=COVER_SIZE[1]
        )
        self.cover_label.pack(expand=True)

        # 信息区域
        self.info_frame = ctk.CTkFrame(self.right_frame, corner_radius=10)
        self.info_frame.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="nsew")
        self.info_frame.grid_rowconfigure(0, weight=1)
        self.info_frame.grid_rowconfigure(1, weight=0)

        # 创建固定框架用于信息显示（无滚动条）
        self.info_content_frame = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        self.info_content_frame.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="nsew")

        # 创建信息字段
        self.info_labels = {}
        info_fields = [
            ("🎵 歌曲标题:", "title", "未选择", 380),
            ("📝 歌曲副标题:", "subtitle", "无", 380),
            ("👤 歌曲作者:", "author", "未知", 380),
            ("📊 文件版本:", "version", "未知", 380),
            ("🎼 歌曲BPM:", "bpm", "未知", 380),
        ]

        for label_text, key, default_value, wraplength in info_fields:
            field_frame = ctk.CTkFrame(self.info_content_frame, fg_color="transparent")
            field_frame.pack(fill="x", padx=10, pady=5)

            ctk.CTkLabel(
                field_frame,
                text=label_text,
                font=(FONT_FAMILY, 12, "bold"),
                width=100,
                anchor="w"
            ).pack(side="left", padx=(0, 10))

            label = ctk.CTkLabel(
                field_frame,
                text=default_value,
                font=(FONT_FAMILY, 12),
                anchor="w",
                wraplength=wraplength,
                justify="left"
            )
            label.pack(side="left", fill="x", expand=True)

            self.info_labels[key] = label

        # 难度信息 - 固定高度的框架
        difficulty_frame = ctk.CTkFrame(self.info_content_frame, fg_color="transparent", height=120)  # 固定高度
        difficulty_frame.pack(fill="x", padx=10, pady=5)
        difficulty_frame.pack_propagate(False)  # 禁止自动调整大小

        ctk.CTkLabel(
            difficulty_frame,
            text="🎮 包含难度:",
            font=(FONT_FAMILY, 12, "bold"),
            width=100,
            anchor="w"
        ).pack(side="left", padx=(0, 10), anchor="n")

        # 难度容器框架 - 固定高度
        self.difficulty_container = ctk.CTkScrollableFrame(
            difficulty_frame,
            fg_color="transparent",
            height=100  # 固定高度
        )
        self.difficulty_container.pack(side="left", fill="both", expand=True)

        # 初始化难度标签
        self.difficulty_labels = []
        self.update_difficulty_display([])

        # 个人评分区域
        rating_frame = ctk.CTkFrame(self.info_content_frame, fg_color="transparent")
        rating_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(
            rating_frame,
            text="⭐ 个人评分:",
            font=(FONT_FAMILY, 12, "bold"),
            width=100,
            anchor="w"
        ).pack(side="left", padx=(0, 10))

        # 评分星星框架
        self.rating_frame = ctk.CTkFrame(rating_frame, fg_color="transparent")
        self.rating_frame.pack(side="left", fill="x", expand=True)

        # 创建5个星星按钮（使用固定宽度）
        self.rating_stars = []
        for i in range(1, 6):
            star = ctk.CTkLabel(
                self.rating_frame,
                text="☆",  # 空心星星
                font=(FONT_FAMILY, 24),
                cursor="hand2",
                width=30,
                anchor="center"
            )
            star.pack(side="left", padx=2)
            star.bind("<Button-1>", lambda e, rating=i: self.update_rating(rating))
            self.rating_stars.append(star)

        # 个人评价区域
        comment_frame = ctk.CTkFrame(self.info_content_frame, fg_color="transparent")
        comment_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(
            comment_frame,
            text="💭 个人评价:",
            font=(FONT_FAMILY, 12, "bold"),
            width=100,
            anchor="w"
        ).pack(side="left", padx=(0, 10))

        # 评价标签（可双击编辑）
        self.comment_label = ctk.CTkLabel(
            comment_frame,
            text="未评价",
            font=(FONT_FAMILY, 12),
            anchor="w",
            wraplength=380,
            justify="left",
            cursor="hand2"
        )
        self.comment_label.pack(side="left", fill="x", expand=True)
        self.comment_label.bind("<Double-Button-1>", self.edit_comment)

    def create_progress_info_section(self):
        """创建进度信息显示部分（常驻状态）"""
        # 进度信息区域（放在信息区域底部，常驻显示）
        self.progress_container = ctk.CTkFrame(self.info_frame, height=40, corner_radius=5)
        self.progress_container.grid(row=1, column=0, padx=15, pady=(5, 15), sticky="ew")
        self.progress_container.grid_propagate(False)  # 固定高度

        # 使用pack布局
        self.progress_label = ctk.CTkLabel(
            self.progress_container,
            text="📊 状态: 就绪",
            font=(FONT_FAMILY, 12),
            anchor="w",
            justify="left",
            height=30
        )
        self.progress_label.pack(side="left", fill="both", expand=True, padx=10, pady=5)

    def update_difficulty_display(self, difficulties):
        """
        更新难度显示 - 修复UI跳动问题

        参数:
            difficulties: 难度列表
        """
        try:
            # 清除现有难度标签
            for label in self.difficulty_labels:
                label.destroy()
            self.difficulty_labels = []

            if not difficulties:
                # 显示"未知" - 放在固定高度的滚动框中
                label = ctk.CTkLabel(
                    self.difficulty_container,
                    text="未知",
                    font=(FONT_FAMILY, 12),
                    anchor="w",
                    height=25
                )
                label.pack(side="top", anchor="w", pady=(2, 2), fill="x")
                self.difficulty_labels.append(label)
            else:
                # 为每个难度创建一个标签，每个单独一行
                for difficulty in difficulties:
                    # 根据难度设置颜色
                    colors = {
                        "Easy": "#2ecc71",      # 绿色
                        "Normal": "#3498db",     # 蓝色
                        "Hard": "#f39c12",       # 橙色
                        "Expert": "#e74c3c",     # 红色
                        "ExpertPlus": "#9b59b6"  # 紫色
                    }

                    color = colors.get(difficulty, "#95a5a6")  # 默认灰色

                    # 创建难度标签 - 固定高度
                    label = ctk.CTkLabel(
                        self.difficulty_container,
                        text=f"• {difficulty}",
                        font=(FONT_FAMILY, 12, "bold"),
                        text_color=color,
                        anchor="w",
                        height=25
                    )
                    label.pack(side="top", anchor="w", pady=(2, 2), fill="x")

                    self.difficulty_labels.append(label)
        except Exception as e:
            logger.error(f"更新难度显示失败: {e}")

    # ================ 核心功能方法 ================

    def select_folder(self, path_var, folder_type):
        """
        选择文件夹并更新路径变量（增强版，支持MTP设备）

        参数:
            path_var: 要更新的路径变量
            folder_type: 文件夹类型（"本地"或"Quest"）
        """
        try:
            if folder_type == "Quest":
                # 尝试自动检测Quest3的常见路径
                common_quest_paths = self.detect_quest_paths()
                if common_quest_paths:
                    # 如果找到多个可能路径，让用户选择
                    if len(common_quest_paths) > 1:
                        path_selection = QuestPathSelectionDialog(
                            self.root,
                            "选择Quest路径",
                            common_quest_paths
                        )
                        selected_path = path_selection.show()
                        if selected_path:
                            path_var.set(selected_path)
                            self.save_recent_paths()
                            logger.info(f"用户选择了Quest路径: {selected_path}")
                            return
                    else:
                        # 只有一个路径，直接使用
                        path_var.set(common_quest_paths[0])
                        self.save_recent_paths()
                        logger.info(f"自动选择Quest路径: {common_quest_paths[0]}")
                        return

                # 如果没有自动检测到，显示提示信息
                messagebox.showinfo("Quest路径选择",
                    "请手动选择您的Quest设备路径。\n\n"
                    "常见路径位置:\n"
                    "1. 在'此电脑'中找到Quest设备\n"
                    "2. 进入Quest的内部存储\n"
                    "3. 路径类似: Quest3\\内部共享存储空间\\Android\\data\\com.beatgames.beatsaber\\files\\CustomLevels")

            # 使用文件对话框选择文件夹
            folder = filedialog.askdirectory(
                title=f"选择{folder_type}歌曲文件夹",
                mustexist=True
            )

            if folder:
                if folder_type == "Quest":
                    # 验证Quest路径
                    if not self.validate_quest_path(folder):
                        if not messagebox.askyesno("路径验证",
                            "选择的路径可能不是有效的Beat Saber歌曲文件夹。\n"
                            "通常路径应包含CustomLevels文件夹。\n"
                            "是否仍然使用此路径？"):
                            return

                path_var.set(folder)
                self.save_recent_paths()
                logger.info(f"用户选择了{folder_type}路径: {folder}")

        except Exception as e:
            logger.error(f"选择{folder_type}文件夹错误: {e}", exc_info=True)
            self.show_error(f"选择文件夹失败: {str(e)}")

    def detect_quest_paths(self):
        """
        尝试自动检测Quest3的可能路径（改进版）

        返回:
            可能的Quest路径列表
        """
        quest_paths = []

        try:
            # 检查Windows驱动器
            if os.name == 'nt':  # Windows系统
                import string
                import ctypes

                # 获取所有驱动器
                drives = []
                bitmask = ctypes.windll.kernel32.GetLogicalDrives()
                for letter in string.ascii_uppercase:
                    if bitmask & 1:
                        drives.append(f"{letter}:\\")
                    bitmask >>= 1

                for drive in drives:
                    try:
                        # 检查驱动器类型
                        drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive)
                        # 移动设备类型是2
                        if drive_type == 2:
                            logger.debug(f"检测到移动设备: {drive}")

                            # 检查是否是Quest设备
                            try:
                                for item in os.listdir(drive):
                                    item_lower = item.lower()
                                    # Quest设备可能的名字
                                    if any(name in item_lower for name in ['quest', 'oculus', 'meta', 'android', '内部共享']):
                                        quest_root = os.path.join(drive, item)

                                        # 尝试查找CustomLevels文件夹
                                        search_paths = [
                                            quest_root,
                                            os.path.join(quest_root, "内部共享存储空间"),
                                            os.path.join(quest_root, "内部存储"),
                                            os.path.join(quest_root, "Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"),
                                            os.path.join(quest_root, "Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"),
                                            os.path.join(quest_root, "Data", "com.beatgames.beatsaber", "files", "CustomLevels"),
                                        ]

                                        for search_path in search_paths:
                                            if os.path.exists(search_path):
                                                if os.path.isdir(search_path):
                                                    # 检查是否是CustomLevels文件夹
                                                    if os.path.basename(search_path) == "CustomLevels":
                                                        quest_paths.append(search_path)
                                                    # 检查子目录中是否有CustomLevels
                                                    else:
                                                        try:
                                                            for root, dirs, _ in os.walk(search_path, topdown=True):
                                                                if 'CustomLevels' in dirs:
                                                                    custom_path = os.path.join(root, 'CustomLevels')
                                                                    quest_paths.append(custom_path)
                                                                    break
                                                        except:
                                                            continue

                                        break  # 找到设备后退出
                            except (PermissionError, OSError) as e:
                                logger.debug(f"无法访问 {drive}: {e}")
                                continue

                    except Exception as e:
                        logger.debug(f"检查驱动器 {drive} 失败: {e}")
                        continue

            # 检查常见的挂载点（Linux/Mac）
            common_mounts = [
                "/media", "/mnt", "/Volumes",
                os.path.expanduser("~/Quest"),
                os.path.expanduser("~/Oculus"),
                os.path.expanduser("~/Downloads/Quest")
            ]

            for mount in common_mounts:
                if os.path.exists(mount):
                    try:
                        for item in os.listdir(mount):
                            item_path = os.path.join(mount, item)
                            if os.path.isdir(item_path):
                                # 检查是否是Quest相关
                                item_lower = item.lower()
                                if any(name in item_lower for name in ['quest', 'oculus', 'meta', 'android']):
                                    # 查找CustomLevels
                                    search_patterns = [
                                        "**/CustomLevels",
                                        "**/com.beatgames.beatsaber/files/CustomLevels",
                                        "Android/data/com.beatgames.beatsaber/files/CustomLevels"
                                    ]

                                    import glob
                                    for pattern in search_patterns:
                                        try:
                                            for found_path in glob.glob(os.path.join(item_path, pattern), recursive=True):
                                                if os.path.isdir(found_path):
                                                    quest_paths.append(found_path)
                                        except:
                                            continue
                    except (PermissionError, OSError):
                        continue

        except Exception as e:
            logger.warning(f"自动检测Quest路径时出错: {e}")

        # 去重并过滤
        unique_paths = []
        for path in quest_paths:
            # 验证路径是否有效（包含CustomLevels文件夹）
            if path and os.path.exists(path) and path not in unique_paths:
                # 确保路径以CustomLevels结尾
                if os.path.basename(path) != "CustomLevels":
                    # 尝试找到CustomLevels子文件夹
                    custom_path = os.path.join(path, "CustomLevels")
                    if os.path.exists(custom_path):
                        path = custom_path

                unique_paths.append(path)

        # 排序，让较短的路径（可能更直接的路径）在前
        unique_paths.sort(key=lambda x: len(x))

        logger.info(f"自动检测到Quest路径: {unique_paths}")
        return unique_paths

    def validate_quest_path(self, path):
        """
        验证Quest路径是否有效

        参数:
            path: 要验证的路径

        返回:
            是否有效
        """
        if not os.path.exists(path):
            return False

        # 检查路径中是否包含常见Beat Saber相关关键词
        path_lower = path.lower()
        keywords = ['beat saber', 'customlevels', 'beatsaber', 'quest', 'oculus', 'android']

        if any(keyword in path_lower for keyword in keywords):
            return True

        # 检查是否是CustomLevels文件夹
        if os.path.basename(path) == "CustomLevels":
            return True

        # 检查路径中是否有info.dat文件（表示是歌曲文件夹）
        try:
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                if os.path.isdir(item_path):
                    # 检查是否有info.dat文件
                    info_files = ["info.dat", "Info.dat", "INFO.DAT"]
                    for info_file in info_files:
                        if os.path.exists(os.path.join(item_path, info_file)):
                            return True
        except:
            pass

        return False

    def load_songs_in_thread(self, song_type):
        """
        在线程中加载歌曲（避免界面卡顿）

        参数:
            song_type: 歌曲类型（"local"或"quest"）
        """
        try:
            if song_type == "local":
                path = self.local_path_var.get()
                if not path:
                    self.show_error("请先选择本地歌曲路径")
                    return
                logger.info(f"开始加载本地歌曲，路径: {path}")
            else:
                path = self.quest_path_var.get()
                if not path:
                    self.show_error("请先选择Quest歌曲路径")
                    return
                logger.info(f"开始加载Quest歌曲，路径: {path}")

            # 验证路径是否存在
            if not os.path.exists(path):
                self.show_error(f"路径不存在: {path}")
                logger.error(f"路径不存在: {path}")
                return

            # 更新进度信息
            self.update_progress_info(f"开始加载{song_type}歌曲")

            # 在线程中加载
            thread = threading.Thread(
                target=self.load_songs_background,
                args=(song_type, path),
                name=f"Load_{song_type}_Songs"
            )
            thread.daemon = True
            thread.start()

        except Exception as e:
            logger.error(f"加载{song_type}歌曲失败: {e}", exc_info=True)
            self.show_error(f"加载失败: {str(e)}")

    def load_songs_background(self, song_type, path):
        """
        后台加载歌曲（在线程中执行）

        参数:
            song_type: 歌曲类型
            path: 歌曲路径
        """
        def progress_callback(message, percent):
            """进度回调函数"""
            self.progress_queue.put(("progress", message, percent))

        try:
            logger.info(f"后台加载{song_type}歌曲，路径: {path}")

            # 创建带进度回调的歌曲管理器
            song_manager = SongManager(self.db_manager, progress_callback)
            songs = song_manager.load_songs_from_path(path)

            # 更新歌曲评分和名称缓存
            if song_type == "local":
                # 获取所有歌曲的评分
                self.song_ratings = self.db_manager.get_all_song_ratings()
                # 获取所有歌曲的名称
                self.song_names = self.db_manager.get_all_song_names()

            # 更新UI
            if song_type == "local":
                self.local_songs = songs
                self.root.after(0, lambda: self.local_list.set_songs(
                    songs, self.song_ratings, self.song_names
                ))
                self.root.after(0, lambda: self.update_status(f"已加载 {len(songs)} 首本地歌曲"))
                logger.info(f"本地歌曲加载完成，共 {len(songs)} 首")
            else:
                self.quest_songs = songs
                self.root.after(0, lambda: self.quest_list.set_songs(
                    songs, self.song_ratings, self.song_names
                ))
                self.root.after(0, lambda: self.update_status(f"已加载 {len(songs)} 首Quest歌曲"))
                logger.info(f"Quest歌曲加载完成，共 {len(songs)} 首")

            # 完成
            self.progress_queue.put(("complete", f"加载完成，共 {len(songs)} 首歌曲", 100))

        except Exception as e:
            logger.error(f"后台加载{song_type}歌曲错误: {e}", exc_info=True)
            self.progress_queue.put(("error", f"加载失败: {str(e)}", 100))

    def on_song_selected(self, song_name, list_type):
        """
        处理歌曲选中事件

        参数:
            song_name: 歌曲名称
            list_type: 列表类型（"local", "quest", "missing"）
        """
        try:
            # 保存当前选中的列表和歌曲
            self.current_selected_list = list_type
            self.current_selected_song = song_name

            # 确定路径
            base_path = None

            if list_type == "local":
                base_path = os.path.join(self.local_path_var.get(), song_name)
            elif list_type == "quest":
                base_path = os.path.join(self.quest_path_var.get(), song_name)
            else:  # missing (缺失歌曲，根据对比方向确定路径)
                # 先检查是否在本地路径
                local_path = os.path.join(self.local_path_var.get(), song_name)
                quest_path = os.path.join(self.quest_path_var.get(), song_name)

                if os.path.exists(local_path):
                    base_path = local_path
                elif os.path.exists(quest_path):
                    base_path = quest_path
                else:
                    self.update_status(f"歌曲不存在: {song_name}", is_error=True)
                    logger.warning(f"选中的缺失歌曲不存在: {song_name}")
                    return

            if not base_path or not os.path.exists(base_path):
                self.update_status(f"路径不存在: {base_path}", is_error=True)
                logger.warning(f"歌曲路径不存在: {base_path}")
                return

            logger.debug(f"选中歌曲: {song_name} (类型: {list_type}, 路径: {base_path})")

            # 在线程中加载歌曲信息
            thread = threading.Thread(
                target=self.load_song_info_background,
                args=(song_name, base_path),
                name=f"Load_Song_Info_{song_name}"
            )
            thread.daemon = True
            thread.start()

        except Exception as e:
            logger.error(f"选中歌曲处理错误: {e}", exc_info=True)
            self.show_error(f"处理选中歌曲失败: {str(e)}")

    def load_song_info_background(self, song_name, base_path):
        """
        后台加载歌曲信息

        参数:
            song_name: 歌曲名称
            base_path: 歌曲路径
        """
        try:
            logger.debug(f"开始加载歌曲信息: {song_name}")
            song_info = self.song_manager.get_song_info(base_path)

            if song_info:
                # 更新UI
                self.root.after(0, lambda: self.update_song_info_ui(song_info, song_name))
                logger.debug(f"成功加载歌曲信息: {song_name}")
            else:
                self.root.after(0, lambda: self.show_error("无法读取歌曲信息"))
                logger.warning(f"无法读取歌曲信息: {song_name}")

        except Exception as e:
            logger.error(f"读取歌曲信息失败: {e}", exc_info=True)
            self.root.after(0, lambda: self.show_error(f"读取歌曲信息失败: {str(e)}"))

    def update_song_info_ui(self, song_info, song_name):
        """
        更新歌曲信息UI

        参数:
            song_info: 歌曲信息字典
            song_name: 歌曲名称
        """
        try:
            # 更新信息显示
            for key, label in self.info_labels.items():
                if key in song_info:
                    value = song_info[key] or "无"
                    if key == "bpm" and value:
                        try:
                            # 如果是数字，去掉小数部分
                            bpm_float = float(value)
                            if bpm_float.is_integer():
                                value = str(int(bpm_float))
                        except ValueError:
                            pass
                    label.configure(text=value)
                else:
                    label.configure(text="未知")

            # 更新难度显示
            difficulties = []
            if 'difficulties_list' in song_info and song_info['difficulties_list']:
                difficulties = song_info['difficulties_list']
            elif 'difficulties' in song_info and song_info['difficulties']:
                # 如果是字符串，尝试转换为列表
                if isinstance(song_info['difficulties'], str) and song_info['difficulties'] != '未知':
                    difficulties = [d.strip() for d in song_info['difficulties'].split(',') if d.strip()]

            self.update_difficulty_display(difficulties)

            # 更新封面（固定尺寸）
            self.update_cover_image(song_info.get('cover_path'))

            # 更新评分（星星）
            rating = song_info.get('rating', 0)
            self.update_rating_display(rating)

            # 更新评价
            comment = song_info.get('comment', '')
            if not comment:
                comment = "未评价"
            self.comment_label.configure(text=comment)

            # 更新状态
            self.update_status(f"已加载: {song_info.get('title', song_name)}")

        except Exception as e:
            logger.error(f"更新歌曲信息UI失败: {e}", exc_info=True)
            self.show_error(f"更新显示失败: {str(e)}")

    def update_cover_image(self, image_path):
        """
        更新封面图片（固定尺寸） - 修复Image导入问题

        参数:
            image_path: 图片路径
        """
        try:
            if image_path and os.path.exists(image_path):
                # 延迟导入PIL，避免不必要的依赖
                try:
                    from PIL import Image

                    # 加载图片并缩放到固定尺寸
                    img = Image.open(image_path)
                    # 保持宽高比，缩放到固定大小
                    img.thumbnail(COVER_SIZE, Image.Resampling.LANCZOS)

                    # 创建新图像，将缩放的图片放在中心
                    new_img = Image.new('RGB', COVER_SIZE, (50, 50, 50))
                    # 计算位置使图片居中
                    x_offset = (COVER_SIZE[0] - img.size[0]) // 2
                    y_offset = (COVER_SIZE[1] - img.size[1]) // 2
                    new_img.paste(img, (x_offset, y_offset))

                    # 转换为CTkImage
                    ctk_img = ctk.CTkImage(light_image=new_img, dark_image=new_img, size=COVER_SIZE)

                    # 更新标签
                    self.cover_label.configure(image=ctk_img, text="")
                    self.cover_label.image = ctk_img  # 保持引用

                except ImportError:
                    # 确保在except块中处理Image相关代码
                    self.cover_label.configure(
                        text="⚠️\n需要安装pillow库\npip install pillow",
                        image=""
                    )
                except Exception as e:
                    self.cover_label.configure(
                        text="❌\n图片加载失败",
                        image=""
                    )
                    logger.warning(f"加载封面图片失败: {e}")
            else:
                self.cover_label.configure(
                    text="🎵\n无封面图片",
                    image=""
                )
        except Exception as e:
            logger.error(f"更新封面图片失败: {e}")

    def update_rating_display(self, rating):
        """
        更新评分显示（使用星星）

        参数:
            rating: 评分值(0-5)
        """
        try:
            for i, star in enumerate(self.rating_stars):
                if i < rating:
                    star.configure(text="★", text_color="#FFD700")  # 金色实心星星
                else:
                    star.configure(text="☆", text_color="gray")    # 灰色空心星星
        except Exception as e:
            logger.error(f"更新评分显示失败: {e}")

    def update_rating(self, rating):
        """
        更新歌曲评分

        参数:
            rating: 评分值(1-5)
        """
        try:
            if not self.current_selected_song:
                self.show_error("请先选择一首歌曲")
                return

            # 更新数据库
            success = self.db_manager.update_song_rating(self.current_selected_song, rating)

            if success:
                # 更新显示
                self.update_rating_display(rating)
                # 更新当前歌曲信息
                if self.current_song_info:
                    self.current_song_info['rating'] = rating

                # 更新缓存
                self.song_ratings[self.current_selected_song] = rating

                # 如果当前列表使用评分排序，需要重新排序
                if self.current_selected_list == "local" and self.local_list.sort_mode == "rating":
                    self.local_list.set_songs(self.local_songs, self.song_ratings, self.song_names)
                elif self.current_selected_list == "quest" and self.quest_list.sort_mode == "rating":
                    self.quest_list.set_songs(self.quest_songs, self.song_ratings, self.song_names)
                elif self.current_selected_list == "missing" and self.missing_list.sort_mode == "rating":
                    self.missing_list.set_songs(self.missing_songs, self.song_ratings, self.song_names)

                self.update_status(f"已更新评分: {rating}星")
                logger.info(f"更新歌曲评分: {self.current_selected_song} -> {rating}星")
            else:
                self.show_error("更新评分失败")
                logger.error(f"更新评分失败: {self.current_selected_song}")

        except Exception as e:
            logger.error(f"更新评分失败: {e}", exc_info=True)
            self.show_error(f"更新评分失败: {str(e)}")

    def edit_comment(self, event):
        """
        编辑个人评价
        """
        try:
            if not self.current_selected_song:
                self.show_error("请先选择一首歌曲")
                return

            # 创建编辑对话框
            current_comment = self.comment_label.cget("text")
            if current_comment == "未评价":
                current_comment = ""

            comment_dialog = CommentEditDialog(
                self.root,
                "编辑评价",
                current_comment,
                lambda new_comment: self.save_comment(new_comment)
            )
            comment_dialog.show()

        except Exception as e:
            logger.error(f"编辑评价失败: {e}", exc_info=True)
            self.show_error(f"编辑评价失败: {str(e)}")

    def save_comment(self, comment):
        """
        保存个人评价

        参数:
            comment: 评价内容
        """
        try:
            # 验证长度
            if len(comment) > 20:
                self.show_error("评价不能超过20个字符")
                return

            # 更新数据库
            success = self.db_manager.update_song_comment(self.current_selected_song, comment)

            if success:
                # 更新显示
                if not comment:
                    comment = "未评价"
                self.comment_label.configure(text=comment)

                # 更新当前歌曲信息
                if self.current_song_info:
                    self.current_song_info['comment'] = comment

                self.update_status("评价已保存")
                logger.info(f"更新歌曲评价: {self.current_selected_song} -> {comment}")
            else:
                self.show_error("保存评价失败")
                logger.error(f"保存评价失败: {self.current_selected_song}")

        except Exception as e:
            logger.error(f"保存评价失败: {e}", exc_info=True)
            self.show_error(f"保存评价失败: {str(e)}")

    def on_sort_changed(self, list_type, sort_mode, reverse):
        """
        处理排序方式改变

        参数:
            list_type: 列表类型 (local, quest, missing)
            sort_mode: 排序模式
            reverse: 是否反序
        """
        order = "反序" if reverse else "正序"
        logger.info(f"{list_type}列表排序方式改为: {sort_mode} ({order})")

    def compare_quest_missing_songs(self):
        """对比Quest缺失的歌曲（本地有而Quest没有）"""
        # 清除Quest列表的高亮
        self.quest_list.clear_highlights()
        self._compare_songs("Quest缺失")

    def compare_local_missing_songs(self):
        """对比本地缺失的歌曲（Quest有而本地没有）"""
        # 清除本地列表的高亮
        self.local_list.clear_highlights()
        self._compare_songs("本地缺失")

    def _compare_songs(self, compare_type):
        """
        对比歌曲

        参数:
            compare_type: 对比类型 "Quest缺失" 或 "本地缺失"
        """
        try:
            if not self.local_songs or not self.quest_songs:
                self.show_error("请先加载本地和Quest歌曲")
                return

            logger.info(f"开始对比歌曲: {compare_type}")

            # 更新进度信息
            self.update_progress_info(f"正在对比{compare_type}歌曲...")

            # 计算缺失歌曲
            local_set = set(self.local_songs)
            quest_set = set(self.quest_songs)

            if compare_type == "Quest缺失":
                # 本地有而Quest没有的
                missing_songs = list(local_set - quest_set)
                highlight_list = self.local_list
                source_list = self.local_songs
            else:  # 本地缺失
                # Quest有而本地没有的
                missing_songs = list(quest_set - local_set)
                highlight_list = self.quest_list
                source_list = self.quest_songs

            missing_songs.sort(key=str.lower)

            # 计算需要高亮的歌曲索引
            missing_indices = []
            for i, song in enumerate(source_list):
                if song in missing_songs:
                    missing_indices.append(i)

            # 更新列表
            self.missing_songs = missing_songs
            self.missing_list.set_songs(missing_songs, self.song_ratings, self.song_names)

            # 高亮显示
            highlight_list.highlight_items(missing_indices)

            # 更新状态
            if missing_songs:
                self.update_status(f"发现 {len(missing_songs)} 首{compare_type}歌曲")
                logger.info(f"发现 {len(missing_songs)} 首{compare_type}歌曲")
                self.update_progress_info(f"发现 {len(missing_songs)} 首{compare_type}歌曲")
            else:
                self.update_status(f"没有发现{compare_type}歌曲")
                logger.info(f"没有发现{compare_type}歌曲")
                self.update_progress_info(f"没有发现{compare_type}歌曲")

        except Exception as e:
            logger.error(f"对比{compare_type}歌曲失败: {e}", exc_info=True)
            self.show_error(f"对比失败: {str(e)}")

    def copy_missing_to_quest(self):
        """将缺失歌曲复制到Quest路径"""
        self._copy_missing_songs("Quest")

    def copy_missing_to_local(self):
        """将缺失歌曲复制到本地路径"""
        self._copy_missing_songs("本地")

    def _copy_missing_songs(self, target):
        """
        复制缺失歌曲到目标路径

        参数:
            target: 目标路径 "Quest" 或 "本地"
        """
        try:
            if not self.missing_songs:
                self.show_error("没有可复制的缺失歌曲")
                return

            # 确定源路径和目标路径
            if target == "Quest":
                source_path = self.local_path_var.get()
                target_path = self.quest_path_var.get()
                confirm_message = f"确定要将 {len(self.missing_songs)} 首缺失歌曲复制到Quest吗？"
                operation_name = "复制到Quest"
            else:
                source_path = self.quest_path_var.get()
                target_path = self.local_path_var.get()
                confirm_message = f"确定要将 {len(self.missing_songs)} 首缺失歌曲复制到本地吗？"
                operation_name = "复制到本地"

            if not source_path or not target_path:
                self.show_error(f"请先设置本地和Quest歌曲路径")
                return

            # 验证源路径是否存在
            if not os.path.exists(source_path):
                self.show_error(f"源路径不存在: {source_path}")
                logger.error(f"源路径不存在: {source_path}")
                return

            # 确认对话框
            confirm_dialog = ConfirmDialog(
                self.root,
                f"确认{operation_name}",
                confirm_message,
                f"将复制 {len(self.missing_songs)} 首歌曲",
                lambda: self._execute_copy_missing(source_path, target_path, target)
            )
            confirm_dialog.show()

        except Exception as e:
            logger.error(f"准备复制缺失歌曲到{target}失败: {e}", exc_info=True)
            self.show_error(f"准备复制失败: {str(e)}")

    def _execute_copy_missing(self, source_path, target_path, target):
        """
        执行复制缺失歌曲操作

        参数:
            source_path: 源路径
            target_path: 目标路径
            target: 目标类型 "Quest" 或 "本地"
        """
        try:
            # 更新进度信息
            self.update_progress_info(f"开始复制缺失歌曲到{target}...")

            # 在线程中执行复制操作
            thread = threading.Thread(
                target=self.copy_missing_background,
                args=(source_path, target_path, self.missing_songs, target),
                name=f"Copy_Missing_To_{target}"
            )
            thread.daemon = True
            thread.start()

        except Exception as e:
            logger.error(f"执行复制到{target}失败: {e}", exc_info=True)
            self.show_error(f"执行复制失败: {str(e)}")

    def copy_missing_background(self, source_path, target_path, songs, target):
        """
        后台复制缺失歌曲

        参数:
            source_path: 源路径
            target_path: 目标路径
            songs: 要复制的歌曲列表
            target: 目标类型 "Quest" 或 "本地"
        """
        success_count = 0
        fail_count = 0
        fail_songs = []

        logger.info(f"开始后台复制{len(songs)}首歌曲到{target}")

        for i, song in enumerate(songs):
            try:
                src_path = os.path.join(source_path, song)
                dst_path = os.path.join(target_path, song)

                # 检查源路径是否存在
                if not os.path.exists(src_path):
                    logger.warning(f"源路径不存在: {src_path}")
                    fail_count += 1
                    fail_songs.append(f"{song} (源路径不存在)")
                    continue

                # 如果目标路径已存在，先删除
                if os.path.exists(dst_path):
                    try:
                        shutil.rmtree(dst_path)
                        logger.debug(f"删除已存在的目标文件夹: {dst_path}")
                    except Exception as e:
                        logger.warning(f"删除目标文件夹失败 {dst_path}: {e}")
                        fail_count += 1
                        fail_songs.append(f"{song} (删除目标失败: {str(e)})")
                        continue

                # 复制文件夹
                shutil.copytree(src_path, dst_path)
                success_count += 1
                logger.debug(f"成功复制: {song}")

                # 更新进度
                progress = int((i + 1) / len(songs) * 100)
                self.progress_queue.put(("progress", f"正在复制: {song} ({i+1}/{len(songs)})", progress))

            except Exception as e:
                logger.error(f"复制失败 {song}: {e}")
                fail_count += 1
                fail_songs.append(f"{song} (错误: {str(e)})")

        # 完成
        if success_count > 0:
            # 重新加载目标歌曲列表
            if target == "Quest":
                self.root.after(0, lambda: self.load_songs_in_thread("quest"))
            else:
                self.root.after(0, lambda: self.load_songs_in_thread("local"))

        # 准备完成消息
        complete_message = f"复制完成: 成功 {success_count}, 失败 {fail_count}"
        if fail_songs:
            complete_message += f"\n失败的歌曲: {', '.join(fail_songs[:5])}"
            if len(fail_songs) > 5:
                complete_message += f" 等{len(fail_songs)}首"

        logger.info(f"复制到{target}完成: 成功 {success_count}, 失败 {fail_count}")
        self.progress_queue.put(("complete", complete_message, 100))

    def delete_selected_song(self):
        """删除选中的歌曲"""
        if not self.current_selected_list or not self.current_selected_song:
            self.show_error("请先选择要删除的歌曲")
            return

        # 调用删除函数
        self.delete_song(self.current_selected_song, self.current_selected_list)

    def delete_song(self, song_name, list_type):
        """
        删除歌曲

        参数:
            song_name: 歌曲名称
            list_type: 列表类型
        """
        try:
            # 确定路径
            if list_type == "local":
                base_path = os.path.join(self.local_path_var.get(), song_name)
                list_title = "本地歌曲"
            elif list_type == "quest":
                base_path = os.path.join(self.quest_path_var.get(), song_name)
                list_title = "Quest歌曲"
            else:  # missing
                # 尝试在两个位置都删除
                local_path = os.path.join(self.local_path_var.get(), song_name)
                quest_path = os.path.join(self.quest_path_var.get(), song_name)

                if os.path.exists(local_path):
                    base_path = local_path
                    list_title = "本地歌曲"
                elif os.path.exists(quest_path):
                    base_path = quest_path
                    list_title = "Quest歌曲"
                else:
                    self.show_error(f"歌曲不存在: {song_name}")
                    return

            if not base_path or not os.path.exists(base_path):
                self.show_error(f"路径不存在: {base_path}")
                return

            logger.info(f"准备删除歌曲: {song_name} (类型: {list_type}, 路径: {base_path})")

            # 创建确认对话框
            confirm_dialog = DeleteConfirmDialog(
                self.root,
                title="确认删除",
                message=f"确定要删除 {list_title} 中的 '{song_name}' 吗？\n\n此操作将永久删除文件夹及其所有内容！",
                on_confirm=lambda: self.execute_delete(song_name, base_path, list_type),
                on_cancel=None
            )

            # 显示对话框
            confirm_dialog.show()

        except Exception as e:
            logger.error(f"准备删除歌曲失败: {e}", exc_info=True)
            self.show_error(f"准备删除失败: {str(e)}")

    def execute_delete(self, song_name, base_path, list_type):
        """
        执行删除操作

        参数:
            song_name: 歌曲名称
            base_path: 歌曲路径
            list_type: 列表类型
        """
        try:
            # 删除文件夹
            shutil.rmtree(base_path)
            logger.info(f"已删除歌曲文件夹: {base_path}")

            # 更新状态
            self.update_status(f"已删除: {song_name}")

            # 从列表中移除歌曲
            if list_type == "local":
                if song_name in self.local_songs:
                    self.local_songs.remove(song_name)
                    self.local_list.set_songs(self.local_songs, self.song_ratings, self.song_names)
            elif list_type == "quest":
                if song_name in self.quest_songs:
                    self.quest_songs.remove(song_name)
                    self.quest_list.set_songs(self.quest_songs, self.song_ratings, self.song_names)
            else:  # missing
                # 从缺失列表中移除
                if song_name in self.missing_songs:
                    self.missing_songs.remove(song_name)
                    self.missing_list.set_songs(self.missing_songs, self.song_ratings, self.song_names)
                # 同时从原始列表中移除高亮
                if song_name in self.local_songs:
                    self.local_songs.remove(song_name)
                    self.local_list.set_songs(self.local_songs, self.song_ratings, self.song_names)
                if song_name in self.quest_songs:
                    self.quest_songs.remove(song_name)
                    self.quest_list.set_songs(self.quest_songs, self.song_ratings, self.song_names)

            # 清空当前选中
            self.current_selected_list = None
            self.current_selected_song = None

            # 清空歌曲信息显示
            for label in self.info_labels.values():
                label.configure(text="未选择")
            self.update_difficulty_display([])
            self.update_rating_display(0)
            self.comment_label.configure(text="未评价")
            self.cover_label.configure(text="🎵\n选择歌曲查看封面", image="")

            self.show_info(f"歌曲 '{song_name}' 删除成功")

        except Exception as e:
            logger.error(f"删除歌曲失败: {e}", exc_info=True)
            self.show_error(f"删除失败: {str(e)}")

    def _on_delete_key(self, event):
        """
        处理Delete键事件

        参数:
            event: 键盘事件
        返回:
            "break" 阻止事件传播
        """
        self.delete_selected_song()
        return "break"

    def clear_lists(self):
        """清空所有列表"""
        try:
            if messagebox.askyesno("确认", "确定要清空所有列表吗？"):
                self.local_songs = []
                self.quest_songs = []
                self.missing_songs = []

                self.local_list.set_songs([], self.song_ratings, self.song_names)
                self.quest_list.set_songs([], self.song_ratings, self.song_names)
                self.missing_list.set_songs([], self.song_ratings, self.song_names)

                # 清除高亮
                self.local_list.clear_highlights()
                self.quest_list.clear_highlights()

                # 清空歌曲信息
                for label in self.info_labels.values():
                    label.configure(text="未选择")

                # 清空难度显示
                self.update_difficulty_display([])

                # 清空评分和评价
                self.update_rating_display(0)
                self.comment_label.configure(text="未评价")

                self.cover_label.configure(text="🎵\n选择歌曲查看封面", image="")

                # 清空当前选中
                self.current_selected_list = None
                self.current_selected_song = None

                self.update_status("已清空所有列表")
                self.update_progress_info("已清空所有列表")
                logger.info("已清空所有歌曲列表")
        except Exception as e:
            logger.error(f"清空列表失败: {e}", exc_info=True)
            self.show_error(f"清空列表失败: {str(e)}")

    def open_help(self):
        """打开帮助文档"""
        try:
            help_window = HelpDialog(self.root)
            help_window.show()
        except Exception as e:
            logger.error(f"打开帮助文档失败: {e}", exc_info=True)
            self.show_error(f"打开帮助文档失败: {str(e)}")

    def open_settings(self):
        """打开设置对话框"""
        try:
            settings_window = ctk.CTkToplevel(self.root)
            settings_window.title("设置")
            settings_window.geometry("700x650")
            settings_window.transient(self.root)
            settings_window.grab_set()

            # 设置窗口居中
            settings_window.update_idletasks()
            width = settings_window.winfo_width()
            height = settings_window.winfo_height()
            x = (settings_window.winfo_screenwidth() // 2) - (width // 2)
            y = (settings_window.winfo_screenheight() // 2) - (height // 2)
            settings_window.geometry(f'{width}x{height}+{x}+{y}')

            # 标题
            ctk.CTkLabel(
                settings_window,
                text="⚙️ 设置",
                font=(FONT_FAMILY, 24, "bold")
            ).pack(pady=(30, 25))

            # 主题设置框架
            theme_frame = ctk.CTkFrame(settings_window, height=120)
            theme_frame.pack(fill="x", padx=40, pady=(0, 20))

            ctk.CTkLabel(
                theme_frame,
                text="主题设置",
                font=(FONT_FAMILY, 18, "bold")
            ).pack(anchor="w", padx=20, pady=(20, 15))

            # 外观模式
            appearance_frame = ctk.CTkFrame(theme_frame, fg_color="transparent")
            appearance_frame.pack(fill="x", padx=20, pady=(0, 20))

            ctk.CTkLabel(
                appearance_frame,
                text="外观模式:",
                font=(FONT_FAMILY, 14),
                width=100
            ).pack(side="left")

            # 获取当前模式
            current_mode = ctk.get_appearance_mode()
            if current_mode == "System":
                current_mode = "dark"

            appearance_var = ctk.StringVar(value=current_mode)
            appearance_combo = ctk.CTkOptionMenu(
                appearance_frame,
                values=["dark", "light"],
                variable=appearance_var,
                width=200,
                height=35,
                font=(FONT_FAMILY, 13),
                dropdown_font=(FONT_FAMILY, 12)
            )
            appearance_combo.pack(side="left", padx=(20, 0))

            # 数据库管理框架
            db_frame = ctk.CTkFrame(settings_window, height=150)
            db_frame.pack(fill="x", padx=40, pady=(0, 20))

            ctk.CTkLabel(
                db_frame,
                text="数据库管理",
                font=(FONT_FAMILY, 18, "bold")
            ).pack(anchor="w", padx=20, pady=(20, 15))

            # 刷新缓存按钮
            ctk.CTkButton(
                db_frame,
                text="🔄 刷新歌曲缓存",
                width=250,
                height=45,
                font=(FONT_FAMILY, 14),
                fg_color="#f39c12",
                hover_color="#e67e22",
                command=lambda: self.refresh_cache(settings_window)
            ).pack(padx=20, pady=(0, 10))

            ctk.CTkLabel(
                db_frame,
                text="清空并重新扫描所有歌曲信息",
                font=(FONT_FAMILY, 12),
                text_color="gray"
            ).pack(padx=20, pady=(0, 20))

            # 日志设置框架
            log_frame = ctk.CTkFrame(settings_window, height=120)
            log_frame.pack(fill="x", padx=40, pady=(0, 20))

            ctk.CTkLabel(
                log_frame,
                text="日志设置",
                font=(FONT_FAMILY, 18, "bold")
            ).pack(anchor="w", padx=20, pady=(20, 15))

            # 查看日志按钮
            ctk.CTkButton(
                log_frame,
                text="📁 查看日志文件",
                width=250,
                height=45,
                font=(FONT_FAMILY, 14),
                fg_color="#3498db",
                hover_color="#2980b9",
                command=self.open_log_directory
            ).pack(padx=20, pady=(0, 10))

            ctk.CTkLabel(
                log_frame,
                text="查看应用程序运行日志",
                font=(FONT_FAMILY, 12),
                text_color="gray"
            ).pack(padx=20, pady=(0, 20))

            # 按钮框架
            button_frame = ctk.CTkFrame(settings_window, fg_color="transparent", height=80)
            button_frame.pack(fill="x", padx=40, pady=(10, 30))

            # 保存按钮
            ctk.CTkButton(
                button_frame,
                text="💾 保存设置",
                width=180,
                height=50,
                font=(FONT_FAMILY, 16, "bold"),
                fg_color="#27ae60",
                hover_color="#229954",
                command=lambda: self.save_settings(appearance_var.get(), settings_window)
            ).pack(side="left", padx=(0, 30))

            # 取消按钮
            ctk.CTkButton(
                button_frame,
                text="❌ 取消",
                width=180,
                height=50,
                font=(FONT_FAMILY, 16),
                fg_color="#7f8c8d",
                hover_color="#616a6b",
                command=settings_window.destroy
            ).pack(side="right")

        except Exception as e:
            logger.error(f"打开设置窗口失败: {e}", exc_info=True)
            self.show_error(f"打开设置失败: {str(e)}")

    def save_settings(self, appearance, window):
        """
        保存设置 - 修复类型提示问题

        参数:
            appearance: 外观模式
            window: 设置窗口
        """
        try:
            # 应用主题设置
            ctk.set_appearance_mode(appearance)

            # 保存设置到文件
            settings = {
                "appearance": appearance,
                "local_path": self.local_path_var.get(),
                "quest_path": self.quest_path_var.get()
            }

            with open("settings.json", "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)

            self.update_status(f"设置已保存: {appearance}主题")
            self.show_info("设置已保存")
            logger.info(f"设置已保存: 外观模式={appearance}")

            window.destroy()

        except Exception as e:
            logger.error(f"保存设置失败: {e}", exc_info=True)
            self.show_error(f"保存设置失败: {e}")

    def open_log_directory(self):
        """打开日志目录"""
        try:
            log_dir = "logs"
            if os.path.exists(log_dir):
                # 根据操作系统打开目录
                if os.name == 'nt':  # Windows
                    os.startfile(log_dir)
                elif os.name == 'posix':  # Linux/Mac
                    import subprocess
                    subprocess.call(['open' if sys.platform == 'darwin' else 'xdg-open', log_dir])

                logger.info("打开日志目录")
            else:
                self.show_info("日志目录不存在")
        except Exception as e:
            logger.error(f"打开日志目录失败: {e}")
            self.show_error(f"打开日志目录失败: {str(e)}")

    def refresh_cache(self, window=None):
        """
        刷新数据库缓存

        参数:
            window: 设置窗口
        """
        try:
            if messagebox.askyesno("确认", "确定要刷新数据库缓存吗？这将重新扫描所有歌曲信息。"):
                # 更新进度信息
                self.update_progress_info("正在刷新数据库缓存...")

                # 在线程中执行
                thread = threading.Thread(
                    target=self.refresh_cache_background,
                    args=(window,),
                    name="Refresh_Cache"
                )
                thread.daemon = True
                thread.start()

        except Exception as e:
            logger.error(f"刷新缓存准备失败: {e}", exc_info=True)
            self.show_error(f"刷新缓存失败: {str(e)}")

    def refresh_cache_background(self, window=None):
        """
        后台刷新数据库缓存
        """
        try:
            # 清空数据库并重新创建
            if os.path.exists(self.db_manager.db_path):
                os.remove(self.db_manager.db_path)
                logger.info("已删除旧数据库文件")

            # 重新初始化数据库
            self.db_manager = DatabaseManager()
            self.song_manager = SongManager(self.db_manager)

            # 清空缓存
            self.song_ratings = {}
            self.song_names = {}

            # 完成
            self.progress_queue.put(("complete", "数据库缓存已刷新", 100))

            self.root.after(0, lambda: self.show_info("数据库缓存已刷新，下次加载时会重新扫描歌曲信息"))
            logger.info("数据库缓存已刷新")

            if window:
                self.root.after(0, window.destroy)

        except Exception as e:
            logger.error(f"刷新数据库失败: {e}", exc_info=True)
            self.progress_queue.put(("error", f"刷新数据库失败: {str(e)}", 100))

    def load_recent_paths(self):
        """加载最近使用的路径"""
        try:
            # 首先尝试从settings.json加载
            if os.path.exists("settings.json"):
                with open("settings.json", 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    if "local_path" in settings:
                        self.local_path_var.set(settings["local_path"])
                        logger.info(f"加载本地路径: {settings['local_path']}")
                    if "quest_path" in settings:
                        self.quest_path_var.set(settings["quest_path"])
                        logger.info(f"加载Quest路径: {settings['quest_path']}")

                    # 加载主题设置
                    if "appearance" in settings:
                        ctk.set_appearance_mode(settings["appearance"])
                        logger.info(f"加载主题设置: {settings['appearance']}")
        except Exception as e:
            logger.error(f"加载最近路径错误: {e}")

    def save_recent_paths(self):
        """保存最近使用的路径"""
        try:
            # 保存到settings.json
            settings = {}
            if os.path.exists("settings.json"):
                with open("settings.json", 'r', encoding='utf-8') as f:
                    settings = json.load(f)

            settings["local_path"] = self.local_path_var.get()
            settings["quest_path"] = self.quest_path_var.get()

            with open("settings.json", 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)

            logger.debug("保存最近路径到settings.json")
        except Exception as e:
            logger.error(f"保存最近路径错误: {e}")

    def check_progress_queue(self):
        """检查进度队列并更新UI"""
        try:
            while not self.progress_queue.empty():
                msg_type, message, percent = self.progress_queue.get_nowait()

                if msg_type == "progress":
                    self.update_progress_info(message)
                elif msg_type == "complete":
                    self.update_progress_info(message)
                    # 延迟2秒后重置为就绪状态
                    self.root.after(2000, lambda: self.update_progress_info("📊 状态: 就绪"))
                elif msg_type == "error":
                    self.update_progress_info(f"❌ {message}")
                    self.update_status(message, is_error=True)
                    # 延迟2秒后重置为就绪状态
                    self.root.after(2000, lambda: self.update_progress_info("📊 状态: 就绪"))
                    self.show_error(message)

        except queue.Empty:
            pass
        except Exception as e:
            logger.error(f"检查进度队列错误: {e}")

        # 定期检查队列
        self.root.after(100, self.check_progress_queue)

    def update_progress_info(self, message):
        """
        更新进度信息

        参数:
            message: 进度信息
        """
        self.progress_label.configure(text=message)

    def update_status(self, message, is_error=False):
        """
        更新状态栏

        参数:
            message: 状态信息
            is_error: 是否为错误信息
        """
        # 记录到日志
        if is_error:
            logger.error(f"状态更新: {message}")
        else:
            logger.info(f"状态更新: {message}")

    def show_error(self, message):
        """
        显示错误对话框

        参数:
            message: 错误信息
        """
        self.update_status(message, is_error=True)
        messagebox.showerror("错误", message)
        logger.error(f"显示错误对话框: {message}")

    def show_info(self, message):
        """
        显示信息对话框

        参数:
            message: 信息内容
        """
        messagebox.showinfo("信息", message)
        logger.info(f"显示信息对话框: {message}")

    def safe_quit(self):
        """安全退出程序"""
        try:
            logger.info("用户请求退出程序")
            self.root.quit()
        except Exception as e:
            logger.error(f"退出程序失败: {e}", exc_info=True)
            self.root.quit()

    def run(self):
        """运行应用程序主循环"""
        try:
            logger.info("启动应用程序主循环")
            self.root.mainloop()
            logger.info("应用程序正常退出")
        except Exception as e:
            logger.critical(f"应用程序主循环异常: {e}", exc_info=True)

# ================ 帮助对话框类 ================
class HelpDialog:
    """帮助文档对话框"""

    def __init__(self, parent):
        """
        初始化帮助对话框

        参数:
            parent: 父窗口
        """
        self.parent = parent

        # 创建对话框
        self.dialog = ctk.CTkToplevel(parent)
        self.dialog.title("帮助文档")
        self.dialog.geometry("800x700")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 设置窗口居中
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        x = (self.dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (height // 2)
        self.dialog.geometry(f'{width}x{height}+{x}+{y}')

        # 创建界面
        self._create_widgets()

    def _create_widgets(self):
        """创建对话框控件"""
        # 标题
        title_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        title_frame.pack(pady=(20, 15))

        ctk.CTkLabel(
            title_frame,
            text="❓ Quest歌单对比工具 - 使用说明",
            font=(FONT_FAMILY, 24, "bold")
        ).pack()

        ctk.CTkLabel(
            title_frame,
            text="版本 13.5 | 作者: 基于您的需求进行开发",
            font=(FONT_FAMILY, 12),
            text_color="gray"
        ).pack(pady=(5, 0))

        # 创建选项卡
        self.tabview = ctk.CTkTabview(self.dialog, width=750, height=550)
        self.tabview.pack(padx=20, pady=(0, 20), fill="both", expand=True)

        # 添加选项卡
        self.tabview.add("📖 基本介绍")
        self.tabview.add("🛠️ 使用方法")
        self.tabview.add("🔍 对比功能")
        self.tabview.add("📋 列表操作")
        self.tabview.add("⭐ 评分评价")
        self.tabview.add("⚙️ 其他功能")

        # 填充各选项卡内容
        self._fill_intro_tab()
        self._fill_usage_tab()
        self._fill_compare_tab()
        self._fill_list_tab()
        self._fill_rating_tab()
        self._fill_other_tab()

        # 关闭按钮
        button_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        button_frame.pack(pady=(0, 20))

        ctk.CTkButton(
            button_frame,
            text="✅ 关闭帮助",
            width=200,
            height=45,
            font=(FONT_FAMILY, 14, "bold"),
            fg_color="#27ae60",
            hover_color="#229954",
            command=self.dialog.destroy
        ).pack()

    def _fill_intro_tab(self):
        """填充基本介绍选项卡"""
        tab = self.tabview.tab("📖 基本介绍")

        content = r"""
🎯 软件简介
Quest歌单对比工具是一款专门用于管理Beat Saber自定义歌曲的软件。

📋 主要功能：
• 对比PC本地和Quest设备上的歌曲
• 识别缺失的歌曲并一键复制
• 为歌曲添加评分和个人评价
• 支持多种排序方式
• 显示歌曲详细信息（封面、难度等）
• 批量删除和管理歌曲

🖥️ 界面布局：
• 左侧：三个歌曲列表（本地、Quest、缺失）
• 右侧：歌曲详细信息区域
• 底部：操作按钮和状态信息

🔄 版本更新：
当前版本：V13.5
新增功能：歌单收藏、评分评价、增强错误日志、双向对比复制
修复问题：数据库字段缺失、高亮清除逻辑、takefocus参数错误
改进：移除进度条、排序支持正反序、添加帮助文档、修复封面尺寸、改进UI布局、修复上下键操作
        """

        text_widget = ctk.CTkTextbox(tab, wrap="word", font=(FONT_FAMILY, 12))
        text_widget.pack(padx=10, pady=10, fill="both", expand=True)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    def _fill_usage_tab(self):
        """填充使用方法选项卡"""
        tab = self.tabview.tab("🛠️ 使用方法")

        content = r"""
📍 第一步：设置路径
1. 点击"📂"按钮选择本地歌曲文件夹
   • 通常路径：...\Beat Saber\CustomLevels
2. 点击"📂"按钮选择Quest歌曲文件夹
   • 连接Quest设备到电脑
   • 通常路径：Quest3\内部共享存储空间\Android\data\com.beatgames.beatsaber\files\CustomLevels
   • 软件会自动尝试检测Quest路径

📥 第二步：加载歌曲
1. 点击"📥 载入本地歌曲"按钮
2. 点击"📱 载入Quest歌曲"按钮
3. 等待加载完成，歌曲会显示在对应列表中

🔍 第三步：对比歌曲
1. 点击"🔍 对比Quest缺失歌曲"按钮
   • 找出本地有但Quest没有的歌曲
2. 点击"🔍 对比本地缺失歌曲"按钮
   • 找出Quest有但本地没有的歌曲
3. 缺失的歌曲会显示在"缺失歌曲"列表中

📋 第四步：管理歌曲
• 点击列表中的歌曲可以查看详细信息
• 使用Delete键可以删除选中的歌曲
• 使用方向键可以在列表中导航
• 点击星星可以给歌曲评分
• 双击评价可以编辑个人评价

💡 小贴士：
• 保持本地和Quest路径正确设置
• 定期对比确保歌单同步
• 使用评分功能标记喜欢的歌曲
        """

        text_widget = ctk.CTkTextbox(tab, wrap="word", font=(FONT_FAMILY, 12))
        text_widget.pack(padx=10, pady=10, fill="both", expand=True)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    def _fill_compare_tab(self):
        """填充对比功能选项卡"""
        tab = self.tabview.tab("🔍 对比功能")

        content = r"""
🔄 双向对比功能
软件支持两种对比方式，确保歌单完全同步：

1. 🔍 对比Quest缺失歌曲
   • 功能：找出本地有但Quest没有的歌曲
   • 结果：在本地列表中用红色高亮显示
   • 用途：将本地新增的歌曲复制到Quest

2. 🔍 对比本地缺失歌曲
   • 功能：找出Quest有但本地没有的歌曲
   • 结果：在Quest列表中用红色高亮显示
   • 用途：将Quest新增的歌曲复制到本地

📤 一键复制功能
发现缺失歌曲后，可以一键复制：

1. 📤 缺失歌曲复制到Quest
   • 将"缺失歌曲"列表中的所有歌曲复制到Quest
   • 自动跳过已存在的歌曲
   • 显示复制进度和结果

2. 📤 缺失歌曲复制到本地
   • 将"缺失歌曲"列表中的所有歌曲复制到本地
   • 自动跳过已存在的歌曲
   • 显示复制进度和结果

🎯 使用场景：
• 场景1：在电脑上下载了新歌曲，需要同步到Quest
  → 载入本地和Quest歌曲 → 对比Quest缺失 → 复制到Quest

• 场景2：在Quest上安装了新歌曲，需要备份到电脑
  → 载入本地和Quest歌曲 → 对比本地缺失 → 复制到本地

• 场景3：清理重复或不需要的歌曲
  → 对比双方歌单 → 删除不需要的歌曲

⚠️ 注意事项：
• 复制前请确认目标路径有足够空间
• 复制过程可能需要较长时间（取决于歌曲数量）
• 建议定期备份重要歌曲
        """

        text_widget = ctk.CTkTextbox(tab, wrap="word", font=(FONT_FAMILY, 12))
        text_widget.pack(padx=10, pady=10, fill="both", expand=True)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    def _fill_list_tab(self):
        """填充列表操作选项卡"""
        tab = self.tabview.tab("📋 列表操作")

        content = r"""
📋 歌曲列表操作
软件有三个主要列表，每个都支持多种操作：

🎵 列表功能：
1. 📂 本地歌曲列表
   • 显示本地电脑上的歌曲
   • 红色高亮表示这些歌曲在Quest中缺失

2. 🎮 Quest歌曲列表
   • 显示Quest设备上的歌曲
   • 红色高亮表示这些歌曲在本地缺失

3. ✨ 缺失歌曲列表
   • 显示对比后发现的缺失歌曲
   • 根据对比方向显示不同的缺失歌曲

🔤 排序功能：
每个列表顶部有三个排序按钮：
• 🔢 默认排序：按原始顺序排列
• 🔤 名称排序：按歌曲名称排列
• ⭐ 评分排序：按个人评分排列

🖱️ 排序操作：
• 左键点击：正序排序（A-Z，低分到高分）
• 右键点击：反序排序（Z-A，高分到低分）
• 按钮上的箭头表示当前排序方向

🔍 搜索功能：
• 每个列表都有搜索框
• 输入关键词可以快速过滤歌曲
• 支持中文和英文搜索

⌨️ 键盘快捷键：
• ↑↓ 方向键：在列表中上下移动
• Enter键：选中歌曲（自动复制到剪贴板）
• Delete键：删除选中的歌曲
• 鼠标单击：选中歌曲并查看详情

📋 选中歌曲：
• 选中歌曲会自动复制到剪贴板
• 右侧会显示歌曲详细信息
• 可以给选中的歌曲评分和评价

🗑️ 删除歌曲：
• 点击"🗑️ 删除歌曲"按钮
• 或按Delete键
• 会永久删除歌曲文件夹
• 请谨慎操作！

🗑️ 清空列表：
• 点击"🗑️ 清空列表"按钮
• 仅清空列表显示，不删除文件
• 可以重新加载歌曲
        """

        text_widget = ctk.CTkTextbox(tab, wrap="word", font=(FONT_FAMILY, 12))
        text_widget.pack(padx=10, pady=10, fill="both", expand=True)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    def _fill_rating_tab(self):
        """填充评分评价选项卡"""
        tab = self.tabview.tab("⭐ 评分评价")

        content = r"""
⭐ 个人评分系统
为歌曲添加评分，方便管理和查找喜欢的歌曲。

🎯 评分功能：
• 点击星星可以给歌曲评分（1-5星）
• 金色星星表示已评分
• 灰色星星表示未评分
• 评分会自动保存到数据库

💭 个人评价：
• 双击"个人评价"区域可以编辑评价
• 评价最多20个字符
• 评价会自动保存到数据库
• 支持中文和特殊字符

📊 评分排序：
• 使用"⭐"排序按钮可以按评分排序
• 左键：高分在前（推荐）
• 右键：低分在前
• 未评分的歌曲默认0分

💾 数据保存：
• 评分和评价保存在本地数据库中
• 重新加载歌曲时会自动读取
• 数据库文件：songs_cache.db

🎮 使用建议：
• 给自己喜欢的歌曲打高分（4-5星）
• 给一般的歌曲打中分（2-3星）
• 给不喜欢的歌曲打低分（1星或不评分）
• 用评价记录特殊信息（如：难度太高、节奏感好等）

🔍 查找歌曲：
• 使用评分排序快速找到喜欢的歌曲
• 高评分歌曲通常值得优先玩
• 低评分歌曲可以考虑删除

🔄 修改评分：
• 随时可以修改评分和评价
• 修改后即时生效
• 重新加载后仍然保留
        """

        text_widget = ctk.CTkTextbox(tab, wrap="word", font=(FONT_FAMILY, 12))
        text_widget.pack(padx=10, pady=10, fill="both", expand=True)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    def _fill_other_tab(self):
        """填充其他功能选项卡"""
        tab = self.tabview.tab("⚙️ 其他功能")

        content = r"""
⚙️ 设置功能
点击"⚙️ 设置"按钮可以打开设置窗口：

🌓 外观设置：
• 深色模式（默认）：适合夜间使用
• 浅色模式：适合白天使用
• 设置会保存到settings.json文件

🗃️ 数据库管理：
• 刷新歌曲缓存：重新扫描所有歌曲信息
• 用于解决歌曲信息显示不正确的问题
• 注意：这会删除所有评分和评价

📁 日志管理：
• 查看日志文件：打开日志文件夹
• 日志记录所有操作和错误信息
• 用于故障排查和问题反馈

❓ 帮助文档：
• 点击"❓ 帮助"按钮打开此文档
• 包含详细的使用说明和技巧
• 建议新用户先阅读帮助文档

❌ 退出程序：
• 点击"❌ 退出程序"按钮安全退出
• 所有设置和数据会自动保存
• 下次启动时会自动恢复

🛠️ 故障排除：
常见问题及解决方法：

1. 无法检测到Quest路径
   • 确保Quest已连接到电脑
   • 在Quest上选择"文件传输"模式
   • 尝试手动选择路径

2. 歌曲信息显示不正确
   • 点击"设置" → "刷新歌曲缓存"
   • 确保歌曲文件夹包含info.dat文件

3. 复制歌曲失败
   • 检查目标路径是否有写入权限
   • 确保磁盘有足够空间
   • 查看日志文件了解具体错误

4. 程序运行缓慢
   • 歌曲数量过多时可能较慢
   • 可以分批加载和复制
   • 关闭不必要的程序

📞 技术支持：
• 查看日志文件获取详细错误信息
• 确保安装了所有依赖库
• 重新启动程序尝试解决问题

💡 使用技巧：
• 定期对比和同步歌单
• 使用评分功能管理歌曲
• 备份重要的歌曲文件夹
• 查看日志了解程序运行状态
        """

        text_widget = ctk.CTkTextbox(tab, wrap="word", font=(FONT_FAMILY, 12))
        text_widget.pack(padx=10, pady=10, fill="both", expand=True)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    def show(self):
        """显示对话框"""
        self.dialog.focus_set()
        self.dialog.wait_window()

# ================ 删除确认对话框类 ================
class DeleteConfirmDialog:
    """删除确认对话框"""

    def __init__(self, parent, title="确认删除", message="确定要删除吗？", on_confirm=None, on_cancel=None):
        """
        初始化删除确认对话框

        参数:
            parent: 父窗口
            title: 对话框标题
            message: 提示消息
            on_confirm: 确认回调函数
            on_cancel: 取消回调函数
        """
        self.parent = parent
        self.title = title
        self.message = message
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

        # 创建对话框
        self.dialog = ctk.CTkToplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("500x300")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 设置窗口居中
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        x = (self.dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (height // 2)
        self.dialog.geometry(f'{width}x{height}+{x}+{y}')

        # 创建界面
        self._create_widgets()

        # 绑定键盘事件
        self.dialog.bind("<Return>", lambda e: self._on_confirm())
        self.dialog.bind("<Escape>", lambda e: self._on_cancel())

    def _create_widgets(self):
        """创建对话框控件"""
        # 警告图标和标题
        warning_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        warning_frame.pack(pady=(40, 20))

        ctk.CTkLabel(
            warning_frame,
            text="⚠️",
            font=(FONT_FAMILY, 48),
            text_color="#e74c3c"
        ).pack()

        ctk.CTkLabel(
            warning_frame,
            text="警告",
            font=(FONT_FAMILY, 24, "bold"),
            text_color="#e74c3c"
        ).pack(pady=(10, 0))

        # 消息内容
        message_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        message_frame.pack(fill="x", padx=40, pady=(0, 30))

        ctk.CTkLabel(
            message_frame,
            text=self.message,
            font=(FONT_FAMILY, 14),
            justify="center",
            wraplength=400
        ).pack()

        # 按钮框架
        button_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        button_frame.pack(fill="x", padx=40, pady=(0, 30))

        # 确定按钮
        ctk.CTkButton(
            button_frame,
            text="✅ 确定删除 (Enter)",
            width=180,
            height=45,
            font=(FONT_FAMILY, 14, "bold"),
            fg_color="#e74c3c",
            hover_color="#c0392b",
            command=self._on_confirm
        ).pack(side="left", padx=(0, 20))

        # 取消按钮
        ctk.CTkButton(
            button_frame,
            text="❌ 取消 (Esc)",
            width=180,
            height=45,
            font=(FONT_FAMILY, 14),
            fg_color="#7f8c8d",
            hover_color="#616a6b",
            command=self._on_cancel
        ).pack(side="right")

    def _on_confirm(self):
        """确定删除回调"""
        if self.on_confirm:
            self.on_confirm()
        self.dialog.destroy()

    def _on_cancel(self):
        """取消删除回调"""
        if self.on_cancel:
            self.on_cancel()
        self.dialog.destroy()

    def show(self):
        """显示对话框"""
        self.dialog.focus_set()
        self.dialog.wait_window()

# ================ 确认对话框类 ================
class ConfirmDialog:
    """通用确认对话框（用于复制操作）"""

    def __init__(self, parent, title="确认操作", message="确定要执行此操作吗？",
                 detail="", on_confirm=None):
        """
        初始化确认对话框

        参数:
            parent: 父窗口
            title: 对话框标题
            message: 主要消息
            detail: 详细信息
            on_confirm: 确认回调函数
        """
        self.parent = parent
        self.title = title
        self.message = message
        self.detail = detail
        self.on_confirm = on_confirm

        # 创建对话框
        self.dialog = ctk.CTkToplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("500x350")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 设置窗口居中
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        x = (self.dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (height // 2)
        self.dialog.geometry(f'{width}x{height}+{x}+{y}')

        # 创建界面
        self._create_widgets()

        # 绑定键盘事件
        self.dialog.bind("<Return>", lambda e: self._on_confirm())
        self.dialog.bind("<Escape>", lambda e: self._on_cancel())

    def _create_widgets(self):
        """创建对话框控件"""
        # 图标和标题
        header_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        header_frame.pack(pady=(30, 20))

        ctk.CTkLabel(
            header_frame,
            text="❓",
            font=(FONT_FAMILY, 48),
            text_color="#3498db"
        ).pack()

        ctk.CTkLabel(
            header_frame,
            text="请确认操作",
            font=(FONT_FAMILY, 24, "bold"),
            text_color="#3498db"
        ).pack(pady=(10, 0))

        # 主要消息
        message_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        message_frame.pack(fill="x", padx=40, pady=(0, 10))

        ctk.CTkLabel(
            message_frame,
            text=self.message,
            font=(FONT_FAMILY, 16, "bold"),
            justify="center",
            wraplength=400
        ).pack()

        # 详细信息
        if self.detail:
            detail_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
            detail_frame.pack(fill="x", padx=40, pady=(0, 20))

            ctk.CTkLabel(
                detail_frame,
                text=self.detail,
                font=(FONT_FAMILY, 14),
                justify="center",
                wraplength=400,
                text_color="gray"
            ).pack()

        # 按钮框架
        button_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        button_frame.pack(fill="x", padx=40, pady=(0, 30))

        # 确定按钮
        ctk.CTkButton(
            button_frame,
            text="✅ 确定执行 (Enter)",
            width=180,
            height=45,
            font=(FONT_FAMILY, 14, "bold"),
            fg_color="#27ae60",
            hover_color="#229954",
            command=self._on_confirm
        ).pack(side="left", padx=(0, 20))

        # 取消按钮
        ctk.CTkButton(
            button_frame,
            text="❌ 取消 (Esc)",
            width=180,
            height=45,
            font=(FONT_FAMILY, 14),
            fg_color="#7f8c8d",
            hover_color="#616a6b",
            command=self._on_cancel
        ).pack(side="right")

    def _on_confirm(self):
        """确定回调"""
        if self.on_confirm:
            self.on_confirm()
        self.dialog.destroy()

    def _on_cancel(self):
        """取消回调"""
        self.dialog.destroy()

    def show(self):
        """显示对话框"""
        self.dialog.focus_set()
        self.dialog.wait_window()

# ================ 评价编辑对话框类 ================
class CommentEditDialog:
    """评价编辑对话框"""

    def __init__(self, parent, title="编辑评价", initial_text="", on_save=None):
        """
        初始化评价编辑对话框

        参数:
            parent: 父窗口
            title: 对话框标题
            initial_text: 初始文本
            on_save: 保存回调函数
        """
        self.parent = parent
        self.title = title
        self.initial_text = initial_text
        self.on_save = on_save

        # 创建对话框
        self.dialog = ctk.CTkToplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("500x350")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 设置窗口居中
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        x = (self.dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (height // 2)
        self.dialog.geometry(f'{width}x{height}+{x}+{y}')

        # 创建界面
        self._create_widgets()

        # 绑定键盘事件
        self.dialog.bind("<Return>", lambda e: self._on_save())
        self.dialog.bind("<Escape>", lambda e: self._on_cancel())

    def _create_widgets(self):
        """创建对话框控件"""
        # 标题
        title_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        title_frame.pack(pady=(30, 20))

        ctk.CTkLabel(
            title_frame,
            text="💭 编辑评价",
            font=(FONT_FAMILY, 24, "bold")
        ).pack()

        ctk.CTkLabel(
            title_frame,
            text="请输入您的评价（最多20个字符）",
            font=(FONT_FAMILY, 14),
            text_color="gray"
        ).pack(pady=(10, 0))

        # 文本输入框
        input_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        input_frame.pack(fill="both", expand=True, padx=40, pady=(0, 20))

        self.text_var = StringVar(value=self.initial_text)
        self.text_entry = ctk.CTkTextbox(
            input_frame,
            height=120,
            font=(FONT_FAMILY, 13),
            wrap="word"
        )
        self.text_entry.pack(fill="both", expand=True)
        self.text_entry.insert("1.0", self.initial_text)

        # 字符计数
        self.char_count_label = ctk.CTkLabel(
            input_frame,
            text=f"字符数: {len(self.initial_text)}/20",
            font=(FONT_FAMILY, 11),
            text_color="gray"
        )
        self.char_count_label.pack(anchor="e", pady=(5, 0))

        # 绑定文本变化事件
        self.text_entry.bind("<KeyRelease>", self._update_char_count)

        # 按钮框架
        button_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        button_frame.pack(fill="x", padx=40, pady=(0, 30))

        # 保存按钮
        ctk.CTkButton(
            button_frame,
            text="💾 保存 (Enter)",
            width=180,
            height=45,
            font=(FONT_FAMILY, 14, "bold"),
            fg_color="#27ae60",
            hover_color="#229954",
            command=self._on_save
        ).pack(side="left", padx=(0, 20))

        # 取消按钮
        ctk.CTkButton(
            button_frame,
            text="❌ 取消 (Esc)",
            width=180,
            height=45,
            font=(FONT_FAMILY, 14),
            fg_color="#7f8c8d",
            hover_color="#616a6b",
            command=self._on_cancel
        ).pack(side="right")

    def _update_char_count(self, event=None):
        """更新字符计数"""
        text = self.text_entry.get("1.0", "end-1c")
        char_count = len(text)
        self.char_count_label.configure(text=f"字符数: {char_count}/20")

        # 如果超过20个字符，截断
        if char_count > 20:
            self.text_entry.delete("1.0+20c", "end")
            self.char_count_label.configure(text="字符数: 20/20", text_color="red")
        else:
            self.char_count_label.configure(text_color="gray")

    def _on_save(self):
        """保存回调"""
        text = self.text_entry.get("1.0", "end-1c").strip()
        if self.on_save:
            self.on_save(text)
        self.dialog.destroy()

    def _on_cancel(self):
        """取消回调"""
        self.dialog.destroy()

    def show(self):
        """显示对话框"""
        self.dialog.focus_set()
        self.text_entry.focus_set()
        self.dialog.wait_window()

# ================ Quest路径选择对话框类 ================
class QuestPathSelectionDialog:
    """Quest路径选择对话框"""

    def __init__(self, parent, title="选择Quest路径", paths=None):
        """
        初始化Quest路径选择对话框

        参数:
            parent: 父窗口
            title: 对话框标题
            paths: 可选路径列表
        """
        self.parent = parent
        self.title = title
        self.paths = paths or []
        self.selected_path = None

        # 创建对话框
        self.dialog = ctk.CTkToplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("600x400")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 设置窗口居中
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        x = (self.dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (height // 2)
        self.dialog.geometry(f'{width}x{height}+{x}+{y}')

        # 创建界面
        self._create_widgets()

    def _create_widgets(self):
        """创建对话框控件"""
        # 标题
        title_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        title_frame.pack(pady=(30, 20))

        ctk.CTkLabel(
            title_frame,
            text="📱 选择Quest路径",
            font=(FONT_FAMILY, 24, "bold")
        ).pack()

        ctk.CTkLabel(
            title_frame,
            text="检测到以下可能的Quest歌曲路径，请选择：",
            font=(FONT_FAMILY, 14),
            text_color="gray"
        ).pack(pady=(10, 0))

        # 路径列表
        list_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=40, pady=(0, 20))

        if self.paths:
            # 创建滚动框架
            scroll_frame = ctk.CTkScrollableFrame(list_frame, height=200)
            scroll_frame.pack(fill="both", expand=True)

            # 为每个路径创建选择按钮
            for i, path in enumerate(self.paths):
                path_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
                path_frame.pack(fill="x", padx=5, pady=5)

                # 显示路径
                path_label = ctk.CTkLabel(
                    path_frame,
                    text=path,
                    font=(FONT_FAMILY, 12),
                    anchor="w",
                    wraplength=450,
                    justify="left"
                )
                path_label.pack(side="left", fill="x", expand=True, padx=(0, 10))

                # 选择按钮
                select_btn = ctk.CTkButton(
                    path_frame,
                    text="选择",
                    width=60,
                    height=30,
                    font=(FONT_FAMILY, 11),
                    command=lambda p=path: self._select_path(p)
                )
                select_btn.pack(side="right")
        else:
            # 没有检测到路径
            ctk.CTkLabel(
                list_frame,
                text="未检测到Quest路径。\n请点击下方按钮手动选择。",
                font=(FONT_FAMILY, 14),
                justify="center"
            ).pack(expand=True)

        # 按钮框架
        button_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        button_frame.pack(fill="x", padx=40, pady=(0, 30))

        # 手动选择按钮
        ctk.CTkButton(
            button_frame,
            text="📂 手动选择路径",
            width=180,
            height=45,
            font=(FONT_FAMILY, 14),
            fg_color="#3498db",
            hover_color="#2980b9",
            command=self._manual_select
        ).pack(side="left", padx=(0, 20))

        # 取消按钮
        ctk.CTkButton(
            button_frame,
            text="❌ 取消",
            width=180,
            height=45,
            font=(FONT_FAMILY, 14),
            fg_color="#7f8c8d",
            hover_color="#616a6b",
            command=self._on_cancel
        ).pack(side="right")

    def _select_path(self, path):
        """选择路径"""
        self.selected_path = path
        self.dialog.destroy()

    def _manual_select(self):
        """手动选择路径"""
        self.selected_path = None
        self.dialog.destroy()

    def _on_cancel(self):
        """取消选择"""
        self.selected_path = None
        self.dialog.destroy()

    def show(self):
        """显示对话框并返回选择结果"""
        self.dialog.focus_set()
        self.dialog.wait_window()

        if self.selected_path is None:
            # 如果用户点击了手动选择，使用文件对话框
            folder = filedialog.askdirectory(
                title="选择Quest歌曲文件夹",
                mustexist=True
            )
            return folder
        else:
            return self.selected_path

# ================ 主程序入口 ================
def main():
    """主函数，程序入口点"""
    try:
        logger.info("=" * 60)
        logger.info("程序启动")
        logger.info("=" * 60)

        # 检查customtkinter依赖
        import importlib.util
        spec = importlib.util.find_spec("customtkinter")
        if spec is None:
            print("错误: 需要安装 customtkinter")
            print("请运行: pip install customtkinter")
            return

        # 创建并运行应用
        app = SongComparatorApp()
        app.run()

    except Exception as e:
        logger.critical(f"程序启动失败: {e}", exc_info=True)
        print(f"程序启动失败: {e}")
        traceback.print_exc()

        # 显示错误对话框
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("启动错误", f"程序启动失败:\n{str(e)}")
        except:
            pass

if __name__ == "__main__":
    main()