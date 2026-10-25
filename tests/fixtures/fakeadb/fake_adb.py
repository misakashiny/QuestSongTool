"""鍋囩殑 adb锛氭妸璁惧绔懡浠ゆ槧灏勫埌涓€涓湰鍦扮洰褰曪紝鐢ㄦ潵鍦?*娌℃湁鐪熸満**鐨勬儏鍐典笅楠岃瘉 AdbFS銆?
鍘熺悊锛氱湡瀹?adb 鎺ュ彈 `-s SERIAL shell "鍛戒护"` / `push` / `pull` / `exec-out`銆?杩欎釜鑴氭湰瑙ｆ瀽鍚屾牱鐨勫弬鏁帮紝鎶?`/sdcard/...` 鏄犲皠鍒?`FAKE_DEVICE_ROOT` 涓嬬殑鍚屽悕鐩綍锛?鐒跺悗鎵ц绛変环鐨勬湰鍦版搷浣溿€傝繖鏍峰彲浠ョ湡瀹為獙璇侊細
  * 鍛戒护瀛楃涓叉嫾寰楀涓嶅銆佸崟寮曞彿杞箟瀵逛笉瀵癸紱
  * `find -printf`銆乣ls -1p`銆乣tar -cf -` 鐨勮緭鍑鸿兘鍚﹁ AdbFS 姝ｇ‘瑙ｆ瀽锛?  * push/pull 鐨勬枃浠舵祦鍚戞槸鍚︿笌棰勬湡涓€鑷淬€?
鐢ㄦ硶锛堢敱 _review/fakeadb/adb.bat 璋冪敤锛夛細
    python fake_adb.py -s SERIAL shell "ls -1p '/sdcard/x'"
"""
from __future__ import annotations

import io
import os
import re
import sys
import tarfile
from pathlib import Path

DEVICE_ROOT = Path(os.environ.get("FAKE_DEVICE_ROOT", Path(__file__).with_name("device")))
SERIAL = "FAKEQUEST1234"


def remote_to_local(remote: str) -> Path:
    text = str(remote).strip()
    for prefix in ("/sdcard", "/storage/emulated/0"):
        if text == prefix:
            return DEVICE_ROOT
        if text.startswith(prefix + "/"):
            return DEVICE_ROOT / text[len(prefix) + 1 :]
    return DEVICE_ROOT / text.lstrip("/")


def local_to_remote(path: Path) -> str:
    rel = path.relative_to(DEVICE_ROOT).as_posix()
    return "/sdcard" + ("" if rel == "." else "/" + rel)


def out(text: str = "", code: int = 0) -> int:
    """输出文本结果（**只走二进制通道**）。

    教训：同一个进程里既用 `sys.stdout.write()`（文本，会按 UTF-8 重新编码）
    又用 `sys.stdout.buffer.write()`（原始字节）会互相破坏 —— 文本层会把
    tar 的二进制内容当成文本重新编码，结果两端出现 UTF-8 BOM 和错位字节，
    tarfile 直接报 "bad checksum"。所以这里统一只用 buffer。
    """
    flush_binary()
    if text:
        sys.stdout.buffer.write(text.encode("utf-8", "replace"))
        if not text.endswith("\n"):
            sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()
    return code


def flush_binary() -> None:
    """把已缓冲的二进制数据真正推到管道另一端。"""
    try:
        sys.stdout.buffer.flush()
    except (AttributeError, ValueError, OSError):
        pass


def write_bytes(data: bytes) -> None:
    """写原始字节（tar 流 / 文件内容）。"""
    flush_binary()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


# --------------------------------------------------------------------------------------
# 设备端命令实现
# --------------------------------------------------------------------------------------
# 统一处理"带引号的路径"：AdbFS 会用单引号包裹含空格的路径，
# 如果正则里用 \S+ / .+?，遇到空格就会被截断（实测踩过：`Song A` 变成 `Song`）。
QUOTED = r"('[^']*(?:'\\''[^']*)*'|\"[^\"]*\"|\S+)"

TEST_RE = re.compile(
    r"^\[\s+(-[de])\s+" + QUOTED + r"\s+\]\s*&&\s*echo\s+YES\s*\|\|\s*echo\s+NO$"
)


def cmd_test(command: str) -> int | None:
    match = TEST_RE.match(command.strip())
    if not match:
        return None
    flag, quoted = match.groups()
    path = remote_to_local(sh_split(quoted)[0])
    ok = path.is_dir() if flag == "-d" else path.exists()
    return out("YES" if ok else "NO")


