import asyncio
import edge_tts
import os
import re
import wave
from config import TTS_VOICE, AUDIO_OUTPUT_DIR

WORDS_PER_MINUTE = 145  # velocidade média da voz pt-BR-AntonioNeural


def clean_text(text):
    """Remove markdown formatting so TTS doesn't read symbols aloud."""
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{1,3}(.*?)_{1,3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'[*_`~]', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _split_text(text, max_chars=2000):
    """Split text at paragraph/sentence boundaries to respect TTS limits."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = ""

    for para in text.split('\n\n'):
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) > max_chars:
                current = ""
                for sent in re.split(r'(?<=[.!?])\s+', para):
                    if len(current) + len(sent) + 1 <= max_chars:
                        current = (current + " " + sent).strip()
                    else:
                        if current:
                            chunks.append(current)
                        current = sent
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks


def _write_silence(path: str, duration_s: float, sample_rate: int = 22050) -> None:
    """Gera WAV silencioso — fallback quando TTS falha completamente."""
    n = max(1, int(duration_s * sample_rate))
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n)


async def _stream_to_bytes(text: str, max_retries: int = 3) -> bytes:
    """Converte texto em bytes MP3 via Edge TTS com retry automático em falha."""
    cleaned = clean_text(text)
    last_err: Exception | None = None

    for attempt in range(max_retries):
        try:
            data = bytearray()
            for chunk in _split_text(cleaned, max_chars=2000):
                communicate = edge_tts.Communicate(chunk, TTS_VOICE)
                async for c in communicate.stream():
                    if c["type"] == "audio":
                        data.extend(c["data"])
            if data:
                return bytes(data)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  TTS tentativa {attempt + 1} falhou ({e}). Retentando em {wait}s...")
                await asyncio.sleep(wait)

    print(f"  TTS falhou após {max_retries} tentativas: {last_err}")
    return b""


async def generate_audio(text, filename):
    """Gera áudio a partir do texto usando edge-tts, suportando textos longos."""
    if not os.path.exists(AUDIO_OUTPUT_DIR):
        os.makedirs(AUDIO_OUTPUT_DIR)

    output_path = os.path.join(AUDIO_OUTPUT_DIR, filename)
    data = await _stream_to_bytes(text)
    with open(output_path, "wb") as f:
        f.write(data)
    print(f"Áudio salvo: {output_path} ({len(data) / 1024:.0f} KB)")
    return output_path


async def generate_audio_segments(segment_texts, output_dir, filename_base):
    """
    Gera um MP3 por segmento de texto, mede a duração exata de cada um via
    AudioFileClip e concatena tudo em um único arquivo final.
    Retorna (caminho_final, [durações_em_segundos]).
    """
    from moviepy.editor import AudioFileClip, concatenate_audioclips

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    segment_paths = []
    for i, text in enumerate(segment_texts):
        seg_path = os.path.join(output_dir, f"_seg{i}_{filename_base}.mp3")
        print(f"  Gerando segmento {i + 1}/{len(segment_texts)}...")
        data = await _stream_to_bytes(text)
        if data:
            with open(seg_path, "wb") as f:
                f.write(data)
        else:
            # TTS falhou — gera silêncio com duração estimada para manter sync
            n_words = max(10, len(clean_text(text).split()))
            est = round(n_words / WORDS_PER_MINUTE * 60, 2)
            seg_path = seg_path.replace(".mp3", ".wav")
            print(f"  Silêncio de {est}s gerado (TTS falhou para segmento {i + 1})")
            _write_silence(seg_path, est)
        segment_paths.append(seg_path)

    clips = [AudioFileClip(p) for p in segment_paths]
    durations = [c.duration for c in clips]

    combined_path = os.path.join(output_dir, f"{filename_base}.mp3")
    combined_clip = concatenate_audioclips(clips)
    combined_clip.write_audiofile(combined_path, verbose=False)
    combined_clip.close()
    for c in clips:
        c.close()
    for p in segment_paths:
        os.remove(p)

    total = sum(durations)
    print(f"Áudio final: {combined_path} ({total:.1f}s / {total / 60:.1f} min)")
    return combined_path, durations


if __name__ == "__main__":
    test_text = "Esta é uma demonstração de geração de áudio para o seu aplicativo de notícias."
    asyncio.run(generate_audio(test_text, "test.mp3"))
    print("Áudio de teste gerado em ./audio_news/test.mp3")
