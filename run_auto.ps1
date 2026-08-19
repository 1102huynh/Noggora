# Wrapper invoked by the daily Windows Scheduled Task ("Noggora Daily Video").
# Not meant to be run manually day-to-day -- use `python main.py auto` directly
# for that. This just adds logging + a stable working directory so the task
# works the same regardless of what directory Task Scheduler starts it in.

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

# Make sure ffmpeg/ffprobe are resolvable even if this task's process
# inherited an environment captured before ffmpeg was added to PATH (observed
# to happen for long-lived parent processes on this machine) -- locate the
# winget-installed copy explicitly and prepend it rather than assuming PATH
# is already correct.
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    $ffmpegBin = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin" -Directory -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
    if ($ffmpegBin) {
        $env:Path = "$ffmpegBin;$env:Path"
    }
}

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "auto.log"

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    [System.IO.File]::AppendAllText($logFile, "$(Get-Date -Format o)  ERROR: venv not found at $venvPython -- run: python -m venv .venv; pip install -r requirements.txt`r`n")
    exit 1
}

# Redirect via Start-Process (raw byte-level file redirection) instead of
# PowerShell's *>>/2>&1 operators -- those pipe output through the console's
# text formatting first, which mangles UTF-8 (✅/✋/❌ markers, Vietnamese
# diacritics) on this system's default codepage. Start-Process writes exactly
# the bytes the child process emits.
$stdoutTmp = Join-Path $logDir "auto_stdout.tmp"
$stderrTmp = Join-Path $logDir "auto_stderr.tmp"

[System.IO.File]::AppendAllText($logFile, "`r`n===== $(Get-Date -Format o) : starting python main.py auto =====`r`n")

$proc = Start-Process -FilePath $venvPython -ArgumentList @("main.py", "auto") `
    -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $stdoutTmp -RedirectStandardError $stderrTmp

foreach ($tmp in @($stdoutTmp, $stderrTmp)) {
    if (Test-Path $tmp) {
        $text = Get-Content -Path $tmp -Raw -Encoding UTF8
        if ($text) { [System.IO.File]::AppendAllText($logFile, $text) }
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

[System.IO.File]::AppendAllText($logFile, "===== $(Get-Date -Format o) : exit code $($proc.ExitCode) =====`r`n")
