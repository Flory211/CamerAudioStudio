param([string]$Python = "$PSScriptRoot\.venv\Scripts\python.exe")
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Создай .venv и установи requirements.txt и pyinstaller, либо укажи -Python путь к python.exe.'
}
$previousPath = $env:PATH
Push-Location $PSScriptRoot
try {
    # Helper tools can ship an incompatible ICU DLL. Analyze with Windows/Python only.
    $runtimeDir = & $Python -c 'import sys; from pathlib import Path; print(Path(sys.base_prefix))'
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$runtimeDir;$(Split-Path -Parent $Python)"
    & $Python -m PyInstaller --noconfirm --workpath build\studio-release --distpath build\packaged CamerAudioStudio.spec *> build\studio-build.log
    if ($LASTEXITCODE -ne 0) { throw 'Сборка PyInstaller завершилась с ошибкой.' }
    $staged = Join-Path $PSScriptRoot 'build\packaged\CamerAudioStudio'
    $destination = Join-Path $PSScriptRoot 'dist\CamerAudioStudio'
    $backup = Join-Path $PSScriptRoot ('dist\CamerAudioStudio-previous-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    $report = Join-Path $PSScriptRoot ('build\studio-health-' + [guid]::NewGuid().ToString('N') + '.json')
    $process = Start-Process -FilePath (Join-Path $staged 'CamerAudioStudio.exe') -ArgumentList @('--smoke-test', '--report', ('"{0}"' -f $report)) -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(45000)) {
        $process.Kill()
        throw 'Проверка EXE не завершилась за 45 секунд. Предыдущая сборка сохранена.'
    }
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) { throw 'Проверка нового EXE не прошла. Предыдущая сборка сохранена.' }
    if (-not (Get-Content -LiteralPath $report -Raw | ConvertFrom-Json).ok) { throw 'Ошибка проверки интерфейса EXE.' }
    Copy-Item -LiteralPath $report -Destination (Join-Path $PSScriptRoot 'build\studio-packaged-health.json') -Force
    $workspaceRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    foreach ($target in @($staged, $destination, $backup)) {
        if (-not [IO.Path]::GetFullPath($target).StartsWith($workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Путь сборки вышел за пределы проекта.'
        }
    }
    if (Test-Path -LiteralPath $destination) { Move-Item -LiteralPath $destination -Destination $backup }
    New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'dist') -Force | Out-Null
    Move-Item -LiteralPath $staged -Destination $destination
    Write-Output "$PSScriptRoot\dist\CamerAudioStudio\CamerAudioStudio.exe"
} finally {
    $env:PATH = $previousPath
    Pop-Location
}
