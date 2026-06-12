"""
Menu interativo do Youtuber no Automatico.
Uso: python menu.py
"""
import json
import os
import re
import sys
import subprocess

# Forçar UTF-8 no terminal Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PYTHON   = sys.executable
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

IS_WINDOWS = os.name == "nt"

# Windows Task Scheduler
TASK_FOLDER       = "YoutuberAutomatico"
TASK_NOTICIAS     = f"{TASK_FOLDER}\\Noticias"
TASK_AUDIO        = f"{TASK_FOLDER}\\AudioLongo"
TASK_CURIOSIDADES = f"{TASK_FOLDER}\\Curiosidades"

# Linux cron — tags únicas para identificar linhas gerenciadas
CRON_NOTICIAS     = "YOUTUBER:noticias"
CRON_AUDIO        = "YOUTUBER:audio"
CRON_CURIOSIDADES = "YOUTUBER:curiosidades"
LOG_DIR           = os.path.join(BASE_DIR, "logs")

SCHEDULER_CFG = os.path.join(BASE_DIR, "scheduler.json")

# Multi-horário: cada pipeline pode ter VÁRIOS horários no mesmo dia.
_CFG_PADRAO = {
    "noticias":     {"ativo": False, "horarios": ["06:00"], "privado": False},
    "audio":        {"ativo": False, "horarios": ["08:00"], "tipo": "rain", "horas": 8, "privado": False},
    "curiosidades": {"ativo": False, "horarios": ["10:00"], "privado": False},
}

SONS = {
    "1": ("rain",       "Chuva"),
    "2": ("ocean",      "Ondas do Mar"),
    "3": ("fire",       "Lareira"),
    "4": ("forest",     "Floresta"),
    "5": ("whitenoise", "Ruído Branco"),
    "6": ("brownnoise", "Ruído Marrom"),
    "7": ("todos",      "Todos os tipos"),
}


# -- agendamento (cross-platform: Task Scheduler no Windows, cron no Linux) ----

def _ler_cfg():
    if os.path.exists(SCHEDULER_CFG):
        try:
            with open(SCHEDULER_CFG, encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in _CFG_PADRAO.items():
                cfg.setdefault(k, dict(v))
            # Backward compat: converte horario (str) → horarios (list)
            for k, entry in cfg.items():
                if "horario" in entry and "horarios" not in entry:
                    entry["horarios"] = [entry.pop("horario")]
                elif "horarios" not in entry:
                    entry["horarios"] = _CFG_PADRAO.get(k, {}).get("horarios", ["06:00"])
            return cfg
        except Exception:
            pass
    return {k: dict(v) for k, v in _CFG_PADRAO.items()}


def _salvar_cfg(cfg):
    with open(SCHEDULER_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# -- Windows Task Scheduler ----------------------------------------------------

def _win_tarefa_existe(task_name):
    r = subprocess.run(["schtasks", "/Query", "/TN", task_name], capture_output=True)
    return r.returncode == 0


def _win_criar_tarefa_unica(task_name, comando, horario):
    """Cria UMA task no Task Scheduler (uso interno)."""
    subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/TR", comando,
         "/SC", "DAILY", "/ST", horario, "/F"],
        check=True, capture_output=True,
    )


def _win_remover_tarefa_unica(task_name):
    if _win_tarefa_existe(task_name):
        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True)


def _win_criar_tarefa(task_prefix, comando, horarios):
    """Cria UMA task por horário, com sufixo no nome. Ex: Noticias_06_00, Noticias_12_00"""
    _win_remover_tarefa(task_prefix)  # limpa as antigas primeiro
    for horario in horarios:
        sufixo = horario.replace(":", "_")
        task_name = f"{task_prefix}_{sufixo}"
        _win_criar_tarefa_unica(task_name, comando, horario)


