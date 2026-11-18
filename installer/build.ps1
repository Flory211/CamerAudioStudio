param(
    [string]$Archive = "$PSScriptRoot\..\dist\CamerAudioStudio-Windows-x64.zip",
    [string]$Compiler = 'makensis.exe'
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $project 'dist'
$stageRoot = (New-Item -ItemType Directory -Path (Join-Path $project 'build') -Force).FullName
$stage = New-Item -ItemType Directory -Path (Join-Path $stageRoot ('installer-' + [guid]::NewGuid().ToString('N')))
$setup = Join-Path $dist 'CamerAudioStudio-Setup-Windows-x64.exe'

try {
    $zip = (Get-Item -LiteralPath $Archive).FullName
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $stage.FullName)
    $payload = Join-Path $stage.FullName 'CamerAudioStudio'
    foreach ($file in 'CamerAudioStudio.exe', '_internal\python313.dll', 'Licenses\NOTICE.txt') {
        if (!(Test-Path -LiteralPath (Join-Path $payload $file) -PathType Leaf)) {
            throw "Release archive is missing $file"
        }
    }

    # Remove only shipped files and empty directories when uninstalling.
    $entries = Get-ChildItem -LiteralPath $payload -Recurse -Force |
        Sort-Object { $_.FullName.Length } -Descending
    $commands = foreach ($entry in $entries) {
        $relative = $entry.FullName.Substring($payload.Length).Replace('$', '$$')
        $verb = if ($entry.PSIsContainer) { 'RMDir' } else { 'Delete' }
        $verb + ' "$INSTDIR' + $relative + '"'
    }
    $uninstallFiles = Join-Path $stage.FullName 'uninstall.nsh'
    [IO.File]::WriteAllLines($uninstallFiles, [string[]]$commands, [Text.UTF8Encoding]::new($true))

    New-Item -ItemType Directory -Path $dist -Force | Out-Null
    & $Compiler /V3 /WX "/DSETUP_EXE=$setup" "/DPAYLOAD_DIR=$payload" "/DUNINSTALL_FILES=$uninstallFiles" (Join-Path $PSScriptRoot 'setup.nsi')
    if ($LASTEXITCODE -ne 0) { throw "NSIS exited with code $LASTEXITCODE" }
    Get-Item -LiteralPath $setup
} finally {
    $stagePath = [IO.Path]::GetFullPath($stage.FullName)
    if (![string]::Equals((Split-Path -Parent $stagePath), $stageRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unexpected installer staging directory: $stagePath"
    }
    Remove-Item -LiteralPath $stagePath -Recurse -Force
}
