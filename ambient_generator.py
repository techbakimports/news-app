"""
Gera áudio ambiente loopável (chuva, mar, lareira, floresta, ruídos).
Usa numpy + scipy para síntese por filtros — sem dependências de assets externos.
"""
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
    noise = np.random.randn(n)
    rain = sosfilt(_bandpass(600, 8000), noise)

    t = np.linspace(0, duration_s, n)
    # variação lenta de intensidade (vento)
    envelope = 1.0 + 0.30 * np.sin(2 * np.pi * 0.05 * t) \
                   + 0.15 * np.sin(2 * np.pi * 0.13 * t)
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