def cmd_ls(command: str) -> int | None:
    match = re.match(r"^ls\s+-1p\s+" + QUOTED + r"(\s+2>/dev/null)?$", command.strip())
    if not match:
        return None
    target = remote_to_local(sh_split(match.group(1))[0])
    if not target.is_dir():
        return out("", 1)
    lines = []
    for entry in sorted(target.iterdir(), key=lambda p: p.name):
        lines.append(entry.name + ("/" if entry.is_dir() else ""))
    return out("\n".join(lines))


def cmd_stat(command: str) -> int | None:
    match = re.match(
        r"^stat\s+-c\s+%Y\s+" + QUOTED + r"(\s+2>/dev/null)?(\s*\|\|\s*echo\s+0)?$", command.strip()
    )
    if not match:
        return None
    target = remote_to_local(sh_split(match.group(1))[0])
    if not target.exists():
        return out("0")
    return out(str(int(target.stat().st_mtime)))


def cmd_cat(command: str) -> int | None:
    match = re.match(r"^cat\s+" + QUOTED + r"$", command.strip())
    if not match:
        return None
    target = remote_to_local(sh_split(match.group(1))[0])
    if not target.is_file():
        return out("", 1)
    write_bytes(target.read_bytes())
    return 0


def cmd_find(command: str) -> int | None:
    stripped = command.strip()
    if not stripped.startswith("find "):
        return None
    match = re.match(
        r"^find\s+" + QUOTED + r"\s+(-mindepth\s+(\d+)\s+)?(-maxdepth\s+(\d+)\s+)?(.*)$", stripped
    )
    if not match:
        return None
    base = remote_to_local(sh_split(match.group(1))[0])
    min_depth = int(match.group(3)) if match.group(3) else 0
    max_depth = int(match.group(5)) if match.group(5) else None
    rest = match.group(6)
    if not base.is_dir():
        return out("", 1)

    name_filter: set[str] | None = None
    name_match = re.search(r"\\?\(\s*-name\s+(\S+)((?:\s+-o\s+-name\s+\S+)*)\s*\\?\)", rest)
    if name_match:
        name_filter = set(re.findall(r"-name\s+(\S+)", name_match.group(0)))
    elif "-name " in rest:
        name_filter = set(re.findall(r"-name\s+(\S+)", rest))

    type_filter = None
    type_match = re.search(r"-type\s+([fd])", rest)
    if type_match:
        type_filter = type_match.group(1)

    want_printf = "-printf" in rest
    lines: list[str] = []
    base_depth = len(base.parts)
    for current, dirs, files in os.walk(base):
        depth = len(Path(current).parts) - base_depth
        if max_depth is not None and depth > max_depth:
            dirs[:] = []
            continue
        entries = [(d, True) for d in dirs] + [(f, False) for f in files]
        for name, is_dir in entries:
            item_depth = depth + 1
            if item_depth < min_depth:
                continue
            if max_depth is not None and item_depth > max_depth:
                continue
            if type_filter == "f" and is_dir:
                continue
            if type_filter == "d" and not is_dir:
                continue
            if name_filter and name not in name_filter:
                continue
            path = Path(current) / name
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            if want_printf:
                # 忠实模拟真机（Quest 3 / Android 14 / toybox 0.8.9）的 **%y 缺陷**：
                #   单独 `-printf '%y\n'`      -> 0 行
                #   与其他格式符组合            -> 整个输出被截成 1 行
                # （实测：%f / %p / %T@ / %s 单独或两两组合都正常，只有沾 %y 就坏。）
                # 不模拟这个坑的话，`AdbFS.list_files` 曾经"每个目录只返回 1 个文件"
                # 那个缺陷就测不出来 —— 详见 quest_tool/device_adb.py 的 list_files。
                if "%y" in rest:
                    other_specifiers = any(
                        spec in rest for spec in ("%f", "%p", "%T", "%s")
                    )
                    if not other_specifiers or len(lines) >= 1:
                        continue  # 只 %y -> 什么都不输出；组合 -> 只留第一行
                # 按出现的格式符拼输出（不要靠整段字符串比较：命令里的转义写法
                # 可能有一层反斜杠差异，实测比较会失配）
                fields: list[str] = []
                if "%p" in rest:
                    fields.append(local_to_remote(path))
                elif "%f" in rest:
                    fields.append(name)
                if "%T@" in rest:
                    fields.append(f"{mtime:.6f}")
                elif "%T" in rest:
                    fields.append(str(int(mtime)))
                if "%y" in rest:
                    fields.append("d" if is_dir else "f")
                if "%s" in rest:
                    try:
                        fields.append(str(path.stat().st_size))
                    except OSError:
                        fields.append("0")
                lines.append("\t".join(fields) if fields else name)
            else:
                lines.append(local_to_remote(path))
    return out("\n".join(lines))


