#!/usr/bin/env python3
"""Guided launcher: `make start`. Runs scripts/start.sh, opens the Cloudflare tunnel, points the Meta webhook at it
and walks through the remaining steps (Chatwoot, school panel, iPhone Mirroring) with the values already on the clipboard.

Needs `rich` (scripts/start_tui.sh installs it into .venv). macOS only for clipboard/open; elsewhere it just prints."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
console = Console()
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
GRAPH = "https://graph.facebook.com/v21.0"


# ---------- helpers ----------
def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_schools() -> list[tuple[str, str]]:
    """Same parser as scripts/school.sh: the SCHOOLS registry in seed.py."""
    schools, inside, current = [], False, None
    for line in (ROOT / "apps/school-data/app/seed.py").read_text().splitlines():
        if line.startswith("SCHOOLS = {"):
            inside = True
        elif inside and line.startswith("}"):
            inside = False
        elif inside:
            m = re.match(r'^    "([^"]+)": \{', line)
            if m:
                current = m.group(1)
            m = re.match(r'^        "name": "([^"]+)"', line)
            if m and current:
                schools.append((current, m.group(1)))
    return schools


def copy(text: str) -> bool:
    if shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=text.encode(), check=False)
        return True
    return False


def open_url(target: str, app: str | None = None) -> None:
    if shutil.which("open"):
        subprocess.run(["open", "-a", app, target] if app else ["open", target], check=False)


def http(url: str, timeout: float = 5) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001 - any network failure is just "down"
        return 0, str(exc)


def step(number: int, title: str) -> None:
    console.print()
    console.rule(f"[bold cyan]{number}. {title}[/]", align="left", style="cyan")


def pause(message: str) -> None:
    Prompt.ask(f"[bold]{message}[/] [dim](Enter)[/]", default="", show_default=False)


# ---------- 1. preflight ----------
def check_token(env: dict[str, str]) -> tuple[bool, str]:
    token, phone_id = env.get("META_API_TOKEN"), env.get("WHATSAPP_PHONE_NUMBER_ID")
    if not (token and phone_id):
        return False, "META_API_TOKEN / WHATSAPP_PHONE_NUMBER_ID vazios no .env"
    status, body = http(f"{GRAPH}/{phone_id}?{urllib.parse.urlencode({'access_token': token})}")
    if status == 200:
        return True, "token da Meta válido"
    if status == 0:
        return False, "sem internet para validar o token"
    try:
        message = json.loads(body)["error"]["message"]
    except Exception:  # noqa: BLE001
        message = body[:80]
    return False, f"token da Meta recusado: {message} (gere um novo e atualize o .env)"


def graph(path: str, token: str, data: dict[str, str] | None = None, query: dict[str, str] | None = None) -> tuple[int, dict]:
    """GET (or POST when `data` is given) on the Graph API; returns (status, parsed JSON)."""
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode({**(query or {}), 'access_token': token})}"
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001 - offline / DNS: report, don't crash the guide
        return 0, {"error": {"message": str(exc)}}


def configure_webhook(env: dict[str, str], callback: str) -> tuple[bool, str]:
    """Points the test number's webhook at the tunnel (phone-number override, needs whatsapp_business_management).
    Meta runs the verify handshake against the tunnel before answering, so success means it can reach us.
    The number-level URL wins over the app-level one set in the Meta panel."""
    token, phone_id = env.get("META_API_TOKEN", ""), env.get("WHATSAPP_PHONE_NUMBER_ID", "")
    if not (token and phone_id and env.get("WHATSAPP_VERIFY_TOKEN")):
        return False, "META_API_TOKEN, WHATSAPP_PHONE_NUMBER_ID ou WHATSAPP_VERIFY_TOKEN vazio no .env"
    config = json.dumps({"override_callback_uri": callback, "verify_token": env["WHATSAPP_VERIFY_TOKEN"]})
    status, body = graph(phone_id, token, data={"webhook_configuration": config})
    if status != 200 or not body.get("success"):
        return False, (body.get("error") or {}).get("message", f"HTTP {status}")
    status, body = graph(phone_id, token, query={"fields": "webhook_configuration"})
    active = (body.get("webhook_configuration") or {}).get("phone_number")
    return (True, "") if active == callback else (False, f"a Meta ficou com outra URL: {active}")


def business_number(env: dict[str, str]) -> str | None:
    """The WhatsApp number guardians write to (the Meta test number behind WHATSAPP_PHONE_NUMBER_ID), e.g. '+1 555-140-2234'."""
    token, phone_id = env.get("META_API_TOKEN", ""), env.get("WHATSAPP_PHONE_NUMBER_ID", "")
    if not (token and phone_id):
        return None
    status, body = graph(phone_id, token, query={"fields": "display_phone_number"})
    return body.get("display_phone_number") if status == 200 else None


def meta_app_id(env: dict[str, str]) -> str | None:
    """META_APP_ID from .env, else the app the token belongs to (so the panel link opens on the right app)."""
    if env.get("META_APP_ID"):
        return env["META_APP_ID"]
    token = env.get("META_API_TOKEN", "")
    status, body = graph("debug_token", token, query={"input_token": token}) if token else (0, {})
    return (body.get("data") or {}).get("app_id") if status == 200 else None


def preflight(env: dict[str, str]) -> bool:
    checks: list[tuple[str, bool, str, bool]] = []  # label, ok, detail, blocking

    def add(label: str, ok: bool, detail: str = "", blocking: bool = True) -> None:
        checks.append((label, ok, detail, blocking))

    add("Docker", shutil.which("docker") is not None, "instale o Docker Desktop")
    add("cloudflared", shutil.which("cloudflared") is not None, "brew install cloudflared")
    add("OpenRouter", bool(env.get("OPENROUTER_MODEL") and env.get("OPEN_ROUTER_API_KEY") and env.get("OPENROUTER_CLASSIFIER_MODEL")),
        "defina OPENROUTER_MODEL, OPEN_ROUTER_API_KEY e OPENROUTER_CLASSIFIER_MODEL no .env")
    with console.status("Validando o token da Meta..."):
        ok, detail = check_token(env)
    add("WhatsApp (Meta)", ok, detail, blocking=False)
    add("Verify token", bool(env.get("WHATSAPP_VERIFY_TOKEN")), "defina WHATSAPP_VERIFY_TOKEN no .env")
    add("Número permitido", bool(env.get("WHATSAPP_ALLOWED_NUMBERS")), "defina WHATSAPP_ALLOWED_NUMBERS no .env")

    table = Table.grid(padding=(0, 2))
    for label, ok, detail, blocking in checks:
        mark = "[green]✓[/]" if ok else ("[red]✗[/]" if blocking else "[yellow]![/]")
        table.add_row(mark, label, "" if ok else f"[dim]{detail}[/]")
    console.print(table)
    return all(ok or not blocking for _, ok, _, blocking in checks)


# ---------- 2. school ----------
def choose_school(env: dict[str, str]) -> tuple[str, str]:
    schools = load_schools()
    if not schools:
        sys.exit("Nenhuma escola encontrada em apps/school-data/app/seed.py")
    wanted = os.getenv("SCHOOL")
    if wanted:
        for school in schools:
            if school[0] == wanted:
                return school
        sys.exit(f"Escola '{wanted}' não existe. Opções: {', '.join(s[0] for s in schools)}")
    last = env.get("APOLO_SCHOOL")
    default = next((i for i, s in enumerate(schools, 1) if s[0] == last), 1)
    for i, (sid, name) in enumerate(schools, 1):
        console.print(f"  [cyan]{i}.[/] {name} [dim]({sid})[/]")
    choice = Prompt.ask("Qual sistema de escola você quer utilizar?", choices=[str(i) for i in range(1, len(schools) + 1)], default=str(default))
    return schools[int(choice) - 1]


# ---------- 3. stack ----------
def run_stack(school_id: str) -> bool:
    """Runs scripts/start.sh with a spinner showing its latest line; the full output goes to a log file."""
    log_path = Path(tempfile.gettempdir()) / "apolo-start.log"
    tail: deque[str] = deque(maxlen=200)
    env = {**os.environ, "SCHOOL": school_id, "LLM": os.getenv("LLM", "openrouter"), "NO_BROWSER": "1", "TERM": "dumb"}
    with log_path.open("w") as log, console.status("Preparando...", spinner="dots12") as status:
        proc = subprocess.Popen(["bash", str(ROOT / "scripts/start.sh")], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert proc.stdout
        started = time.monotonic()
        for raw in proc.stdout:
            log.write(raw)
            line = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw).strip()
            if line:
                tail.append(line)
                status.update(f"[bold]{line[:90]}[/] [dim]({int(time.monotonic() - started)}s)[/]")
        proc.wait()
    if proc.returncode != 0:
        console.print(Panel("\n".join(list(tail)[-25:]), title=f"[red]start.sh falhou (código {proc.returncode})[/]", subtitle=f"log completo: {log_path}", border_style="red"))
        return False
    console.print(f"[green]✓[/] Serviços no ar [dim](log: {log_path})[/]")
    return True


# ---------- 4. tunnel ----------
class Tunnel:
    def __init__(self) -> None:
        self.url: str | None = None
        self.proc: subprocess.Popen | None = None
        self.lines: deque[str] = deque(maxlen=30)
        self.connected = threading.Event()

    def start(self, timeout: int = 90) -> bool:
        """True once cloudflared has a URL *and* a registered edge connection. The URL shows up within seconds, but
        the tunnel only serves traffic after "Registered tunnel connection", which can take 30 s or more."""
        self.proc = subprocess.Popen(["cloudflared", "tunnel", "--url", "http://localhost:8000"], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not (self.url and self.connected.is_set()) and self.proc.poll() is None:
            time.sleep(0.3)
        return self.url is not None and self.connected.is_set()

    def _read(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if "precheck" not in line and not line.endswith("|") and not line.endswith("+"):  # skip the diagnostics table
                self.lines.append(line)
            if not self.url and (m := TUNNEL_RE.search(line)):
                self.url = m.group(0)
            if "Registered tunnel connection" in line:
                self.connected.set()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def tunnel_works(url: str, env: dict[str, str], timeout: int = 60) -> bool:
    """Plays Meta's verification handshake through the tunnel, so a broken tunnel shows up before the Meta page does."""
    query = urllib.parse.urlencode({"hub.mode": "subscribe", "hub.verify_token": env.get("WHATSAPP_VERIFY_TOKEN", ""), "hub.challenge": "apolo-ok"})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:  # right after registering, the edge still answers 530 for a few seconds
        status, body = http(f"{url}/webhooks/whatsapp?{query}", timeout=8)
        if status == 200 and body == "apolo-ok":
            return True
        time.sleep(2)
    return False


