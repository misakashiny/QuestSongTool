# 通过 Shell.Application COM 枚举“此电脑”/便携设备(MTP) 的目录内容。
#
# 为什么需要它：Quest 3 以 MTP 连接时，Windows 只把它挂在 Shell 命名空间里（“此电脑”下），
# 没有盘符，tkinter 的 askdirectory 无法选中其中的文件夹。这个脚本用 COM 走命名空间，
# 就能把设备里的目录树读出来给自绘的“设备文件夹选择器”用。
#
# 输出：JSON（单行）
#   { ok, path, count, items: [ {name, shell_path, is_folder, type, size, modified} ] }
param(
    [string]$Path = "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-Json($obj) {
    $obj | ConvertTo-Json -Depth 4 -Compress
}

try {
    $shell = New-Object -ComObject Shell.Application
    if ([string]::IsNullOrEmpty($Path)) {
        $folder = $shell.NameSpace(17)          # ssfDRIVES = 此电脑
    } else {
        $folder = $shell.NameSpace($Path)
    }
} catch {
    Write-Json @{ ok = $false; error = "Shell.Application 初始化失败: $($_.Exception.Message)"; items = @() }
    exit 0
}

if ($null -eq $folder) {
    Write-Json @{ ok = $false; error = "无法打开命名空间: $Path"; items = @() }
    exit 0
}

# 解析列名 -> 列号（不同语言/系统列顺序不同，不能写死）
$colIndex = @{}
for ($i = 0; $i -lt 20; $i++) {
    $name = $folder.GetDetailsOf($null, $i)
    if ($name) { $colIndex[$name] = $i }
}
function Get-Col($item, [string[]]$names) {
    foreach ($n in $names) {
        if ($colIndex.ContainsKey($n)) { return $folder.GetDetailsOf($item, $colIndex[$n]) }
    }
    return ""
}

$items = New-Object System.Collections.ArrayList
foreach ($item in $folder.Items()) {
    $entry = [ordered]@{
        name       = $item.Name
        shell_path = $item.Path
        is_folder  = [bool]$item.IsFolder
        type       = Get-Col $item @("类型", "Type", "項目類型", "Item type")
        size       = Get-Col $item @("大小", "Size", "尺寸")
        modified   = Get-Col $item @("修改日期", "Date modified", "日期修改")
    }
    [void]$items.Add($entry)
}

Write-Json @{ ok = $true; path = $Path; count = $items.Count; items = $items }