def sh_split(text: str) -> list[str]:
    """解析 POSIX shell 风格的参数（支持 AdbFS 用的单引号转义写法）。

    `shlex.split` 处理不了 `'\\''` 这种转义，会抛 No closing quotation，
    所以这里自己实现一个小的词法解析器。
    """
    tokens: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_single:
            if char == "'":
                in_single = False
            else:
                current.append(char)
            index += 1
            continue
        if in_double:
            if char == "\\" and index + 1 < len(text):
                current.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_double = False
            else:
                current.append(char)
            index += 1
            continue
        if char == "'":
            in_single = True
        elif char == '"':
            in_double = True
        elif char == "\\" and index + 1 < len(text):
            current.append(text[index + 1])
            index += 2
            continue
        elif char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
        index += 1
    if current:
        tokens.append("".join(current))
    return tokens


def cmd_tar(command: str) -> int | None:
    # 注意：`-C` 的参数是**带引号**的路径，可能含空格（AdbFS 用单引号包裹路径），
    # 所以这里必须先按"整段带引号"匹配，不能简单用 \S+（那会在空格处截断）。
    match = re.match(
        r"^tar\s+-cf\s+-\s+-C\s+('[^']*(?:'\\''[^']*)*'|\"[^\"]*\"|\S+)\s+(.+?)(\s+2>/dev/null)?$",
        command.strip(),
    )
    if not match:
        return None
    directory = remote_to_local(sh_split(match.group(1))[0])
    names = [sh_split(token)[0] for token in re.findall(r"'[^']*(?:'\\''[^']*)*'|\"[^\"]*\"|\S+", match.group(2))]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name in names:
            # 两种形式都要支持（成员名都原样保留成 tar 的 arcname）：
            #   旧：-C '<某歌曲目录>' info.dat
            #   新：-C / sdcard/.../<某歌曲目录>/info.dat
            # 后者是 bulk_read_files 的优化形式：一次 tar 覆盖任意目录，
            # 避免"一首歌一次 adb 往返"（真机实测差约 50 倍）。
            if name.startswith(("sdcard/", "storage/")):
                path = remote_to_local("/" + name)
            else:
                path = directory / name
            if path.is_file():
                tar.add(path, arcname=name)
    write_bytes(buffer.getvalue())
    return 0


def cmd_df(command: str) -> int | None:
    if not command.strip().startswith("df "):
        return None
    return out("Filesystem     1K-blocks     Used Available Use% Mounted on\n"
               "/dev/block/dm-9  60000000 20000000  40000000  34% /data\n")


def cmd_cd_ls(command: str) -> int | None:
    """支持 `cd '<dir>' && ls -1p 2>/dev/null`（AdbFS 使用的形式）。"""
    match = re.match(r"^cd\s+(.+?)\s*&&\s*ls\s+-1p(\s+2>/dev/null)?$", command.strip())
    if not match:
        return None
    target = remote_to_local(sh_split(match.group(1))[0])
    if not target.is_dir():
        return out("", 1)
    lines = [entry.name + ("/" if entry.is_dir() else "") for entry in sorted(target.iterdir(), key=lambda p: p.name)]
    return out("\n".join(lines))


def cmd_cd_find(command: str) -> int | None:
    """支持 `cd '<dir>' && find . -maxdepth N ...`（probe_adb 的兜底搜索用）。

    为什么要 `cd` 再 `find .`：真实设备上 `/sdcard` 是符号链接，
    toybox find 从它出发返回 0 行；改成先 cd 再搜 `.` 才有效。
    这里如实模拟"输出以 `./` 开头的相对路径"，好让调用方的路径拼接被真正测到。
    """
    match = re.match(r"^cd\s+(.+?)\s*&&\s*find\s+\.\s*(.*)$", command.strip())
    if not match:
        return None
    base = remote_to_local(sh_split(match.group(1))[0])
    if not base.is_dir():
        return out("", 1)
    rest = match.group(2)

    max_depth = None
    depth_match = re.search(r"-maxdepth\s+(\d+)", rest)
    if depth_match:
        max_depth = int(depth_match.group(1))

    name_filter: set[str] | None = None
    name_match = re.search(r"\\?\(\s*-name\s+(\S+)((?:\s+-o\s+-name\s+\S+)*)\s*\\?\)", rest)
    if name_match:
        name_filter = set(re.findall(r"-name\s+(\S+)", name_match.group(0)))

    type_filter = None
    type_match = re.search(r"-type\s+([fd])", rest)
    if type_match:
        type_filter = type_match.group(1)

    lines: list[str] = []
    base_depth = len(base.parts)
    for current, dirs, files in os.walk(base):
        depth = len(Path(current).parts) - base_depth
        if max_depth is not None and depth >= max_depth:
            dirs[:] = []
        candidates = [(d, True) for d in dirs] + [(f, False) for f in files]
        for name, is_dir in candidates:
            if type_filter == "f" and is_dir:
                continue
            if type_filter == "d" and not is_dir:
                continue
            if name_filter and name not in name_filter:
                continue
            rel = (Path(current) / name).relative_to(base).as_posix()
            lines.append("./" + rel)
    return out("\n".join(lines))


