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
CRON_NOTICIAS     = "YOUTUBER:noticias"
CRON_AUDIO        = "YOUTUBER:audio"
CRON_CURIOSIDADES = "YOUTUBER:curiosidades"
LOG_DIR           = BASE_DIR / "logs"

# Mapeamentos pra acelerar lookups
_TASKS_BY_TIPO = {
    "noticias":     TASK_NOTICIAS,
    "audio":        TASK_AUDIO,
    "curiosidades": TASK_CURIOSIDADES,
}
_TAGS_BY_TIPO = {
    "noticias":     CRON_NOTICIAS,
    "audio":        CRON_AUDIO,
    "curiosidades": CRON_CURIOSIDADES,
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
            _tg_last_edit = loop.time()
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


def _cmd_noticias(privado: bool, plataforma: str = "ambos") -> str:
    """plataforma: 'ambos' | 'youtube' | 'tiktok'"""
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{BASE_DIR / "main.py"}"'
    if plataforma == "youtube":
        cmd += " --apenas-youtube"
    elif plataforma == "tiktok":
        cmd += " --apenas-tiktok"
    if privado:
        cmd += " --privado"
    return cmd


def _cmd_audio(tipo: str, horas: float, privado: bool) -> str:
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{BASE_DIR / "ambient_video.py"}" {tipo} --horas {horas}'
    return cmd + " --privado" if privado else cmd


def _cmd_curiosidades(privado: bool, plataforma: str = "ambos") -> str:
    """plataforma: 'ambos' | 'youtube' | 'tiktok'"""
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{BASE_DIR / "curiosidades.py"}"'
    if plataforma == "youtube":
        cmd += " --apenas-youtube"
    elif plataforma == "tiktok":
        cmd += " --apenas-tiktok"
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
        [InlineKeyboardButton("📰 Notícias",            callback_data="nav|noticias")],
        [InlineKeyboardButton("🎵 Áudio Longo",          callback_data="nav|audio")],
        [InlineKeyboardButton("📱 Shorts",               callback_data="nav|shorts")],
        [InlineKeyboardButton("⏰ Agendamento",           callback_data="nav|agenda")],
        [InlineKeyboardButton("📂 Organizar Playlists",  callback_data="run|playlists")],
        [InlineKeyboardButton("📸 Status Instagram",     callback_data="nav|instagram")],
        [InlineKeyboardButton("🎵 Status TikTok",        callback_data="nav|tiktok")],
        [InlineKeyboardButton("🔬 Tech Digest",          callback_data="run|tech_digest")],
        [InlineKeyboardButton("💻 Tech Shorts",          callback_data="nav|tech_news")],
        [InlineKeyboardButton("🧠 Curiosidades",         callback_data="nav|curiosidades")],
        [InlineKeyboardButton("📊 Status Gemini",        callback_data="run|gemini_check")],
    ])


def kb_noticias() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ YouTube + TikTok (público)",   callback_data="run|noticias|pub_ambos")],
        [InlineKeyboardButton("📺 Apenas YouTube (público)",     callback_data="run|noticias|pub_yt")],
        [InlineKeyboardButton("🎵 Apenas TikTok",                callback_data="run|noticias|pub_tk")],
        [InlineKeyboardButton("🔒 YouTube privado (+TikTok)",    callback_data="run|noticias|priv_ambos")],
        [InlineKeyboardButton("💾 Só gerar (sem upload)",        callback_data="run|noticias|local")],
        [InlineKeyboardButton("⬅️ Voltar",                       callback_data="nav|main")],
    ])


def kb_tech_news() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Gerar Tech Shorts (público)", callback_data="run|tech_news|pub")],
        [InlineKeyboardButton("💾 Só gerar (sem upload)",       callback_data="run|tech_news|local")],
        [InlineKeyboardButton("🔒 Publicar como privado",       callback_data="run|tech_news|priv")],
        [InlineKeyboardButton("⬅️ Voltar",                      callback_data="nav|main")],
    ])


