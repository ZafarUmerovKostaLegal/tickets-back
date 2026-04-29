# Import vacation schedule from Excel into vacation_db (Docker).
# Run from repo root tickets-back:
#   .\vacation\scripts\import_via_docker.ps1
#   .\vacation\scripts\import_via_docker.ps1 -ExcelPath "D:\path\file.xlsx" -Year 2026
# Requires Docker Desktop.

param(
    [Parameter(Mandatory = $false)]
    [string] $ExcelPath = "",
    [int] $Year = 2026
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

if (-not $ExcelPath) {
    Write-Error "Required: -ExcelPath 'C:\path\to\Grafik_2026.xlsx'"
}

if (-not (Test-Path -LiteralPath $ExcelPath)) {
    Write-Error "File not found: $ExcelPath"
}

docker info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker is not running. Start Docker Desktop, then run this script again."
}

Write-Host "Starting vacation_db..." -ForegroundColor Cyan
docker compose up -d vacation_db

$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    docker compose exec -T vacation_db pg_isready -U vacation -d kosta_vacation 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 2
}
if (-not $ready) {
    Write-Error "vacation_db not ready. Check: docker compose ps, docker compose logs vacation_db"
}

Write-Host "Building vacation image..." -ForegroundColor Cyan
docker compose build vacation

$winPath = (Resolve-Path -LiteralPath $ExcelPath).Path
Write-Host "Import: $winPath year=$Year" -ForegroundColor Cyan
docker compose run --rm -v "${winPath}:/in.xlsx:ro" vacation python scripts/import_excel.py /in.xlsx --year $Year

if ($LASTEXITCODE -ne 0) {
    Write-Error "Import failed exit code $LASTEXITCODE"
}

Write-Host "Done. Next: docker compose up -d vacation gateway" -ForegroundColor Green
