"""
Webserver — painel de controle dos pipelines do Youtuber no Automático.
Roda com: uvicorn webserver:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

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