def open_tunnel(env: dict[str, str], attempts: int = 3) -> Tunnel | None:
    """Opens a quick tunnel that already passes the webhook handshake; a fresh URL is tried when one does not."""
    for attempt in range(1, attempts + 1):
        tunnel = Tunnel()
        label = f"Abrindo o túnel da Cloudflare (tentativa {attempt}/{attempts})..."
        with console.status(label, spinner="dots12") as status:
            ok = tunnel.start()
            if ok:
                status.update(f"Túnel registrado; testando o handshake da Meta em {tunnel.url}...")
                ok = tunnel_works(tunnel.url, env)
        if ok:
            console.print(f"[green]✓[/] Túnel no ar e respondendo ao handshake de verificação [dim]({tunnel.url})[/]")
            return tunnel
        reason = ("cloudflared encerrou" if not tunnel.alive() else
                  "sem conexão registrada com a Cloudflare" if not tunnel.connected.is_set() else
                  "a URL não respondeu ao handshake")
        console.print(f"[yellow]![/] Tentativa {attempt}: {reason}.")
        tunnel.stop()
        if attempt == attempts:
            console.print(Panel("\n".join(list(tunnel.lines)[-15:]) or "sem saída", title="[red]O túnel não ficou utilizável[/]",
                                subtitle="o cloudflared precisa de saída para a porta 7844 (UDP ou TCP)", border_style="red"))
    return None


