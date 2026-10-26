# Remove the app, its data, its keychain entry and its start-at-login entry (Windows).
$ErrorActionPreference = "Stop"
$App = "local-assistant"  # = APP_ID in backend/contracts/app.py
$Prefix = if ($env:PREFIX) { $env:PREFIX } else { Join-Path $env:LOCALAPPDATA "Programs\$App" }
$Python = "$Prefix\venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "$App doesn't seem to be installed in $Prefix" }

# Deletes app data, the API key from Credential Manager and the login item (asks first; pass --yes to skip).
& $Python -m core.cli uninstall @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Remove-Item (Join-Path ([Environment]::GetFolderPath("Programs")) "$App.lnk") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$Prefix\venv"
Remove-Item -Force "$Prefix\uninstall.ps1" -ErrorAction SilentlyContinue
Remove-Item $Prefix -ErrorAction SilentlyContinue
Write-Host "$App is uninstalled."
