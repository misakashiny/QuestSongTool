"""SQLite 缓存层。

相对原版的改进：
1. **单连接 + WAL**：原版每个方法都 `sqlite3.connect/close`，还跨线程使用；
   这里一个连接、`check_same_thread=False` + 互斥锁，读写性能与安全性都更好。
2. **不再用 INSERT OR REPLACE**：原版会 DELETE+INSERT，导致 `id` 变化、`created_at` 重置，
   且必须先把 rating/comment 读出来再写回去（多一次查询，还有竞态）。这里改成 UPSERT，
   并且把"用户数据"(评分/评价) 与"缓存数据"(标题/BPM…) 放到**两张表**，各写各的。
3. **评分/评价按位置存储**：原版以 folder_name 为主键，本地和 Quest 上同名歌曲会共用一条评分，
   在 Quest 上打分本地也跟着变。这里 users 表以 (location, folder_name) 为键。
4. **批量查询**：列表渲染前用一次 `get_all_ratings()` / `get_all_titles()` 拿全量，
   避免 N+1。
5. **schema 版本表**，方便以后平滑升级。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .logging_setup import get_logger

logger = get_logger("db")

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);

-- 缓存：以 (folder_name, mtime) 为键，mtime 变化即视为需要重新解析
CREATE TABLE IF NOT EXISTS meta (
    folder_name TEXT NOT NULL,
    mtime       REAL NOT NULL DEFAULT 0,
    title       TEXT NOT NULL DEFAULT '',
    subtitle    TEXT NOT NULL DEFAULT '',
    author      TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL DEFAULT '',
    bpm         REAL NOT NULL DEFAULT 0,
    duration    REAL NOT NULL DEFAULT 0,
    difficulties TEXT NOT NULL DEFAULT '',
    cover_file  TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (folder_name, mtime)
);
CREATE INDEX IF NOT EXISTS idx_meta_folder ON meta(folder_name);

-- 用户数据：按"位置 + 文件夹名"存储，位置标识由 fsapi 提供
CREATE TABLE IF NOT EXISTS userdata (
    location    TEXT NOT NULL,
    folder_name TEXT NOT NULL,
    rating      INTEGER NOT NULL DEFAULT 0,
    comment     TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (location, folder_name)
);
CREATE INDEX IF NOT EXISTS idx_userdata_folder ON userdata(folder_name);
"""


@dataclass(frozen=True)
class MetaRow:
    """缓存中的歌曲元数据。"""

    folder_name: str
    mtime: float
    title: str = ""
    subtitle: str = ""
    author: str = ""
    version: str = ""
    bpm: float = 0.0
    duration: float = 0.0
    difficulties: str = ""
    cover_file: str = ""

    @property
    def difficulty_list(self) -> list[str]:
        if not self.difficulties or self.difficulties in ("未知", "无"):
            return []
        return [d.strip() for d in self.difficulties.split(",") if d.strip()]


