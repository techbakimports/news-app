"""
Menu interativo do Youtuber no Automatico.
Uso: python menu.py
"""
import os
import sys
import subprocess

# Forçar UTF-8 no terminal Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PYTHON = sys.executable

SONS = {
    "1": ("rain",       "Chuva"),
    "2": ("ocean",      "Ondas do Mar"),
    "3": ("fire",       "Lareira"),
    "4": ("forest",     "Floresta"),
    "5": ("whitenoise", "Ruído Branco"),
    "6": ("brownnoise", "Ruído Marrom"),
    "7": ("todos",      "Todos os tipos"),
}


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


# -- menu principal ------------------------------------------------------------

def main():
    while True:
        cabecalho()
        print("  1.  Postar Notícias")
        print("  2.  Postar Áudio Longo")
        print()
        print("  0.  Sair")
        print()
        op = input("  Escolha: ").strip()

        if op == "1":
            menu_noticias()
        elif op == "2":
            menu_audio_longo()
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
