# 用 Shell.Application(COM) 在"便携设备(MTP)"与本地之间复制文件。
#
# 背景：Quest 3 走 MTP 时没有盘符，Python 的 os/shutil 完全用不了。
# 但 Shell 命名空间里的条目可以用 Folder.CopyHere() 复制 —— 这是**不需要装 adb**就能
# 读取设备歌曲的唯一途径。注意：
#   * 只能批量复制目录/文件，粒度粗（无法逐块读取）；
#   * CopyHere 是**异步**的，返回后要轮询目标位置的文件数判断是否完成；
#   * ★ 目标已存在同名文件夹时 CopyHere **不会替换**，而是跳过冲突文件
#     （旧 info.dat 原样保留，等于"这首歌没更新"）；
#   * ★ 更麻烦的是：标志里缺 FOF_NOCONFIRMATION 时，Windows 会弹
#     「目标已包含名为 X 的文件夹 / 替换或跳过」对话框并**一直等人点**。
#     实测（_review/probe_copyhere.ps1）：flags=4 时旧 info.dat 未被覆盖、耗时 38 秒
#     （就是在等人点），这正是"执行时老是弹复制/跳过弹窗"的原因。
#
# 因此本脚本：
#   1. 默认 $FLAGS = FOF_SILENT|FOF_NOCONFIRMATION|FOF_NOCONFIRMMKDIR|FOF_NOERRORUI
#      （=1556），任何情况下都不弹窗；
#   2. 提供 -Replace：先把目标里的同名文件夹**改名挪开**再复制，复制一定能进去
#      （绕开"已存在就跳过"），旧内容留在 `<名字>__旧版本_questbak` 里由用户自行清理。
#      为什么不在脚本里删：实测删除 shell/MTP 条目要 10~16 秒且 Windows 仍会弹确认框
#      （_review/probe_delete.ps1），自动化里不能等。
param(
    [Parameter(Mandatory = $true)][ValidateSet("to_local", "to_device")][string]$Direction,
    [Parameter(Mandatory = $true)][string]$Source,   # 源：shell 路径或本地路径
    [Parameter(Mandatory = $true)][string]$Dest,     # 目标：shell 路径或本地路径
    [int]$TimeoutSeconds = 900,
    [switch]$Move,
    [switch]$Replace,                                # 目标同名项先改名挪开再复制（真正的"覆盖更新"）
    [string]$BackupSuffix = "__旧版本_questbak",
    # 只复制源目录里**这一个**条目（不填则复制源目录里的全部条目）。
    #
    # ★ 为什么必须有它：CopyHere 的语义是"把**源文件夹里的条目**放进目标文件夹"，
    #   所以想复制"某一首歌"时，源必须传**歌曲库目录** + `-Only <歌曲名>`；
    #   直接传歌曲库目录会把**整个歌库**都推过去（v14.5 之前就是这样，复制一首歌
    #   等于把用户上千首全传一遍）；直接传歌曲目录则会把它的**内容**平铺进目标
    #   （info.dat 散落在 CustomLevels 根下）。
    [string]$Only = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-Json($obj) { $obj | ConvertTo-Json -Depth 3 -Compress }

# Shell 复制标志：
#   FOF_SILENT(4)           不显示进度对话框
#   FOF_NOCONFIRMATION(16)  不弹"确认/替换"对话框
#   FOF_NOERRORUI(1024)     出错也不弹窗（避免自动化被对话框卡住）
#   FOF_NOCONFIRMMKDIR(512) 自动创建目录不询问
# 组合后 = 1556。注意这些标志**只影响弹窗**，不改变"已存在则跳过"的语义，
# 真正的替换由 -Replace 走"改名挪开"的流程完成。
$FLAGS = 1556

try {
    $shell = New-Object -ComObject Shell.Application
    $started = Get-Date

    function Resolve-Folder([string]$p) {
        return $shell.NameSpace($p)
    }

    function Count-LocalItems([string]$path) {
        if (-not (Test-Path -LiteralPath $path)) { return 0 }
        return @(Get-ChildItem -LiteralPath $path -Force -Recurse -ErrorAction SilentlyContinue).Count
    }

    function Remove-LocalQuiet([string]$path) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    function Remove-ShellItem([object]$item, [string]$fallbackLocalPath) {
        # 优先用 shell 动词删除（MTP 设备也能用），失败再退回本地删除
        try {
            $item.InvokeVerb("delete")
            Start-Sleep -Milliseconds 800
            return $true
        } catch {
            if ($fallbackLocalPath) { Remove-LocalQuiet $fallbackLocalPath }
            return $false
        }
    }

    if ($Direction -eq "to_local" -and -not (Test-Path -LiteralPath $Dest)) {
        New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    }

    $sourceFolder = Resolve-Folder $Source
    $destFolder = Resolve-Folder $Dest
    if ($null -eq $sourceFolder) { Write-Json @{ ok = $false; error = "无法打开源: $Source"; copied = 0 }; exit 0 }
    if ($null -eq $destFolder) { Write-Json @{ ok = $false; error = "无法打开目标: $Dest"; copied = 0 }; exit 0 }

    # 要复制的条目：指定了 -Only 就只取那一个，否则取源目录的全部条目
    $onlyItem = $null
    if ($Only) {
        $onlyItem = $sourceFolder.ParseName($Only)
        if ($null -eq $onlyItem) {
            Write-Json @{ ok = $false; copied = 0; error = "源目录里找不到条目: $Only" }
            exit 0
        }
        $sourceCount = 1
    } else {
        $sourceCount = $sourceFolder.Items().Count
    }
    if ($Direction -eq "to_local" -and $sourceCount -eq 0) {
        Write-Json @{ ok = $false; copied = 0; error = "源目录为空或无法访问: $Source" }
        exit 0
    }

    # 参与"同名冲突"判断的源条目名字集合
    $sourceNames = @()
    if ($Only) {
        $sourceNames = @($Only)
    } else {
        foreach ($srcItem in @($sourceFolder.Items())) { $sourceNames += $srcItem.Name }
    }

    # ---------- 可选：把目标里同名条目挪开，保证是"替换"而不是"跳过" ----------
    $backups = @()
    if ($Replace) {
        foreach ($item in @($destFolder.Items())) {
            if ($sourceNames -notcontains $item.Name) { continue }
            $newName = $item.Name + $BackupSuffix
            try {
                $item.Name = $newName                      # MTP 与本地都支持改名
                $backups += ,@($newName, $item)
                Start-Sleep -Milliseconds 400
            } catch {
                # 改名失败：退而求其次，直接删掉旧的（内容将被新复制覆盖）
                $local = if ($Direction -eq "to_local") { Join-Path $Dest $item.Name } else { "" }
                [void](Remove-ShellItem $item $local)
            }
        }
    }

    $before = 0
    if ($Direction -eq "to_local") { $before = Count-LocalItems $Dest }

    if ($Only) {
        # 只复制这一个条目（直接传 FolderItem）
        if ($Move) {
            $destFolder.MoveHere($onlyItem, $FLAGS)
        } else {
            $destFolder.CopyHere($onlyItem, $FLAGS)
        }
    } else {
        $items = $sourceFolder.Items()
        if ($Move) {
            $destFolder.MoveHere($items, $FLAGS)
        } else {
            $destFolder.CopyHere($items, $FLAGS)
        }
    }

    # CopyHere 是异步的：轮询直到数量稳定（连续 5 次不变）或超时
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $stable = 0
    $last = -1
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
        if ($Direction -eq "to_local") {
            $now = Count-LocalItems $Dest
        } else {
            $now = $destFolder.Items().Count
        }
        if ($now -eq $last) {
            $stable++
            if ($stable -ge 5) { break }
        } else {
            $stable = 0
        }
        $last = $now
    }

    # ---------- 清理挪走的旧文件夹 ----------
    # 只在本地目标上做（Remove-Item 是瞬时的）；MTP 目标上删除要 10~16 秒且会弹确认框，
    # 留给用户自己在资源管理器里删 <名字>__旧版本_questbak。
    if ($Direction -eq "to_local") {
        foreach ($pair in $backups) {
            Remove-LocalQuiet (Join-Path $Dest $pair[0])
        }
    }

    if ($Direction -eq "to_local") {
        $copied = (Count-LocalItems $Dest) - $before
    } else {
        $copied = $sourceCount
    }
    $elapsed = [int]((Get-Date) - $started).TotalSeconds
    Write-Json @{
        ok             = $true
        copied         = $copied
        expected       = $sourceCount
        replaced       = $backups.Count
        waited_seconds = $elapsed
        error          = ""
    }
} catch {
    Write-Json @{ ok = $false; copied = 0; error = $_.Exception.Message }
}
