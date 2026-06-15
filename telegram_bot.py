"""Bot Telegram — Youtuber no Automático.

Uso: python telegram_bot.py

Vars necessárias no .env:
    TELEGRAM_BOT_TOKEN=<token do BotFather>
    TELEGRAM_CHAT_ID=<seu chat_id numérico — obter via @userinfobot no Telegram>

Manter rodando em background:
    Windows: pythonw telegram_bot.py   (sem janela de terminal)
    Linux:   nohup python telegram_bot.py &
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from html import escape as html_escape

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import RetryAfter
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

PYTHON   = sys.executable
BASE_DIR = Path(__file__).parent

IS_WINDOWS        = os.name == "nt"
TASK_FOLDER       = "YoutuberAutomatico"
TASK_NOTICIAS     = f"{TASK_FOLDER}\\Noticias"
TASK_AUDIO        = f"{TASK_FOLDER}\\AudioLongo"
TASK_CURIOSIDADES = f"{TASK_FOLDER}\\Curiosidades"
TASK_CELEBRIDADES = f"{TASK_FOLDER}\\Celebridades"
TASK_TECNOLOGIA   = f"{TASK_FOLDER}\\Tecnologia"
CRON_NOTICIAS     = "YOUTUBER:noticias"
CRON_AUDIO        = "YOUTUBER:audio"
CRON_CURIOSIDADES = "YOUTUBER:curiosidades"
CRON_CELEBRIDADES = "YOUTUBER:celebridades"
CRON_TECNOLOGIA   = "YOUTUBER:tecnologia"
LOG_DIR           = BASE_DIR / "logs"

# Mapeamentos pra acelerar lookups
_TASKS_BY_TIPO = {
    "noticias":     TASK_NOTICIAS,
    "audio":        TASK_AUDIO,
    "curiosidades": TASK_CURIOSIDADES,
    "celebridades": TASK_CELEBRIDADES,
    "tecnologia":   TASK_TECNOLOGIA,
}
_TAGS_BY_TIPO = {
    "noticias":     CRON_NOTICIAS,
    "audio":        CRON_AUDIO,
    "curiosidades": CRON_CURIOSIDADES,
    "celebridades": CRON_CELEBRIDADES,
    "tecnologia":   CRON_TECNOLOGIA,
}
SCHEDULER_CFG = BASE_DIR / "scheduler.json"

# Rate-limiter global para editMessageText (Telegram: ~20 edições/min por chat)
_tg_edit_lock: asyncio.Lock | None = None
_tg_last_edit: float = 0.0
_TG_MIN_GAP = 3.5  # segundos mínimos entre chamadas consecutivas

# Proteção contra processos travados
_PIPELINE_TIMEOUT = 3600  # 60 minutos max por pipeline (pipeline cresceu: 10 fontes + 5 Shorts por categoria)
_active_pipelines: int = 0


async def _safe_edit(msg, text: str, **kwargs) -> None:
    """Edita mensagem com rate-limit global (máx ~17 edições/min)."""
    global _tg_edit_lock, _tg_last_edit
    if _tg_edit_lock is None:
        _tg_edit_lock = asyncio.Lock()
    async with _tg_edit_lock:
        loop = asyncio.get_running_loop()
        gap = _TG_MIN_GAP - (loop.time() - _tg_last_edit)
        if gap > 0:
            await asyncio.sleep(gap)
        try:
            await msg.edit_text(text, parse_mode="HTML", **kwargs)
            _tg_last_edit = loop.time()
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await msg.edit_text(text, parse_mode="HTML", **kwargs)
                _tg_last_edit = loop.time()
            except Exception:
                pass
        except Exception:
            pass


SONS = {
    "rain":       "Chuva",
    "ocean":      "Ondas do Mar",
    "fire":       "Lareira",
    "forest":     "Floresta",
    "whitenoise": "Ruído Branco",
    "brownnoise": "Ruído Marrom",
}

# visib codes: "pub" → público | "priv" → privado | "local" → sem upload
_VISIB_LABEL = {"pub": "público", "priv": "privado", "local": "local"}


# ── agendamento (mesmo logic do menu.py) ──────────────────────────────────────

def _ler_cfg() -> dict:
    padrao = {
        "noticias": {"ativo": False, "horario": "06:00", "privado": False},
        "audio":    {"ativo": False, "horario": "08:00", "tipo": "rain", "horas": 8, "privado": False},
    }
    if SCHEDULER_CFG.exists():
        try:
            cfg = json.loads(SCHEDULER_CFG.read_text(encoding="utf-8"))
            for k, v in padrao.items():
                cfg.setdefault(k, dict(v))
            return cfg
        except Exception:
            pass
    return padrao


def _salvar_cfg(cfg: dict) -> None:
    SCHEDULER_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _cmd_noticias(privado: bool) -> str:
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{BASE_DIR / "main.py"}"'
    if privado:
        cmd += " --privado"
    return cmd


def _cmd_audio(tipo: str, horas: float, privado: bool) -> str:
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{BASE_DIR / "ambient_video.py"}" {tipo} --horas {horas}'
    return cmd + " --privado" if privado else cmd


def _cmd_curiosidades(privado: bool) -> str:
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{BASE_DIR / "curiosidades.py"}"'
    if privado:
        cmd += " --privado"
    return cmd


def _cmd_celebridades(privado: bool) -> str:
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{BASE_DIR / "celebridades.py"}"'
    if privado:
        cmd += " --privado"
    return cmd


def _cmd_tecnologia(privado: bool) -> str:
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{BASE_DIR / "tech_news.py"}" --apenas-youtube'
    if privado:
        cmd += " --privado"
    return cmd


def _criar_agendamento(tipo: str, comando: str, horarios) -> None:
    """
    Cria entradas de cron/Task pra um pipeline.
    horarios pode ser uma string única (compat) ou uma lista.
    """
    # Normaliza pra lista
    if isinstance(horarios, str):
        horarios = [horarios]

    if IS_WINDOWS:
        task_prefix = _TASKS_BY_TIPO.get(tipo, TASK_NOTICIAS)
        # Remove tasks antigas com o mesmo prefixo
        _remover_agendamento(tipo)
        # Cria 1 task por horário
        for h in horarios:
            sufixo = h.replace(":", "_")
            task_name = f"{task_prefix}_{sufixo}"
            subprocess.run(
                ["schtasks", "/Create", "/TN", task_name, "/TR", comando,
                 "/SC", "DAILY", "/ST", h, "/F"],
                check=True, capture_output=True,
            )
    else:
        tag = _TAGS_BY_TIPO.get(tipo, CRON_NOTICIAS)
        LOG_DIR.mkdir(exist_ok=True)
        log_path = LOG_DIR / f"{tag.split(':')[-1]}.log"
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        crontab = r.stdout if r.returncode == 0 else ""
        # Remove TODAS as linhas existentes desse tag, depois adiciona uma por horário
        linhas = [l for l in crontab.splitlines(keepends=True) if f"# {tag}" not in l]
        for h in horarios:
            hh, mm = h.split(":")
            linhas.append(f"{mm} {hh} * * * {comando} >> {log_path} 2>&1  # {tag}\n")
        subprocess.run(["crontab", "-"], input="".join(linhas), text=True, check=True)


def _remover_agendamento(tipo: str) -> None:
    if IS_WINDOWS:
        task_prefix = _TASKS_BY_TIPO.get(tipo, TASK_NOTICIAS)
        # Remove TODAS as tasks que começam com o prefixo
        try:
            r = subprocess.run(
                ["schtasks", "/Query", "/FO", "CSV", "/NH"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                folder_prefix = "\\" + task_prefix
                for line in r.stdout.splitlines():
                    parts = [p.strip('"') for p in line.split('","')]
                    if not parts:
                        continue
                    tn = parts[0].lstrip('"')
                    if tn.startswith(folder_prefix):
                        subprocess.run(
                            ["schtasks", "/Delete", "/TN", tn, "/F"],
                            capture_output=True,
                        )
        except Exception:
            pass
    else:
        tag = _TAGS_BY_TIPO.get(tipo, CRON_NOTICIAS)
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        crontab = r.stdout if r.returncode == 0 else ""
        linhas = [l for l in crontab.splitlines(keepends=True) if f"# {tag}" not in l]
        subprocess.run(["crontab", "-"], input="".join(linhas), text=True, check=True)


# ── teclados ──────────────────────────────────────────────────────────────────

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Vídeo Longo",          callback_data="nav|video_longo")],
        [InlineKeyboardButton("📱 Shorts",               callback_data="nav|shorts_menu")],
        [InlineKeyboardButton("🎵 Áudio Longo",          callback_data="nav|audio")],
        [InlineKeyboardButton("⏰ Agendamento",           callback_data="nav|agenda")],
        [InlineKeyboardButton("📂 Organizar Playlists",  callback_data="run|playlists")],
    ])


def kb_video_longo() -> InlineKeyboardMarkup:
    """Submenu de Vídeo Longo — 4 nichos."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📰 Notícias",    callback_data="nav|vl|noticias")],
        [InlineKeyboardButton("🧠 Curiosidades",callback_data="nav|vl|curiosidades")],
        [InlineKeyboardButton("🌟 Celebridades",callback_data="nav|vl|celebridades")],
        [InlineKeyboardButton("💻 Tecnologia",  callback_data="nav|vl|tecnologia")],
        [InlineKeyboardButton("⬅️ Voltar",      callback_data="nav|main")],
    ])


