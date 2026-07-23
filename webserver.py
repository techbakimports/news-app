"""
Webserver — Youtuber no Automático
Roda com: uvicorn webserver:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime

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

# pipeline status global
_pipeline_status: dict[str, dict] = {}
# status por cliente (user_id → dict)
_client_status: dict[str, dict] = {}

TOKEN_PATH = "credentials/token.json"

PIPELINES = [
    {"key": "noticias",     "label": "Notícias",     "icon": "📰"},
    {"key": "celebridades", "label": "Celebridades", "icon": "🎤"},
    {"key": "tech",         "label": "Tecnologia",   "icon": "💻"},
    {"key": "curiosidades", "label": "Curiosidades", "icon": "🧠"},
    {"key": "novela",       "label": "Novela IA",    "icon": "🎭"},
]

SCRIPTS = {
    "noticias":     "main.py",
    "celebridades": "celebridades.py",
    "tech":         "tech_news.py",
    "curiosidades": "curiosidades.py",
    "novela":       "novela.py",
}


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


def _pipeline_state(key: str) -> dict:
    return _pipeline_status.get(key, {"status": "idle", "last_run": None, "message": ""})


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
    # admin
    if auth.verify_admin(email, password):
        token = auth.create_session("admin", os.getenv("ADMIN_NAME", "Admin"), "admin")
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=8 * 3600)
        return resp

    # cliente
    c = db.verify_client(email, password)
    if c:
        token = auth.create_session(c["id"], c["name"], "client")
        resp = RedirectResponse("/cliente", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=8 * 3600)
        return resp

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
async def admin_dashboard(request: Request, created: str = None, error: str = None):
    s = _require_admin(request)
    if not s:
        return RedirectResponse("/login", status_code=303)

    pipelines = []
    for p in PIPELINES:
        state = _pipeline_state(p["key"])
        pipelines.append({**p, **state})

    clients = db.list_clients()

    client_msg = None
    client_ok = False
    if created:
        client_msg = f"Cliente {created} criado com sucesso."
        client_ok = True
    elif error:
        msgs = {"duplicate": "Este e-mail já está cadastrado.", "short_password": "A senha deve ter pelo menos 6 caracteres."}
        client_msg = msgs.get(error, "Erro ao criar cliente.")

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


# ── pipeline routes (admin only) ──────────────────────────────────────────────

@app.get("/status")
async def status():
    return {"ok": True, "time": datetime.now().isoformat(), "pipelines": _pipeline_status}


@app.get("/token-status")
async def token_status():
    return _get_token_status()


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

    return templates.TemplateResponse(
        request=request, name="painel_cliente.html",
        context={"session": s, "client": c, "config": config},
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
        await proc.wait()
        _client_status[uid].update({
            "status": "done" if proc.returncode == 0 else "error",
            "message": last_line or ("Concluído" if proc.returncode == 0 else "Erro"),
        })
    except Exception as e:
        _client_status[uid].update({"status": "error", "message": str(e)})