# ---------- 5. guided steps ----------
def phone_target(env: dict[str, str], school_id: str) -> None:
    """Which parent the iPhone impersonates: the school's guardian registry first, .env as the fallback."""
    first = next((p.split(":") for p in env.get("WHATSAPP_ALLOWED_NUMBERS", "").split(",") if p.count(":") == 2), None)
    if not first:
        console.print("Cadastre o número do iPhone em [bold]Responsáveis[/] no painel da escola (passo 3) para o Apolo reconhecê-lo.")
        return
    number = first[0].strip().lstrip("+")
    masked = f"…{number[-4:]}"
    status, body = http(f"http://localhost:8001/lookup/phone/{number}")
    if status == 200:
        found = json.loads(body)
        console.print(f"No iPhone ({masked}) você é [bold]{found['name']}[/] [dim]({found['guardian_id']}, cadastro do painel)[/].")
        return
    _, school, guardian = (x.strip() for x in first)
    if school != school_id:
        console.print(f"[yellow]![/] O número {masked} não está cadastrado em [bold]{school_id}[/] (e o .env o liga a [bold]{school}[/]). "
                      f"Cadastre-o em [bold]Responsáveis[/] no painel, ou o iPhone não será reconhecido.")
    else:
        console.print(f"No iPhone ({masked}) você é [bold]{guardian}[/] [dim](pelo .env; cadastre o número em Responsáveis para aparecer no painel)[/].")