def kb_shorts_menu() -> InlineKeyboardMarkup:
    """Submenu de Shorts — 4 nichos."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📰 Notícias",    callback_data="nav|sh|noticias")],
        [InlineKeyboardButton("🧠 Curiosidades",callback_data="nav|sh|curiosidades")],
        [InlineKeyboardButton("🌟 Celebridades",callback_data="nav|sh|celebridades")],
        [InlineKeyboardButton("💻 Tecnologia",  callback_data="nav|sh|tecnologia")],
        [InlineKeyboardButton("⬅️ Voltar",      callback_data="nav|main")],
    ])


def kb_nicho_yt(nicho: str, back: str) -> InlineKeyboardMarkup:
    """Opções YouTube-only para um nicho (Shorts ou Vídeo Longo)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Publicar YouTube (público)",  callback_data=f"run|sh|{nicho}|pub")],
        [InlineKeyboardButton("🔒 Publicar YouTube (privado)",  callback_data=f"run|sh|{nicho}|priv")],
        [InlineKeyboardButton("💾 Só gerar (sem upload)",       callback_data=f"run|sh|{nicho}|local")],
        [InlineKeyboardButton("⬅️ Voltar",                      callback_data=back)],
    ])


# ── teclados legado (mantidos para compatibilidade com callbacks antigos) ──

def kb_noticias() -> InlineKeyboardMarkup:
    return kb_nicho_yt("noticias", "nav|shorts_menu")



def kb_curiosidades() -> InlineKeyboardMarkup:
    return kb_nicho_yt("curiosidades", "nav|shorts_menu")


def kb_audio_tipo_run() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"🎧 {label}", callback_data=f"ah_run|{tipo}")]
            for tipo, label in SONS.items()]
    rows.append([InlineKeyboardButton("🎛️ Todos os tipos", callback_data="ah_run|todos")])
    rows.append([InlineKeyboardButton("⬅️ Voltar", callback_data="nav|main")])
    return InlineKeyboardMarkup(rows)


def kb_audio_tipo_agenda() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"🎧 {label}", callback_data=f"ah_ag|{tipo}")]
            for tipo, label in SONS.items()]
    rows.append([InlineKeyboardButton("⬅️ Voltar", callback_data="nav|agenda")])
    return InlineKeyboardMarkup(rows)


def _kb_horas(prefixo: str, back: str) -> InlineKeyboardMarkup:
    horas = ["1", "2", "4", "6", "8", "10"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{h}h", callback_data=f"{prefixo}|{h}") for h in horas[:3]],
        [InlineKeyboardButton(f"{h}h", callback_data=f"{prefixo}|{h}") for h in horas[3:]],
        [InlineKeyboardButton("⬅️ Voltar", callback_data=back)],
    ])


def kb_audio_horas_run(tipo: str) -> InlineKeyboardMarkup:
    return _kb_horas(f"au_run|{tipo}", "nav|audio")


def kb_audio_horas_agenda(tipo: str) -> InlineKeyboardMarkup:
    return _kb_horas(f"au_ag|{tipo}", "ag_a_tipo")


def kb_audio_upload(tipo: str, horas: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Público",    callback_data=f"run|audio|{tipo}|{horas}|pub")],
        [InlineKeyboardButton("🔒 Privado",    callback_data=f"run|audio|{tipo}|{horas}|priv")],
        [InlineKeyboardButton("💾 Sem upload", callback_data=f"run|audio|{tipo}|{horas}|local")],
        [InlineKeyboardButton("⬅️ Voltar",     callback_data=f"ah_run|{tipo}")],
    ])


