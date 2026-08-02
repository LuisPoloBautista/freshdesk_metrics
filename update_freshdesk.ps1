[CmdletBinding()]
param(
    [datetime]$ProcessDate = (Get-Date).Date.AddDays(-1),
    [switch]$NoPull,
    [switch]$NoPush
)
$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectDir "logs"
$LogFile = Join-Path $LogDir "fetch_daily.log"
$Lock = [Threading.Mutex]::new($false, "Global\FreshdeskMetricsDailyUpdate")
$HasLock = $false
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
function Write-Log([string]$Message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - $Message" | Tee-Object -FilePath $LogFile -Append
}
function Invoke-NativeLogged([string]$Program, [string[]]$Arguments) {
    # Windows PowerShell convierte cualquier texto de stderr de programas nativos
    # en ErrorRecord. Git usa stderr tambien para mensajes informativos, por lo
    # que temporalmente usamos Continue y validamos el codigo de salida real.
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Program @Arguments *>> $LogFile
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
}
try {
    $HasLock = $Lock.WaitOne(0)
    if (-not $HasLock) { Write-Log "Ya existe una ejecucion activa."; exit 0 }
    if (-not (Test-Path $Python)) { throw "No existe .venv. Ejecuta primero .\setup_windows.ps1" }
    $GitCommand = Get-Command git -ErrorAction SilentlyContinue
    if (-not $GitCommand -and (Test-Path "C:\Program Files\Git\cmd\git.exe") ) {
        $GitCommand = Get-Item "C:\Program Files\Git\cmd\git.exe"
    }
    if (-not $GitCommand) { throw "Git no esta instalado o no esta disponible en PATH." }
    $Git = if ($GitCommand.Source) { $GitCommand.Source } else { $GitCommand.FullName }
    Push-Location $ProjectDir
    try {
        & $Git rev-parse --is-inside-work-tree 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "La carpeta no es un repositorio Git." }
        $DateText = $ProcessDate.ToString("yyyy-MM-dd")
        Write-Log "Procesando $DateText"
        if (-not $NoPull) {
            $ExitCode = Invoke-NativeLogged $Git @("pull", "--rebase", "origin", "main")
            if ($ExitCode -ne 0) { throw "Fallo git pull (codigo $ExitCode); revisa $LogFile" }
        }
        $ExitCode = Invoke-NativeLogged $Python @("scripts\fetch_ticket_activities.py", "--date", $DateText)
        if ($ExitCode -ne 0) { throw "Fallo la descarga de Freshdesk (codigo $ExitCode); revisa $LogFile" }
        $ExitCode = Invoke-NativeLogged $Git @("add", "--", "activities_*.json")
        if ($ExitCode -ne 0) { throw "Fallo git add (codigo $ExitCode); revisa $LogFile" }
        & $Git diff --cached --quiet
        if ($LASTEXITCODE -eq 0) { Write-Log "No hay cambios para subir."; exit 0 }
        $ExitCode = Invoke-NativeLogged $Git @("commit", "-m", "Freshdesk activities $DateText")
        if ($ExitCode -ne 0) { throw "Fallo git commit (codigo $ExitCode); revisa $LogFile" }
        if (-not $NoPush) {
            $ExitCode = Invoke-NativeLogged $Git @("push", "origin", "main")
            if ($ExitCode -ne 0) { throw "Fallo git push (codigo $ExitCode); revisa $LogFile" }
            Write-Log "Cambios publicados en GitHub."
        } else { Write-Log "Commit creado; push omitido por -NoPush." }
    } finally { Pop-Location }
} catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
} finally {
    if ($HasLock) { $Lock.ReleaseMutex() }
    $Lock.Dispose()
}
Write-Log "Proceso finalizado."
