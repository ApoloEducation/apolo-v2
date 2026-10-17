$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
docker compose down
Write-Host 'Serviços interrompidos. Os dados locais foram preservados nos volumes Docker.' -ForegroundColor Cyan
