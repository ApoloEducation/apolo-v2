# frozen_string_literal: true
# Local demonstration bootstrap. It deliberately creates predictable development accounts.
# Safe to run multiple times (fully idempotent).

require 'json'

admin_email = ENV.fetch('CHATWOOT_ADMIN_EMAIL', 'admin@escola.local')
admin_password = ENV.fetch('CHATWOOT_ADMIN_PASSWORD', 'ApoloLocal2026!')
human_email = ENV.fetch('CHATWOOT_HUMAN_EMAIL', 'atendente@escola.local')
human_password = ENV.fetch('CHATWOOT_HUMAN_PASSWORD', 'ApoloLocal2026!')

# ── account ───────────────────────────────────────────────────────────────────

account = Account.find_by(name: 'Escola Horizonte — Demo') || Account.create!(name: 'Escola Horizonte — Demo')

# ── users ─────────────────────────────────────────────────────────────────────

[
  [admin_email,  admin_password,  'Admin Escola'],
  [human_email,  human_password,  'Atendente Escola']
].each do |email, password, name|
  user = User.find_or_initialize_by(email: email)
  user.name = name
  user.password = password
  user.password_confirmation = password
  user.skip_confirmation! if user.respond_to?(:skip_confirmation!)
  user.save!
end

admin = User.find_by!(email: admin_email)
human = User.find_by!(email: human_email)

# ── account memberships ───────────────────────────────────────────────────────

unless AccountUser.exists?(account: account, user: admin)
  AccountUser.create!(account: account, user: admin, role: :administrator)
end
unless AccountUser.exists?(account: account, user: human)
  AccountUser.create!(account: account, user: human, role: :agent)
end

# ── team (case-insensitive lookup — Chatwoot stores names in lowercase) ───────

TEAM_NAME = 'Atendimento Humano'
team = Team.where(account: account).where('LOWER(name) = ?', TEAM_NAME.downcase).first
team ||= Team.create!(account: account, name: TEAM_NAME)
team.add_members([human.id]) unless team.members.exists?(id: human.id)

# ── label ─────────────────────────────────────────────────────────────────────

label = Label.find_by(account: account, title: 'escalado-humano') ||
        Label.create!(account: account, title: 'escalado-humano')
label.description = 'Pergunta encaminhada pelo agente escolar'
label.save!

%w[financeiro saude disciplina pedagogico documentos eventos_calendario outros].each do |category|
  Label.find_by(account: account, title: "cat-#{category}") ||
    Label.create!(account: account, title: "cat-#{category}", description: "Categoria: #{category}")
end

# ── channel + inbox ───────────────────────────────────────────────────────────

WEBHOOK_URL = 'http://agent-api:8000/webhooks/chatwoot'
INBOX_NAME  = 'Mini-WhatsApp Escola'

channel = Channel::Api.find_by(account: account, webhook_url: WEBHOOK_URL)
channel ||= Channel::Api.create!(account: account, webhook_url: WEBHOOK_URL)

inbox = Inbox.find_by(account: account, name: INBOX_NAME) ||
        Inbox.find_by(account: account, channel: channel)
inbox ||= Inbox.create!(account: account, channel: channel,
                        name: INBOX_NAME, timezone: 'America/Sao_Paulo')
inbox.add_members([human.id]) unless inbox.members.exists?(id: human.id)

# ── webhook ───────────────────────────────────────────────────────────────────

webhook = Webhook.find_by(account: account, url: WEBHOOK_URL) ||
          Webhook.new(account: account, url: WEBHOOK_URL)
webhook.name = 'Apolo Agent Bridge'
webhook.subscriptions = %w[conversation_status_changed]
webhook.save!

# ── write integration.json ────────────────────────────────────────────────────

token = admin.access_token || admin.create_access_token
output = {
  account_id:           account.id,
  inbox_id:             inbox.id,
  api_access_token:     token.token,
  human_user_id:        human.id,
  team_id:              team.id,
  api_inbox_identifier: channel.identifier,
  webhook_url:          channel.webhook_url
}
File.write('/bootstrap/integration.json', JSON.pretty_generate(output))
puts "Chatwoot demo bootstrap completed for account #{account.id}."
