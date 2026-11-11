Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $ProjectRoot '.runtime'
$VenvDir = Join-Path $RuntimeDir 'venv'
$PipCache = Join-Path $RuntimeDir 'pip-cache'
$UserBase = Join-Path $RuntimeDir 'python-user'
$TempDir = Join-Path $RuntimeDir 'tmp'
$CacheDir = Join-Path $RuntimeDir 'cache'
$TorchHome = Join-Path $RuntimeDir 'torch'
$HfHome = Join-Path $RuntimeDir 'huggingface'
$MatplotlibDir = Join-Path $RuntimeDir 'matplotlib'
$RuntimePathsFile = Join-Path $RuntimeDir 'runtime_paths.json'

function Check-NativeExit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step that bai (ma loi $LASTEXITCODE)." }
}

try {
    foreach ($path in @($RuntimeDir, $PipCache, $UserBase, $TempDir, $CacheDir, $TorchHome, $HfHome, $MatplotlibDir)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
    $env:PIP_CACHE_DIR = $PipCache
    $env:PYTHONUSERBASE = $UserBase
    $env:TMP = $TempDir
    $env:TEMP = $TempDir
    $env:XDG_CACHE_HOME = $CacheDir
    $env:TORCH_HOME = $TorchHome
    $env:HF_HOME = $HfHome
    $env:HUGGINGFACE_HUB_CACHE = Join-Path $HfHome 'hub'
    $env:MPLCONFIGDIR = $MatplotlibDir
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

    if ($env:PYTHON_EXE) {
        $Python = $env:PYTHON_EXE
        $PythonArgs = @()
    } else {
        $Python = 'py'
        $PythonArgs = @('-3.13')
    }

    Write-Output '[INFO] Kiem tra Python 3.13 64-bit va Tkinter...'
    & $Python @PythonArgs -c 'import sys, tkinter; assert sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32, "Can Python 3.13 64-bit"; print(sys.version)'
    Check-NativeExit 'Kiem tra Python'

    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Output '[INFO] Tao moi truong trong thu muc cua tool...'
        & $Python @PythonArgs -m venv $VenvDir
        Check-NativeExit 'Tao moi truong Python'
    }

    Write-Output '[INFO] Dang cai cac thanh phan can thiet. Buoc nay can mang va co the mat nhieu phut...'
    & $VenvPython -m pip install --cache-dir $PipCache -r (Join-Path $ProjectRoot 'requirements.txt')
    Check-NativeExit 'Cai dat thu vien'

    Write-Output '[INFO] Kiem tra cac thanh phan da cai...'
    & $VenvPython -c 'import demucs, torch, torchaudio, lameenc, imageio_ffmpeg; print("Demucs va cac thu vien da san sang")'
    Check-NativeExit 'Kiem tra thu vien'
    $Ffmpeg = & $VenvPython -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'
    Check-NativeExit 'Tim FFmpeg'
    $Demucs = Join-Path $VenvDir 'Scripts\demucs.exe'
    $YtDlp = Join-Path $VenvDir 'Scripts\yt-dlp.exe'
    foreach ($program in @($Ffmpeg, $Demucs, $YtDlp)) {
        if (-not (Test-Path -LiteralPath $program)) { throw "Khong tim thay: $program" }
    }

    $paths = [ordered]@{
        runtime_dir = $RuntimeDir
        python = $VenvPython
        ffmpeg = $Ffmpeg
        demucs = $Demucs
        ytdlp = $YtDlp
        pip_cache = $PipCache
        python_userbase = $UserBase
        temp = $TempDir
        cache = $CacheDir
        torch_home = $TorchHome
        hf_home = $HfHome
    }
    $paths | ConvertTo-Json -Depth 4 | Out-File -LiteralPath $RuntimePathsFile -Encoding UTF8
    Write-Output '[OK] Cai dat thanh cong. Mo tool bang MO_TACH_AM_THANH.bat.'
    Write-Output '[INFO] Lan tach dau tien can mang de tai model Demucs.'
    exit 0
} catch {
    Write-Warning "Cai dat that bai: $_"
    Write-Output '[INFO] Kiem tra Python 3.13 64-bit, ket noi mang va dung luong trong, sau do chay lai CAI_DAT.bat.'
    exit 1
}
