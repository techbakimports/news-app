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

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

IS_WINDOWS    = os.name == "nt"
TASK_FOLDER   = "YoutuberAutomatico"
TASK_NOTICIAS = f"{TASK_FOLDER}\\Noticias"
TASK_AUDIO    = f"{TASK_FOLDER}\\AudioLongo"
CRON_NOTICIAS = "YOUTUBER:noticias"
CRON_AUDIO    = "YOUTUBER:audio"
LOG_DIR       = BASE_DIR / "logs"
SCHEDULER_CFG = BASE_DIR / "scheduler.json"

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
    cmd = f'"{PYTHON}" "{BASE_DIR / "main.py"}"'
    return cmd + " --privado" if privado else cmd


def _cmd_audio(tipo: str, horas: float, privado: bool) -> str:
    cmd = f'"{PYTHON}" "{BASE_DIR / "ambient_video.py"}" {tipo} --horas {horas}'
    return cmd + " --privado" if privado else cmd


def _criar_agendamento(tipo: str, comando: str, horario: str) -> None:
    if IS_WINDOWS:
        task = TASK_NOTICIAS if tipo == "noticias" else TASK_AUDIO
        subprocess.run(
            ["schtasks", "/Create", "/TN", task, "/TR", comando,
             "/SC", "DAILY", "/ST", horario, "/F"],
            check=True, capture_output=True,
        )
    else:
        tag = CRON_NOTICIAS if tipo == "noticias" else CRON_AUDIO
        LOG_DIR.mkdir(exist_ok=True)
        log_path = LOG_DIR / f"{tag.split(':')[-1]}.log"
        hh, mm = horario.split(":")
        nova = f"{mm} {hh} * * * {comando} >> {log_path} 2>&1  # {tag}\n"
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        crontab = r.stdout if r.returncode == 0 else ""
        linhas = [l for l in crontab.splitlines(keepends=True) if f"# {tag}" not in l]
        linhas.append(nova)
        subprocess.run(["crontab", "-"], input="".join(linhas), text=True, check=True)


def _remover_agendamento(tipo: str) -> None:
    if IS_WINDOWS:
        task = TASK_NOTICIAS if tipo == "noticias" else TASK_AUDIO
        subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"], capture_output=True)
    else:
        tag = CRON_NOTICIAS if tipo == "noticias" else CRON_AUDIO
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
    ])


def kb_noticias() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Pipeline completo",      callback_data="run|noticias|pub")],
        [InlineKeyboardButton("💾 Só gerar (sem upload)",  callback_data="run|noticias|local")],
        [InlineKeyboardButton("🔒 Publicar como privado",  callback_data="run|noticias|priv")],
        [InlineKeyboardButton("⬅️ Voltar",                 callback_data="nav|main")],
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


def kb_agenda() -> InlineKeyboardMarkup:
    cfg = _ler_cfg()
    n, a = cfg["noticias"], cfg["audio"]
    sn = f"✅ {n['horario']}" if n["ativo"] else "❌ inativo"
    sa = f"✅ {a['horario']}" if a["ativo"] else "❌ inativo"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📰 Notícias — {sn}",     callback_data="ag_n_hora")],
        [InlineKeyboardButton(f"🎵 Áudio Longo — {sa}",  callback_data="ag_a_tipo")],
        [InlineKeyboardButton("🗑️ Desativar Notícias",    callback_data="run|ag|des|noticias")],
        [InlineKeyboardButton("🗑️ Desativar Áudio Longo", callback_data="run|ag|des|audio")],
        [InlineKeyboardButton("⬅️ Voltar",                callback_data="nav|main")],
    ])


def _kb_horarios(prefixo: str, back: str) -> InlineKeyboardMarkup:
    horarios = ["05:00", "06:00", "07:00", "08:00", "09:00", "10:00"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(h, callback_data=f"{prefixo}|{h}") for h in horarios[:3]],
        [InlineKeyboardButton(h, callback_data=f"{prefixo}|{h}") for h in horarios[3:]],
        [InlineKeyboardButton("⬅️ Voltar", callback_data=back)],
    ])


def _kb_privacidade(prefixo: str, back: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Público",  callback_data=f"{prefixo}|pub")],
        [InlineKeyboardButton("🔒 Privado",  callback_data=f"{prefixo}|priv")],
        [InlineKeyboardButton("⬅️ Voltar",  callback_data=back)],
    ])


# ── execução de pipelines ─────────────────────────────────────────────────────

