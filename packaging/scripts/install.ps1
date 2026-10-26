# Install from this offline bundle (Windows). Needs Python 3.12+; no internet, no admin.
#   powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$App = "local-assistant"  # = APP_ID in backend/contracts/app.py
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Prefix = if ($env:PREFIX) { $env:PREFIX } else { Join-Path $env:LOCALAPPDATA "Programs\$App" }

# The bundled libraries are built for one Python version (python-version.txt): use exactly that.
$Want = if (Test-Path "$Here\python-version.txt") { (Get-Content "$Here\python-version.txt").Trim() } else { "3.12" }
$Py = $null
foreach ($candidate in @(@("py", "-$Want"), @("python"))) {
  try {
    $found = & $candidate[0] $candidate[1..9] -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($LASTEXITCODE -eq 0 -and "$found".Trim() -eq $Want) { $Py = $candidate; break }
  } catch { }
}
if (-not $Py) { throw "This bundle needs Python $Want. Install it from python.org (or use the one-line installer, which brings its own Python) and run this again." }

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