def kb_shorts() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📰 Notícias de hoje (público)",      callback_data="sq|not|pub")],
        [InlineKeyboardButton("📺 De vídeos existentes (público)",  callback_data="sq|exi|pub")],
        [InlineKeyboardButton("💾 Notícias de hoje (sem upload)",   callback_data="sq|not|local")],
        [InlineKeyboardButton("🔒 De vídeos existentes (privado)",  callback_data="sq|exi|priv")],
        [InlineKeyboardButton("⬅️ Voltar",                          callback_data="nav|main")],
    ])


def kb_qtd(fonte: str, visib: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(str(q), callback_data=f"run|shorts|{fonte}|{visib}|{q}") for q in [1, 2, 3, 5]],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="nav|shorts")],
    ])


def _status_label(entry: dict) -> str:
    if not entry.get("ativo"):
        return "❌ inativo"
    horarios = entry.get("horarios") or ([entry["horario"]] if "horario" in entry else [])
    return "✅ " + ", ".join(horarios) if horarios else "✅ (sem horários)"


def kb_agenda() -> InlineKeyboardMarkup:
    cfg = _ler_cfg()
    # .get com default — não quebra se scheduler.json for legado/incompleto
    _default = {"ativo": False, "horarios": [], "privado": False}
    n  = cfg.get("noticias",     _default)
    a  = cfg.get("audio",        _default)
    c  = cfg.get("curiosidades", _default)
    ce = cfg.get("celebridades", _default)
    te = cfg.get("tecnologia",   _default)
    sn, sa, sc, sce, ste = (
        _status_label(n), _status_label(a), _status_label(c),
        _status_label(ce), _status_label(te),
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📰 Notícias — {sn}",      callback_data="ag_n_hora")],
        [InlineKeyboardButton(f"🎵 Áudio Longo — {sa}",   callback_data="ag_a_tipo")],
        [InlineKeyboardButton(f"🧠 Curiosidades — {sc}",  callback_data="ag_c_hora")],
        [InlineKeyboardButton(f"🌟 Celebridades — {sce}", callback_data="ag_cel_hora")],
        [InlineKeyboardButton(f"💻 Tecnologia — {ste}",   callback_data="ag_tec_hora")],
        [InlineKeyboardButton("🗑️ Desativar Notícias",     callback_data="run|ag|des|noticias")],
        [InlineKeyboardButton("🗑️ Desativar Áudio Longo",  callback_data="run|ag|des|audio")],
        [InlineKeyboardButton("🗑️ Desativar Curiosidades", callback_data="run|ag|des|curiosidades")],
        [InlineKeyboardButton("🗑️ Desativar Celebridades", callback_data="run|ag|des|celebridades")],
        [InlineKeyboardButton("🗑️ Desativar Tecnologia",   callback_data="run|ag|des|tecnologia")],
        [InlineKeyboardButton("⬅️ Voltar",                  callback_data="nav|main")],
    ])


def _kb_horarios(prefixo: str, back: str) -> InlineKeyboardMarkup:
    horarios = ["05:00", "06:00", "07:00", "08:00", "09:00", "10:00"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(h, callback_data=f"{prefixo}|{h}") for h in horarios[:3]],
        [InlineKeyboardButton(h, callback_data=f"{prefixo}|{h}") for h in horarios[3:]],
        [InlineKeyboardButton("⬅️ Voltar", callback_data=back)],
    ])


def _kb_picker_horarios(prefix_h: str, horarios_str: str, back: str,
                        prefix_next: str, extra_params: str = "",
                        tipo_ag: str = "") -> InlineKeyboardMarkup:
    """
    Picker de múltiplos horários (toggle).
    Cada botão de hora alterna se está na lista.
    'extra_params' vai concatenado em prefix_next (ex: tipo|horas pro áudio).
    'tipo_ag' (noticias|curiosidades|audio): quando informado, exibe o botão
    'Desativar agendamento' enquanto a seleção estiver vazia — dispara
    'run|ag|des|<tipo_ag>' que remove o cron e marca ativo=False no cfg.
    """
    horarios_atuais = [h for h in horarios_str.split(",") if h] if horarios_str else []
    horas = ["05:00", "06:00", "07:00", "08:00", "09:00", "10:00",
             "12:00", "14:00", "16:00", "18:00", "20:00", "22:00"]
    rows = []
    for i in range(0, len(horas), 3):
        row = []
        for h in horas[i:i+3]:
            if h in horarios_atuais:
                novo = [x for x in horarios_atuais if x != h]
                row.append(InlineKeyboardButton(
                    f"✓ {h}", callback_data=f"{prefix_h}|{','.join(novo)}"
                ))
            else:
                novo = sorted(horarios_atuais + [h])
                row.append(InlineKeyboardButton(
                    h, callback_data=f"{prefix_h}|{','.join(novo)}"
                ))
        rows.append(row)
    if horarios_atuais:
        next_data = f"{prefix_next}|{extra_params}|{horarios_str}" if extra_params else f"{prefix_next}|{horarios_str}"
        rows.append([InlineKeyboardButton(
            f"✅ Continuar ({len(horarios_atuais)} horário{'s' if len(horarios_atuais) > 1 else ''})",
            callback_data=next_data,
        )])
        rows.append([InlineKeyboardButton("🗑️ Limpar", callback_data=f"{prefix_h}|")])
    elif tipo_ag:
        # Seleção vazia + tipo conhecido → permite desativar agendamento de fato
        rows.append([InlineKeyboardButton(
            "🚫 Desativar agendamento",
            callback_data=f"run|ag|des|{tipo_ag}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Voltar", callback_data=back)])
    return InlineKeyboardMarkup(rows)


def _ag_titulo(nome: str, horarios: list[str]) -> str:
    if horarios:
        return (
            f"⏰ <b>{nome}</b> — Horários selecionados:\n"
            f"<code>{', '.join(horarios)}</code>\n\n"
            f"Toque pra adicionar/remover. Vários horários permitidos."
        )
    return (
        f"⏰ <b>{nome}</b> — Selecione um ou mais horários diários.\n"
        f"<i>Toque numa hora pra adicionar. Toque de novo pra remover.</i>"
    )