async def _run_pipeline(chat_id: int, bot, cmd: list, descricao: str, msg=None) -> None:
    if msg is None:
        msg = await bot.send_message(chat_id, f"⏳ <b>{descricao}</b>", parse_mode="HTML")
    else:
        try:
            await msg.edit_text(f"⏳ <b>{descricao}</b>", parse_mode="HTML")
        except Exception:
            msg = await bot.send_message(chat_id, f"⏳ <b>{descricao}</b>", parse_mode="HTML")

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
        try:
            await msg.edit_text(text, parse_mode="HTML")
        except Exception:
            pass

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    texto = f"❌ Erro interno desconhecido"
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
                await asyncio.sleep(5.0)
                await _editar()

        reader  = asyncio.create_task(_ler())
        updater = asyncio.create_task(_atualizar())
        await reader
        updater.cancel()

        await proc.wait()
        tail = "\n".join(lines[-20:])[:3800]

        if proc.returncode == 0:
            texto = f"✅ <b>{descricao}</b> concluído!\n\n<code>{tail}</code>"
        else:
            texto = f"❌ <b>{descricao}</b> falhou (código {proc.returncode})\n\n<code>{tail}</code>"

    except Exception as exc:
        texto = f"❌ Erro interno: {exc}"

    await _editar(texto)


# ── handlers ──────────────────────────────────────────────────────────────────

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
            await q.edit_message_text("📰 <b>Notícias</b>", reply_markup=kb_noticias(), parse_mode="HTML")
        elif dest == "audio":
            await q.edit_message_text(
                "🎵 <b>Áudio Longo</b> — Tipo de som:",
                reply_markup=kb_audio_tipo_run(), parse_mode="HTML",
            )
        elif dest == "shorts":
            await q.edit_message_text("📱 <b>Shorts</b>", reply_markup=kb_shorts(), parse_mode="HTML")
        elif dest == "agenda":
            await q.edit_message_text("⏰ <b>Agendamento</b>", reply_markup=kb_agenda(), parse_mode="HTML")
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

    # ── agenda: horário diário de notícias ────────────────────────────────────
    if action == "ag_n_hora":
        await q.edit_message_text(
            "⏰ <b>Notícias</b> — Horário diário:",
            reply_markup=_kb_horarios("ag_n_priv", "nav|agenda"),
            parse_mode="HTML",
        )
        return

    # ── agenda: privacidade de notícias ───────────────────────────────────────
    if action == "ag_n_priv":
        horario = parts[1]
        await q.edit_message_text(
            f"⏰ Notícias às {horario} — Publicar como:",
            reply_markup=_kb_privacidade(f"run|ag|ativar_n|{horario}", "ag_n_hora"),
            parse_mode="HTML",
        )
        return

    # ── execução ──────────────────────────────────────────────────────────────
    if action == "run":
        await _handle_run(q, context, parts[1:])
        return


async def _handle_run(q, context, parts: list) -> None:
    tipo    = parts[0]
    chat_id = q.message.chat_id

    # -- notícias --
    if tipo == "noticias":
        visib = parts[1]
        cmd   = [PYTHON, str(BASE_DIR / "main.py")]
        if visib == "priv":
            cmd.append("--privado")
        elif visib == "local":
            cmd.append("--sem-upload")
        descricao = f"Notícias → YouTube {_VISIB_LABEL[visib]}"
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
            horario, visib = parts[2], parts[3]
            privado = visib == "priv"
            cfg     = _ler_cfg()
            try:
                _criar_agendamento("noticias", _cmd_noticias(privado), horario)
                cfg["noticias"] = {"ativo": True, "horario": horario, "privado": privado}
                _salvar_cfg(cfg)
                priv_label = "privado" if privado else "público"
                await q.edit_message_text(
                    f"✅ Notícias agendadas para <b>{horario}</b> diariamente ({priv_label}).",
                    reply_markup=kb_agenda(), parse_mode="HTML",
                )
            except Exception as e:
                await q.edit_message_text(f"❌ Erro ao agendar: {e}", reply_markup=kb_agenda())
            return

        if subacao == "ativar_a":
            audio_tipo, horas, horario, visib = parts[2], parts[3], parts[4], parts[5]
            privado = visib == "priv"
            cfg     = _ler_cfg()
            label   = SONS.get(audio_tipo, audio_tipo.title())
            try:
                _criar_agendamento("audio", _cmd_audio(audio_tipo, float(horas), privado), horario)
                cfg["audio"] = {"ativo": True, "horario": horario, "tipo": audio_tipo,
                                "horas": float(horas), "privado": privado}
                _salvar_cfg(cfg)
                priv_label = "privado" if privado else "público"
                await q.edit_message_text(
                    f"✅ {label} {horas}h agendado para <b>{horario}</b> diariamente ({priv_label}).",
                    reply_markup=kb_agenda(), parse_mode="HTML",
                )
            except Exception as e:
                await q.edit_message_text(f"❌ Erro ao agendar: {e}", reply_markup=kb_agenda())
            return

        if subacao == "des":
            tipo_ag = parts[2]
            cfg     = _ler_cfg()
            try:
                _remover_agendamento(tipo_ag)
                cfg[tipo_ag]["ativo"] = False
                _salvar_cfg(cfg)
                nome = "Notícias" if tipo_ag == "noticias" else "Áudio Longo"
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

    print(f"Bot iniciado. Aguardando comandos de chat_id={CHAT_ID}…")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
