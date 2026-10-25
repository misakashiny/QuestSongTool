# 实验 2：1) 只用安静 flags（不 Replace）会不会弹窗/跳过；
#         2) 删除 MTP/shell 条目的几种方式哪种快且可靠。
$ErrorActionPreference = 'Stop'
$base = 'D:\QA\Quest3bs\_review\_copytest3'
$shell = New-Object -ComObject Shell.Application

# --- 准备 ---
Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path "$base\src\SongX" -Force | Out-Null
New-Item -ItemType Directory -Path "$base\dst\SongX" -Force | Out-Null
Set-Content "$base\src\SongX\info.dat" '{"new":true}' -Encoding UTF8
Set-Content "$base\src\SongX\newfile.dat" 'brand new' -Encoding UTF8
Set-Content "$base\dst\SongX\info.dat" '{"old":true}' -Encoding UTF8
Set-Content "$base\dst\SongX\stale.txt" 'stale' -Encoding UTF8

$src = $shell.NameSpace("$base\src")
$dst = $shell.NameSpace("$base\dst")

# --- 1) 只用安静 flags ---
$t0 = Get-Date
$dst.CopyHere($src.Items(), 1556)
Start-Sleep -Milliseconds 2500
$ms = [int]((Get-Date) - $t0).TotalMilliseconds
Write-Output ("[flags=1556] {0}ms info={1} 文件={2}" -f $ms,
    (Get-Content "$base\dst\SongX\info.dat" -Raw).Trim(),
    ((Get-ChildItem "$base\dst\SongX" | ForEach-Object Name) -join ','))

# --- 2) 删除方式对比（对本地文件系统的 shell 条目） ---
foreach ($mode in 'InvokeVerb', 'InvokeVerbEx', 'Shell.Application.Delete') {
    $victim = "$base\victim_$mode"
    New-Item -ItemType Directory -Path $victim -Force | Out-Null
    Set-Content "$victim\x.txt" 'x' -Encoding UTF8
    $parent = $shell.NameSpace($base)
    $item = $parent.ParseName((Split-Path $victim -Leaf))
    $t0 = Get-Date
    try {
        switch ($mode) {
            'InvokeVerb'   { $item.InvokeVerb('delete') }
            'InvokeVerbEx' { $item.InvokeVerbEx('delete') }
            'Shell.Application.Delete' { $item.InvokeVerb('删除') }
        }
    } catch { Write-Output ("[{0}] 抛异常: {1}" -f $mode, $_.Exception.Message) }
    # 轮询等待真的消失
    $gone = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        if (-not (Test-Path -LiteralPath $victim)) { $gone = $true; break }
    }
    $ms = [int]((Get-Date) - $t0).TotalMilliseconds
    Write-Output ("[{0}] {1}ms 已删除={2}" -f $mode, $ms, $gone)
}
