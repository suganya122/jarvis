# Install from this offline bundle (Windows). Needs Python 3.12+; no internet, no admin.
#   powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$App = "local-assistant"  # = APP_ID in backend/contracts/app.py
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Prefix = if ($env:PREFIX) { $env:PREFIX } else { Join-Path $env:LOCALAPPDATA "Programs\$App" }

$Py = $null
foreach ($candidate in @(@("py", "-3.13"), @("py", "-3.12"), @("python"))) {
  try {
    & $candidate[0] $candidate[1..9] -c "import sys; sys.exit(sys.version_info < (3, 12))" 2>$null
    if ($LASTEXITCODE -eq 0) { $Py = $candidate; break }
  } catch { }
}
if (-not $Py) { throw "Python 3.12 or newer is needed. Install it from python.org and run this again." }

Write-Host "Installing $App into $Prefix"
New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
& $Py[0] $Py[1..9] -m venv "$Prefix\venv"
& "$Prefix\venv\Scripts\python.exe" -m pip install --quiet --no-index --find-links "$Here\wheels" $App
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Copy-Item "$Here\uninstall.ps1" "$Prefix\uninstall.ps1" -Force

# Start Menu shortcut (no console window).
$Shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "$App.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($Shortcut)
$Link.TargetPath = "$Prefix\venv\Scripts\pythonw.exe"
$Link.Arguments = "-m core --open"
$Link.WorkingDirectory = $Prefix
$Link.Save()

Write-Host ""
Write-Host "Done. Start it from the Start Menu ($App), or run:"
Write-Host "  `"$Prefix\venv\Scripts\$App.exe`" --open"
Write-Host "Uninstall later with: powershell -ExecutionPolicy Bypass -File `"$Prefix\uninstall.ps1`""