def kb_curiosidades() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ YouTube + TikTok (público)",   callback_data="run|curiosidades|pub_ambos")],
        [InlineKeyboardButton("📺 Apenas YouTube (público)",     callback_data="run|curiosidades|pub_yt")],
        [InlineKeyboardButton("🎵 Apenas TikTok",                callback_data="run|curiosidades|pub_tk")],
        [InlineKeyboardButton("🔒 YouTube privado (+TikTok)",    callback_data="run|curiosidades|priv_ambos")],
        [InlineKeyboardButton("💾 Só gerar (sem upload)",        callback_data="run|curiosidades|local")],
        [InlineKeyboardButton("⬅️ Voltar",                       callback_data="nav|main")],
    ])


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
    n = cfg.get("noticias",     _default)
    a = cfg.get("audio",        _default)
    c = cfg.get("curiosidades", _default)
    sn, sa, sc = _status_label(n), _status_label(a), _status_label(c)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📰 Notícias — {sn}",     callback_data="ag_n_hora")],
        [InlineKeyboardButton(f"🎵 Áudio Longo — {sa}",  callback_data="ag_a_tipo")],
        [InlineKeyboardButton(f"🧠 Curiosidades — {sc}", callback_data="ag_c_hora")],
        [InlineKeyboardButton("🗑️ Desativar Notícias",     callback_data="run|ag|des|noticias")],
        [InlineKeyboardButton("🗑️ Desativar Áudio Longo",  callback_data="run|ag|des|audio")],
        [InlineKeyboardButton("🗑️ Desativar Curiosidades", callback_data="run|ag|des|curiosidades")],
        [InlineKeyboardButton("⬅️ Voltar",                 callback_data="nav|main")],
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