def _win_remover_tarefa(task_prefix):
    """Remove TODAS as tasks que começam com task_prefix_."""
    # Lista todas no folder
    r = subprocess.run(
        ["schtasks", "/Query", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return
    folder_prefix = "\\" + task_prefix  # ex: \YoutuberAutomatico\Noticias
    for line in r.stdout.splitlines():
        # CSV: "TaskName","NextRunTime","Status"
        parts = [p.strip('"') for p in line.split('","')]
        if not parts:
            continue
        tn = parts[0].lstrip('"')
        if tn.startswith(folder_prefix):
            _win_remover_tarefa_unica(tn)


# -- Linux cron ----------------------------------------------------------------

def _lin_cron_ler():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _lin_cron_escrever(texto):
    subprocess.run(["crontab", "-"], input=texto, text=True, check=True)


def _lin_tarefa_existe(tag):
    return f"# {tag}" in _lin_cron_ler()


def _lin_criar_tarefa(tag, comando, horarios):
    """Cria múltiplas linhas no crontab — uma por horário — todas com o mesmo tag."""
    os.makedirs(LOG_DIR, exist_ok=True)
    nome_log = tag.split(":")[-1]
    log_path = os.path.join(LOG_DIR, f"{nome_log}.log")
    crontab = _lin_cron_ler()
    # Remove TODAS as linhas existentes desse tag antes de recriar
    linhas = [l for l in crontab.splitlines(keepends=True) if f"# {tag}" not in l]
    for horario in horarios:
        hh, mm = horario.split(":")
        linhas.append(f"{mm} {hh} * * * {comando} >> {log_path} 2>&1  # {tag}\n")
    _lin_cron_escrever("".join(linhas))


def _lin_remover_tarefa(tag):
    crontab = _lin_cron_ler()
    linhas = [l for l in crontab.splitlines(keepends=True) if f"# {tag}" not in l]
    _lin_cron_escrever("".join(linhas))


# -- wrappers cross-platform ---------------------------------------------------

_TASKS = {
    "noticias":     TASK_NOTICIAS,
    "audio":        TASK_AUDIO,
    "curiosidades": TASK_CURIOSIDADES,
}
_TAGS = {
    "noticias":     CRON_NOTICIAS,
    "audio":        CRON_AUDIO,
    "curiosidades": CRON_CURIOSIDADES,
}


def _tarefa_existe(tipo):
    if IS_WINDOWS:
        # No Windows precisa verificar se existe ALGUMA task com o prefixo
        prefix = _TASKS[tipo]
        r = subprocess.run(
            ["schtasks", "/Query", "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return False
        folder_prefix = "\\" + prefix
        return any(folder_prefix in line for line in r.stdout.splitlines())
    return _lin_tarefa_existe(_TAGS[tipo])


def _tarefa_existe_n():
    return _tarefa_existe("noticias")


def _tarefa_existe_a():
    return _tarefa_existe("audio")


def _tarefa_existe_c():
    return _tarefa_existe("curiosidades")


def _criar_agendamento(tipo, comando, horarios):
    """horarios pode ser uma string única (compat) ou uma lista."""
    if isinstance(horarios, str):
        horarios = [horarios]
    if IS_WINDOWS:
        _win_criar_tarefa(_TASKS[tipo], comando, horarios)
    else:
        _lin_criar_tarefa(_TAGS[tipo], comando, horarios)


def _remover_agendamento(tipo):
    if IS_WINDOWS:
        _win_remover_tarefa(_TASKS[tipo])
    else:
        _lin_remover_tarefa(_TAGS[tipo])


# -- validação e comandos ------------------------------------------------------

def _validar_horario(h):
    return bool(re.match(r"^\d{2}:\d{2}$", h))


def _parse_horarios(texto: str) -> list[str] | None:
    """Aceita 'HH:MM' ou 'HH:MM,HH:MM,...'. Retorna lista ou None se invalido."""
    horarios = [h.strip() for h in texto.replace(";", ",").split(",") if h.strip()]
    if not horarios:
        return None
    for h in horarios:
        if not _validar_horario(h):
            return None
    # Remove duplicatas mantendo ordem
    visto = []
    for h in horarios:
        if h not in visto:
            visto.append(h)
    return visto


def _cmd_noticias(privado, plataforma="youtube"):
    script = os.path.join(BASE_DIR, "main.py")
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{script}"'
    if privado:
        cmd += " --privado"
    return cmd


def _cmd_audio(tipo, horas, privado):
    script = os.path.join(BASE_DIR, "ambient_video.py")
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{script}" {tipo} --horas {horas}'
    return cmd + " --privado" if privado else cmd


def _cmd_curiosidades(privado, plataforma="youtube"):
    script = os.path.join(BASE_DIR, "curiosidades.py")
    cd = f"cd {BASE_DIR} &&" if not IS_WINDOWS else ""
    cmd = f'{cd} "{PYTHON}" "{script}"'
    if privado:
        cmd += " --privado"
    return cmd


# -- configuração interativa ---------------------------------------------------

def _configurar_noticias(cfg):
    cabecalho("-- AGENDAR NOTÍCIAS --")
    n = cfg["noticias"]
    atuais = ",".join(n.get("horarios", ["06:00"]))

    print("  Aceita um ou vários horários separados por vírgula.")
    print("  Ex: 06:00,12:00,18:00")
    txt = input(f"  Horários [padrão {atuais}]: ").strip() or atuais
    horarios = _parse_horarios(txt)
    if not horarios:
        input("  Formato inválido. Use HH:MM,HH:MM,... [Enter]")
        return

    privado = perguntar_privacidade()

    try:
        _criar_agendamento("noticias", _cmd_noticias(privado), horarios)
        cfg["noticias"] = {"ativo": True, "horarios": horarios, "privado": privado}
        _salvar_cfg(cfg)
        print(f"\n  Notícias agendadas para: {', '.join(horarios)} todos os dias.")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        print(f"\n  Erro ao criar agendamento: {stderr or e}")
    aguardar()


def _perguntar_plataforma():
    return "youtube"


def _configurar_curiosidades(cfg):
    cabecalho("-- AGENDAR CURIOSIDADES --")
    c = cfg["curiosidades"]
    atuais = ",".join(c.get("horarios", ["10:00"]))

    print("  Aceita um ou vários horários separados por vírgula.")
    print("  Ex: 10:00,14:00,20:00")
    txt = input(f"  Horários [padrão {atuais}]: ").strip() or atuais
    horarios = _parse_horarios(txt)
    if not horarios:
        input("  Formato inválido. Use HH:MM,HH:MM,... [Enter]")
        return

    plataforma = _perguntar_plataforma()
    privado = perguntar_privacidade()

    try:
        _criar_agendamento("curiosidades", _cmd_curiosidades(privado, plataforma), horarios)
        cfg["curiosidades"] = {"ativo": True, "horarios": horarios, "privado": privado, "plataforma": plataforma}
        _salvar_cfg(cfg)
        labels = {"youtube": "apenas YouTube", "ambos": "apenas YouTube"}
        print(f"\n  Curiosidades agendadas para: {', '.join(horarios)} ({labels[plataforma]}).")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        print(f"\n  Erro ao criar agendamento: {stderr or e}")
    aguardar()


def _configurar_audio(cfg):
    cabecalho("-- AGENDAR ÁUDIO LONGO --")
    a = cfg["audio"]

    print("  Tipo de som:")
    for k, (tipo, label) in SONS.items():
        if tipo != "todos":
            print(f"    {k}. {label}")
    op = input(f"  Escolha (padrão {a.get('tipo', 'rain')}): ").strip()
    tipo = SONS[op][0] if (op in SONS and SONS[op][0] != "todos") else a.get("tipo", "rain")

    horas_str = input(f"  Duração em horas (padrão {a.get('horas', 8)}): ").strip() or str(a.get("horas", 8))
    try:
        horas = float(horas_str)
        if horas <= 0:
            raise ValueError
    except ValueError:
        input("  Valor inválido. [Enter]")
        return

    atuais = ",".join(a.get("horarios", ["08:00"]))
    print("  Aceita um ou vários horários separados por vírgula.")
    txt = input(f"  Horários [padrão {atuais}]: ").strip() or atuais
    horarios = _parse_horarios(txt)
    if not horarios:
        input("  Formato inválido. Use HH:MM,HH:MM,... [Enter]")
        return

    privado = perguntar_privacidade()

    try:
        _criar_agendamento("audio", _cmd_audio(tipo, horas, privado), horarios)
        cfg["audio"] = {"ativo": True, "horarios": horarios, "tipo": tipo, "horas": horas, "privado": privado}
        _salvar_cfg(cfg)
        label = next(lb for t, lb in SONS.values() if t == tipo)
        print(f"\n  {label} ({horas}h) agendado para: {', '.join(horarios)} todos os dias.")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        print(f"\n  Erro ao criar agendamento: {stderr or e}")
    aguardar()


def _status_linha(entry, ativo_real, label_extra=""):
    if entry["ativo"] and ativo_real:
        priv = "privado" if entry.get("privado") else "público"
        hs = ", ".join(entry.get("horarios", []))
        return f"ATIVO — {hs} ({priv}){label_extra}"
    if entry["ativo"] and not ativo_real:
        return "DESCONFIGURADO (use opção pra recriar)"
    return "inativo"


def menu_agendamento():
    while True:
        cfg = _ler_cfg()
        n, a, c = cfg["noticias"], cfg["audio"], cfg["curiosidades"]
        n_real = _tarefa_existe_n()
        a_real = _tarefa_existe_a()
        c_real = _tarefa_existe_c()

        cabecalho("-- AGENDAMENTO --")
        plataforma = "Windows Task Scheduler" if IS_WINDOWS else "cron Linux"
        print(f"  Plataforma: {plataforma}")
        print()

        status_n = _status_linha(n, n_real)
        audio_extra = f" | {a.get('tipo', '?')} {a.get('horas', '?')}h" if n["ativo"] else ""
        status_a = _status_linha(a, a_real, audio_extra)
        status_c = _status_linha(c, c_real)

        print(f"  Notícias:      {status_n}")
        print(f"  Áudio Longo:   {status_a}")
        print(f"  Curiosidades:  {status_c}")
        print()
        print("  1.  Configurar notícias")
        print("  2.  Configurar áudio longo")
        print("  3.  Configurar curiosidades")
        print("  4.  Desativar notícias")
        print("  5.  Desativar áudio longo")
        print("  6.  Desativar curiosidades")
        print()
        print("  0.  Voltar")
        print()
        op = input("  Escolha: ").strip()

        if op == "1":
            _configurar_noticias(cfg)
        elif op == "2":
            _configurar_audio(cfg)
        elif op == "3":
            _configurar_curiosidades(cfg)
        elif op == "4":
            _remover_agendamento("noticias")
            cfg["noticias"]["ativo"] = False
            _salvar_cfg(cfg)
            print("\n  Agendamento de notícias removido.")
            aguardar()
        elif op == "5":
            _remover_agendamento("audio")
            cfg["audio"]["ativo"] = False
            _salvar_cfg(cfg)
            print("\n  Agendamento de áudio longo removido.")
            aguardar()
        elif op == "6":
            _remover_agendamento("curiosidades")
            cfg["curiosidades"]["ativo"] = False
            _salvar_cfg(cfg)
            print("\n  Agendamento de curiosidades removido.")
            aguardar()
        elif op == "0":
            return
        else:
            input("  Opção inválida. [Enter]")


# -- utilidades ----------------------------------------------------------------

def cls():
    os.system("cls" if os.name == "nt" else "clear")


def cabecalho(subtitulo=""):
    cls()
    print("=" * 45)
    print("   YOUTUBER NO AUTOMATICO")
    if subtitulo:
        print(f"   {subtitulo}")
    print("=" * 45)
    print()


def aguardar():
    print()
    input("  Pressione Enter para continuar...")


def perguntar_privacidade():
    print("  Privacidade:")
    print("    1. Público")
    print("    2. Privado")
    op = input("  Escolha (padrão 1): ").strip() or "1"
    return op == "2"


def perguntar_upload():
    op = input("  Fazer upload no YouTube? (S/n): ").strip().lower()
    return op != "n"


def rodar(cmd, descricao, pipeline=None, upload=True):
        except Exception as e:
            print(f"\n  Aviso: erro na verificação pré-pipeline: {e}")
            print("  Continuando mesmo assim...\n")

    print(f"\n  Iniciando: {descricao}")
    print("-" * 45)
    result = subprocess.run(cmd)
    print("-" * 45)
    if result.returncode == 0:
        print(f"  Concluído com sucesso.")
    else:
        print(f"  Finalizado com erros (código {result.returncode}).")
    aguardar()


# -- submenus ------------------------------------------------------------------

def menu_noticias():
    while True:
        cabecalho("-- NOTÍCIAS --")
        print("  Pipeline: 4 Shorts (1 por categoria)")
        print("    • Política, Entretenimento, Mercado Financeiro, Policial")
        print("    • Cada Short ~3 min com CTA")
        print("    • Sem vídeo longo")
        print()
        print("  1.  YouTube (público)")
        print("  2.  YouTube privado")
        print("  3.  Só gerar (sem upload)")
        print()
        print("  0.  Voltar")
        print()
        op = input("  Escolha: ").strip()

        if op == "1":
            rodar([PYTHON, "main.py"], "Notícias → YouTube público", pipeline="noticias")
        elif op == "2":
            rodar([PYTHON, "main.py", "--privado"], "Notícias → YouTube privado", pipeline="noticias")
        elif op == "3":
            rodar([PYTHON, "main.py", "--sem-upload"], "Notícias (sem upload)", pipeline="noticias", upload=False)
        elif op == "0":
            return
        else:
            input("  Opção inválida. [Enter]")


def menu_audio_longo():
    while True:
        cabecalho("-- ÁUDIO LONGO --")
        for k, (_, label) in SONS.items():
            print(f"  {k}.  {label}")
        print()
        print("  0.  Voltar")
        print()
        op = input("  Escolha o tipo de som: ").strip()

        if op == "0":
            return

        if op not in SONS:
            input("  Opção inválida. [Enter]")
            continue

        tipo, label = SONS[op]
        cabecalho(f"-- {label.upper()} --")

        # Duração
        horas_str = input("  Duração em horas (padrão 8): ").strip() or "8"
        try:
            horas = float(horas_str)
            if horas <= 0:
                raise ValueError
        except ValueError:
            input("  Valor inválido. [Enter]")
            continue

        # Upload
        upload = perguntar_upload()

        cmd = [PYTHON, "ambient_video.py", tipo, "--horas", str(horas)]

        if upload:
            privado = perguntar_privacidade()
            if privado:
                cmd.append("--privado")
        else:
            cmd.append("--sem-upload")

        resumo = f"{label} — {horas}h"
        resumo += " → YouTube " + ("privado" if "--privado" in cmd else "público") if upload else " (local)"
        rodar(cmd, resumo, pipeline="audio", upload=upload)


# -- submenu Shorts ------------------------------------------------------------

def menu_shorts():
    while True:
        cabecalho("-- SHORTS --")
        print("  1.  Gerar Shorts das notícias de hoje")
        print("  2.  Gerar Shorts de vídeos já postados")
        print("  3.  Gerar Shorts sem upload (só local)")
        print("  4.  Gerar Shorts de vídeos existentes (privado)")
        print()
        print("  0.  Voltar")
        print()
        op = input("  Escolha: ").strip()

        if op == "0":
            return

        if op == "1":
            n_str = input("  Quantos Shorts? (padrão 3): ").strip() or "3"
            try:
                n = max(1, int(n_str))
            except ValueError:
                input("  Valor inválido. [Enter]")
                continue
            rodar(
                [PYTHON, "shorts.py", "--quantidade", str(n)],
                f"{n} Short(s) das notícias de hoje → YouTube público",
                pipeline="shorts",
            )

        elif op == "2":
            n_str = input("  Quantos vídeos usar? (padrão 5): ").strip() or "5"
            try:
                n = max(1, int(n_str))
            except ValueError:
                input("  Valor inválido. [Enter]")
                continue
            rodar(
                [PYTHON, "shorts.py", "--de-existentes", "--quantidade", str(n)],
                f"{n} Short(s) de vídeos já postados → YouTube público",
                pipeline="shorts",
            )

        elif op == "3":
            n_str = input("  Quantos Shorts? (padrão 3): ").strip() or "3"
            try:
                n = max(1, int(n_str))
            except ValueError:
                input("  Valor inválido. [Enter]")
                continue
            rodar(
                [PYTHON, "shorts.py", "--quantidade", str(n), "--sem-upload"],
                f"{n} Short(s) das notícias (sem upload)",
                pipeline="shorts", upload=False,
            )

        elif op == "4":
            n_str = input("  Quantos vídeos usar? (padrão 5): ").strip() or "5"
            try:
                n = max(1, int(n_str))
            except ValueError:
                input("  Valor inválido. [Enter]")
                continue
            rodar(
                [PYTHON, "shorts.py", "--de-existentes", "--quantidade", str(n), "--privado"],
                f"{n} Short(s) de vídeos existentes → YouTube privado",
                pipeline="shorts",
            )

        else:
            input("  Opção inválida. [Enter]")


# -- menu principal ------------------------------------------------------------

def menu_curiosidades():
    while True:
        cabecalho("-- CURIOSIDADES --")
        print("  Pipeline: Groq/Gemini gera curiosidade aleatoria -> Short")
        print("  (1 Short por execucao, tema sempre novo, ~2m30s + CTA)")
        print()
        print("  1.  YouTube (publico)")
        print("  2.  YouTube privado")
        print("  3.  So gerar (sem upload)")
        print()
        print("  0.  Voltar")
        print()
        op = input("  Escolha: ").strip()

        if op == "1":
            rodar([PYTHON, "curiosidades.py"], "Curiosidade -> YouTube publico", pipeline="curiosidades")
        elif op == "2":
            rodar([PYTHON, "curiosidades.py", "--privado"], "Curiosidade -> YouTube privado", pipeline="curiosidades")
        elif op == "3":
            rodar([PYTHON, "curiosidades.py", "--sem-upload"], "Curiosidade (sem upload)", pipeline="curiosidades", upload=False)
        elif op == "0":
            return
        else:
            input("  Opcao invalida. [Enter]")


def menu_tech_news():
    while True:
        cabecalho("-- TECH SHORTS --")
        print("  Pipeline: Google News (10 sites tech) -> Groq/Gemini -> N Shorts -> YouTube")
        print("  (SEM video longo — apenas Shorts verticais)")
        print()
        print("  1.  Executar pipeline (publica como publico)")
        print("  2.  Executar sem upload (so gera os Shorts localmente)")
        print("  3.  Executar e publicar como privado")
        print()
        print("  0.  Voltar")
        print()
        op = input("  Escolha: ").strip()

        if op == "1":
            rodar([PYTHON, "tech_news.py"], "Tech Shorts -> YouTube publico", pipeline="tech_news")
        elif op == "2":
            rodar([PYTHON, "tech_news.py", "--sem-upload"], "Tech Shorts (sem upload)", pipeline="tech_news", upload=False)
        elif op == "3":
            rodar([PYTHON, "tech_news.py", "--privado"], "Tech Shorts -> YouTube privado", pipeline="tech_news")
        elif op == "0":
            return
        else:
            input("  Opcao invalida. [Enter]")


def menu_celebridades():
    while True:
        cabecalho("-- CELEBRIDADES --")
        print("  Pipeline: Google News (portais de fofoca BR) -> Groq/Gemini -> Shorts")
        print("  Voz: Thalita (pt-BR) | Cor: Rosa pink | CTA de entretenimento")
        print()
        print("  1.  Executar (publico, ate 3 Shorts)")
        print("  2.  Executar sem upload (so gera localmente)")
        print("  3.  Executar como privado")
        print("  4.  Executar (1 Short apenas)")
        print()
        print("  0.  Voltar")
        print()
        op = input("  Escolha: ").strip()

        if op == "1":
            rodar([PYTHON, "celebridades.py"],
                  "Celebridades -> YouTube publico", pipeline="celebridades")
        elif op == "2":
            rodar([PYTHON, "celebridades.py", "--sem-upload"],
                  "Celebridades (sem upload)", pipeline="celebridades", upload=False)
        elif op == "3":
            rodar([PYTHON, "celebridades.py", "--privado"],
                  "Celebridades -> YouTube privado", pipeline="celebridades")
        elif op == "4":
            rodar([PYTHON, "celebridades.py", "--max", "1"],
                  "Celebridades -> 1 Short publico", pipeline="celebridades")
        elif op == "0":
            return
        else:
            input("  Opcao invalida. [Enter]")


def main():
    while True:
        cabecalho()
        print("  1.  Postar Noticias")
        print("  2.  Postar Audio Longo")
        print("  3.  Shorts")
        print("  4.  Tech Shorts (Google News -> Shorts)")
        print("  5.  Curiosidades (Gemini -> 1 Short)")
        print("  6.  Celebridades (Fofoca BR -> Shorts)")
        print("  7.  Agendamento")
        print("  8.  Organizar videos em playlists")
        print("  9.  Status das Redes Sociais")
        print()
        print("  0.  Sair")
        print()
        op = input("  Escolha: ").strip()

        if op == "1":
            menu_noticias()
        elif op == "2":
            menu_audio_longo()
        elif op == "3":
            menu_shorts()
        elif op == "4":
            menu_tech_news()
        elif op == "5":
            menu_curiosidades()
        elif op == "6":
            menu_celebridades()
        elif op == "7":
            menu_agendamento()
        elif op == "8":
            cabecalho("-- ORGANIZAR PLAYLISTS --")
            print("  Isso vai listar todos os videos do canal e")
            print("  adiciona-los as playlists correspondentes.\n")
            confirmar = input("  Continuar? (S/n): ").strip().lower()
            if confirmar != "n":
                print()
                from playlists import organize_existing_videos
                organize_existing_videos()
            aguardar()
        elif op == "9":
            cabecalho("-- REDES SOCIAIS --")

            # Instagram
            from instagram_uploader import INSTAGRAM_ENABLED
            print("  INSTAGRAM")
            if INSTAGRAM_ENABLED:
                print("    Status: ATIVO")
                print("    Credenciais configuradas no .env")
            else:
                print("    Status: INATIVO")
                print("    Para ativar, adicione no .env:")
                print("      INSTAGRAM_USERNAME=seu_usuario")
                print("      INSTAGRAM_PASSWORD=sua_senha")

            print()
            print("  Ao publicar Shorts, o upload vai para YouTube + Instagram (Reel).")
            aguardar()
        elif op == "0":
            cls()
            print("  Até logo!")
            sys.exit(0)
        else:
            input("  Opção inválida. [Enter]")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        cls()
        print("\n  Até logo!")
        sys.exit(0)
