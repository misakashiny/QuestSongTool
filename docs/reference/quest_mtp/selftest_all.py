r"""selftest_all.py —— 一键自检全部模块（不需要连接 Quest，也不会弹窗阻塞）。

用法::

    python selftest_all.py

它会：

1. 逐个 `py_compile` 编译四个模块（语法检查）
2. 依次运行各模块的 `selftest()`
3. 真实驱动 folder_picker 的**懒加载展开**逻辑（构造对话框对象后立即销毁，
   不需要用户点鼠标）
4. 汇总三条查找路线的结果

全部通过返回 0，有任一模块失败返回 1。
"""

from __future__ import annotations

import io
import os
import py_compile
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MODULES = ("shell_mtp.py", "adb_bridge.py", "quest_paths.py", "folder_picker.py")


def out(*a, **k):
    k.setdefault("flush", True)
    print(*a, **k)


def section(title: str) -> None:
    out("")
    out("#" * 72)
    out(f"# {title}")
    out("#" * 72)


def step_compile() -> bool:
    section("步骤 1：py_compile 语法检查")
    import shutil

    ok = True
    # 说明：这里用 py_compile 的默认输出位置（模块旁边的 __pycache__），
    # 不写临时目录 —— 本机沙箱会拒绝写 %TEMP%。
    # 同样不能用 cfile=os.devnull：Windows 上 "nul" 不是普通文件，
    # py_compile 会抛 FileExistsError。
    cache = os.path.join(HERE, "__pycache__")
    try:
        for name in MODULES:
            path = os.path.join(HERE, name)
            try:
                py_compile.compile(path, doraise=True)
                out(f"  [OK]   {name}")
            except py_compile.PyCompileError as exc:
                out(f"  [FAIL] {name}: {exc}")
                ok = False
            except Exception as exc:  # noqa: BLE001
                out(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
                ok = False
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    return ok


def step_imports() -> bool:
    section("步骤 2：导入检查")
    ok = True
    for mod in ("shell_mtp", "adb_bridge", "quest_paths", "folder_picker"):
        try:
            __import__(mod)
            out(f"  [OK]   import {mod}")
        except Exception as exc:  # noqa: BLE001
            out(f"  [FAIL] import {mod}: {type(exc).__name__}: {exc}")
            ok = False
    return ok


def step_module_selftests() -> bool:
    section("步骤 3：各模块 selftest()")
    import adb_bridge
    import folder_picker
    import quest_paths
    import shell_mtp

    ok = True
    for label, fn in (
        ("shell_mtp.selftest()", shell_mtp.selftest),
        ("adb_bridge.selftest()", adb_bridge.selftest),
        ("quest_paths.selftest()", quest_paths.selftest),
        ("folder_picker.selftest()", folder_picker.selftest),
    ):
        out("")
        out(f"----- {label} -----")
        try:
            # 把每个模块的输出原样打出来，同时保留返回码
            rc = fn(verbose=True)
            out(f"  ==> {label} 返回 {rc}")
            if rc != 0:
                ok = False
        except Exception:  # noqa: BLE001
            out(f"  [FAIL] {label} 抛异常：")
            out(traceback.format_exc())
            ok = False
    return ok


def step_picker_lazy_expand() -> bool:
    """真实驱动对话框的懒加载逻辑（不弹窗、不阻塞）。"""
    section("步骤 4：folder_picker 懒加载展开实测")
    import tkinter as tk

    import folder_picker as fp

    if not fp.TK_AVAILABLE:
        out("  tkinter 不可用，跳过。")
        return True

    root = tk.Tk()
    root.withdraw()
    dlg = fp.QuestFolderPicker(master=root, allow_all_roots=True)

    roots = dlg.tree.get_children("")
    out(f"  根节点数 = {len(roots)}")
    ok = len(roots) > 0
    for r in roots:
        n = dlg._node_by_item.get(r)
        nkids = len(dlg.tree.get_children(r))
        out(f"    根: {dlg.tree.item(r, 'text')!r} kind={n.kind} 占位子节点={nkids}")

    # 挑一个有真实文件系统路径的节点来展开
    target = None
    for r in roots:
        n = dlg._node_by_item.get(r)
        if n and n.fs_path:
            target = r
            break

    if target is None:
        out("  没有可展开的本地盘节点（正常：可能只有便携设备）。")
    else:
        n = dlg._node_by_item[target]
        out(f"  展开 {dlg.tree.item(target, 'text')!r} …")
        dlg.tree.item(target, open=True)
        dlg.tree.focus(target)
        dlg._on_open(None)
        kids = dlg.tree.get_children(target)
        out(f"    展开后子项数 = {len(kids)}")
        n_folder = sum(1 for k in kids if dlg._node_by_item[k].is_folder)
        n_file = len(kids) - n_folder
        out(f"    其中 目录 {n_folder} 个 / 文件 {n_file} 个")
        out(f"    状态栏: {dlg.status_var.get()}")
        # 分类正确性对账（只对本地盘有意义）
        import os as _os
        mism = []
        for k in kids:
            nd = dlg._node_by_item[k]
            if nd.fs_path:
                real = _os.path.isdir(nd.fs_path)
                if real != nd.is_folder:
                    mism.append((nd.name, nd.is_folder, real))
        out(f"    is_folder 与 os.path.isdir 不一致: {len(mism)} 项")
        for m in mism:
            out(f"      ! {m}")
        ok = ok and not mism
        if kids:
            dlg.tree.selection_set(kids[0])
            dlg.tree.focus(kids[0])
            dlg._on_select(None)
            out(f"    选中后 entry = {dlg.entry_var.get()!r}")

    # 手输 → 结果对象
    out("  手输路径 → 结果对象:")
    for s in (
        "C:\\Windows",
        "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels",
        "Quest 3\\内部共享存储空间\\ModData",
        "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\Quest 3",
    ):
        r = dlg._make_result_from_text(s)
        out(f"    {s[:56]:<58} kind={r.kind:<6} usable={r.usable}")

    dlg._on_cancel()
    try:
        root.update()
        root.destroy()
    except Exception:
        pass
    return ok


def step_three_routes() -> bool:
    section("步骤 5：三条查找路线汇总")
    import quest_paths as qp

    found = qp.find_quest_customlevels()
    out(f"  find_quest_customlevels() -> {len(found)} 条")
    for r in found:
        out(f"    kind={r.kind:<6} usable={r.usable!s:<5} display={r.display}")
    if not found:
        out("  （本机没有连接 Quest，属正常结果。）")
    out("")
    out(qp.diagnose().as_text())
    return True


def main() -> int:
    out("=" * 72)
    out("Quest MTP 模块总自检 (selftest_all.py)")
    out("=" * 72)
    out(f"工作目录: {HERE}")
    out(f"Python  : {sys.version.split()[0]} ({'64' if sys.maxsize > 2**32 else '32'}-bit)")
    out(f"平台    : {sys.platform}")

    results = {
        "py_compile": step_compile(),
        "import": step_imports(),
        "module selftests": step_module_selftests(),
        "picker 懒加载": step_picker_lazy_expand(),
        "三条路线": step_three_routes(),
    }

    section("总结果")
    for k, v in results.items():
        out(f"  {'[PASS]' if v else '[FAIL]'}  {k}")
    all_ok = all(results.values())
    out("")
    out("=" * 72)
    out("全部通过 ✔" if all_ok else "存在失败项 ✘")
    out("=" * 72)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