async def _run_gemini_check(chat_id: int, bot, msg) -> None:
    """Verifica status da cota Gemini fazendo uma chamada teste."""
    try:
        await msg.edit_text(
            "📊 <b>Status Gemini</b>\n\n⏳ Verificando cota...\n"
            "<i>(uma chamada teste será feita — gasta 1 da cota diária)</i>",
            parse_mode="HTML",
        )
    except Exception:
        msg = await bot.send_message(
            chat_id, "📊 <b>Status Gemini</b>\n\n⏳ Verificando...", parse_mode="HTML",
        )

    try:
        from notebooklm_session import check_gemini_quota
        result = await asyncio.to_thread(check_gemini_quota, False)
    except Exception as e:
        await _safe_edit(msg, f"📊 <b>Status Gemini</b>\n\n⚠️ Erro ao verificar: {html_escape(str(e))}")
        return

    status = result.get("status", "error")
    msg_txt = result.get("message", "")

    if status == "ok":
        text = (
            f"📊 <b>Status Gemini</b>\n\n"
            f"🟢 <b>Cota disponível</b>\n\n"
            f"{html_escape(msg_txt)}\n\n"
            f"<i>Plano free: 20 reqs/dia, 5 reqs/min</i>"
        )
    elif status == "per_day":
        text = (
            f"📊 <b>Status Gemini</b>\n\n"
            f"🔴 <b>Cota DIÁRIA esgotada</b>\n\n"
            f"{html_escape(msg_txt)}\n\n"
            f"<b>Impacto agora:</b>\n"
            f"• Notícias: fallback automático pra Groq (se configurado)\n"
            f"• Curiosidades: vai falhar\n"
            f"• Shorts standalone: vai falhar\n\n"
            f"<i>Aguarde reset à meia-noite PT (~4h da manhã BR).</i>"
        )
    elif status == "per_minute":
        retry = result.get("retry_in") or 60
        text = (
            f"📊 <b>Status Gemini</b>\n\n"
            f"🟡 <b>Rate limit por minuto</b>\n\n"
            f"{html_escape(msg_txt)}\n\n"
            f"Aguarde ~{retry}s e tente novamente."
        )
    elif status == "no_key":
        text = (
            f"📊 <b>Status Gemini</b>\n\n"
            f"❌ <b>Sem API key</b>\n\n"
            f"Adicione <code>GEMINI_API_KEY=...</code> no <code>.env</code>"
        )
    else:
        text = (
            f"📊 <b>Status Gemini</b>\n\n"
            f"⚠️ <b>Erro</b>\n\n{html_escape(msg_txt)}"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Verificar novamente", callback_data="run|gemini_check")],
        [InlineKeyboardButton("⬅️ Voltar",              callback_data="nav|main")],
    ])
    await _safe_edit(msg, text, reply_markup=kb)


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
    await update.message.reply_text(
        "🤖 <b>Youtuber no Automático</b>\nEscolha uma opção:",
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
        dest = parts[1]
        if dest == "main":
            await q.edit_message_text(
                "🤖 <b>Youtuber no Automático</b>\nEscolha uma opção:",
                reply_markup=kb_main(), parse_mode="HTML",
            )
        elif dest == "noticias":
            await q.edit_message_text(
                "📰 <b>Notícias</b>\n\n"
                "<b>Pipeline gera 4 Shorts (1 por categoria):</b>\n"
                "• 🏛️ Política\n"
                "• 🎬 Entretenimento\n"
                "• 💰 Mercado Financeiro\n"
                "• 👮 Policial\n\n"
                "Cada Short ~3 min com CTA + Pexels.\n"
                "<i>Escolha a plataforma:</i>",
                reply_markup=kb_noticias(), parse_mode="HTML",
            )
        elif dest == "audio":
            await q.edit_message_text(
                "🎵 <b>Áudio Longo</b> — Tipo de som:",
                reply_markup=kb_audio_tipo_run(), parse_mode="HTML",
            )
        elif dest == "shorts":
            await q.edit_message_text("📱 <b>Shorts</b>", reply_markup=kb_shorts(), parse_mode="HTML")
        elif dest == "tech_news":
            await q.edit_message_text(
                "💻 <b>Tech Shorts</b>\n\n"
                "<b>Pipeline gera APENAS Shorts:</b>\n"
                "• Google News busca top tópicos em 10 sites tech\n"
                "• Groq resume (fallback Gemini)\n"
                "• 1 Short vertical por tópico\n"
                "• Upload: YouTube + TikTok\n"
                "• <i>Sem vídeo longo</i>",
                reply_markup=kb_tech_news(), parse_mode="HTML",
            )
        elif dest == "curiosidades":
            await q.edit_message_text(
                "🧠 <b>Curiosidades</b>\n\n"
                "<b>Pipeline gera 1 Short:</b>\n"
                "• Groq/Gemini sorteia tema novo (ciência, história, espaço…)\n"
                "• Escreve curiosidade densa (~2m30s) + CTA\n"
                "• <b>Escolha a plataforma:</b> YouTube, TikTok ou ambos\n"
                "• <i>Histórico evita repetir tema</i>",
                reply_markup=kb_curiosidades(), parse_mode="HTML",
            )
        elif dest == "agenda":
            await q.edit_message_text("⏰ <b>Agendamento</b>", reply_markup=kb_agenda(), parse_mode="HTML")
        elif dest == "instagram":
            from instagram_uploader import INSTAGRAM_ENABLED
            if INSTAGRAM_ENABLED:
                status = "✅ <b>ATIVO</b> — credenciais configuradas"
                detail = "\nPosts automáticos:\n• Shorts → Reel\n• Notícias → Thumbnail no feed"
            else:
                status = "❌ <b>INATIVO</b>"
                detail = "\nAdicione no .env:\n<code>INSTAGRAM_USERNAME=\nINSTAGRAM_PASSWORD=</code>"
            await q.edit_message_text(
                f"📸 Instagram\n\n{status}{detail}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Voltar", callback_data="nav|main")],
                ]),
                parse_mode="HTML",
            )
        elif dest == "tiktok":
            from tiktok_publisher import TIKTOK_ENABLED
            if TIKTOK_ENABLED:
                status = "✅ <b>ATIVO</b> — cookies configurados"
                detail = "\nPosts automáticos:\n• Shorts → Vídeo no TikTok"
            else:
                status = "❌ <b>INATIVO</b>"
                detail = (
                    "\nPara ativar:"
                    "\n1. Login no TikTok pelo browser"
                    "\n2. Exporte cookies (extensão Get cookies.txt LOCALLY)"
                    "\n3. Salve em <code>credentials/tiktok_cookies.json</code>"
                )
            await q.edit_message_text(
                f"🎵 TikTok\n\n{status}{detail}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Voltar", callback_data="nav|main")],
                ]),
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

    # ── agenda: plataforma de curiosidades ────────────────────────────────────
    if action == "ag_c_priv":
        horarios_str = parts[1]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📺 + 🎵 YouTube + TikTok", callback_data=f"ag_c_priv2|{horarios_str}|ambos")],
            [InlineKeyboardButton("📺 Apenas YouTube",        callback_data=f"ag_c_priv2|{horarios_str}|youtube")],
            [InlineKeyboardButton("🎵 Apenas TikTok",         callback_data=f"ag_c_priv2|{horarios_str}|tiktok")],
            [InlineKeyboardButton("⬅️ Voltar",                callback_data=f"ag_c_h|{horarios_str}")],
        ])
        await q.edit_message_text(
            f"⏰ Curiosidades em <code>{horarios_str.replace(',', ', ')}</code> — Plataforma:",
            reply_markup=kb, parse_mode="HTML",
        )
        return

    # ── agenda: privacidade de curiosidades (passo 2 — só relevante pro YT) ───
    if action == "ag_c_priv2":
        horarios_str, plataforma = parts[1], parts[2]
        if plataforma == "tiktok":
            # TikTok não tem público/privado — vai direto
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Confirmar", callback_data=f"run|ag|ativar_c|{horarios_str}|{plataforma}|pub")],
                [InlineKeyboardButton("⬅️ Voltar",   callback_data=f"ag_c_priv|{horarios_str}")],
            ])
            await q.edit_message_text(
                f"⏰ Curiosidades em <code>{horarios_str.replace(',', ', ')}</code> — só TikTok:",
                reply_markup=kb, parse_mode="HTML",
            )
            return
        # Plataforma usa YouTube → escolhe público/privado
        # callback final: run|ag|ativar_c|<horarios>|<plataforma>|<visib>
        await q.edit_message_text(
            f"⏰ Curiosidades em <code>{horarios_str.replace(',', ', ')}</code> ({plataforma}) — YouTube como:",
            reply_markup=_kb_privacidade(
                f"run|ag|ativar_c|{horarios_str}|{plataforma}",
                f"ag_c_priv|{horarios_str}",
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
    elif tipo == "tech_news" and len(parts) > 1:
        return ("tech_news", parts[1] != "local")
    elif tipo == "curiosidades" and len(parts) > 1:
        return ("curiosidades", parts[1] != "local")
    elif tipo == "audio" and len(parts) > 3:
        return ("audio", parts[3] != "local")
    elif tipo == "shorts" and len(parts) > 2:
        return ("shorts", parts[2] != "local")
    return None


async def _handle_run(q, context, parts: list, force: bool = False) -> None:
    tipo    = parts[0]
    chat_id = q.message.chat_id

    # ── bloqueio de concorrência (evita acumular processos) ──────────────────
    if tipo in ("noticias", "tech_news", "curiosidades", "audio", "shorts") and _active_pipelines > 0:
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

    # ── verificação pré-pipeline ─────────────────────────────────────────────
    if not force:
        info = _get_pipeline_info(parts)
        if info:
            try:
                from notebooklm_session import preflight_check
                result = await asyncio.to_thread(preflight_check, info[0], info[1], False)
                if result["critical"]:
                    text = "❌ <b>Verificação pré-pipeline</b>\n\n"
                    for c in result["critical"]:
                        text += f"• {html_escape(c)}\n"
                    if result["warnings"]:
                        text += f"\n⚠️ <b>Avisos:</b>\n"
                        for w in result["warnings"]:
                            text += f"• {html_escape(w)}\n"

                    force_data = "force|" + "|".join(parts)
                    back_map = {
                        "noticias": "nav|noticias", "tech_news": "nav|tech_news",
                        "curiosidades": "nav|curiosidades",
                        "audio": "nav|audio", "shorts": "nav|shorts",
                    }
                    back = back_map.get(tipo, "nav|main")

                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("▶️ Iniciar mesmo assim", callback_data=force_data)],
                        [InlineKeyboardButton("⬅️ Voltar", callback_data=back)],
                    ])
                    try:
                        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
                    except Exception:
                        await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
                    return
            except ImportError:
                pass
            except Exception:
                pass  # erro no preflight não deve bloquear o pipeline

    # -- status Gemini (cota) --
    if tipo == "gemini_check":
        asyncio.create_task(_run_gemini_check(chat_id, context.bot, q.message))
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
        if visib == "pub_ambos":
            descricao_extra = "YouTube + TikTok (público)"
        elif visib == "pub_yt":
            cmd.append("--apenas-youtube")
            descricao_extra = "apenas YouTube (público)"
        elif visib == "pub_tk":
            cmd.append("--apenas-tiktok")
            descricao_extra = "apenas TikTok"
        elif visib == "priv_ambos":
            cmd.append("--privado")
            descricao_extra = "YouTube privado + TikTok"
        elif visib == "local":
            cmd.append("--sem-upload")
            descricao_extra = "local (sem upload)"
        # Legacy compat
        elif visib == "priv":
            cmd.append("--privado")
            descricao_extra = "YouTube privado"
        elif visib == "pub":
            descricao_extra = "YouTube + TikTok"
        descricao = f"Notícias → {descricao_extra}"
        asyncio.create_task(_run_pipeline(chat_id, context.bot, cmd, descricao, q.message))
        return

    # -- tech news (Shorts via Google News + Groq) --
    if tipo == "tech_news":
        visib = parts[1]
        cmd   = [PYTHON, str(BASE_DIR / "tech_news.py")]
        if visib == "priv":
            cmd.append("--privado")
        elif visib == "local":
            cmd.append("--sem-upload")
        descricao = f"Tech News → YouTube {_VISIB_LABEL[visib]}"
        asyncio.create_task(_run_pipeline(chat_id, context.bot, cmd, descricao, q.message))
        return

    # -- curiosidades --
    if tipo == "curiosidades":
        visib = parts[1]
        cmd = [PYTHON, str(BASE_DIR / "curiosidades.py")]
        descricao_extra = ""
        if visib == "pub_ambos":
            descricao_extra = "YouTube + TikTok (público)"
        elif visib == "pub_yt":
            cmd.append("--apenas-youtube")
            descricao_extra = "apenas YouTube (público)"
        elif visib == "pub_tk":
            cmd.append("--apenas-tiktok")
            descricao_extra = "apenas TikTok"
        elif visib == "priv_ambos":
            cmd.append("--privado")
            descricao_extra = "YouTube privado + TikTok"
        elif visib == "local":
            cmd.append("--sem-upload")
            descricao_extra = "local (sem upload)"
        # Legacy compat
        elif visib == "priv":
            cmd.append("--privado")
            descricao_extra = "YouTube privado"
        elif visib == "pub":
            descricao_extra = "YouTube + TikTok"
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
            # callback: run|ag|ativar_c|<horarios>|<plataforma>|<visib>
            # ou compat: run|ag|ativar_c|<horarios>|<visib> (sem plataforma → ambos)
            if len(parts) >= 5:
                horarios_str, plataforma, visib = parts[2], parts[3], parts[4]
            else:
                horarios_str, visib = parts[2], parts[3]
                plataforma = "ambos"
            horarios = [h for h in horarios_str.split(",") if h]
            privado = visib == "priv"
            cfg = _ler_cfg()
            try:
                _criar_agendamento(
                    "curiosidades",
                    _cmd_curiosidades(privado, plataforma),
                    horarios,
                )
                cfg["curiosidades"] = {
                    "ativo": True, "horarios": horarios,
                    "privado": privado, "plataforma": plataforma,
                }
                _salvar_cfg(cfg)
                labels = {"ambos": "YouTube + TikTok", "youtube": "apenas YouTube", "tiktok": "apenas TikTok"}
                plat_label = labels.get(plataforma, plataforma)
                priv_label = " — privado" if privado and plataforma != "tiktok" else ""
                await q.edit_message_text(
                    f"✅ Curiosidades agendadas para <b>{', '.join(horarios)}</b> "
                    f"({plat_label}{priv_label}).",
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

        if subacao == "des":
            tipo_ag = parts[2]
            cfg = _ler_cfg()
            try:
                _remover_agendamento(tipo_ag)
                # setdefault: se a chave não existir (ex.: usuário desativa sem nunca ter agendado),
                # cria entrada vazia em vez de KeyError
                cfg.setdefault(tipo_ag, {})["ativo"] = False
                _salvar_cfg(cfg)
                nomes = {"noticias": "Notícias", "audio": "Áudio Longo", "curiosidades": "Curiosidades"}
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
