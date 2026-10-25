# Apaga todas as mensagens do sistema (histórico do assistente, conversas do Chatwoot e telas abertas do messenger).
# Mensagens já entregues no celular pelo WhatsApp não podem ser apagadas pela API; só o lado do sistema é limpo.
$result = Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/v1/admin/reset'
Write-Host "Limpo: $($result.conversations) conversa(s). Falhas no Chatwoot: $($result.chatwoot_failures.Count)"
