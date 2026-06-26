"""
Webserver — painel de controle dos pipelines do Youtuber no Automático.
Roda com: uvicorn webserver:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

app = FastAPI(title="Youtuber no Automático", version="0.1.0")
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------------------------
# Status em memória — pipelines rodando
# ---------------------------------------------------------------------------
_pipeline_status: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Token YouTube
# ---------------------------------------------------------------------------
TOKEN_PATH = "credentials/token.json"


def _get_token_status() -> dict:
    """Verifica e tenta renovar as credenciais OAuth do YouTube."""
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
                with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                expiry = creds.expiry.strftime("%d/%m %H:%M") if creds.expiry else "?"
                return {"ok": True, "msg": f"Renovado — válido até {expiry}", "expiry": expiry}
            except Exception as e:
                msg = "Token revogado — reautenticar" if "invalid_grant" in str(e) else "Refresh falhou"
                return {"ok": False, "msg": msg, "expiry": None}

        return {"ok": False, "msg": "Token expirado — reautenticar necessário", "expiry": None}

    except Exception as e:
        return {"ok": False, "msg": f"Erro ao ler token: {e}", "expiry": None}


def _pipeline_state(name: str) -> dict:
    return _pipeline_status.get(name, {"status": "idle", "last_run": None, "message": ""})


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    pipelines = [
        {"key": "noticias",      "label": "Notícias",      "icon": "📰"},
        {"key": "celebridades",  "label": "Celebridades",  "icon": "🎤"},
        {"key": "tech",          "label": "Tecnologia",    "icon": "💻"},
        {"key": "curiosidades",  "label": "Curiosidades",  "icon": "🧠"},
        {"key": "novela",        "label": "Novela IA",     "icon": "🎭"},
    ]
    for p in pipelines:
        p.update(_pipeline_state(p["key"]))
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"pipelines": pipelines})


@app.get("/status")
async def status():
    return {"ok": True, "time": datetime.now().isoformat(), "pipelines": _pipeline_status}


@app.get("/token-status")
async def token_status():
    return _get_token_status()


@app.post("/run/{pipeline}")
async def run_pipeline(pipeline: str):
    allowed = {"noticias", "celebridades", "tech", "curiosidades", "novela"}
    if pipeline not in allowed:
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
    """Executa o pipeline como subprocess e atualiza o status."""
    scripts = {
        "noticias":     "main.py",
        "celebridades": "celebridades.py",
        "tech":         "tech_news.py",
        "curiosidades": "curiosidades.py",
        "novela":       "novela.py",
    }
    script = scripts[name]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script,
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
