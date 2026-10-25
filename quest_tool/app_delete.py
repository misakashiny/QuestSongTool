"""删除：「缺失」列表的删除要删对侧，这是踩过坑的地方。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .fsapi import FSError, FSUnavailable
from .logging_setup import get_logger
from .ui_dialogs import ConfirmDialog
from .workers import GuardError, Token, guard_delete
from .messages import tr

logger = get_logger("app_delete")


class DeleteMixin:
    """删除：「缺失」列表的删除要删对侧，这是踩过坑的地方。"""

    def _real_kind(self, kind: str) -> str:
        """把 "missing" 映射到它当前代表的那一侧（本地/设备）。

        "missing"（Quest 缺的）只是一个**视图**，它自己没有 fs ——
        删除、取封面、取评分这类操作必须先映射到真实的一侧。
        v14.6 之前删除直接传 kind="missing"，`self.libraries["missing"].fs` 是 None，
        于是按 Delete 只弹一句"请先载入该歌曲列表"，**什么都不做**。
        """
        return self.missing_side if kind == "missing" else kind

    def _on_row_delete(self, kind: str, folder: str) -> None:
        """Delete 键/右键删除：多选时批量删除，单选时删一首。"""
        names = self.selected_songs.get(kind) or []
        if len(names) > 1 and folder in names:
            self.delete_songs(self._real_kind(kind), names)
        elif kind == "missing":
            # 缺失列删的是"另一侧"的那首，用专门的确认文案
            self.delete_missing_song(folder)
        else:
            self.delete_song(kind, folder)

    def delete_songs(self, kind: str, names: Sequence[str]) -> None:
        """批量删除（一次确认、逐首执行、失败逐条列出）。"""
        kind = self._real_kind(kind)
        library = self.libraries[kind]
        if library.fs is None or library.location is None:
            self.toast.warning("无法删除", detail=tr("del.need_load"))
            return
        names = [n for n in names if n]
        if not names:
            return
        location = library.location

        if location.kind == "local":
            try:
                targets = [guard_delete(Path(location.root), n) for n in names]
            except GuardError as exc:
                self.toast.error("已阻止删除", detail=str(exc))
                return
            detail = f"目录：{location.root}\n共 {len(targets)} 个文件夹，此操作不可撤销。"
        else:
            detail = f"设备目录：{location.root}\n共 {len(names)} 个文件夹，此操作不可撤销。"
            if not self._confirm_device_delete():
                return

        preview = "\n".join(names[:8]) + (f"\n…另有 {len(names) - 8} 首" if len(names) > 8 else "")
        if not ConfirmDialog(
            self.root,
            title=tr("del.confirm_batch_title"),
            message=f"确定删除这 {len(names)} 首歌曲吗？",
            detail=f"{preview}\n\n{detail}",
            confirm_text=f"删除 {len(names)} 首",
            confirm_is_danger=True,
        ).show():
            return

        def worker(token: Token) -> None:
            ok = 0
            failures: list[str] = []
            for index, name in enumerate(names, start=1):
                if not token.alive():
                    break
                try:
                    assert library.fs is not None
                    library.fs.delete_folder(name)
                    if library.fs.local_folder_exists(name):
                        raise FSError("删除后目录仍然存在")
                    ok += 1
                    self.tasks.emit("delete", f"已删除 {name}", -1,
                                    payload={"ok": True, "kind": kind, "folder": name}, token=token)
                except (FSError, FSUnavailable, OSError, GuardError) as exc:
                    failures.append(f"{name}: {exc}")
                self.tasks.emit("progress", f"删除中 {index}/{len(names)}", int(index / len(names) * 100),
                                token=token)
            summary = f"批量删除完成：成功 {ok}，失败 {len(failures)}"
            if failures:
                summary += "\n" + "\n".join(failures[:5])
            self.tasks.emit("done", summary, 100, token=token)
            # 注意：这里是后台线程，**不能**直接调 self.toast.*（那会直接操作 Tk）
            self.tasks.emit(
                "toast", "", -1,
                payload={
                    "level": "warning" if failures else "success",
                    "text": summary.splitlines()[0],
                    "detail": "\n".join(failures[:3]) if failures else "",
                },
                token=token,
            )

        self.tasks.submit(worker, name="batch_delete")

    def delete_selected_song(self) -> None:
        if not self.current_song:
            self.toast.warning("请先在列表里选中一首歌曲")
            return
        self.delete_song(self.current_song[0], self.current_song[1])

    def delete_song(self, kind: str, folder: str) -> None:
        kind = self._real_kind(kind)
        library = self.libraries[kind]
        if library.fs is None or library.location is None:
            self.toast.warning("无法删除", detail=tr("del.need_load"))
            return
        location = library.location
        if location.kind == "local":
            try:
                target = guard_delete(Path(location.root), folder)
            except GuardError as exc:
                self.toast.error("已阻止删除", detail=str(exc))
                return
            detail = f"目录：{target}\n此操作不可撤销。"
        else:
            detail = f"设备目录：{location.root}/{folder}\n此操作不可撤销。"
            if not self._confirm_device_delete():
                return

        if not ConfirmDialog(
            self.root,
            title=tr("del.confirm_title"),
            message=f"确定删除歌曲「{folder}」吗？",
            detail=detail,
            confirm_text="删除",
            confirm_is_danger=True,
        ).show():
            return

        def worker(token: Token) -> None:
            try:
                assert library.fs is not None
                library.fs.delete_folder(folder)
            except (FSError, FSUnavailable, OSError, GuardError) as exc:
                self.tasks.emit("delete", f"删除失败：{exc}", 100, payload={"ok": False}, token=token)
                return
            assert library.fs is not None
            if library.fs.local_folder_exists(folder):
                self.tasks.emit("delete", "删除后目录仍然存在，请到设备上手动确认", 100,
                                payload={"ok": False}, token=token)
                return
            self.tasks.emit("delete", f"已删除：{folder}", 100,
                            payload={"ok": True, "kind": kind, "folder": folder}, token=token)

        self.tasks.submit(worker, name=f"delete_{folder}")

    def _confirm_device_delete(self) -> bool:
        if not self.settings.verify_before_delete:
            return True
        dialog = ConfirmDialog(
            self.root,
            title=tr("del.device_title"),
            message=tr("del.device_message"),
            detail=tr("del.device_detail"),
            confirm_text="我明白，继续",
            confirm_is_danger=True,
            show_again_checkbox=True,
            checkbox_text="以后不再提示（可在设置里重新打开）",
        )
        confirmed = dialog.show()
        if confirmed and dialog.dont_ask_again:
            self.settings.verify_before_delete = False
            self.settings.save()
        return confirmed

    def delete_missing_song(self, folder: str) -> None:
        """删除「缺失」列表里的一首 —— 删的是**拥有它的那一侧**。

        方向很容易写反：`missing_side` 指的是"**缺**它的那一侧"。
        例如 `missing_side == "quest"`（标题「Quest 缺的」）时，这首歌在**本地**，
        所以要删本地那一份。与 `show_missing_song` 的取法保持一致。

        （v14.6 之前这里写的是 `"local" if missing_side == "local" else "quest"`，
         方向正好反了 —— 会去设备上删一首本来就不在设备上的歌，必然失败；
         而且这个方法此前根本没有被调用过，所以一直没暴露。）
        """
        side = "local" if self.missing_side == "quest" else "quest"
        if ConfirmDialog(
            self.root,
            title=tr("del.missing_title"),
            message=f"删除「{folder}」？",
            detail=f"将删除 {self._label_for(side)} 中的该歌曲（另一侧保留）。",
            confirm_text=f"删除 {self._label_for(side)} 中的",
            confirm_is_danger=True,
        ).show():
            self.delete_song(side, folder)

    def _after_delete(self, payload: dict[str, Any]) -> None:
        kind = payload.get("kind", "")
        folder = payload.get("folder", "")
        library = self.libraries.get(kind)
        if library and folder in library.folders:
            library.folders.remove(folder)
            library.meta.pop(folder, None)
        self._list_for(kind).remove_item(folder)
        self.list_missing.remove_item(folder)
        if self.current_song and self.current_song[1] == folder:
            self.current_song = None
