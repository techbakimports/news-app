"""
Gera vídeos longos de sons ambiente para YouTube (chuva, mar, lareira, etc.).
Fluxo: síntese de áudio → vídeo Pexels → ffmpeg loop → upload YouTube.
"""
import argparse
import os
import re
import sys
import subprocess
import time
import random
import requests
from datetime import datetime
from dotenv import load_dotenv
from ambient_generator import generate_ambient_audio
from video import _search_pexels  # fallback de fotos

load_dotenv()

AMBIENT_OUTPUT_DIR = "./ambient_videos"
LOOP_AUDIO_SECONDS = 180   # 3 min de áudio base (loopado pelo ffmpeg)
VIDEO_W, VIDEO_H = 1280, 720

SOUND_CONFIG = {
    "rain": {
        "label":        "Chuva Relaxante",
        "pexels_query": "rain falling window",
        "yt_title":     "Chuva Relaxante — {hours}h para Dormir e Focar 🌧️",
        "yt_tags":      ["chuva", "rain sounds", "sleep sounds", "relaxante", "para dormir", "focar"],
        "yt_desc":      "Sons de chuva suave para ajudar você a dormir, estudar ou relaxar.",
    },
    "ocean": {
        "label":        "Ondas do Mar",
        "pexels_query": "ocean waves beach",
        "yt_title":     "Ondas do Mar — {hours}h para Dormir e Meditar 🌊",
        "yt_tags":      ["ondas", "mar", "praia", "sleep sounds", "meditação", "relaxante"],
        "yt_desc":      "Sons suaves de ondas do mar para meditação, sono e relaxamento.",
    },
    "fire": {
        "label":        "Lareira Aconchegante",
        "pexels_query": "fireplace fire burning cozy",
        "yt_title":     "Lareira Aconchegante — {hours}h para Relaxar e Focar 🔥",
        "yt_tags":      ["lareira", "fogo", "sleep sounds", "aconchegante", "focar", "relaxante"],
        "yt_desc":      "Sons de lareira crepitante para criar uma atmosfera aconchegante.",
    },
    "forest": {
        "label":        "Floresta e Natureza",
        "pexels_query": "forest nature wind trees",
        "yt_title":     "Sons da Floresta — {hours}h de Natureza para Dormir 🌿",
        "yt_tags":      ["floresta", "natureza", "vento", "sleep sounds", "pássaros", "relaxante"],
        "yt_desc":      "Sons da floresta — vento entre as árvores para relaxar e dormir.",
    },
    "whitenoise": {
        "label":        "Ruído Branco",
        "pexels_query": "minimalist abstract light calm",
        "yt_title":     "Ruído Branco — {hours}h para Concentração e Sono ⬜",
        "yt_tags":      ["ruído branco", "white noise", "concentração", "sono", "foco"],
        "yt_desc":      "Ruído branco contínuo para bloquear distrações e dormir melhor.",
    },
    "brownnoise": {
        "label":        "Ruído Marrom",
        "pexels_query": "dark calm abstract nature texture",
        "yt_title":     "Ruído Marrom — {hours}h para Sono Profundo 🟫",
        "yt_tags":      ["ruído marrom", "brown noise", "sono profundo", "relaxante", "meditação"],
        "yt_desc":      "Ruído marrom (brown noise) — frequências graves para sono profundo.",
    },
}


def _baixar_video_pexels(query: str, dest_path: str) -> bool:
    """Baixa um clipe de vídeo real do Pexels (720p ou melhor)."""
    api_key = (os.getenv("PEXELS_API_KEY") or "").strip()
    if not api_key:
        return False
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 8, "orientation": "landscape"},
            timeout=15,
        )
        if r.status_code != 200:
            return False
        videos = r.json().get("videos", [])
        if not videos:
            return False

        video = random.choice(videos[:4])
        # Prefere HD (1280×720) — evita 4K que ocupa muito espaço
        files = sorted(
            video.get("video_files", []),
            key=lambda f: (f.get("width", 0) >= 1280, -(abs(f.get("width", 0) - 1280))),
            reverse=True,
        )
        chosen = files[0]
        print(f"  Baixando vídeo Pexels ({chosen.get('width')}×{chosen.get('height')})...", end=" ", flush=True)
        resp = requests.get(chosen["link"], stream=True, timeout=60)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=512 * 1024):
                f.write(chunk)
        print("OK")
        return True
    except Exception as e:
        print(f"erro: {e}")
        return False


