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
TASK_FOLDER   = "YoutuberAutomatico"
TASK_NOTICIAS = f"{TASK_FOLDER}\\Noticias"
TASK_AUDIO    = f"{TASK_FOLDER}\\AudioLongo"

# Linux cron — tags únicas para identificar linhas gerenciadas
CRON_NOTICIAS = "YOUTUBER:noticias"
CRON_AUDIO    = "YOUTUBER:audio"
LOG_DIR       = os.path.join(BASE_DIR, "logs")

SCHEDULER_CFG = os.path.join(BASE_DIR, "scheduler.json")

_CFG_PADRAO = {
    "noticias": {"ativo": False, "horario": "06:00", "privado": False},
    "audio":    {"ativo": False, "horario": "08:00", "tipo": "rain", "horas": 8, "privado": False},
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


def _win_criar_tarefa(task_name, comando, horario):
    subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/TR", comando,
         "/SC", "DAILY", "/ST", horario, "/F"],
        check=True, capture_output=True,
    )


def _win_remover_tarefa(task_name):
    if _win_tarefa_existe(task_name):
        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True)


# -- Linux cron ----------------------------------------------------------------

def _lin_cron_ler():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _lin_cron_escrever(texto):
    subprocess.run(["crontab", "-"], input=texto, text=True, check=True)


def _lin_tarefa_existe(tag):
    return f"# {tag}" in _lin_cron_ler()


def _lin_criar_tarefa(tag, comando, horario):
    os.makedirs(LOG_DIR, exist_ok=True)
    nome_log = tag.split(":")[-1]
    log_path = os.path.join(LOG_DIR, f"{nome_log}.log")
    hh, mm = horario.split(":")
    nova = f"{mm} {hh} * * * {comando} >> {log_path} 2>&1  # {tag}\n"
    crontab = _lin_cron_ler()
    linhas = [l for l in crontab.splitlines(keepends=True) if f"# {tag}" not in l]
    linhas.append(nova)
    _lin_cron_escrever("".join(linhas))


def _lin_remover_tarefa(tag):
    crontab = _lin_cron_ler()
    linhas = [l for l in crontab.splitlines(keepends=True) if f"# {tag}" not in l]
    _lin_cron_escrever("".join(linhas))


# -- wrappers cross-platform ---------------------------------------------------

def _tarefa_existe_n():
    return _win_tarefa_existe(TASK_NOTICIAS) if IS_WINDOWS else _lin_tarefa_existe(CRON_NOTICIAS)


def _tarefa_existe_a():
    return _win_tarefa_existe(TASK_AUDIO) if IS_WINDOWS else _lin_tarefa_existe(CRON_AUDIO)


def _criar_agendamento(tipo, comando, horario):
    if IS_WINDOWS:
        task = TASK_NOTICIAS if tipo == "noticias" else TASK_AUDIO
        _win_criar_tarefa(task, comando, horario)
    else:
        tag = CRON_NOTICIAS if tipo == "noticias" else CRON_AUDIO
        _lin_criar_tarefa(tag, comando, horario)


def _remover_agendamento(tipo):
    if IS_WINDOWS:
        _win_remover_tarefa(TASK_NOTICIAS if tipo == "noticias" else TASK_AUDIO)
    else:
        _lin_remover_tarefa(CRON_NOTICIAS if tipo == "noticias" else CRON_AUDIO)


# -- validação e comandos ------------------------------------------------------

def _validar_horario(h):
    return bool(re.match(r"^\d{2}:\d{2}$", h))


def _cmd_noticias(privado):
    script = os.path.join(BASE_DIR, "main.py")
    cmd = f'"{PYTHON}" "{script}"'
    return cmd + " --privado" if privado else cmd


def _cmd_audio(tipo, horas, privado):
    script = os.path.join(BASE_DIR, "ambient_video.py")
    cmd = f'"{PYTHON}" "{script}" {tipo} --horas {horas}'
    return cmd + " --privado" if privado else cmd


# -- configuração interativa ---------------------------------------------------

def _configurar_noticias(cfg):
    cabecalho("-- AGENDAR NOTÍCIAS --")
    n = cfg["noticias"]

    horario = input(f"  Horário diário (HH:MM) [padrão {n['horario']}]: ").strip() or n["horario"]
    if not _validar_horario(horario):
        input("  Formato inválido. Use HH:MM. [Enter]")
        return

    privado = perguntar_privacidade()

    try:
        _criar_agendamento("noticias", _cmd_noticias(privado), horario)
        cfg["noticias"] = {"ativo": True, "horario": horario, "privado": privado}
        _salvar_cfg(cfg)
        print(f"\n  Notícias agendadas para {horario} todos os dias.")
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

    horario = input(f"  Horário diário (HH:MM) [padrão {a.get('horario', '08:00')}]: ").strip() or a.get("horario", "08:00")
    if not _validar_horario(horario):
        input("  Formato inválido. Use HH:MM. [Enter]")
        return

    privado = perguntar_privacidade()

    try:
        _criar_agendamento("audio", _cmd_audio(tipo, horas, privado), horario)
        cfg["audio"] = {"ativo": True, "horario": horario, "tipo": tipo, "horas": horas, "privado": privado}
        _salvar_cfg(cfg)
        label = next(lb for t, lb in SONS.values() if t == tipo)
        print(f"\n  {label} ({horas}h) agendado para {horario} todos os dias.")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        print(f"\n  Erro ao criar agendamento: {stderr or e}")
    aguardar()