def _kb_privacidade(prefixo: str, back: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Público",  callback_data=f"{prefixo}|pub")],
        [InlineKeyboardButton("🔒 Privado",  callback_data=f"{prefixo}|priv")],
        [InlineKeyboardButton("⬅️ Voltar",  callback_data=back)],
    ])


# ── execução de pipelines ─────────────────────────────────────────────────────

async def _run_pipeline(chat_id: int, bot, cmd: list, descricao: str, msg=None) -> None:
    global _active_pipelines

    if msg is None:
        msg = await bot.send_message(chat_id, f"⏳ <b>{descricao}</b>", parse_mode="HTML")
    else:
        try:
            await msg.edit_text(f"⏳ <b>{descricao}</b>", parse_mode="HTML")
        except Exception:
            msg = await bot.send_message(chat_id, f"⏳ <b>{descricao}</b>", parse_mode="HTML")

    _active_pipelines += 1
    lines: list[str] = []
    loop       = asyncio.get_running_loop()
    start_time = loop.time()

    async def _editar(texto_final: str = "") -> None:
        tail = "\n".join(lines[-12:])[:3400]
        if texto_final:
            text = texto_final
        else:
            elapsed = int(loop.time() - start_time)
            text = f"⏳ <b>{descricao}</b> ({elapsed}s)\n\n<code>{tail}</code>"
        await _safe_edit(msg, text)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    texto = f"❌ Erro interno desconhecido"
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=BASE_DIR,
            env=env,
        )

        async def _ler():
            buf = b""
            while True:
                chunk = await proc.stdout.read(256)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
                buf += chunk
                while b"\n" in buf:
                    line_bytes, buf = buf.split(b"\n", 1)
                    decoded = line_bytes.decode(errors="replace")
                    # \r-overwrite lines: pega só o último "frame"
                    linha = decoded.split("\r")[-1].strip()
                    if linha:
                        lines.append(linha)
            if buf.strip():
                linha = buf.decode(errors="replace").split("\r")[-1].strip()
                if linha:
                    lines.append(linha)

        async def _atualizar():
            while True:
                await asyncio.sleep(10.0)
                await _editar()

        reader  = asyncio.create_task(_ler())
        updater = asyncio.create_task(_atualizar())

        # Aguardar com timeout — mata processos travados
        done, _ = await asyncio.wait({reader}, timeout=_PIPELINE_TIMEOUT)
        updater.cancel()

        if not done:
            # Timeout — processo travou, matar
            proc.kill()
            reader.cancel()
            await proc.wait()
            elapsed = int(loop.time() - start_time)
            texto = (
                f"⏰ <b>{descricao}</b> — timeout ({elapsed // 60} min)\n\n"
                "Processo excedeu o tempo máximo e foi encerrado.\n"
                "Causa provável: autenticação travada ou processo em loop."
            )
        else:
            await proc.wait()
            tail = "\n".join(lines[-20:])[:3800]

            if proc.returncode == 0:
                texto = f"✅ <b>{descricao}</b> concluído!\n\n<code>{tail}</code>"
            else:
                texto = f"❌ <b>{descricao}</b> falhou (código {proc.returncode})\n\n<code>{tail}</code>"

    except Exception as exc:
        texto = f"❌ Erro interno: {exc}"
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
    finally:
        _active_pipelines -= 1

    await _editar(texto)




# ── tech digest (execução inline, não subprocess) ────────────────────────────

