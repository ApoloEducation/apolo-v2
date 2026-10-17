[CmdletBinding()]
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Write-Panel([string]$Title, [string]$Text, [ConsoleColor]$Color = [ConsoleColor]::Cyan) {
  $line = '=' * 68
  Write-Host "`n+$line+" -ForegroundColor $Color
  Write-Host ("| {0,-66} |" -f $Title) -ForegroundColor $Color
  Write-Host "+$line+" -ForegroundColor $Color
  $Text -split "`n" | ForEach-Object {
    $remaining = $_.Trim()
    if (-not $remaining) { Write-Host ("| {0,-66} |" -f ''); return }
    while ($remaining.Length -gt 66) {
      $breakAt = $remaining.LastIndexOf(' ', 65)
      if ($breakAt -lt 1) { $breakAt = 66 }
      Write-Host ("| {0,-66} |" -f $remaining.Substring(0, $breakAt).Trim())
      $remaining = $remaining.Substring($breakAt).Trim()
    }
    Write-Host ("| {0,-66} |" -f $remaining)
  }
  Write-Host "+$line+" -ForegroundColor $Color
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Panel 'Docker Desktop necessário' 'Instale e inicie o Docker Desktop, então execute .\start.ps1 novamente.' Yellow
  exit 1
}

try { docker info *> $null } catch {
  Write-Panel 'Docker Desktop não está em execução' 'Abra o Docker Desktop e aguarde o mecanismo ficar pronto.' Yellow
  exit 1
}

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
  Write-Panel 'Ollama necessário' 'Instale o Ollama em https://ollama.com e execute .\start.ps1 novamente.' Yellow
  exit 1
}

if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
$model = ((Get-Content '.env' | Where-Object { $_ -match '^OLLAMA_MODEL=' }) -replace '^OLLAMA_MODEL=', '')
if (-not $model) { $model = 'qwen2.5:3b' }

try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:11434/api/tags' *> $null } catch {
  Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden
  $ollamaDeadline = (Get-Date).AddSeconds(30)
  do {
    Start-Sleep -Seconds 2
    try { $ollamaReady = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:11434/api/tags').StatusCode -eq 200 } catch { $ollamaReady = $false }
  } until ($ollamaReady -or (Get-Date) -gt $ollamaDeadline)
  if (-not $ollamaReady) { throw 'O Ollama não iniciou em 30 segundos. Execute: ollama serve' }
}

$installed = (& ollama list 2>$null | Out-String)
if ($installed -notmatch [regex]::Escape($model)) {
  Write-Panel 'Preparando modelo local' "Baixando $model. Isso ocorre apenas no primeiro uso e pode levar alguns minutos."
  & ollama pull $model
}

Write-Panel 'Apolo School MVP' 'Iniciando banco de dados e cache...'
docker compose up -d chatwoot-postgres redis

# Wait explicitly for postgres to be healthy before running db prepare
Write-Host 'Aguardando PostgreSQL ficar saudável...' -ForegroundColor DarkGray
$pgDeadline = (Get-Date).AddSeconds(120)
$pgReady = $false
do {
  Start-Sleep -Seconds 3
  $pgStatus = (docker inspect --format='{{.State.Health.Status}}' apolo-school-mvp-chatwoot-postgres-1 2>$null)
  if ($pgStatus -eq 'healthy') { $pgReady = $true }
} until ($pgReady -or (Get-Date) -gt $pgDeadline)
if (-not $pgReady) { throw 'PostgreSQL não ficou saudável em 120 segundos. Execute: docker compose logs chatwoot-postgres' }
Write-Host 'PostgreSQL pronto.' -ForegroundColor DarkGray

$prepared = $false
for ($attempt = 1; $attempt -le 15 -and -not $prepared; $attempt++) {
  Write-Host "Preparando banco do Chatwoot (tentativa $attempt/15)..." -ForegroundColor DarkGray
  docker compose run --rm --no-deps chatwoot bundle exec rails db:chatwoot_prepare
  if ($LASTEXITCODE -eq 0) { $prepared = $true } else { Start-Sleep -Seconds 10 }
}
if (-not $prepared) { throw 'Não foi possível preparar o banco do Chatwoot. Execute: docker compose logs chatwoot-postgres' }

docker compose up --build -d chatwoot chatwoot-worker school-data agent-api messenger

Write-Host 'Aguardando serviços ficarem prontos...' -ForegroundColor DarkGray
$deadline = (Get-Date).AddMinutes(5)
do {
  Start-Sleep -Seconds 3
  try {
    $agentOk = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://localhost:8000/health').StatusCode -eq 200
    $chatwootOk = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://localhost:3000/api').StatusCode -eq 200
    $ok = $agentOk -and $chatwootOk
  } catch { $ok = $false }
} until ($ok -or (Get-Date) -gt $deadline)
if (-not $ok) { throw 'agent-api não ficou pronto. Execute: docker compose logs agent-api' }

docker compose run --rm -e BOOTSTRAP=true chatwoot bundle exec rails runner /bootstrap/chatwoot_bootstrap.rb
$readyMessage = @(
  'Mini-WhatsApp: http://localhost:5173'
  'Chatwoot:       http://localhost:3000'
  'Admin:          admin@escola.local / ApoloLocal2026!'
  'Atendente:      atendente@escola.local / ApoloLocal2026!'
  ''
  'Use Ctrl+C apenas para sair deste terminal; os serviços permanecem ativos.'
  'Para parar tudo: .\stop.ps1'
) -join [Environment]::NewLine
Write-Panel 'Tudo pronto' $readyMessage Green

if (-not $NoBrowser) { Start-Process 'http://localhost:5173'; Start-Process 'http://localhost:3000' }
