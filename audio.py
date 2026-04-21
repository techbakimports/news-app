import asyncio
import edge_tts
import os
import re
from config import TTS_VOICE, AUDIO_OUTPUT_DIR


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


async def generate_audio(text, filename):
    """Gera áudio a partir do texto usando edge-tts, suportando textos longos."""
    if not os.path.exists(AUDIO_OUTPUT_DIR):
        os.makedirs(AUDIO_OUTPUT_DIR)

    output_path = os.path.join(AUDIO_OUTPUT_DIR, filename)
    clean = clean_text(text)
    chunks = _split_text(clean, max_chars=2000)

    print(f"Gerando áudio em {len(chunks)} parte(s)...")

    audio_data = bytearray()
    for i, chunk in enumerate(chunks, 1):
        print(f"  Processando parte {i}/{len(chunks)}...")
        communicate = edge_tts.Communicate(chunk, TTS_VOICE)
        async for audio_chunk in communicate.stream():
            if audio_chunk["type"] == "audio":
                audio_data.extend(audio_chunk["data"])

    with open(output_path, "wb") as f:
        f.write(audio_data)

    print(f"Áudio salvo: {output_path} ({len(audio_data) / 1024:.0f} KB)")
    return output_path


if __name__ == "__main__":
    test_text = "Esta é uma demonstração de geração de áudio para o seu aplicativo de notícias."
    asyncio.run(generate_audio(test_text, "test.mp3"))
    print("Áudio de teste gerado em ./audio_news/test.mp3")
