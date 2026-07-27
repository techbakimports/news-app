"""
Webserver — Youtuber no Automático
Roda com: uvicorn webserver:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(override=True)

import auth
import db

db.init_db()

app = FastAPI(title="Youtuber no Automático", version="2.0.0")
templates = Jinja2Templates(directory="templates")
templates.env.filters["tojson"] = json.dumps

# pipeline status global
_pipeline_status: dict[str, dict] = {}
# status por cliente (user_id → dict)
_client_status: dict[str, dict] = {}
# state do OAuth do TikTok em andamento (single-admin, não precisa ser por sessão)
_tiktok_oauth_state: str | None = None

TOKEN_PATH = "credentials/token.json"

PIPELINES = [
    {"key": "noticias",     "label": "Notícias",     "icon": "📰"},
    {"key": "celebridades", "label": "Celebridades", "icon": "🎤"},
    {"key": "tech",         "label": "Tecnologia",   "icon": "💻"},
    {"key": "curiosidades", "label": "Curiosidades", "icon": "🧠"},
    {"key": "novela",       "label": "Novela IA",    "icon": "🎭"},
    {"key": "produtos",     "label": "Produtos",     "icon": "🛍️"},
]

SCRIPTS = {
    "noticias":     "main.py",
    "celebridades": "celebridades.py",
    "tech":         "tech_news.py",
    "curiosidades": "curiosidades.py",
    "novela":       "novela.py",
    "produtos":     "afiliados.py",
}

# Argumentos extras que o crontab já usa pra cada script (preservados ao
# regenerar a linha de agendamento pela interface).
SCRIPT_ARGS = {
    "tech": ["--apenas-youtube"],
}

# Tag usada no comentário do crontab (# YOUTUBER:<tag>) → chave do pipeline no dashboard.
# tech_news.py é agendado com a tag "tecnologia", não "tech".
CRON_TAG_TO_KEY = {
    "noticias":     "noticias",
    "celebridades": "celebridades",
    "tecnologia":   "tech",
    "curiosidades": "curiosidades",
    "novela":       "novela",
    "produtos":     "produtos",
}
KEY_TO_CRON_TAG = {v: k for k, v in CRON_TAG_TO_KEY.items()}

LOG_FILES = {
    "noticias":     "logs/noticias.log",
    "celebridades": "logs/celebridades.log",
    "tech":         "logs/tecnologia.log",
    "curiosidades": "logs/curiosidades.log",
    "novela":       "logs/novela.log",
    "produtos":     "logs/afiliados.log",
}

_ERROR_MARKERS = ("Traceback (most recent call last)", "❌", "ERRO FATAL", "Pipeline abortado")


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_token_status() -> dict:
    if not os.path.exists(TOKEN_PATH):
        return {"ok": False, "msg": "token.json não encontrado", "expiry": None}
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GRequest

        creds = Credentials.from_authorized_user_file(TOKEN_PATH)
        if creds.valid:
            expiry = creds.expiry.strftime("%d/%m %H:%M") if creds.expiry else "?"
            return {"ok": True, "msg": f"Válido até {expiry}", "expiry": expiry}
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GRequest())
                with open(TOKEN_PATH, "w") as f:
                    f.write(creds.to_json())
                expiry = creds.expiry.strftime("%d/%m %H:%M") if creds.expiry else "?"
                return {"ok": True, "msg": f"Renovado — válido até {expiry}", "expiry": expiry}
            except Exception as e:
                msg = "Token revogado — reautenticar" if "invalid_grant" in str(e) else "Refresh falhou"
                return {"ok": False, "msg": msg, "expiry": None}
        return {"ok": False, "msg": "Token expirado — reautenticar necessário", "expiry": None}
    except Exception as e:
        return {"ok": False, "msg": f"Erro ao ler token: {e}", "expiry": None}


def _get_tiktok_status() -> dict:
    try:
        from tiktok_publisher import check_tiktok_token
        ok, msg = check_tiktok_token()
        return {"ok": ok, "msg": msg}
    except Exception as e:
        return {"ok": False, "msg": f"Erro: {e}"}


def _pipeline_state(key: str) -> dict:
    return _pipeline_status.get(key, {"status": "idle", "last_run": None, "message": ""})


_CRON_LINE_RE = re.compile(r"^(\d+)\s+(\d+)\s+\*\s+\*\s+\*\s+.*#\s*YOUTUBER:(\w+)")


def _get_cron_schedules() -> dict[str, list[str]]:
    """Lê o crontab do sistema e retorna {pipeline_key: ["HH:MM", ...]}.

    Só funciona em Linux (VPS) — em Windows local retorna vazio silenciosamente.
    """
    schedules: dict[str, list[str]] = {}
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return schedules
    if result.returncode != 0:
        return schedules

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _CRON_LINE_RE.match(line)
        if not match:
            continue
        minute, hour, tag = match.groups()
        key = CRON_TAG_TO_KEY.get(tag, tag)
        schedules.setdefault(key, []).append(f"{int(hour):02d}:{int(minute):02d}")

    for key in schedules:
        schedules[key].sort()
    return schedules


def _next_run_time(times: list[str]) -> str | None:
    """Dado horários 'HH:MM', retorna o próximo a ocorrer formatado (dd/mm HH:MM)."""
    if not times:
        return None
    now = datetime.now()
    candidates = []
    for t in times:
        hh, mm = map(int, t.split(":"))
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates).strftime("%d/%m %H:%M")


def _get_log_derived_state(key: str) -> dict | None:
    """Deriva status/last_run/message a partir do arquivo de log do pipeline.

    Cobre execuções via cron, que nunca passam pelo _pipeline_status em memória.
    """
    path = LOG_FILES.get(key)
    if not path or not os.path.exists(path):
        return None
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(max(0, os.path.getsize(path) - 8000))
            tail_lines = f.read().splitlines()
        is_error = any(marker in line for line in tail_lines for marker in _ERROR_MARKERS)
        last_line = next((l.strip() for l in reversed(tail_lines) if l.strip()), "")
        return {
            "status": "error" if is_error else "done",
            "last_run": mtime.isoformat(),
            "message": f"[cron] {last_line}"[:200],
        }
    except OSError:
        return None


def _merged_pipeline_state(key: str) -> dict:
    """Combina status em memória (execução manual via web) com o log (cron).

    Se uma execução manual está rodando agora, ela manda. Caso contrário,
    mostra o que tiver ocorrido mais recentemente entre os dois.
    """
    mem_state = _pipeline_status.get(key)
    if mem_state and mem_state.get("status") == "running":
        return mem_state

    log_state = _get_log_derived_state(key)
    if mem_state and log_state:
        mem_dt = datetime.fromisoformat(mem_state["last_run"]) if mem_state.get("last_run") else datetime.min
        log_dt = datetime.fromisoformat(log_state["last_run"])
        return mem_state if mem_dt >= log_dt else log_state

    return mem_state or log_state or {"status": "idle", "last_run": None, "message": ""}


def _set_pipeline_schedule(pipeline_key: str, times: list[str]) -> None:
    """Regenera as linhas de crontab de um pipeline com os horários informados.

    Lista vazia remove o agendamento (pipeline passa a ser só manual).
    """
    tag = KEY_TO_CRON_TAG.get(pipeline_key, pipeline_key)
    script = SCRIPTS[pipeline_key]
    extra_args = "".join(f" {a}" for a in SCRIPT_ARGS.get(pipeline_key, []))
    project_dir = os.path.dirname(os.path.abspath(__file__))
    log_file = os.path.join(project_dir, LOG_FILES.get(pipeline_key, f"logs/{tag}.log"))

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        existing = result.stdout.splitlines() if result.returncode == 0 else []
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        raise RuntimeError("crontab não disponível neste sistema") from e

    lines = [l for l in existing if f"# YOUTUBER:{tag}" not in l]

    for t in times:
        hh, mm = map(int, t.split(":"))
        lines.append(
            f'{mm:02d} {hh:02d} * * * cd {project_dir} && '
            f'"{sys.executable}" "{os.path.join(project_dir, script)}"{extra_args} '
            f'>> "{log_file}" 2>&1  # YOUTUBER:{tag}'
        )

    new_crontab = "\n".join(l for l in lines if l.strip()) + "\n"
    try:
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, timeout=5, check=True)
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        raise RuntimeError("crontab não disponível neste sistema") from e


def _cookie_token(request: Request) -> str | None:
    return request.cookies.get("session")


def _require_session(request: Request) -> dict | None:
    return auth.get_session(_cookie_token(request))


def _require_admin(request: Request) -> dict | None:
    s = _require_session(request)
    return s if s and s.get("role") == "admin" else None


def _require_client(request: Request) -> dict | None:
    s = _require_session(request)
    return s if s and s.get("role") == "client" else None


# ── auth routes ───────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _require_session(request):
        return _redirect_by_role(_require_session(request))
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    if auth.is_locked_out(email):
        return templates.TemplateResponse(
            request=request, name="login.html",
            context={"error": f"Muitas tentativas incorretas. Tente novamente em até {auth.LOCKOUT_MINUTES} minutos."},
        )

    # admin
    if auth.verify_admin(email, password):
        auth.clear_attempts(email)
        token = auth.create_session("admin", os.getenv("ADMIN_NAME", "Admin"), "admin")
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=8 * 3600)
        return resp

    # cliente
    c = db.verify_client(email, password)
    if c:
        auth.clear_attempts(email)
        token = auth.create_session(c["id"], c["name"], "client")
        resp = RedirectResponse("/cliente", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=8 * 3600)
        return resp

    auth.record_failed_attempt(email)
    return templates.TemplateResponse(
        request=request, name="login.html",
        context={"error": "E-mail ou senha incorretos."},
    )


@app.get("/logout")
async def logout(request: Request):
    auth.delete_session(_cookie_token(request))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


def _redirect_by_role(session: dict):
    if session["role"] == "admin":
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse("/cliente", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    s = _require_session(request)
    if s:
        return _redirect_by_role(s)
    return RedirectResponse("/login", status_code=303)


# ── admin routes ───────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, created: str = None, error: str = None, tiktok_ok: str = None):
    s = _require_admin(request)
    if not s:
        return RedirectResponse("/login", status_code=303)

    schedules = _get_cron_schedules()
    pipelines = []
    for p in PIPELINES:
        state = _merged_pipeline_state(p["key"])
        times = schedules.get(p["key"], [])
        pipelines.append({
            **p, **state,
            "schedule_times": times,
            "next_run": _next_run_time(times),
        })

    clients = db.list_clients()

    client_msg = None
    client_ok = False
    if created:
        client_msg = f"Cliente {created} criado com sucesso."
        client_ok = True
    elif tiktok_ok:
        client_msg = "TikTok conectado com sucesso."
        client_ok = True
    elif error:
        msgs = {
            "duplicate": "Este e-mail já está cadastrado.",
            "short_password": "A senha deve ter pelo menos 6 caracteres.",
            "tiktok_config": "TikTok não configurado — defina TIKTOK_CLIENT_KEY no .env.",
            "tiktok_denied": "Autorização do TikTok negada ou cancelada.",
            "tiktok_state": "Sessão de autorização do TikTok expirada — tente novamente.",
            "tiktok_token": "Falha ao trocar o código de autorização do TikTok pelo token.",
        }
        client_msg = msgs.get(error, "Erro ao processar solicitação.")

    return templates.TemplateResponse(
        request=request, name="dashboard.html",
        context={"session": s, "pipelines": pipelines, "clients": clients,
                 "client_msg": client_msg, "client_ok": client_ok},
    )


@app.post("/admin/clientes")
async def admin_create_client(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    s = _require_admin(request)
    if not s:
        return RedirectResponse("/login", status_code=303)

    if len(password) < 6:
        return RedirectResponse("/admin?error=short_password", status_code=303)

    try:
        c = db.create_client(name, email, password)
        return RedirectResponse(f"/admin?created={c['name']}", status_code=303)
    except Exception:
        return RedirectResponse("/admin?error=duplicate", status_code=303)


@app.post("/admin/clientes/{cid}/toggle")
async def admin_toggle_client(cid: str, request: Request):
    s = _require_admin(request)
    if not s:
        return RedirectResponse("/login", status_code=303)
    db.toggle_active(cid)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/schedule/{pipeline}")
async def admin_set_schedule(pipeline: str, request: Request):
    s = _require_admin(request)
    if not s:
        return JSONResponse({"error": "não autorizado"}, status_code=401)

    if pipeline not in SCRIPTS:
        return JSONResponse({"error": "pipeline desconhecido"}, status_code=400)

    body = await request.json()
    times = body.get("times", [])
    if not isinstance(times, list):
        return JSONResponse({"error": "'times' deve ser uma lista"}, status_code=422)
    for t in times:
        if not re.match(r"^\d{1,2}:\d{2}$", t or ""):
            return JSONResponse({"error": f"horário inválido: {t}"}, status_code=422)

    try:
        _set_pipeline_schedule(pipeline, times)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return {"ok": True, "times": sorted(times)}


# ── pipeline routes (admin only) ──────────────────────────────────────────────

@app.get("/status")
async def status():
    return {"ok": True, "time": datetime.now().isoformat(), "pipelines": _pipeline_status}


@app.get("/token-status")
async def token_status():
    return _get_token_status()


@app.get("/tiktok-status")
async def tiktok_status():
    return _get_tiktok_status()


@app.get("/admin/tiktok/connect")
async def admin_tiktok_connect(request: Request):
    global _tiktok_oauth_state
    s = _require_admin(request)
    if not s:
        return RedirectResponse("/login", status_code=303)

    try:
        from tiktok_publisher import get_authorization_url
        url, state = get_authorization_url()
    except Exception as e:
        return RedirectResponse(f"/admin?error=tiktok_config&msg={e}", status_code=303)

    _tiktok_oauth_state = state
    return RedirectResponse(url, status_code=303)


@app.get("/tiktok/callback")
async def tiktok_callback(request: Request, code: str = None, state: str = None, error: str = None):
    global _tiktok_oauth_state
    s = _require_admin(request)
    if not s:
        return RedirectResponse("/login", status_code=303)

    if error:
        return RedirectResponse("/admin?error=tiktok_denied", status_code=303)
    if not code or not state or state != _tiktok_oauth_state:
        return RedirectResponse("/admin?error=tiktok_state", status_code=303)

    _tiktok_oauth_state = None
    try:
        from tiktok_publisher import exchange_code_for_token
        exchange_code_for_token(code)
    except Exception:
        return RedirectResponse("/admin?error=tiktok_token", status_code=303)

    return RedirectResponse("/admin?tiktok_ok=1", status_code=303)


@app.post("/run/{pipeline}")
async def run_pipeline(pipeline: str, request: Request):
    s = _require_admin(request)
    if not s:
        return JSONResponse({"error": "não autorizado"}, status_code=401)

    if pipeline not in SCRIPTS:
        return JSONResponse({"error": "pipeline desconhecido"}, status_code=400)

    if _pipeline_status.get(pipeline, {}).get("status") == "running":
        return JSONResponse({"error": "já está rodando"}, status_code=409)

    _pipeline_status[pipeline] = {
        "status": "running",
        "last_run": datetime.now().isoformat(),
        "message": "Iniciando...",
    }
    asyncio.create_task(_execute_pipeline(pipeline))
    return {"ok": True, "pipeline": pipeline, "status": "running"}


async def _execute_pipeline(name: str):
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, SCRIPTS[name],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        last_line = ""
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                last_line = line
                _pipeline_status[name]["message"] = line
        await proc.wait()
        _pipeline_status[name].update({
            "status": "done" if proc.returncode == 0 else "error",
            "message": last_line or ("Concluído" if proc.returncode == 0 else "Erro"),
        })
    except Exception as e:
        _pipeline_status[name].update({"status": "error", "message": str(e)})


# ── cliente routes ─────────────────────────────────────────────────────────────

@app.get("/cliente", response_class=HTMLResponse)
async def cliente_panel(request: Request):
    s = _require_client(request)
    if not s:
        return RedirectResponse("/login", status_code=303)

    c = db.get_by_id(s["user_id"]) or {}
    config = None
    if c.get("nicho_config"):
        try:
            config = json.loads(c["nicho_config"])
        except Exception:
            pass

    runs = db.list_runs(s["user_id"], limit=10)

    return templates.TemplateResponse(
        request=request, name="painel_cliente.html",
        context={"session": s, "client": c, "config": config, "runs": runs},
    )


@app.post("/cliente/gerar-nicho")
async def cliente_gerar_nicho(request: Request):
    s = _require_client(request)
    if not s:
        return JSONResponse({"detail": "não autorizado"}, status_code=401)

    body = await request.json()
    description = (body.get("description") or "").strip()
    if not description:
        return JSONResponse({"detail": "Descrição obrigatória."}, status_code=422)

    try:
        from nicho_generator import generate_nicho_config
        cfg = generate_nicho_config(description)
    except Exception as e:
        return JSONResponse({"detail": str(e)}, status_code=500)

    db.update_nicho(s["user_id"], cfg.get("name", description), json.dumps(cfg, ensure_ascii=False))
    return cfg


@app.post("/cliente/run")
async def cliente_run(request: Request):
    s = _require_client(request)
    if not s:
        return JSONResponse({"detail": "não autorizado"}, status_code=401)

    uid = s["user_id"]
    if _client_status.get(uid, {}).get("status") == "running":
        return JSONResponse({"detail": "já está rodando"}, status_code=409)

    c = db.get_by_id(uid)
    if not c or not c.get("nicho_config"):
        return JSONResponse({"detail": "Nicho não configurado."}, status_code=400)

    _client_status[uid] = {"status": "running", "message": "Iniciando..."}
    asyncio.create_task(_execute_client_pipeline(uid, c["nicho_config"]))
    return {"ok": True}


@app.get("/cliente/run-status")
async def cliente_run_status(request: Request):
    s = _require_client(request)
    if not s:
        return JSONResponse({"detail": "não autorizado"}, status_code=401)
    return _client_status.get(s["user_id"], {"status": "idle", "message": ""})


async def _execute_client_pipeline(uid: str, nicho_config_json: str):
    run_id = db.create_run(uid)
    video_ids: list[str] = []
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "NICHO_CONFIG": nicho_config_json}
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "engine.runner",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        last_line = ""
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                last_line = line
                _client_status[uid]["message"] = line
                if line.startswith("VIDEO_ID:"):
                    video_ids.append(line.split("VIDEO_ID:", 1)[1].strip())
        await proc.wait()
        status = "done" if proc.returncode == 0 else "error"
        message = last_line or ("Concluído" if proc.returncode == 0 else "Erro")
        _client_status[uid].update({"status": status, "message": message})
        db.update_run(run_id, status, video_ids=video_ids, message=message)
    except Exception as e:
        _client_status[uid].update({"status": "error", "message": str(e)})
        db.update_run(run_id, "error", video_ids=video_ids, message=str(e))
