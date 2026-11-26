[CmdletBinding()]
param(
    [ValidateSet('audit', 'export', 'apply-mapping')]
    [string]$Action = 'export',
    [long]$CompanyId,
    [long]$ContactId,
    [string]$StartDate,
    [string]$EndDate,
    [ValidateSet('created_at', 'updated_at')]
    [string]$DateField = 'created_at',
    [int[]]$Status,
    [string]$Product,
    [long]$ProductId,
    [string]$Mapping,
    [string]$Output,
    [switch]$IncludePrivateNotes
)
$ErrorActionPreference = 'Stop'
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Ejecuta setup_windows.ps1 para preparar Python.' }
if ($CompanyId -and $ContactId) { throw 'Usa CompanyId o ContactId, no ambos.' }
if (-not $Output) {
    $Label = if ($CompanyId) { "company_$CompanyId" } elseif ($ContactId) { "contact_$ContactId" } else { $Action }
    $Output = Join-Path $PSScriptRoot ("exports\{0}_{1}" -f $Label, (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
}
$Arguments = @((Join-Path $PSScriptRoot 'scripts\export_freshdesk.py'), $Action, '--output', $Output)
if ($CompanyId) { $Arguments += @('--company-id', "$CompanyId") }
if ($ContactId) { $Arguments += @('--contact-id', "$ContactId") }
if ($StartDate) { $Arguments += @('--start-date', $StartDate) }
if ($EndDate) { $Arguments += @('--end-date', $EndDate) }
$Arguments += @('--date-field', $DateField)
foreach ($Value in $Status) { $Arguments += @('--status', "$Value") }
if ($Product) { $Arguments += @('--product', $Product) }
if ($ProductId) { $Arguments += @('--product-id', "$ProductId") }
if ($Mapping) { $Arguments += @('--mapping', $Mapping) }
if ($IncludePrivateNotes) { $Arguments += '--include-private-notes' }
Push-Location $PSScriptRoot
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Exportacion incompleta o fallida. Revisa $Output y la salida anterior." }
    Write-Host "Resultado: $Output"
} finally {
    Pop-Location
}
