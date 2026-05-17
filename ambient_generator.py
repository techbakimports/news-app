"""
Gera áudio ambiente loopável (chuva, mar, lareira, floresta, ruídos).
Prioridade: assets/audio/<tipo>.wav|mp3|ogg → download CC0 automático → síntese scipy.
"""
import os
import shutil
import urllib.request
import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

SAMPLE_RATE = 44100
FADE_SECONDS = 3  # crossfade para loop seamless

SOUND_TYPES = ["rain", "ocean", "fire", "forest", "whitenoise", "brownnoise"]


def _bandpass(lowcut, highcut, order=4):
    nyq = 0.5 * SAMPLE_RATE
    sos = butter(order, [lowcut / nyq, highcut / nyq], btype="band", output="sos")
    return sos


def _lowpass(cutoff, order=4):
    nyq = 0.5 * SAMPLE_RATE
    sos = butter(order, cutoff / nyq, btype="low", output="sos")
    return sos


def _normalize(audio, peak=0.85):
    m = np.max(np.abs(audio))
    return audio / m * peak if m > 0 else audio


def _crossfade_loop(audio):
    """Crossfade final → início para eliminar clique no ponto de loop."""
    fade = int(SAMPLE_RATE * FADE_SECONDS)
    if len(audio) <= fade * 2:
        return audio
    ramp_in  = np.linspace(0.0, 1.0, fade)
    ramp_out = np.linspace(1.0, 0.0, fade)
    result = audio.copy()
    result[:fade] = audio[:fade] * ramp_in + audio[-fade:] * ramp_out
    return result[:-fade]


def _rain(duration_s):
    n = int(SAMPLE_RATE * duration_s)
    rng = np.random.default_rng()
    rain = np.zeros(n, dtype=np.float64)

    # Chuva = sobreposição de centenas de impactos por segundo.
    # O som contínuo "shhhh" emerge da soma — sem ruído de base separado.
    n_drops = int(duration_s * 500)
    positions = rng.integers(0, n, n_drops)
    # Distribuição log-normal: maioria de gotas pequenas, poucas grandes
    sizes = np.clip(rng.lognormal(mean=-0.3, sigma=0.7, size=n_drops), 0.08, 3.0)

    for pos, size in zip(positions, sizes):
        # Gotas maiores = duração maior, mais graves, mais amplitude
        length = min(int(SAMPLE_RATE * 0.05 * size), n - pos)
        if length < 10:
            continue
        t_d = np.linspace(0, 1, length)
        # Decaimento convexo (mais natural que exponencial puro)
        env = (1.0 - t_d) ** (2.5 / size)
        rain[pos:pos + length] += rng.standard_normal(length) * env * size

    # Faixa real da chuva em superfícies: 250–1800 Hz
    # Nada acima de 1800 Hz — essa faixa é o chiado de estática
    rain = sosfilt(_bandpass(250, 1800), rain)

    t = np.linspace(0, duration_s, n)
    envelope = 1.0 + 0.18 * np.sin(2 * np.pi * 0.05 * t)
    return _normalize(rain * envelope)


def _ocean(duration_s):
    n = int(SAMPLE_RATE * duration_s)
    noise = np.random.randn(n)
    base = sosfilt(_lowpass(350), noise)

    t = np.linspace(0, duration_s, n)
    # ondas de ~8s e ~12s sobrepostas
    wave = (0.5 + 0.5 * np.sin(2 * np.pi * t / 8.0)) ** 2 \
         + 0.3 * (0.5 + 0.5 * np.sin(2 * np.pi * t / 12.0)) ** 2

    crash = sosfilt(_bandpass(400, 3000), noise) * wave * 0.25
    ocean = base * wave + crash
    return _normalize(ocean)


def _fire(duration_s):
    n = int(SAMPLE_RATE * duration_s)
    noise = np.random.randn(n)
    base = sosfilt(_lowpass(280), noise) * 0.6

    # estouros aleatórios de lenha
    crackles = np.zeros(n)
    for _ in range(int(duration_s * 6)):
        pos = np.random.randint(0, n - 2200)
        length = np.random.randint(60, 2200)
        amp = np.random.uniform(0.1, 0.9)
        decay = np.exp(-np.linspace(0, 10, length))
        crackles[pos:pos + length] += np.random.randn(length) * amp * decay

    return _normalize(base + crackles * 0.4)


def _forest(duration_s):
    n = int(SAMPLE_RATE * duration_s)
    noise = np.random.randn(n)

    wind = sosfilt(_bandpass(80, 1000), noise)
    rustle = sosfilt(_bandpass(1000, 5000), noise) * 0.35

    t = np.linspace(0, duration_s, n)
    wind_env = 0.7 + 0.3 * np.sin(2 * np.pi * 0.04 * t)
    return _normalize(wind * wind_env + rustle)


def _whitenoise(duration_s):
    n = int(SAMPLE_RATE * duration_s)
    noise = np.random.randn(n)
    return _normalize(noise, peak=0.70)


def _brownnoise(duration_s):
    n = int(SAMPLE_RATE * duration_s)
    noise = np.random.randn(n)
    brown = sosfilt(_lowpass(450), noise)
    return _normalize(brown, peak=0.80)


_GENERATORS = {
    "rain":       _rain,
    "ocean":      _ocean,
    "fire":       _fire,
    "forest":     _forest,
    "whitenoise": _whitenoise,
    "brownnoise": _brownnoise,
}


ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "audio")