def guided(env: dict[str, str], school_id: str, tunnel: Tunnel) -> None:
    callback = f"{tunnel.url}/webhooks/whatsapp"
    verify = env.get("WHATSAPP_VERIFY_TOKEN", "")

    step(1, "Webhook da Meta")
    with console.status("Apontando o webhook do número para o túnel pela API da Meta...", spinner="dots12"):
        configured, detail = configure_webhook(env, callback)
        app_id = meta_app_id(env)
    meta_url = (f"https://developers.facebook.com/apps/{app_id}/whatsapp-business/wa-settings/" if app_id
                else "https://developers.facebook.com/apps/")
    copied = copy(callback)
    console.print(Panel(f"{callback}\n[dim]Verify token: {verify}[/]", title="Callback URL" + (" (já copiada)" if copied else ""), border_style="green"))
    console.print(f"Painel da Meta (WhatsApp > Configuração): [link={meta_url}]{meta_url}[/link]")
    if configured:
        console.print("[green]✓[/] Webhook configurado pela API: a Meta verificou o túnel e já entrega as mensagens do número aqui.\n"
                      "[dim]No painel, a URL configurada no app pode continuar antiga: a do número tem prioridade.[/]")
        if Confirm.ask("[bold]Abrir o painel da Meta para conferir?[/]", default=False):
            open_url(meta_url)
    else:
        console.print(f"[yellow]![/] Não consegui configurar pela API [dim]({detail})[/]. Vamos pelo painel.")
        pause("Enter abre o painel da Meta (WhatsApp > Configuração > Webhook > Editar). Cole a Callback URL")
        open_url(meta_url)
        pause("Depois de colar a URL, Enter para eu copiar o Verify token")
        copy(verify)
        console.print(f"[green]✓[/] Verify token copiado [dim]({verify})[/]. Cole, clique em Verificar e salvar, e assine o campo [bold]messages[/].")
        pause("Webhook verificado? Enter para continuar")

    step(2, "Chatwoot (a tela do atendente)")
    copy("atendente@escola.local")
    console.print("Login: [bold]atendente@escola.local[/] / [bold]ApoloLocal2026![/] [dim](e-mail copiado)[/]")
    pause("Enter abre o Chatwoot")
    open_url("http://localhost:3000")

    step(3, "Painel da escola (responsáveis e banco de dados)")
    pause("Enter abre a página Responsáveis (cadastro do WhatsApp dos pais)")
    open_url("http://localhost:8001/admin/responsaveis")

    step(4, "iPhone")
    phone_target(env, school_id)
    target = business_number(env)
    if target:
        copy(target)
        console.print(f"Mande a mensagem para [bold]{target}[/] [dim](o número do Apolo na Meta; já copiado)[/].")
    else:
        console.print("Mande a mensagem para o número de teste da Meta [dim](WhatsApp > Configuração da API, campo \"De\")[/].")
    console.print("[yellow]![/] [bold]O iPhone escreve primeiro.[/] A Meta só deixa o Apolo responder até 24 h depois da última "
                  "mensagem do pai; fora disso a resposta é recusada (erro 131047) e não chega.")
    pause("Enter abre o Espelhamento do iPhone")
    open_url("iPhone Mirroring", app="iPhone Mirroring")