def _foto_para_video(query: str, dest_path: str) -> bool:
    """Fallback: foto Pexels com slow zoom Ken Burns de 30s."""
    print(f"  Buscando foto Pexels para '{query}'...", end=" ", flush=True)
    pil_img = _search_pexels(query)
    if pil_img is None:
        print("não encontrada.")
        return False

    tmp_photo = dest_path.replace(".mp4", "_photo.jpg")
    pil_img.save(tmp_photo, "JPEG", quality=92)

    total_frames = 24 * 30  # 30s @ 24fps
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", tmp_photo,
        "-t", "30",
        "-vf", (
            f"scale=3840:-1,"
            f"zoompan=z='min(zoom+0.0003,1.3)':d={total_frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
            f"scale={VIDEO_W}:{VIDEO_H}:flags=lanczos"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-an",
        dest_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print("OK (slow zoom)")
        return True
    except subprocess.CalledProcessError:
        return False
    finally:
        if os.path.exists(tmp_photo):
            os.remove(tmp_photo)


def _obter_clip_visual(query: str, dest_path: str) -> bool:
    """Tenta vídeo real → foto slow zoom → fundo preto."""
    if _baixar_video_pexels(query, dest_path):
        return True
    print("  Vídeo indisponível, tentando foto com slow zoom...")
    return _foto_para_video(query, dest_path)


def _create_black_video(dest_path: str, duration_s: int = 10):
    """Fallback: clipe preto de 10s para usar no loop."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:size={VIDEO_W}x{VIDEO_H}:rate=24:duration={duration_s}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        dest_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _loop_to_final(video_src: str, audio_src: str, output_path: str, total_seconds: int):
    """
    Loopa vídeo + áudio até total_seconds.
    Vídeo já está em H264 → copia bitstream (c:v copy) sem re-encodar.
    Apenas o áudio (WAV→AAC) é encodado, o que torna a montagem quase instantânea.
    """
    horas_label = f"{total_seconds/3600:.1f}h"
    print(f"  Montando vídeo final ({horas_label}) com ffmpeg...")
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", video_src,
        "-stream_loop", "-1", "-i", audio_src,
        "-map", "0:v", "-map", "1:a",
        "-t", str(total_seconds),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    time_re = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    bar_width = 28
    for line in proc.stderr:
        m = time_re.search(line)
        if m:
            atual = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            pct = min(100, int(atual / total_seconds * 100))
            cheio = int(bar_width * pct / 100)
            barra = "=" * cheio + "-" * (bar_width - cheio)
            print(f"\r  [{barra}] {pct:3d}%  ({atual/3600:.2f}h / {horas_label})", end="", flush=True)
    proc.wait()
    print()
    if proc.returncode != 0:
        raise RuntimeError("Falha na montagem do vídeo com ffmpeg")
    print(f"  Vídeo salvo: {output_path}")


def generate_ambient_video(
    sound_type: str,
    hours: float = 8.0,
    upload: bool = True,
    privacy: str = "public",
) -> str:
    """
    Gera um vídeo ambiente de `hours` horas e faz upload no YouTube.

    Args:
        sound_type: um de SOUND_CONFIG.keys()
        hours:      duração do vídeo final (ex: 1, 3, 8, 10)
        upload:     True para subir no YouTube após gerar
        privacy:    "public" | "private"

    Returns:
        Caminho do vídeo gerado.
    """
    if sound_type not in SOUND_CONFIG:
        raise ValueError(f"Tipo '{sound_type}' inválido. Use: {list(SOUND_CONFIG.keys())}")

    cfg = SOUND_CONFIG[sound_type]
    total_seconds = int(hours * 3600)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    os.makedirs(AMBIENT_OUTPUT_DIR, exist_ok=True)

    print(f"\n=== Gerando: {cfg['label']} — {hours}h ===")

    # 1. Áudio ambiente loopável
    audio_path = os.path.join(AMBIENT_OUTPUT_DIR, f"loop_{sound_type}_{ts}.wav")
    print("[1/3] Áudio ambiente:")
    generate_ambient_audio(sound_type, audio_path, loop_duration_s=LOOP_AUDIO_SECONDS)

    # 2. Clipe de vídeo Pexels (loopável)
    video_loop_path = os.path.join(AMBIENT_OUTPUT_DIR, f"loop_{sound_type}_{ts}.mp4")
    print("[2/3] Clipe de vídeo:")
    ok = _obter_clip_visual(cfg["pexels_query"], video_loop_path)
    if not ok:
        print("  Usando fundo preto como fallback.")
        _create_black_video(video_loop_path)

    # 3. Montar vídeo final
    output_filename = f"Ambient_{sound_type}_{int(hours)}h_{ts}.mp4"
    output_path = os.path.join(AMBIENT_OUTPUT_DIR, output_filename)
    print("[3/3] Montagem final:")
    _loop_to_final(video_loop_path, audio_path, output_path, total_seconds)

    # Limpar arquivos temporários de loop
    for f in [audio_path, video_loop_path]:
        try:
            os.remove(f)
        except Exception:
            pass

    # 4. Upload YouTube
    if upload:
        from uploader import upload_video
        hours_label = f"{int(hours)}h" if hours == int(hours) else f"{hours}h"
        yt_title = cfg["yt_title"].format(hours=hours_label)
        yt_desc = (
            f"{cfg['yt_desc']}\n\n"
            f"⏱️ Duração: {hours_label}\n\n"
            "🔔 Inscreva-se para mais sons relaxantes!\n\n"
            "#relaxar #dormir #sleepsounds #sonsambiência"
        )
        print(f"\n[4/4] Upload YouTube: '{yt_title}'")
        try:
            video_id = upload_video(output_path, yt_title, yt_desc, cfg["yt_tags"], privacy=privacy)
            print(f"  YouTube: https://youtu.be/{video_id}")
            try:
                os.remove(output_path)
                print(f"  Vídeo local removido após upload.")
            except Exception:
                pass
        except Exception as e:
            print(f"  Erro no upload: {e}")

    return output_path


if __name__ == "__main__":
    tipos = list(SOUND_CONFIG.keys())

    parser = argparse.ArgumentParser(
        prog="ambient_video.py",
        description="Gera vídeos longos de sons ambiente e faz upload no YouTube.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "exemplos:\n"
            "  python ambient_video.py rain\n"
            "  python ambient_video.py ocean --horas 10\n"
            "  python ambient_video.py fire --horas 3 --privado\n"
            "  python ambient_video.py forest --horas 1 --sem-upload\n"
            "  python ambient_video.py todos --horas 8\n"
        ),
    )
    parser.add_argument(
        "tipo",
        choices=tipos + ["todos"],
        metavar="tipo",
        help=f"tipo de som: {', '.join(tipos)} | todos (gera todos em sequência)",
    )
    parser.add_argument(
        "--horas", "-H",
        type=float,
        default=8.0,
        metavar="N",
        help="duração do vídeo em horas (padrão: 8)",
    )
    parser.add_argument(
        "--sem-upload",
        action="store_true",
        help="gera o vídeo localmente sem fazer upload no YouTube",
    )
    parser.add_argument(
        "--privado",
        action="store_true",
        help="sobe como privado em vez de público",
    )

    args = parser.parse_args()

    if args.horas <= 0:
        parser.error("--horas deve ser maior que zero")

    privacy = "private" if args.privado else "public"
    upload  = not args.sem_upload
    sons    = tipos if args.tipo == "todos" else [args.tipo]

    for som in sons:
        generate_ambient_video(
            sound_type=som,
            hours=args.horas,
            upload=upload,
            privacy=privacy,
        )