class Database:
    """线程安全的 SQLite 封装。"""

    def __init__(self, path: str | Path = "songs_cache.db") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    # ---------------- 基础 ----------------
    def _configure(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            self._conn.commit()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            cur = self._conn.execute("SELECT version FROM schema_info LIMIT 1")
            row = cur.fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] < 2:
                # Only derived metadata with no duration is stale. User ratings
                # and comments live in userdata and must remain untouched.
                self._conn.execute("DELETE FROM meta WHERE duration <= 0")
                self._conn.execute("UPDATE schema_info SET version=?", (SCHEMA_VERSION,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error as exc:  # pragma: no cover
                logger.warning("关闭数据库失败: %s", exc)

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------------- 元数据缓存 ----------------
    def get_meta(self, folder_name: str, mtime: float, tolerance: float = 1e-3) -> MetaRow | None:
        """取缓存；mtime 不一致（或不存在）时返回 None，表示需要重新解析。"""
        with self._lock:
            row = self._conn.execute(
                """SELECT folder_name, mtime, title, subtitle, author, version, bpm,
                          duration, difficulties, cover_file
                   FROM meta WHERE folder_name = ? AND ABS(mtime - ?) < ?""",
                (folder_name, float(mtime), tolerance),
            ).fetchone()
        if row is None:
            return None
        return MetaRow(**dict(row))

    def bulk_get_meta(self, keys: Iterable[tuple[str, float]]) -> dict[str, MetaRow]:
        """批量取缓存：一次查询解决整列表的渲染（避免 N+1）。

        用 `SELECT ? AS folder_name, ? AS mtime UNION ALL ...` 构造派生表，
        比 `(VALUES ...)` 兼容性更好（旧版 sqlite 不支持列命名 VALUES）。
        """
        result: dict[str, MetaRow] = {}
        keys = list(keys)
        if not keys:
            return result
        params: list[object] = []
        for name, mtime in keys:
            params.extend((name, float(mtime)))
        select = " UNION ALL ".join("SELECT ? AS folder_name, ? AS mtime" for _ in keys)
        sql = f"""
            SELECT m.folder_name, m.mtime, m.title, m.subtitle, m.author, m.version,
                   m.bpm, m.duration, m.difficulties, m.cover_file
            FROM meta m
            JOIN ({select}) AS k
              ON m.folder_name = k.folder_name AND ABS(m.mtime - k.mtime) < 0.001
        """
        with self._lock:
            for row in self._conn.execute(sql, params):
                result[row["folder_name"]] = MetaRow(**dict(row))
        return result

    def save_meta(self, meta: MetaRow) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO meta(folder_name, mtime, title, subtitle, author, version,
                                    bpm, duration, difficulties, cover_file, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(folder_name, mtime) DO UPDATE SET
                       title=excluded.title, subtitle=excluded.subtitle, author=excluded.author,
                       version=excluded.version, bpm=excluded.bpm, duration=excluded.duration,
                       difficulties=excluded.difficulties, cover_file=excluded.cover_file,
                       updated_at=excluded.updated_at""",
                (
                    meta.folder_name, float(meta.mtime), meta.title, meta.subtitle, meta.author,
                    meta.version, float(meta.bpm or 0), float(meta.duration or 0),
                    meta.difficulties, meta.cover_file, time.time(),
                ),
            )
            self._conn.commit()

    def save_meta_bulk(self, rows: Sequence[MetaRow]) -> None:
        """批量写入（单事务），扫描上千首歌时比逐条 commit 快一个数量级。"""
        if not rows:
            return
        payload = [
            (
                r.folder_name, float(r.mtime), r.title, r.subtitle, r.author, r.version,
                float(r.bpm or 0), float(r.duration or 0), r.difficulties, r.cover_file, time.time(),
            )
            for r in rows
        ]
        with self._lock:
            self._conn.executemany(
                """INSERT INTO meta(folder_name, mtime, title, subtitle, author, version,
                                    bpm, duration, difficulties, cover_file, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(folder_name, mtime) DO UPDATE SET
                       title=excluded.title, subtitle=excluded.subtitle, author=excluded.author,
                       version=excluded.version, bpm=excluded.bpm, duration=excluded.duration,
                       difficulties=excluded.difficulties, cover_file=excluded.cover_file,
                       updated_at=excluded.updated_at""",
                payload,
            )
            self._conn.commit()

    def prune_meta(self, keep_names: Iterable[str] | None = None) -> int:
        """清理元数据缓存，返回删掉的行数。

        两件事：
        1. **去重**（总是执行）：每个 `folder_name` 只保留 mtime 最新的那条。
           缓存查找是按 `(folder_name, mtime)` 精确匹配的，旧 mtime 的行永远不会命中，
           留着只会让库无限膨胀（歌曲改动一次就多一行）。
        2. **只保留给定集合**（传了 `keep_names` 才执行）：删掉不在集合里的 folder_name。

        ⚠️ 注意 `meta` 表**不按位置区分**（`userdata` 才按 `(location, folder_name)`），
        所以传 `keep_names` 时必须传**所有位置**（本地 + 设备）的并集，
        只传其中一个位置会把另一个位置的缓存全删掉 —— 宁可别传。

        修过的 bug（v14.5）：原实现是 `DELETE ... WHERE folder_name IN (...)`，
        与参数名和文档说的"只保留给定集合"**正好相反** —— 传进去要保留的，
        结果全被删了。因为此前从没被调用过，一直没暴露。
        """
        with self._lock:
            cur = self._conn.execute(
                """DELETE FROM meta WHERE rowid NOT IN (
                       SELECT rowid FROM (
                           SELECT rowid, ROW_NUMBER() OVER (
                               PARTITION BY folder_name ORDER BY mtime DESC) AS rn
                           FROM meta)
                       WHERE rn = 1)"""
            )
            removed = cur.rowcount or 0
            if keep_names is not None:
                names = list(keep_names)
                if names:
                    chunk = 500
                    for i in range(0, len(names), chunk):
                        part = names[i : i + chunk]
                        marks = ",".join("?" for _ in part)
                        removed += (
                            self._conn.execute(
                                f"DELETE FROM meta WHERE folder_name NOT IN ({marks})", part
                            ).rowcount
                            or 0
                        )
            self._conn.commit()
        return removed

    # ---------------- 用户数据 ----------------
    def get_rating(self, location: str, folder_name: str) -> tuple[int, str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT rating, comment FROM userdata WHERE location=? AND folder_name=?",
                (location, folder_name),
            ).fetchone()
        if row is None:
            return 0, ""
        return int(row["rating"] or 0), row["comment"] or ""

    def set_rating(self, location: str, folder_name: str, rating: int) -> None:
        rating = max(0, min(5, int(rating)))
        with self._lock:
            self._conn.execute(
                """INSERT INTO userdata(location, folder_name, rating, comment, updated_at)
                   VALUES (?,?,?, '', ?)
                   ON CONFLICT(location, folder_name) DO UPDATE SET
                       rating=excluded.rating, updated_at=excluded.updated_at""",
                (location, folder_name, rating, time.time()),
            )
            self._conn.commit()

    def set_comment(self, location: str, folder_name: str, comment: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO userdata(location, folder_name, rating, comment, updated_at)
                   VALUES (?,?,0,?,?)
                   ON CONFLICT(location, folder_name) DO UPDATE SET
                       comment=excluded.comment, updated_at=excluded.updated_at""",
                (location, folder_name, comment, time.time()),
            )
            self._conn.commit()

    # ---------------- 旧版数据库迁移（ROADMAP C3） ----------------
    def import_legacy(self, legacy_path: str | Path, location: str) -> dict[str, int]:
        """把原版 `songs_cache.db` 的评分/评价导入本库。

        原版是**单表** `songs`（`folder_name` 唯一），评分与评价混在歌曲元数据里；
        新版把用户数据拆到 `userdata`，键是 `(location, folder_name)` ——
        所以迁移必须知道"这些歌属于哪个位置"（例如 `local:D:\\Songs`、
        `adb:/sdcard/ModData/...`）。

        **幂等**：已存在的 `(location, folder_name)` 一律不动（`DO NOTHING`），
        所以重复执行既不会产生重复行，也**不会覆盖**你在新版里改过的评分。

        返回 `{"found": 旧库里有多少条有评分/评价, "imported": 新导入几条, "skipped": 跳过几条}`。
        """
        legacy_path = Path(legacy_path)
        if not legacy_path.exists():
            raise FileNotFoundError(f"旧数据库不存在：{legacy_path}")

        # 只读打开：绝不改动用户的旧库（那是他唯一的退路）
        legacy = sqlite3.connect(f"file:{legacy_path.as_posix()}?mode=ro", uri=True)
        legacy.row_factory = sqlite3.Row
        try:
            tables = {
                row["name"]
                for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "songs" not in tables:
                raise ValueError(
                    f"{legacy_path.name} 里没有 `songs` 表，不像是原版（单文件版）的数据库。"
                )
            rows = legacy.execute(
                "SELECT folder_name, rating, comment FROM songs "
                "WHERE folder_name IS NOT NULL AND TRIM(folder_name) <> '' "
                "AND (IFNULL(rating, 0) <> 0 OR IFNULL(comment, '') <> '')"
            ).fetchall()
        finally:
            legacy.close()

        imported = skipped = 0
        with self._lock:
            for row in rows:
                name = str(row["folder_name"]).strip()
                rating = max(0, min(5, int(row["rating"] or 0)))
                comment = str(row["comment"] or "")
                cursor = self._conn.execute(
                    """INSERT INTO userdata(location, folder_name, rating, comment, updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(location, folder_name) DO NOTHING""",
                    (location, name, rating, comment, time.time()),
                )
                if cursor.rowcount:
                    imported += 1
                else:
                    skipped += 1
            self._conn.commit()
        logger.info("旧库迁移：位置=%s，旧库有 %d 条，导入 %d 条，跳过 %d 条（已存在）",
                    location, len(rows), imported, skipped)
        return {"found": len(rows), "imported": imported, "skipped": skipped}

    def all_ratings(self, location: str | None = None) -> dict[str, int]:
        """一次性拿全部评分 {folder_name: rating}；location 为空则合并所有位置（取最大值）。"""
        with self._lock:
            if location is None:
                rows = self._conn.execute(
                    "SELECT folder_name, MAX(rating) AS r FROM userdata GROUP BY folder_name"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT folder_name, rating AS r FROM userdata WHERE location=?",
                    (location,),
                ).fetchall()
        return {r["folder_name"]: int(r["r"] or 0) for r in rows}

    def all_comments(self, location: str | None = None) -> dict[str, str]:
        with self._lock:
            if location is None:
                rows = self._conn.execute(
                    "SELECT folder_name, comment FROM userdata WHERE comment <> ''"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT folder_name, comment FROM userdata WHERE location=? AND comment <> ''",
                    (location,),
                ).fetchall()
        return {r["folder_name"]: r["comment"] for r in rows}

    def all_titles(self) -> dict[str, str]:
        """{folder_name: title}，取 mtime 最新的一条。"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT folder_name, title FROM (
                       SELECT folder_name, title, ROW_NUMBER() OVER (
                           PARTITION BY folder_name ORDER BY mtime DESC) AS rn
                       FROM meta)
                   WHERE rn = 1 AND title <> ''"""
            ).fetchall()
        return {r["folder_name"]: r["title"] for r in rows}

    # ---------------- 统计 ----------------
    def stats(self) -> Mapping[str, int]:
        with self._lock:
            meta = self._conn.execute("SELECT COUNT(*) AS c FROM meta").fetchone()["c"]
            user = self._conn.execute("SELECT COUNT(*) AS c FROM userdata").fetchone()["c"]
        return {"meta": int(meta), "userdata": int(user)}