# ---------- 6. live status ----------
def service_rows(tunnel: Tunnel, env: dict[str, str]) -> Table:
    table = Table.grid(padding=(0, 2))
    probes = [("Agent API", "http://localhost:8000/health"), ("Chatwoot", "http://localhost:3000/api"),
              ("Painel da escola", "http://localhost:8001/health"),
              ("Mini-WhatsApp", f"http://localhost:{env.get('MESSENGER_PORT') or 5173}")]  # start.sh may move it off 5173
    for name, url in probes:
        up = http(url, timeout=2)[0] in (200, 401, 403)
        table.add_row("[green]●[/]" if up else "[red]●[/]", name, f"[dim]{url}[/]")
    table.add_row("[green]●[/]" if tunnel.alive() else "[red]●[/]", "Túnel", f"[dim]{tunnel.url}[/]")
    return table


def agent_log_tail(lines: deque[str], stop: threading.Event) -> None:
    proc = subprocess.Popen(["docker", "compose", "logs", "-f", "--tail=0", "--no-color", "agent-api"], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL)
    assert proc.stdout
    for line in proc.stdout:
        if stop.is_set():
            break
        text = line.split("|", 1)[-1].strip()
        if text and "GET /health" not in text:
            lines.append(text[:160])
    proc.terminate()


def live(tunnel: Tunnel, env: dict[str, str]) -> None:
    lines: deque[str] = deque(maxlen=8)
    stop = threading.Event()
    threading.Thread(target=agent_log_tail, args=(lines, stop), daemon=True).start()
    rows, last_probe = service_rows(tunnel, env), 0.0
    try:
        with Live(console=console, refresh_per_second=2, screen=False) as view:
            while True:
                if time.monotonic() - last_probe > 5:
                    rows, last_probe = service_rows(tunnel, env), time.monotonic()
                view.update(Panel(Group(rows, Text(""), Text("agent-api", style="bold"),
                                        Text("\n".join(lines) or "(aguardando mensagens)", style="dim")),
                                  title="[bold green]Apolo no ar[/]", subtitle="Ctrl+C encerra o túnel · make stop derruba o resto", border_style="green"))
                if not tunnel.alive():
                    view.stop()
                    console.print("[red]O túnel caiu.[/] Rode make start de novo (a URL muda e precisa ser colada na Meta).")
                    return
                time.sleep(0.5)
    finally:
        stop.set()


# ---------- main ----------
def main() -> int:
    os.chdir(ROOT)
    console.print(Panel.fit("[bold]Apolo[/]\n[dim]Sobe os serviços, abre o túnel e guia a configuração do WhatsApp, do Chatwoot e do painel.[/]", border_style="cyan"))
    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
        console.print("[dim]Criei o .env a partir do .env.example. Preencha as chaves e rode de novo.[/]")
        return 1
    env = read_env()

    console.rule("[bold cyan]Ambiente[/]", align="left", style="cyan")
    if not preflight(env):
        console.print("\n[red]Corrija os itens marcados com ✗ e rode de novo.[/]")
        return 1

    console.print()
    school_id, school_name = choose_school(env)
    console.print(f"Escola: [bold]{school_name}[/] · LLM: [bold]{os.getenv('LLM', 'openrouter')}[/]\n")
    if not run_stack(school_id):
        return 1

    env = read_env()
    tunnel: Tunnel | None = None
    try:
        tunnel = open_tunnel(env)
        if not tunnel:
            console.print("Os serviços continuam no ar; rode [bold]make start[/] de novo ou [bold]make tunnel[/] em outro terminal.")
            return 1
        guided(env, school_id, tunnel)
        console.print()
        live(tunnel, env)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Encerrando o túnel...[/]")
    finally:
        if tunnel:
            tunnel.stop()
    console.print("Túnel encerrado. Os serviços continuam no ar; [bold]make stop[/] derruba tudo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
