# 实验：Shell Folder.CopyHere 在"目标已存在同名文件夹"时，不同 flags 的行为。
# 结论用来决定 shell_copy.ps1 的正确写法（是否能静默覆盖、会不会弹窗）。
$ErrorActionPreference = 'Stop'
$base = 'D:\QA\Quest3bs\_review\_copytest'
$shell = New-Object -ComObject Shell.Application
$src = $shell.NameSpace("$base\src")
$dst = $shell.NameSpace("$base\dst")

foreach ($flags in 4, 16, 20, 512, 528, 1556) {
    # 每次实验前把目标恢复成"旧版本 + 多余文件"
    $target = "$base\dst\SongX"
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Set-Content -LiteralPath "$target\info.dat" -Value '{"old":true}' -Encoding UTF8
    Set-Content -LiteralPath "$target\stale.txt" -Value 'stale' -Encoding UTF8

    $t0 = Get-Date
    $dst.CopyHere($src.Items(), $flags)
    Start-Sleep -Milliseconds 1500
    $elapsed = [int]((Get-Date) - $t0).TotalMilliseconds
    $info = if (Test-Path -LiteralPath "$target\info.dat") { (Get-Content -LiteralPath "$target\info.dat" -Raw).Trim() } else { '<缺失>' }
    $stale = Test-Path -LiteralPath "$target\stale.txt"
    $hasNew = Test-Path -LiteralPath "$target\newfile.dat"
    Write-Output ("flags={0,-5} {1,6}ms  info.dat={2,-16} 残留stale={3,-6} 新增文件={4}" -f $flags, $elapsed, $info, $stale, $hasNew)
}
