"""一键运行全部验证。

用法：
    python tests/run_all.py            # 跑全部
    python tests/run_all.py core adb   # 只跑指定的几组

注意：每组测试运行前会清掉 `__pycache__`。子目录里的辅助脚本（例如假的 adb）
如果源文件改了但字节码没更新，Python 会继续用旧代码 —— 这个坑实际踩过一次，
所以这里统一先清缓存。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

from _paths import ROOT, TESTS

HERE = TESTS
sys.path.insert(0, str(ROOT))

SUITES: list[tuple[str, str, str]] = [
    # 静态检查放第一个：不跑代码就能抓到的错误，没必要等到后面几组才发现
    ("static", "static_check.py", "静态：未定义名 / 编码 BOM / 未使用 import / 线程安全 / 设置字段"),
    # 配色检查是纯计算（不建窗口），放最前面 —— 改配色后第一时间就知道坏没坏
    ("theme", "theme_contrast.py", "配色：对比度 / 层次分离（防止又变回“一片白”）"),
    ("cover", "cover_geometry.py", "封面几何：装饰后不留大块补边（防止“大黑边”回归）"),
    # 诊断组会自己开一个 Tk 根起看门狗，但不弹可见窗口，放前面不影响后面几组
    ("diag", "smoke_diagnostics.py", "卡死诊断：慢操作记录 / 主线程看门狗 / 异常接管"),
    ("core", "e2e_core.py", "核心：解析 / 缓存 / 对比 / 护栏 / 真实复制删除"),
    ("adb", "e2e_adb.py", "ADB 后端：假 adb 驱动完整读写删"),
    ("mtp", "e2e_mtp.py", "MTP 后端：Shell CopyHere 镜像 + 解析"),
    ("gui", "e2e_gui.py", "GUI：真实窗口驱动 载入→对比→评分→复制→删除→搜索"),
    ("unit", "selftest_core.py", "单元：解析器 / 数据库 / 设置 / 路径判定"),
    ("deviceguard", "selftest_device_guard.py", "真机护栏：Quest 判据 / 拒绝非 Quest / 未验证退出码 2"),
    # 启动热点度量：跑 2 轮（要开真窗口、要多次起子进程，比其它组慢），
    # 只度量"预热是否还兑现"，不做"必须更快"的硬断言（机器忙时会假红）。
    ("startup", "perf_startup.py", "启动热点：emoji 字体预热是否仍兑现（warm vs cold）"),
    ("songlist", "smoke_songlist.py", "列表控件：性能 / 高亮 / 键盘"),
    ("features", "smoke_features.py", "新功能：多选批量 / 列宽 / 悬停 / 封面墙 / 拖放 / 设置往返"),
    ("dialogs", "smoke_dialogs.py", "对话框：可构造性"),
    ("i18n", "smoke_i18n.py", "多语言：文案表一致 / 切换生效 / 不许退回内联中文"),
    ("wireless", "smoke_wireless.py", "无线调试：假 adb 驱动配对/连接全流程 + 对话框"),
    ("duplicates", "smoke_duplicates.py", "重复歌曲检测：归一化 / 三档规则 / 不误报"),
    ("playlist", "smoke_playlist.py", "歌单读写：谱面 hash / 格式往返 / 容错 / 匹配"),
    # 试听会真的开音频设备（有声音）。放在截图组之前，避免和置顶窗口抢焦点；
    # 没装 miniaudio 时本组自己跳过，不会让整套变红。
    ("downloads", "smoke_downloads.py", "下载中心：安全解压 / 队列 / 历史 / 真实窗口入库"),
    ("search", "smoke_search.py", "歌曲搜索：接口 / 取消 / 跨页多选 / 详情 / 真实窗口下载"),
    ("discover", "smoke_discover.py", "推荐筛选媒体：网站参数 / 缓存 / 封面 / 真实试听与下载"),
    ("inspector", "smoke_inspector.py", "控件检查：真实按键 / 动态窗口 / 属性 / 绑定清理"),
    ("filters", "smoke_search_filters.py", "筛选表单：参数兼容 / 草稿 / 中英 / 四档缩放 / 键盘"),
    ("downloadlayout", "smoke_download_layout.py", "下载布局：封面列表身份 / 三页导航 / 共享底栏 / 缩放"),
    ("duration", "smoke_song_duration.py", "歌曲时长：音频头尾 / 旧缓存回填 / 实际界面"),
    ("audio", "smoke_audio.py", "试听：真音频解码 / 限长 / 流式生成器 / 优雅失败"),
    # 缩放检查会连续开三个不同倍率的窗口，截图回归要独占屏幕，所以排在它前面
    ("scaling", "scaling_check.py", "高 DPI：100% / 125% / 150% 下版式不重叠不裁切"),
    # 截图回归要弹一个真实窗口并置顶，所以放最后跑（前面的组都跑完再占屏幕）。
    # 基线是**机器相关**的（字体/DPI），换机器要先按 tests/screenshot_regression.py
    # 顶部的说明重新生成。
    ("shots", "screenshot_regression.py", "截图回归：与基线做像素比对（改错配色会失败）"),
]


def clear_caches() -> None:
    for base in (ROOT, HERE):
        for cache in base.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def run(name: str, script: str, desc: str) -> bool:
    path = HERE / script
    if not path.exists():
        print(f"[跳过] {name}: 找不到 {script}")
        return True
    print(f"\n{'=' * 78}\n[{name}] {desc}\n{'=' * 78}")
    started = time.monotonic()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
    )
    elapsed = time.monotonic() - started
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")

    # 只显示关键行，避免刷屏
    for line in stdout.splitlines():
        if any(token in line for token in ("[PASS]", "[FAIL]", "结果", "===", "DIALOG SMOKE", "SELF-CHECKS")):
            print(line)
    if proc.returncode != 0:
        print(f"--- 退出码 {proc.returncode}，耗时 {elapsed:.1f}s")
        tail = [ln for ln in stderr.splitlines() if ln.strip()][-12:]
        if tail:
            print("--- stderr（末尾）:")
            for line in tail:
                print(f"    {line}")
        # songlist 组有一条对机器负载敏感的性能断言，偶发失败时给出提示
        if name == "songlist":
            print("--- 提示：该组含性能断言（smoke_songlist.py:297「6 次击键 < 1.5s」），")
            print("    机器忙时可能偶发失败；请单独重跑确认：python tests\\smoke_songlist.py")
    else:
        print(f"--- 通过，耗时 {elapsed:.1f}s")
    return proc.returncode == 0


def main(argv: list[str]) -> int:
    wanted = set(argv) or {name for name, _, _ in SUITES}
    results: list[tuple[str, bool]] = []
    for name, script, desc in SUITES:
        if name not in wanted:
            continue
        clear_caches()
        results.append((name, run(name, script, desc)))

    print(f"\n{'=' * 78}\n汇总\n{'=' * 78}")
    for name, ok in results:
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 组通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