def cmd_shell(command: str) -> int:
    for handler in (cmd_test, cmd_cd_find, cmd_cd_ls, cmd_ls, cmd_stat, cmd_cat, cmd_find, cmd_tar, cmd_df):
        result = handler(command)
        if result is not None:
            return result
    if command.strip().startswith("mkdir -p"):
        path = remote_to_local(sh_split(command.split(None, 2)[2])[0])
        path.mkdir(parents=True, exist_ok=True)
        return 0
    if command.strip().startswith("rm -rf"):
        import shutil

        path = remote_to_local(sh_split(command.split(None, 2)[2])[0])
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        return 0
    if command.strip().startswith("mv "):
        # AdbFS.copy_into 用 `mv` 把"ASCII 暂存名"改成真实歌曲名
        # （adb push 从非 ASCII 本地路径推导名字会截断，见 copy_into 的注释）
        tokens = sh_split(command.strip()[3:])
        if len(tokens) == 2:
            src = remote_to_local(tokens[0])
            dst = remote_to_local(tokens[1])
            if src.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                elif dst.exists():
                    dst.unlink()
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
                return 0
        return out("mv: 参数错误或源不存在", 1)
    if "getprop" in command:
        return out("Quest 3" if "model" in command else "12")
    return out(f"fake-adb: 鏈疄鐜扮殑鍛戒护: {command[:120]}", 1)


# --------------------------------------------------------------------------------------
# 主入口
def main(argv: list[str]) -> int:
    args = list(argv)
    if not args:
        return out("fake adb", 0)

    # 真实 adb 的全局参数 -s <serial> 可以出现在子命令之前，先剥掉
    if args[0] == "-s" and len(args) >= 3:
        args = args[2:]

    if not args:
        return out("", 1)
    if args[0] == "devices":
        return out(f"List of devices attached\n{SERIAL}\tdevice product:hollywood model:Quest_3 device:hollywood")
    if args[0] in ("start-server", "kill-server"):
        return out("")

    verb = args[0]
    if verb == "shell":
        return cmd_shell(args[1] if len(args) > 1 else "")
    if verb == "exec-out":
        return cmd_shell(args[1] if len(args) > 1 else "")
    if verb == "push":
        import shutil

        local, remote_dir = Path(args[1]), remote_to_local(args[2])
        remote_dir.mkdir(parents=True, exist_ok=True)
        target = remote_dir / local.name
        if local.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(local, target)
            # 故障注入：模拟"只传了一部分文件"。
            # 真机上 adb push 真会这样（CHANGELOG v14.3 的目录名截断、v14.4 的
            # toybox %y 缺陷都表现为"文件没传全/看不到"），而离线时它太正确、
            # 测不到 `copy_into` 的复制后校验。设 FAKE_ADB_PUSH_SKIP=<子串> 即可。
            skip = os.environ.get("FAKE_ADB_PUSH_SKIP", "").strip()
            if skip:
                for item in list(target.rglob("*")):
                    if item.is_file() and skip in item.name:
                        item.unlink()
        else:
            shutil.copy2(local, target)
        return out(f"{local} pushed")
    if verb == "pull":
        import shutil

        remote, local_dir = remote_to_local(args[1]), Path(args[2])
        local_dir.mkdir(parents=True, exist_ok=True)
        target = local_dir / remote.name
        if remote.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(remote, target)
        elif remote.is_file():
            shutil.copy2(remote, target)
        else:
            return out(f"adb: error: remote object '{args[1]}' does not exist", 1)
        return out("1 file pulled")
    return out(f"fake-adb: 鏈疄鐜? {verb}", 1)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