async def _run_tech_digest(chat_id: int, bot, msg) -> None:
    try:
        await msg.edit_text(
            "🔬 <b>Tech Digest</b>\n\n⏳ Iniciando...",
            parse_mode="HTML",
        )
    except Exception:
        msg = await bot.send_message(
            chat_id,
            "🔬 <b>Tech Digest</b>\n\n⏳ Iniciando...",
            parse_mode="HTML",
        )

    last_status = ""

    async def on_progress(status: str):
        nonlocal last_status
        last_status = status
        await _safe_edit(msg, f"🔬 <b>Tech Digest</b>\n\n⏳ {html_escape(status)}")

    try:
        from tech_news_digest import generate_tech_digest
        result = await generate_tech_digest(on_progress=on_progress)
    except Exception as exc:
        await _safe_edit(msg, f"🔬 <b>Tech Digest</b>\n\n❌ Erro: {html_escape(str(exc))}")
        return

    header = "🔬 <b>Tech Digest — Notícias de Tecnologia</b>\n\n"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Gerar novamente", callback_data="run|tech_digest")],
        [InlineKeyboardButton("⬅️ Voltar",           callback_data="nav|main")],
    ])

    max_len = 4096 - len(header) - 50
    escaped = html_escape(result)
    if len(escaped) > max_len:
        escaped = escaped[:max_len] + "\n\n<i>… (truncado)</i>"

    try:
        await msg.edit_text(header + escaped, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await msg.edit_text(result[:4000], reply_markup=kb)


# ── handlers ──────────────────────────────────────────────────────────────────

async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura excecoes nao tratadas — evita que o bot trave sem feedback."""
    err = context.error
    err_type = type(err).__name__
    print(f"\n⚠️ Erro nao tratado no bot: {err_type}: {err}")

    # Tenta avisar o user na conversa
    try:
        chat_id = None
        if isinstance(update, Update):
            if update.callback_query and update.callback_query.message:
                chat_id = update.callback_query.message.chat_id
            elif update.message:
                chat_id = update.message.chat_id
        if chat_id and str(chat_id) == str(CHAT_ID):
            sugestao = ""
            if "KeyError" in err_type:
                sugestao = (
                    "\n\n<i>Provavelmente bot rodando com codigo antigo "
                    "em memoria. Pra corrigir:</i>\n"
                    "<code>cd ~/news-app && git pull\n"
                    "kill $(pgrep -f telegram_bot.py)\n"
                    "nohup .venv/bin/python telegram_bot.py > logs/bot.log 2>&1 &</code>"
                )
            await context.bot.send_message(
                chat_id,
                f"⚠️ <b>Erro interno do bot</b>\n\n"
                f"<code>{html_escape(err_type)}: {html_escape(str(err))[:300]}</code>"
                f"{sugestao}",
                parse_mode="HTML",
            )
    except Exception as e:
        print(f"   (falhou ao avisar user: {e})")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != CHAT_ID:
        return
    from config import __version__
    await update.message.reply_text(
        f"🤖 <b>Youtuber no Automático</b> <code>v{__version__}</code>\nEscolha uma opção:",
        reply_markup=kb_main(),
        parse_mode="HTML",
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.message.chat_id != CHAT_ID:
        return
    await q.answer()

    parts  = q.data.split("|")
    action = parts[0]

    # ── navegação ──────────────────────────────────────────────────────────────
    if action == "nav":
        dest = parts[1] if len(parts) > 1 else "main"

        # ── menu principal ────────────────────────────────────────────────────
        if dest == "main":
            await q.edit_message_text(
                "🤖 <b>Youtuber no Automático</b>\nEscolha uma opção:",
                reply_markup=kb_main(), parse_mode="HTML",
            )

        # ── vídeo longo — seleção de nicho ────────────────────────────────────
        elif dest == "video_longo":
            await q.edit_message_text(
                "🎬 <b>Vídeo Longo</b>\n\nEscolha o nicho:",
                reply_markup=kb_video_longo(), parse_mode="HTML",
            )

        # ── vídeo longo — nichos individuais (em desenvolvimento) ─────────────
        elif dest == "vl":
            nicho = parts[2] if len(parts) > 2 else ""
            _NOMES = {
                "noticias":    "📰 Notícias",
                "curiosidades":"🧠 Curiosidades",
                "celebridades":"🌟 Celebridades",
                "tecnologia":  "💻 Tecnologia",
            }
            nome = _NOMES.get(nicho, nicho.title())
            await q.edit_message_text(
                f"🎬 <b>Vídeo Longo — {nome}</b>\n\n"
                "🚧 <i>Pipeline de vídeo longo em desenvolvimento.\n"
                "Em breve disponível aqui!</i>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Voltar", callback_data="nav|video_longo")],
                ]),
                parse_mode="HTML",
            )

        # ── shorts — seleção de nicho ─────────────────────────────────────────
        elif dest == "shorts_menu":
            await q.edit_message_text(
                "📱 <b>Shorts</b>\n\nEscolha o nicho:",
                reply_markup=kb_shorts_menu(), parse_mode="HTML",
            )

        # ── shorts — nichos individuais ───────────────────────────────────────
        elif dest == "sh":
            nicho = parts[2] if len(parts) > 2 else ""
            _INFO = {
                "noticias":     ("📰 Notícias",     "1 Short por categoria (Política, Policial, Mercado, Entretenimento, Celebridades)\nVoz por categoria · Groq → Gemini · Pexels"),
                "curiosidades": ("🧠 Curiosidades",  "1 Short com curiosidade aleatória gerada pelo Gemini\nTema sempre novo · Voz: Francisca · ~2m30s"),
                "celebridades": ("🌟 Celebridades",  "Até 3 Shorts de fofoca dos 13 portais BR\nVoz: Thalita · Tom gossip · Groq → Gemini"),
                "tecnologia":   ("💻 Tecnologia",    "Até 5 Shorts de tech via Google News\nVoz: Francisca · Groq → Gemini"),
            }
            nome, desc = _INFO.get(nicho, (nicho.title(), ""))
            await q.edit_message_text(
                f"📱 <b>Shorts — {nome}</b>\n\n{desc}\n\n<i>Publicação:</i>",
                reply_markup=kb_nicho_yt(nicho, "nav|shorts_menu"),
                parse_mode="HTML",
            )

        # ── áudio longo ───────────────────────────────────────────────────────
        elif dest == "audio":
            await q.edit_message_text(
                "🎵 <b>Áudio Longo</b> — Tipo de som:",
                reply_markup=kb_audio_tipo_run(), parse_mode="HTML",
            )

        # ── legado (callbacks antigos ainda podem chegar) ─────────────────────
        elif dest == "shorts":
            await q.edit_message_text("📱 <b>Shorts</b>", reply_markup=kb_shorts(), parse_mode="HTML")
        elif dest == "noticias":
            await q.edit_message_text(
                "📰 <b>Notícias</b>",
                reply_markup=kb_noticias(), parse_mode="HTML",
            )
        elif dest == "curiosidades":
            await q.edit_message_text(
                "🧠 <b>Curiosidades</b>",
                reply_markup=kb_curiosidades(), parse_mode="HTML",
            )

        # ── agendamento ───────────────────────────────────────────────────────
        elif dest == "agenda":
            await q.edit_message_text("⏰ <b>Agendamento</b>", reply_markup=kb_agenda(), parse_mode="HTML")

        # ── instagram (status) ────────────────────────────────────────────────
        elif dest == "instagram":
            from instagram_uploader import INSTAGRAM_ENABLED
            status = "✅ <b>ATIVO</b>" if INSTAGRAM_ENABLED else "❌ <b>INATIVO</b>"
            await q.edit_message_text(
                f"📸 Instagram\n\n{status}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="nav|main")]]),
                parse_mode="HTML",
            )
        return

    # ── áudio: seleção de horas (execução) ────────────────────────────────────
    if action == "ah_run":
        tipo  = parts[1]
        label = SONS.get(tipo, tipo.title())
        await q.edit_message_text(
            f"🎵 <b>{label}</b> — Duração:",
            reply_markup=kb_audio_horas_run(tipo), parse_mode="HTML",
        )
        return

    # ── áudio: seleção de upload (execução) ───────────────────────────────────
    if action == "au_run":
        tipo, horas = parts[1], parts[2]
        label = SONS.get(tipo, tipo.title())
        await q.edit_message_text(
            f"🎵 <b>{label} {horas}h</b> — Publicar como:",
            reply_markup=kb_audio_upload(tipo, horas), parse_mode="HTML",
        )
        return

    # ── shorts: seleção de quantidade ─────────────────────────────────────────
    if action == "sq":
        fonte, visib = parts[1], parts[2]
        titulo = "notícias de hoje" if fonte == "not" else "vídeos existentes"
        await q.edit_message_text(
            f"📱 Shorts de {titulo} — Quantos?",
            reply_markup=kb_qtd(fonte, visib), parse_mode="HTML",
        )
        return

    # ── agenda: tipo de som para áudio ────────────────────────────────────────
    if action == "ag_a_tipo":
        await q.edit_message_text(
            "⏰ <b>Áudio Longo</b> — Tipo de som:",
            reply_markup=kb_audio_tipo_agenda(), parse_mode="HTML",
        )
        return

    # ── agenda: horas de áudio ────────────────────────────────────────────────
    if action == "ah_ag":
        tipo  = parts[1]
        label = SONS.get(tipo, tipo.title())
        await q.edit_message_text(
            f"⏰ {label} — Duração:",
            reply_markup=kb_audio_horas_agenda(tipo), parse_mode="HTML",
        )
        return

    # ── agenda: horário diário de áudio ───────────────────────────────────────
    if action == "au_ag":
        tipo, horas = parts[1], parts[2]
        label = SONS.get(tipo, tipo.title())
        await q.edit_message_text(
            f"⏰ {label} {horas}h — Horário diário:",
            reply_markup=_kb_horarios(f"ap_ag|{tipo}|{horas}", f"ah_ag|{tipo}"),
            parse_mode="HTML",
        )
        return

    # ── agenda: privacidade de áudio ──────────────────────────────────────────
    if action == "ap_ag":
        tipo, horas, horario = parts[1], parts[2], parts[3]
        label = SONS.get(tipo, tipo.title())
        await q.edit_message_text(
            f"⏰ {label} {horas}h às {horario} — Publicar como:",
            reply_markup=_kb_privacidade(
                f"run|ag|ativar_a|{tipo}|{horas}|{horario}",
                f"au_ag|{tipo}|{horas}",
            ),
            parse_mode="HTML",
        )
        return

    # ── agenda: horários de notícias (entrada) ────────────────────────────────
    if action == "ag_n_hora":
        cfg = _ler_cfg()
        entry = cfg.get("noticias", {})
        atual = entry.get("horarios", []) if entry.get("ativo") else []
        horarios_str = ",".join(atual)
        await q.edit_message_text(
            _ag_titulo("Notícias", atual),
            reply_markup=_kb_picker_horarios("ag_n_h", horarios_str, "nav|agenda", "ag_n_priv",
                                             tipo_ag="noticias"),
            parse_mode="HTML",
        )
        return

    # ── agenda: toggle de horário de notícias ─────────────────────────────────
    if action == "ag_n_h":
        horarios_str = parts[1] if len(parts) > 1 else ""
        atual = [h for h in horarios_str.split(",") if h]
        await q.edit_message_text(
            _ag_titulo("Notícias", atual),
            reply_markup=_kb_picker_horarios("ag_n_h", horarios_str, "nav|agenda", "ag_n_priv",
                                             tipo_ag="noticias"),
            parse_mode="HTML",
        )
        return

    # ── agenda: privacidade de notícias ───────────────────────────────────────
    if action == "ag_n_priv":
        horarios_str = parts[1]
        await q.edit_message_text(
            f"⏰ Notícias em <code>{horarios_str.replace(',', ', ')}</code> — Publicar como:",
            reply_markup=_kb_privacidade(
                f"run|ag|ativar_n|{horarios_str}",
                f"ag_n_h|{horarios_str}",
            ),
            parse_mode="HTML",
        )
        return

    # ── agenda: horários de curiosidades (entrada) ────────────────────────────
    if action == "ag_c_hora":
        cfg = _ler_cfg()
        entry = cfg.get("curiosidades", {})
        atual = entry.get("horarios", []) if entry.get("ativo") else []
        horarios_str = ",".join(atual)
        await q.edit_message_text(
            _ag_titulo("Curiosidades", atual),
            reply_markup=_kb_picker_horarios("ag_c_h", horarios_str, "nav|agenda", "ag_c_priv",
                                             tipo_ag="curiosidades"),
            parse_mode="HTML",
        )
        return

    # ── agenda: toggle de horário de curiosidades ─────────────────────────────
    if action == "ag_c_h":
        horarios_str = parts[1] if len(parts) > 1 else ""
        atual = [h for h in horarios_str.split(",") if h]
        await q.edit_message_text(
            _ag_titulo("Curiosidades", atual),
            reply_markup=_kb_picker_horarios("ag_c_h", horarios_str, "nav|agenda", "ag_c_priv",
                                             tipo_ag="curiosidades"),
            parse_mode="HTML",
        )
        return

    if action == "ag_c_priv":
        horarios_str = parts[1]
        await q.edit_message_text(
            f"⏰ Curiosidades em <code>{horarios_str.replace(',', ', ')}</code> — Publicar como:",
            reply_markup=_kb_privacidade(
                f"run|ag|ativar_c|{horarios_str}",
                f"ag_c_h|{horarios_str}",
            ),
            parse_mode="HTML",
        )
        return

    # ── agenda: celebridades ──────────────────────────────────────────────────
    if action == "ag_cel_hora":
        cfg = _ler_cfg()
        entry = cfg.get("celebridades", {})
        atual = entry.get("horarios", []) if entry.get("ativo") else []
        horarios_str = ",".join(atual)
        await q.edit_message_text(
            _ag_titulo("Celebridades", atual),
            reply_markup=_kb_picker_horarios("ag_cel_h", horarios_str, "nav|agenda", "ag_cel_priv",
                                             tipo_ag="celebridades"),
            parse_mode="HTML",
        )
        return

    if action == "ag_cel_h":
        horarios_str = parts[1] if len(parts) > 1 else ""
        atual = [h for h in horarios_str.split(",") if h]
        await q.edit_message_text(
            _ag_titulo("Celebridades", atual),
            reply_markup=_kb_picker_horarios("ag_cel_h", horarios_str, "nav|agenda", "ag_cel_priv",
                                             tipo_ag="celebridades"),
            parse_mode="HTML",
        )
        return

    if action == "ag_cel_priv":
        horarios_str = parts[1]
        await q.edit_message_text(
            f"⏰ Celebridades em <code>{horarios_str.replace(',', ', ')}</code> — Publicar como:",
            reply_markup=_kb_privacidade(
                f"run|ag|ativar_cel|{horarios_str}",
                f"ag_cel_h|{horarios_str}",
            ),
            parse_mode="HTML",
        )
        return

    # ── agenda: tecnologia ────────────────────────────────────────────────────
    if action == "ag_tec_hora":
        cfg = _ler_cfg()
        entry = cfg.get("tecnologia", {})
        atual = entry.get("horarios", []) if entry.get("ativo") else []
        horarios_str = ",".join(atual)
        await q.edit_message_text(
            _ag_titulo("Tecnologia", atual),
            reply_markup=_kb_picker_horarios("ag_tec_h", horarios_str, "nav|agenda", "ag_tec_priv",
                                             tipo_ag="tecnologia"),
            parse_mode="HTML",
        )
        return

    if action == "ag_tec_h":
        horarios_str = parts[1] if len(parts) > 1 else ""
        atual = [h for h in horarios_str.split(",") if h]
        await q.edit_message_text(
            _ag_titulo("Tecnologia", atual),
            reply_markup=_kb_picker_horarios("ag_tec_h", horarios_str, "nav|agenda", "ag_tec_priv",
                                             tipo_ag="tecnologia"),
            parse_mode="HTML",
        )
        return

    if action == "ag_tec_priv":
        horarios_str = parts[1]
        await q.edit_message_text(
            f"⏰ Tecnologia em <code>{horarios_str.replace(',', ', ')}</code> — Publicar como:",
            reply_markup=_kb_privacidade(
                f"run|ag|ativar_tec|{horarios_str}",
                f"ag_tec_h|{horarios_str}",
            ),
            parse_mode="HTML",
        )
        return

    # ── execução ──────────────────────────────────────────────────────────────
    if action == "run":
        await _handle_run(q, context, parts[1:])
        return

    # ── forçar execução após falha no preflight ──────────────────────────────
    if action == "force":
        await _handle_run(q, context, parts[1:], force=True)
        return


def _get_pipeline_info(parts: list):
    """Determina (pipeline_name, upload_flag) a partir dos parts do callback.
    Retorna None se o tipo não precisa de preflight."""
    tipo = parts[0]
    if tipo == "noticias" and len(parts) > 1:
        return ("noticias", parts[1] != "local")
    elif tipo == "curiosidades" and len(parts) > 1:
        return ("curiosidades", parts[1] != "local")
    elif tipo == "celebridades" and len(parts) > 1:
        return ("celebridades", parts[1] != "local")
    elif tipo == "sh" and len(parts) > 2:
        return (parts[1], parts[2] != "local")  # nicho, upload?
    elif tipo == "audio" and len(parts) > 3:
        return ("audio", parts[3] != "local")
    elif tipo == "shorts" and len(parts) > 2:
        return ("shorts", parts[2] != "local")
    return None


async def _handle_run(q, context, parts: list, force: bool = False) -> None:
    tipo    = parts[0]
    chat_id = q.message.chat_id

    # ── bloqueio de concorrência (evita acumular processos) ──────────────────
    if tipo in ("noticias", "curiosidades", "celebridades", "audio", "shorts", "sh") and _active_pipelines > 0:
        text = (
            f"⚠️ Já {'existe' if _active_pipelines == 1 else 'existem'} "
            f"<b>{_active_pipelines}</b> pipeline(s) em execução.\n\n"
            "Aguarde a conclusão antes de iniciar outro."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Voltar", callback_data="nav|main")],
        ])
        try:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
        return

    # -- tech digest --
    if tipo == "tech_digest":
        asyncio.create_task(_run_tech_digest(chat_id, context.bot, q.message))
        return

    # -- notícias --
    if tipo == "noticias":
        visib = parts[1]
        cmd = [PYTHON, str(BASE_DIR / "main.py")]
        descricao_extra = ""
        if visib == "priv":
            cmd.append("--privado")
            descricao_extra = "YouTube privado"
        elif visib == "local":
            cmd.append("--sem-upload")
            descricao_extra = "local (sem upload)"
        else:
            descricao_extra = "YouTube público"
        descricao = f"Notícias → {descricao_extra}"
        asyncio.create_task(_run_pipeline(chat_id, context.bot, cmd, descricao, q.message))
        return

    # -- shorts por nicho (novo menu) --
    # callback: run|sh|{nicho}|{visib}   visib: pub | priv | local
    if tipo == "sh":
        nicho, visib = parts[1], parts[2]
        _SCRIPTS = {
            "noticias":     "main.py",
            "curiosidades": "curiosidades.py",
            "celebridades": "celebridades.py",
            "tecnologia":   "tech_news.py",
        }
        _NOMES = {
            "noticias":     "Notícias",
            "curiosidades": "Curiosidades",
            "celebridades": "Celebridades",
            "tecnologia":   "Tecnologia",
        }
        script = _SCRIPTS.get(nicho)
        if not script:
            await q.edit_message_text(f"❌ Nicho desconhecido: {nicho}")
            return
        cmd = [PYTHON, str(BASE_DIR / script)]
        if visib == "priv":
            cmd.append("--privado")
        elif visib == "local":
            cmd.append("--sem-upload")
        visib_label = {"pub": "YouTube público", "priv": "YouTube privado", "local": "sem upload"}.get(visib, visib)
        descricao = f"{_NOMES.get(nicho, nicho)} → {visib_label}"
        asyncio.create_task(_run_pipeline(chat_id, context.bot, cmd, descricao, q.message))
        return

    # -- curiosidades --
    if tipo == "curiosidades":
        visib = parts[1]
        cmd = [PYTHON, str(BASE_DIR / "curiosidades.py")]
        if visib == "priv":
            cmd.append("--privado")
            descricao_extra = "YouTube privado"
        elif visib == "local":
            cmd.append("--sem-upload")
            descricao_extra = "local (sem upload)"
        else:
            descricao_extra = "YouTube público"
        descricao = f"Curiosidade → {descricao_extra}"
        asyncio.create_task(_run_pipeline(chat_id, context.bot, cmd, descricao, q.message))
        return

    # -- áudio longo --
    if tipo == "audio":
        audio_tipo, horas, visib = parts[1], parts[2], parts[3]
        tipos = list(SONS.keys()) if audio_tipo == "todos" else [audio_tipo]

        # tipo único → atualiza a mesma mensagem; "todos" → mensagem separada por tipo
        msg_unico = q.message if len(tipos) == 1 else None
        if msg_unico is None:
            await q.edit_message_text(f"⏳ {len(tipos)} pipeline(s) iniciado(s)…")

        for t in tipos:
            cmd = [PYTHON, str(BASE_DIR / "ambient_video.py"), t, "--horas", horas]
            if visib == "priv":
                cmd.append("--privado")
            elif visib == "local":
                cmd.append("--sem-upload")
            label     = SONS.get(t, t)
            descricao = f"Áudio {label} {horas}h → {_VISIB_LABEL[visib]}"
            asyncio.create_task(_run_pipeline(chat_id, context.bot, cmd, descricao, msg_unico))
            msg_unico = None  # só o primeiro tipo reutiliza a mensagem original
        return

    # -- shorts --
    if tipo == "shorts":
        fonte, visib, qtd = parts[1], parts[2], parts[3]
        cmd = [PYTHON, str(BASE_DIR / "shorts.py"), "--quantidade", qtd]
        if fonte == "exi":
            cmd.append("--de-existentes")
        if visib == "priv":
            cmd.append("--privado")
        elif visib == "local":
            cmd.append("--sem-upload")
        titulo    = "vídeos existentes" if fonte == "exi" else "notícias"
        descricao = f"{qtd} Short(s) de {titulo} → {_VISIB_LABEL[visib]}"
        asyncio.create_task(_run_pipeline(chat_id, context.bot, cmd, descricao, q.message))
        return

    # -- playlists --
    if tipo == "playlists":
        cmd = [PYTHON, "-c",
               "from playlists import organize_existing_videos; organize_existing_videos()"]
        asyncio.create_task(_run_pipeline(chat_id, context.bot, cmd, "Organizar playlists", q.message))
        return

    # -- agendamento --
    if tipo == "ag":
        subacao = parts[1]

        if subacao == "ativar_n":
            horarios_str, visib = parts[2], parts[3]
            horarios = [h for h in horarios_str.split(",") if h]
            privado = visib == "priv"
            cfg = _ler_cfg()
            try:
                _criar_agendamento("noticias", _cmd_noticias(privado), horarios)
                cfg["noticias"] = {"ativo": True, "horarios": horarios, "privado": privado}
                _salvar_cfg(cfg)
                priv_label = "privado" if privado else "público"
                await q.edit_message_text(
                    f"✅ Notícias agendadas para <b>{', '.join(horarios)}</b> diariamente ({priv_label}).",
                    reply_markup=kb_agenda(), parse_mode="HTML",
                )
            except Exception as e:
                await q.edit_message_text(f"❌ Erro ao agendar: {e}", reply_markup=kb_agenda())
            return

        if subacao == "ativar_c":
            horarios_str, visib = parts[2], parts[3]
            horarios = [h for h in horarios_str.split(",") if h]
            privado = visib == "priv"
            cfg = _ler_cfg()
            try:
                _criar_agendamento("curiosidades", _cmd_curiosidades(privado), horarios)
                cfg["curiosidades"] = {"ativo": True, "horarios": horarios, "privado": privado}
                _salvar_cfg(cfg)
                priv_label = "privado" if privado else "público"
                await q.edit_message_text(
                    f"✅ Curiosidades agendadas para <b>{', '.join(horarios)}</b> diariamente ({priv_label}).",
                    reply_markup=kb_agenda(), parse_mode="HTML",
                )
            except Exception as e:
                await q.edit_message_text(f"❌ Erro ao agendar: {e}", reply_markup=kb_agenda())
            return

        if subacao == "ativar_a":
            audio_tipo, horas, horario, visib = parts[2], parts[3], parts[4], parts[5]
            # Compat: aceita horario simples (str) ou lista separada por vírgula
            horarios = [h for h in horario.split(",") if h] if "," in horario else [horario]
            privado = visib == "priv"
            cfg = _ler_cfg()
            label = SONS.get(audio_tipo, audio_tipo.title())
            try:
                _criar_agendamento("audio", _cmd_audio(audio_tipo, float(horas), privado), horarios)
                cfg["audio"] = {"ativo": True, "horarios": horarios, "tipo": audio_tipo,
                                "horas": float(horas), "privado": privado}
                _salvar_cfg(cfg)
                priv_label = "privado" if privado else "público"
                await q.edit_message_text(
                    f"✅ {label} {horas}h agendado para <b>{', '.join(horarios)}</b> diariamente ({priv_label}).",
                    reply_markup=kb_agenda(), parse_mode="HTML",
                )
            except Exception as e:
                await q.edit_message_text(f"❌ Erro ao agendar: {e}", reply_markup=kb_agenda())
            return

        if subacao == "ativar_cel":
            horarios_str, visib = parts[2], parts[3]
            horarios = [h for h in horarios_str.split(",") if h]
            privado = visib == "priv"
            cfg = _ler_cfg()
            try:
                _criar_agendamento("celebridades", _cmd_celebridades(privado), horarios)
                cfg["celebridades"] = {"ativo": True, "horarios": horarios, "privado": privado}
                _salvar_cfg(cfg)
                priv_label = "privado" if privado else "público"
                await q.edit_message_text(
                    f"✅ Celebridades agendadas para <b>{', '.join(horarios)}</b> diariamente ({priv_label}).",
                    reply_markup=kb_agenda(), parse_mode="HTML",
                )
            except Exception as e:
                await q.edit_message_text(f"❌ Erro ao agendar: {e}", reply_markup=kb_agenda())
            return

        if subacao == "ativar_tec":
            horarios_str, visib = parts[2], parts[3]
            horarios = [h for h in horarios_str.split(",") if h]
            privado = visib == "priv"
            cfg = _ler_cfg()
            try:
                _criar_agendamento("tecnologia", _cmd_tecnologia(privado), horarios)
                cfg["tecnologia"] = {"ativo": True, "horarios": horarios, "privado": privado}
                _salvar_cfg(cfg)
                priv_label = "privado" if privado else "público"
                await q.edit_message_text(
                    f"✅ Tecnologia agendada para <b>{', '.join(horarios)}</b> diariamente ({priv_label}).",
                    reply_markup=kb_agenda(), parse_mode="HTML",
                )
            except Exception as e:
                await q.edit_message_text(f"❌ Erro ao agendar: {e}", reply_markup=kb_agenda())
            return

        if subacao == "des":
            tipo_ag = parts[2]
            cfg = _ler_cfg()
            try:
                _remover_agendamento(tipo_ag)
                # setdefault: se a chave não existir (ex.: usuário desativa sem nunca ter agendado),
                # cria entrada vazia em vez de KeyError
                cfg.setdefault(tipo_ag, {})["ativo"] = False
                _salvar_cfg(cfg)
                nomes = {
                    "noticias": "Notícias", "audio": "Áudio Longo",
                    "curiosidades": "Curiosidades", "celebridades": "Celebridades",
                    "tecnologia": "Tecnologia",
                }
                nome = nomes.get(tipo_ag, tipo_ag)
                await q.edit_message_text(
                    f"✅ Agendamento de {nome} removido.",
                    reply_markup=kb_agenda(),
                )
            except Exception as e:
                await q.edit_message_text(f"❌ Erro: {e}", reply_markup=kb_agenda())
            return


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        print("Erro: TELEGRAM_BOT_TOKEN não definido no .env")
        sys.exit(1)
    if not CHAT_ID:
        print("Erro: TELEGRAM_CHAT_ID não definido no .env")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "menu"], cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(_on_error)

    print(f"Bot iniciado. Aguardando comandos de chat_id={CHAT_ID}…")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