# Arquivos CC0 do Wikimedia Commons — baixados automaticamente na primeira execução
_ASSET_URLS: dict[str, list[str]] = {
    "rain": [
        "https://upload.wikimedia.org/wikipedia/commons/c/cb/Heavy_rain_in_Glenshaw%2C_PA.ogg",
        "https://upload.wikimedia.org/wikipedia/commons/0/09/Rain_thunder_steps.ogg",
        "https://upload.wikimedia.org/wikipedia/commons/4/42/Rain_and_thunder.ogg",
    ],
    "ocean": [
        "https://upload.wikimedia.org/wikipedia/commons/8/84/Sea_waves.wav",
    ],
    "fire": [
        "https://upload.wikimedia.org/wikipedia/commons/d/d8/Dry_grass_burning_in_open_fireplace.ogg",
    ],
    "forest": [
        "https://upload.wikimedia.org/wikipedia/commons/0/0a/20090610_0_ambience.ogg",
    ],
    "whitenoise": [
        "https://upload.wikimedia.org/wikipedia/commons/7/70/White_noise_sample.wav",
        "https://upload.wikimedia.org/wikipedia/commons/a/aa/White_noise.ogg",
    ],
    "brownnoise": [
        "https://upload.wikimedia.org/wikipedia/commons/d/d9/Brown_noise_15-00_69kbps.mp3",
    ],
}


def _download_asset(sound_type: str) -> bool:
    """Baixa arquivo de áudio CC0 do Wikimedia Commons e salva em assets/audio/."""
    urls = _ASSET_URLS.get(sound_type, [])
    if not urls:
        return False

    os.makedirs(ASSETS_DIR, exist_ok=True)

    for url in urls:
        ext = url.rsplit(".", 1)[-1].lower().split("?")[0]
        dest = os.path.join(ASSETS_DIR, f"{sound_type}.{ext}")
        tmp = dest + ".tmp"

        print(f"  Baixando áudio real '{sound_type}' do Wikimedia Commons...", end=" ", flush=True)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "news-app/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f)
            os.replace(tmp, dest)
            size_mb = os.path.getsize(dest) / 1024 / 1024
            print(f"OK ({size_mb:.1f} MB -> assets/audio/{sound_type}.{ext})")
            return True
        except Exception as e:
            print(f"falhou: {e}")
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    print(f"  Todos os downloads falharam — usando síntese para '{sound_type}'.")
    return False


def _load_asset(sound_type: str, output_path: str, loop_duration_s: int) -> bool:
    """
    Se existir assets/audio/<tipo>.wav|mp3|ogg, usa como base do loop.
    Retorna True se usou o asset, False se deve usar síntese.
    """
    import subprocess
    for ext in ("wav", "mp3", "ogg"):
        asset = os.path.join(ASSETS_DIR, f"{sound_type}.{ext}")
        if os.path.exists(asset):
            print(f"  Usando arquivo real: assets/audio/{sound_type}.{ext}")
            cmd = [
                "ffmpeg", "-y",
                "-stream_loop", "-1", "-i", asset,
                "-t", str(loop_duration_s),
                "-ar", str(SAMPLE_RATE), "-ac", "2",
                output_path,
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                return True
            except subprocess.CalledProcessError:
                print("  Falha ao processar asset, caindo para síntese.")
    return False


def generate_ambient_audio(sound_type: str, output_path: str, loop_duration_s: int = 180) -> str:
    """
    Gera um arquivo WAV loopável de `loop_duration_s` segundos.
    O ffmpeg vai repetir esse clipe até atingir a duração total do vídeo.

    Args:
        sound_type:      um dos SOUND_TYPES
        output_path:     caminho de saída .wav
        loop_duration_s: duração do clipe base (padrão 3 min)
    """
    if sound_type not in _GENERATORS:
        raise ValueError(f"Tipo '{sound_type}' inválido. Use: {SOUND_TYPES}")

    # Baixa arquivo real na primeira vez que este tipo for solicitado
    asset_exists = any(
        os.path.exists(os.path.join(ASSETS_DIR, f"{sound_type}.{ext}"))
        for ext in ("wav", "mp3", "ogg")
    )
    if not asset_exists:
        _download_asset(sound_type)

    # Usa arquivo real de assets/audio/<tipo>.wav|mp3|ogg se disponível
    if _load_asset(sound_type, output_path, loop_duration_s):
        print(f"  OK — {loop_duration_s}s, salvo em {output_path}")
        return output_path

    print(f"  Sintetizando '{sound_type}' ({loop_duration_s}s)...", end=" ", flush=True)
    audio = _GENERATORS[sound_type](loop_duration_s)
    audio = _crossfade_loop(audio)

    # Stereo: canal direito levemente atrasado (15ms) para criar sensação de espaço
    delay_samples = int(SAMPLE_RATE * 0.015)
    right = np.roll(audio, delay_samples)
    right[:delay_samples] = audio[:delay_samples]
    stereo = np.stack([audio, right], axis=1)

    audio_int16 = (stereo * 32767).astype(np.int16)
    wavfile.write(output_path, SAMPLE_RATE, audio_int16)
    print(f"OK — {len(audio)/SAMPLE_RATE:.1f}s, salvo em {output_path}")
    return output_path