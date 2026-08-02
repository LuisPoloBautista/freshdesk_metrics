[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $Python)) {
    $Command = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Command) { throw "Python 3 no esta instalado." }
    $Python = $Command.Source
}
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { & $Python -m venv (Join-Path $ProjectDir ".venv") }
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt")
Write-Host "Entorno listo. Ejecuta:"
Write-Host "  .\.venv\Scripts\python.exe -m streamlit run freshdesk_dashboard.py"
