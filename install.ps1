# Jarvis: one-line install for Windows (in PowerShell):
#
#   irm https://raw.githubusercontent.com/suganya122/jarvis/main/install.ps1 | iex
#
# Same steps as install.sh: uv (pinned SHA-256) and a private Python 3.12, Jarvis from the latest
# GitHub Release (checked against SHA256SUMS), your API key, the speech model, the natural voice
# if you want it, start at login, and it starts. No admin rights. Run again to update.
# For tests/CI: JARVIS_YES=1, JARVIS_WHEEL + JARVIS_REQUIREMENTS, JARVIS_SKIP_VOICE=1,
# JARVIS_SKIP_START=1, JARVIS_PREFIX.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Repo = if ($env:JARVIS_REPO) { $env:JARVIS_REPO } else { "suganya122/jarvis" }
$Name = "Jarvis"
$Prefix = if ($env:JARVIS_PREFIX) { $env:JARVIS_PREFIX } else { Join-Path $env:LOCALAPPDATA "JarvisApp" }
$UvVersion = "0.13.0"
$Yes = $env:JARVIS_YES -eq "1"

function Say($text) { Write-Host "==> $text" -ForegroundColor Green }
function Warn($text) { Write-Host "!! $text" -ForegroundColor Yellow }
function Ask($question, $default) {
    if ($Yes) { return $default -eq "y" }
    $hint = if ($default -eq "y") { "Y/n" } else { "y/N" }
    $answer = Read-Host "$question [$hint]"
    if (-not $answer) { $answer = $default }
    return $answer -match "^[yY]"
}
function Fetch($url, $out) { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $out }
function Sha256($path) { (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLower() }

# ---- 1. which system --------------------------------------------------------------------
switch ($env:PROCESSOR_ARCHITECTURE) {
    "AMD64" { $UvTarget = "x86_64-pc-windows-msvc"; $UvSha = "088962f9e7b7bd9ea740c04c650b2a21c8928c345bd99ac24350dc924dba656c" }
    "ARM64" { $UvTarget = "aarch64-pc-windows-msvc"; $UvSha = "cb54028b59aa87f11cf6dec293ce000420043a49a8aea522e822a671321f8932" }
    default { throw "Unsupported processor: $env:PROCESSOR_ARCHITECTURE" }
}
Say "Installing $Name for Windows ($env:PROCESSOR_ARCHITECTURE) into $Prefix"
New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("jarvis-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

try {
    # ---- 2. uv and a private Python -----------------------------------------------------
    $Uv = Join-Path $Prefix "uv\uv.exe"
    $have = if (Test-Path $Uv) { (& $Uv --version).Split(" ")[1] } else { "" }
    if ($have -ne $UvVersion) {
        Say "Getting uv $UvVersion (Python manager)..."
        $zip = Join-Path $Tmp "uv.zip"
        Fetch "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-$UvTarget.zip" $zip
        if ((Sha256 $zip) -ne $UvSha) { throw "uv didn't match its pinned checksum; stopping." }
        Expand-Archive -Force -Path $zip -DestinationPath (Join-Path $Prefix "uv")
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Prefix "python"
    $env:UV_NO_CONFIG = "1"
    Say "Setting up Python 3.12..."
    & $Uv venv --quiet --allow-existing --python 3.12 (Join-Path $Prefix "venv")
    $Py = Join-Path $Prefix "venv\Scripts\python.exe"
    $Pyw = Join-Path $Prefix "venv\Scripts\pythonw.exe"

    # ---- 3. Jarvis itself ----------------------------------------------------------------
    if ($env:JARVIS_WHEEL) {
        $Wheel = $env:JARVIS_WHEEL; $Reqs = $env:JARVIS_REQUIREMENTS
    } else {
        $Base = "https://github.com/$Repo/releases/latest/download"
        Say "Downloading the latest $Name release..."
        $sums = Join-Path $Tmp "SHA256SUMS"
        Fetch "$Base/SHA256SUMS" $sums
        $lines = Get-Content $sums | ForEach-Object { $parts = $_ -split "\s+", 2; [pscustomobject]@{ Hash = $parts[0]; File = $parts[1].TrimStart("*") } }
        $WheelName = ($lines | Where-Object { $_.File -like "*.whl" } | Select-Object -First 1).File
        if (-not $WheelName) { throw "The release has no wheel." }
        foreach ($file in @($WheelName, "requirements.txt")) {
            $out = Join-Path $Tmp $file
            Fetch "$Base/$file" $out
            $want = ($lines | Where-Object { $_.File -eq $file }).Hash
            if ((Sha256 $out) -ne $want) { throw "$file didn't match the release checksum; stopping." }
        }
        $Wheel = Join-Path $Tmp $WheelName; $Reqs = Join-Path $Tmp "requirements.txt"
    }
    Say "Installing $Name and its libraries..."
    & $Uv pip install --quiet --python $Py -r $Reqs
    & $Uv pip install --quiet --python $Py --no-deps --reinstall $Wheel

    $Bin = Join-Path $Prefix "bin"
    New-Item -ItemType Directory -Force -Path $Bin | Out-Null
    Set-Content -Encoding ASCII -Path (Join-Path $Bin "jarvis.cmd") -Value "@`"$Py`" -m core %*"
    Set-Content -Encoding ASCII -Path (Join-Path $Bin "jarvis-cli.cmd") -Value "@`"$Py`" -m core.cli %*"
    Set-Content -Encoding ASCII -Path (Join-Path $Bin "jarvis-uninstall.cmd") -Value @"
@echo off
rem Remove $Name. Your data (settings, chats, API key file) stays unless you add --all.
"$Py" -m core.cli autostart off >nul 2>&1
taskkill /f /im pythonw.exe /fi "WINDOWTITLE eq $Name" >nul 2>&1
if "%1"=="--all" "$Py" -m core.cli uninstall --yes
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\$Name.lnk" >nul 2>&1
rmdir /s /q "$Prefix"
echo $Name was removed.
"@
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not ($userPath -split ";" | Where-Object { $_ -eq $Bin })) {
        [Environment]::SetEnvironmentVariable("Path", (($userPath, $Bin) -join ";").Trim(";"), "User")
    }

    # ---- 4. API key, voice ---------------------------------------------------------------
    & $Py -c "import sys; from core import env_file, paths; sys.exit(0 if env_file.load(env_file.find_env_file(paths.data_dir())).get('ANTHROPIC_API_KEY') else 1)"
    if ($LASTEXITCODE -eq 0) {
        Say "Your API key is already set."
    } elseif (-not $Yes) {
        $secure = Read-Host "Paste your Anthropic API key (from console.anthropic.com; hidden), or press Enter to skip" -AsSecureString
        $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        if ($key) { $key | & $Py -m core.cli set-key }
        Remove-Variable key
    }
    if ($env:JARVIS_SKIP_VOICE -ne "1") {
        & $Py -m core.cli setup-voice
        if ($LASTEXITCODE -ne 0) { Warn "Speech model not installed; run: jarvis-cli setup-voice" }
        if (Ask "Install the natural voice (about 100 MB; Piper, GPL-3.0, run as a separate program)?" "y") {
            & $Py -m core.cli install-voice --yes
            if ($LASTEXITCODE -ne 0) { Warn "Natural voice not installed; it will use the computer's voice." }
        }
    }

    # ---- 5. Start menu, start at login, start now ----------------------------------------
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$Name.lnk"))
    $link.TargetPath = $Pyw
    $link.Arguments = "-m core"
    $link.Description = "Start $Name"
    $link.Save()
    & $Py -m core.cli autostart on | Out-Null
    if ($env:JARVIS_SKIP_START -ne "1") {
        Start-Process -FilePath $Pyw -ArgumentList "-m", "core" -WindowStyle Hidden
        Say "$Name is running."
    }
    Write-Host ""
    Say "Done. Say `"Jarvis, what time is it?`""
    Write-Host "   If Windows asks to use the microphone, click Allow (Settings > Privacy > Microphone >"
    Write-Host "   'Let desktop apps access your microphone' must be on)."
    Write-Host "   Check the microphone: jarvis-cli check-voice     Uninstall: jarvis-uninstall"
} finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}