def menu_agendamento():
    while True:
        cfg = _ler_cfg()
        n, a = cfg["noticias"], cfg["audio"]
        n_real = _tarefa_existe_n()
        a_real = _tarefa_existe_a()

        cabecalho("-- AGENDAMENTO --")
        plataforma = "Windows Task Scheduler" if IS_WINDOWS else "cron Linux"
        print(f"  Plataforma: {plataforma}")
        print()

        if n["ativo"] and n_real:
            priv_n = "privado" if n["privado"] else "público"
            status_n = f"ATIVO  — {n['horario']} diário ({priv_n})"
        elif n["ativo"] and not n_real:
            status_n = "DESCONFIGURADO (use opção 1 para recriar)"
        else:
            status_n = "inativo"

        if a["ativo"] and a_real:
            priv_a = "privado" if a["privado"] else "público"
            status_a = f"ATIVO  — {a['horario']} diário  |  {a['tipo']} {a['horas']}h ({priv_a})"
        elif a["ativo"] and not a_real:
            status_a = "DESCONFIGURADO (use opção 2 para recriar)"
        else:
            status_a = "inativo"

        print(f"  Notícias:    {status_n}")
        print(f"  Áudio Longo: {status_a}")
        print()
        print("  1.  Configurar notícias diárias")
        print("  2.  Configurar áudio longo diário")
        print("  3.  Desativar notícias")
        print("  4.  Desativar áudio longo")
        print()
        print("  0.  Voltar")
        print()
        op = input("  Escolha: ").strip()

        if op == "1":
            _configurar_noticias(cfg)
        elif op == "2":
            _configurar_audio(cfg)
        elif op == "3":
            _remover_agendamento("noticias")
            cfg["noticias"]["ativo"] = False
            _salvar_cfg(cfg)
            print("\n  Agendamento de notícias removido.")
            aguardar()
        elif op == "4":
            _remover_agendamento("audio")
            cfg["audio"]["ativo"] = False
            _salvar_cfg(cfg)
            print("\n  Agendamento de áudio longo removido.")
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


def rodar(cmd, descricao):
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
        print("  1.  Executar pipeline completo")
        print("  2.  Executar sem upload (só gera vídeo local)")
        print("  3.  Executar e publicar como privado")
        print()
        print("  0.  Voltar")
        print()
        op = input("  Escolha: ").strip()

        if op == "1":
            rodar([PYTHON, "main.py"], "Pipeline de notícias → YouTube público")
        elif op == "2":
            rodar([PYTHON, "main.py", "--sem-upload"], "Pipeline de notícias (sem upload)")
        elif op == "3":
            rodar([PYTHON, "main.py", "--privado"], "Pipeline de notícias → YouTube privado")
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
        rodar(cmd, resumo)


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
            )

        else:
            input("  Opção inválida. [Enter]")


# -- menu principal ------------------------------------------------------------

def main():
    while True:
        cabecalho()
        print("  1.  Postar Notícias")
        print("  2.  Postar Áudio Longo")
        print("  3.  Shorts")
        print("  4.  Agendamento")
        print("  5.  Organizar vídeos em playlists")
        print("  6.  Status do Instagram")
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
            menu_agendamento()
        elif op == "5":
            cabecalho("-- ORGANIZAR PLAYLISTS --")
            print("  Isso vai listar todos os vídeos do canal e")
            print("  adicioná-los às playlists correspondentes.\n")
            confirmar = input("  Continuar? (S/n): ").strip().lower()
            if confirmar != "n":
                print()
                from playlists import organize_existing_videos
                organize_existing_videos()
            aguardar()
        elif op == "6":
            cabecalho("-- INSTAGRAM --")
            from instagram_uploader import INSTAGRAM_ENABLED
            if INSTAGRAM_ENABLED:
                print("  Status: ATIVO")
                print("  Credenciais configuradas no .env")
                print()
                print("  O Instagram é acionado automaticamente ao publicar:")
                print("    - Shorts -> Reel no Instagram")
                print("    - Notícias -> Thumbnail como post no feed")
            else:
                print("  Status: INATIVO")
                print()
                print("  Para ativar, adicione no .env:")
                print("    INSTAGRAM_USERNAME=seu_usuario")
                print("    INSTAGRAM_PASSWORD=sua_senha")
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
