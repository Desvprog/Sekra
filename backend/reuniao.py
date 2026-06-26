#!/usr/bin/env python3
"""
Núcleo de gravação, transcrição, diarização e detecção de hotwords.

Pode ser usado como CLI (compatibilidade com versão anterior) ou
importado como módulo pelo server.py.
"""

import argparse
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import config
import meta
import resumo
import llm

# Em dev usa a pasta do projeto; em produção (PyInstaller) usa ~/reunioes
if hasattr(sys, "_MEIPASS"):
    BASE_DIR = Path.home() / "reunioes"
else:
    BASE_DIR = Path(__file__).parent.parent / "reunioes"
CONFIG_DIR = Path.home() / ".config" / "reunioes"
TOKEN_FILE = CONFIG_DIR / "hf_token"
SAMPLE_RATE = 16000


# ────────────────────────────────────────────────────────────────────────────
# Tipos
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class Segmento:
    start: float
    end: float
    text: str
    speaker: str = "?"


# ────────────────────────────────────────────────────────────────────────────
# Detecção de dispositivos (PulseAudio)
# ────────────────────────────────────────────────────────────────────────────

def pactl(*args: str) -> str:
    try:
        r = subprocess.run(["pactl", *args], capture_output=True,
                           text=True, check=True)
        return r.stdout.strip()
    except FileNotFoundError:
        sys.exit("❌ pactl não encontrado. Instale: sudo apt install pulseaudio-utils")
    except subprocess.CalledProcessError as e:
        sys.exit(f"❌ Erro pactl: {e.stderr}")


def detectar_dispositivos() -> tuple[str, str]:
    sink = pactl("get-default-sink")
    mic = pactl("get-default-source")
    return f"{sink}.monitor", mic


# ────────────────────────────────────────────────────────────────────────────
# Gravação — agora separada em "iniciar" e "esperar parada"
# ────────────────────────────────────────────────────────────────────────────

def iniciar_gravacao(pasta: Path, monitor: str, mic: str) -> subprocess.Popen:
    """
    Inicia ffmpeg gravando 3 saídas: mic, loopback, mesclado.
    Retorna o processo Popen — quem chama é responsável por terminar
    via communicate(b"q") quando quiser parar.
    """
    p_mic = pasta / "audio-mic.wav"
    p_loop = pasta / "audio-loopback.wav"
    p_mix = pasta / "audio.wav"

    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-f", "pulse", "-i", monitor,
        "-f", "pulse", "-i", mic,
        "-filter_complex",
        "[0][1]amix=inputs=2:duration=longest[mix]",
        "-map", "1:a", "-ac", "1", "-ar", str(SAMPLE_RATE), "-y", str(p_mic),
        "-map", "0:a", "-ac", "1", "-ar", str(SAMPLE_RATE), "-y", str(p_loop),
        "-map", "[mix]", "-ac", "1", "-ar", str(SAMPLE_RATE), "-y", str(p_mix),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def parar_gravacao(proc: subprocess.Popen) -> None:
    """Para uma gravação iniciada por iniciar_gravacao()."""
    try:
        proc.communicate(b"q", timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait()


def gravar_cli(pasta: Path, monitor: str, mic: str) -> None:
    """Versão CLI: bloqueia até Ctrl+C."""
    print(f"\n🎙️  Gravando em: {pasta}/")
    print("    [Ctrl+C para parar]\n")

    proc = iniciar_gravacao(pasta, monitor, mic)

    def handler(_sig, _frame):
        parar_gravacao(proc)

    signal.signal(signal.SIGINT, handler)
    proc.wait()
    print("⏹️  Gravação finalizada.")


def caminhos_audio(pasta: Path) -> tuple[Path, Path, Path]:
    return (pasta / "audio-mic.wav",
            pasta / "audio-loopback.wav",
            pasta / "audio.wav")


MIN_AUDIO_BYTES = 44 + 512  # cabeçalho WAV + pelo menos alguns frames


def _ffmpeg_remux(entrada: Path, saida: Path) -> bool:
    """Tenta reescrever cabeçalho WAV corrompido via ffmpeg. Retorna True se ok."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-err_detect", "ignore_err",
             "-i", str(entrada), "-c", "copy", str(saida)],
            capture_output=True, timeout=60,
        )
        return saida.exists() and saida.stat().st_size > MIN_AUDIO_BYTES
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def recuperar_audio(pasta: Path,
                    progress_cb: Optional[Callable[[str], None]] = None) -> bool:
    """
    Tenta garantir que audio.wav exista e seja válido.

    Estratégias em ordem:
      1. audio.wav existe e tem tamanho razoável → ok, não faz nada
      2. audio.wav existe mas está truncado → tenta reescrever cabeçalho
      3. audio.wav ausente, mas mic+loopback existem → remonta via ffmpeg amix
      4. audio.wav ausente, apenas mic ou loopback → usa o que tiver
    Retorna True se ao final audio.wav estiver utilizável.
    """
    def report(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    p_mic, p_loop, p_mix = caminhos_audio(pasta)

    if p_mix.exists() and p_mix.stat().st_size > MIN_AUDIO_BYTES:
        return True  # já está bom

    report("Tentando recuperar áudio...")

    # Cabeçalho corrompido: tenta remux in-place
    if p_mix.exists():
        tmp = pasta / "audio-remux.wav"
        if _ffmpeg_remux(p_mix, tmp):
            tmp.replace(p_mix)
            report("Áudio recuperado (cabeçalho corrigido).")
            return True
        p_mix.unlink(missing_ok=True)

    mic_ok = p_mic.exists() and p_mic.stat().st_size > MIN_AUDIO_BYTES
    loop_ok = p_loop.exists() and p_loop.stat().st_size > MIN_AUDIO_BYTES

    # Corrige cabeçalhos parciais antes de usar
    for p, ok in [(p_mic, mic_ok), (p_loop, loop_ok)]:
        if not ok and p.exists():
            tmp = pasta / f"{p.stem}-remux.wav"
            if _ffmpeg_remux(p, tmp):
                tmp.replace(p)

    mic_ok = p_mic.exists() and p_mic.stat().st_size > MIN_AUDIO_BYTES
    loop_ok = p_loop.exists() and p_loop.stat().st_size > MIN_AUDIO_BYTES

    if mic_ok and loop_ok:
        r = subprocess.run(
            ["ffmpeg", "-y",
             "-i", str(p_mic), "-i", str(p_loop),
             "-filter_complex", "[0][1]amix=inputs=2:duration=longest[mix]",
             "-map", "[mix]", "-ac", "1", "-ar", str(SAMPLE_RATE), str(p_mix)],
            capture_output=True, timeout=300,
        )
        if p_mix.exists() and p_mix.stat().st_size > MIN_AUDIO_BYTES:
            report("Áudio recuperado (mic + loopback remixados).")
            return True

    for fonte in (p_mic if mic_ok else None, p_loop if loop_ok else None):
        if fonte is None:
            continue
        if _ffmpeg_remux(fonte, p_mix):
            report(f"Áudio recuperado (usando {fonte.name}).")
            return True

    report("Não foi possível recuperar o áudio.")
    return False


# ────────────────────────────────────────────────────────────────────────────
# Transcrição (faster-whisper)
# ────────────────────────────────────────────────────────────────────────────

_modelo_cache: dict = {"nome": None, "obj": None}


def _get_whisper(modelo: str):
    """Retorna WhisperModel do cache; recria apenas se o modelo mudou."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("❌ Instale: pip install faster-whisper")

    if _modelo_cache["nome"] != modelo or _modelo_cache["obj"] is None:
        _modelo_cache["obj"] = WhisperModel(modelo, device="cpu", compute_type="int8")
        _modelo_cache["nome"] = modelo
    return _modelo_cache["obj"]


def transcrever(audio: Path, modelo: str, label_speaker: str = "?",
                idioma: Optional[str] = None) -> tuple[list[Segmento], Optional[str]]:
    """
    Transcreve audio com faster-whisper.
    idioma=None → detecção automática pelo Whisper.
    Retorna (lista_segmentos, idioma_detectado).
    """
    model = _get_whisper(modelo)
    segments_iter, info = model.transcribe(
        str(audio),
        language=idioma,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
    )
    idioma_detectado = getattr(info, "language", None)
    segs = [
        Segmento(start=s.start, end=s.end, text=s.text.strip(), speaker=label_speaker)
        for s in segments_iter
    ]
    return segs, idioma_detectado


# ────────────────────────────────────────────────────────────────────────────
# Compressão / descompressão de áudio
# ────────────────────────────────────────────────────────────────────────────

def _fonte_audio(pasta: Path, base: str) -> Optional[Path]:
    """Retorna o Path existente preferindo .wav, senão .opus, senão None."""
    wav = pasta / f"{base}.wav"
    opus = pasta / f"{base}.opus"
    if wav.exists():
        return wav
    if opus.exists():
        return opus
    return None


def comprimir_audios(pasta: Path, bitrate: str = "32k") -> None:
    """
    Para cada base (audio, audio-mic, audio-loopback): se existe .wav,
    converte para .opus via ffmpeg e remove o .wav apenas se o .opus foi gerado.
    """
    for base in ("audio", "audio-mic", "audio-loopback"):
        wav = pasta / f"{base}.wav"
        opus = pasta / f"{base}.opus"
        if not wav.exists():
            continue
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav),
                 "-c:a", "libopus", "-b:a", bitrate, str(opus)],
                capture_output=True, timeout=600,
            )
            if opus.exists() and opus.stat().st_size > 0:
                wav.unlink(missing_ok=True)
            else:
                print(f"[reuniao] Compressão falhou para {wav.name}: opus vazio")
        except FileNotFoundError:
            print("[reuniao] ffmpeg não encontrado — compressão ignorada")
        except subprocess.TimeoutExpired:
            print(f"[reuniao] Timeout ao comprimir {wav.name}")


def garantir_wavs(pasta: Path) -> list[Path]:
    """
    Para cada base: se NÃO existe .wav mas existe .opus, recria o .wav.
    Retorna a lista dos .wav criados nesta chamada (para apagar depois).
    """
    criados: list[Path] = []
    for base in ("audio", "audio-mic", "audio-loopback"):
        wav = pasta / f"{base}.wav"
        opus = pasta / f"{base}.opus"
        if wav.exists() or not opus.exists():
            continue
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(opus),
                 "-ac", "1", "-ar", str(SAMPLE_RATE), str(wav)],
                capture_output=True, timeout=600,
            )
            if wav.exists() and wav.stat().st_size > 0:
                criados.append(wav)
            else:
                print(f"[reuniao] Descompressão falhou para {opus.name}")
        except FileNotFoundError:
            print("[reuniao] ffmpeg não encontrado — descompressão ignorada")
        except subprocess.TimeoutExpired:
            print(f"[reuniao] Timeout ao descomprimir {opus.name}")
    return criados


# ────────────────────────────────────────────────────────────────────────────
# Diarização (pyannote-audio)
# ────────────────────────────────────────────────────────────────────────────

def carregar_hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token.strip()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    raise RuntimeError(
        "Token Hugging Face não encontrado. "
        f"Salve em {TOKEN_FILE} ou exporte HF_TOKEN."
    )


def diarizar(audio: Path) -> list[tuple[float, float, str]]:
    try:
        from pyannote.audio import Pipeline
    except ImportError:
        sys.exit("❌ Instale: pip install pyannote.audio")

    token = carregar_hf_token()
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=token,
    )
    if pipeline is None:
        raise RuntimeError("Falha ao carregar pipeline. Verifique token e termos aceitos.")

    result = pipeline(str(audio))
    if hasattr(result, "itertracks"):
        diarization = result
    else:
        from pyannote.core import Annotation
        diarization = next(
            (getattr(result, a) for a in dir(result)
             if not a.startswith("_") and isinstance(getattr(result, a, None), Annotation)),
            None,
        )
        if diarization is None:
            raise RuntimeError(
                f"Não foi possível extrair diarização de {type(result).__name__}. "
                f"Atributos: {[a for a in dir(result) if not a.startswith('_')]}"
            )
    return [
        (turn.start, turn.end, speaker)
        for turn, _, speaker in diarization.itertracks(yield_label=True)
    ]


def aplicar_diarizacao(segs: list[Segmento],
                       turnos: list[tuple[float, float, str]]) -> list[Segmento]:
    for seg in segs:
        melhor_speaker = "?"
        maior_overlap = 0.0
        for t_start, t_end, speaker in turnos:
            overlap = max(0, min(seg.end, t_end) - max(seg.start, t_start))
            if overlap > maior_overlap:
                maior_overlap = overlap
                melhor_speaker = speaker
        seg.speaker = melhor_speaker
    return segs


# ────────────────────────────────────────────────────────────────────────────
# Hotwords (rapidfuzz)
# ────────────────────────────────────────────────────────────────────────────

def encontrar_hotwords(segs: list[Segmento], hotwords: list[str],
                       threshold: int = 85) -> list[dict]:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        sys.exit("❌ Instale: pip install rapidfuzz")

    hotwords_norm = [h.lower().strip() for h in hotwords if h.strip()]
    matches = []
    for seg in segs:
        palavras = re.findall(r"\b[\w]+\b", seg.text.lower())
        ja_marcado = set()
        for palavra in palavras:
            for hw in hotwords_norm:
                if hw in ja_marcado:
                    continue
                ratio = fuzz.ratio(palavra, hw)
                if ratio >= threshold:
                    matches.append({
                        "timestamp": seg.start,
                        "speaker": seg.speaker,
                        "hotword": hw,
                        "match": palavra,
                        "similaridade": ratio,
                        "contexto": seg.text,
                    })
                    ja_marcado.add(hw)
    return matches


# ────────────────────────────────────────────────────────────────────────────
# Saída em arquivo
# ────────────────────────────────────────────────────────────────────────────

def fmt_ts(s: float) -> str:
    mm, ss = divmod(int(s), 60)
    hh, mm = divmod(mm, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"


def escrever_transcricao(segs: list[Segmento], path: Path,
                         titulo: str, modelo: str) -> None:
    segs_ord = sorted(segs, key=lambda s: s.start)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {titulo}\n")
        f.write(f"# Modelo: {modelo} | Segmentos: {len(segs_ord)}\n\n")
        speaker_anterior = None
        for s in segs_ord:
            if s.speaker != speaker_anterior:
                f.write(f"\n**{s.speaker}**\n")
                speaker_anterior = s.speaker
            f.write(f"[{fmt_ts(s.start)}] {s.text}\n")


def escrever_hotwords(matches: list[dict], path: Path,
                      hotwords: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Hotwords detectadas\n\n")
        f.write(f"Buscando: {', '.join(hotwords)}\n\n")
        if not matches:
            f.write("_Nenhuma menção encontrada._\n")
            return
        f.write(f"**{len(matches)} menção(ões) encontrada(s):**\n\n")
        for m in matches:
            f.write(
                f"- **[{fmt_ts(m['timestamp'])}]** "
                f"`{m['hotword']}` ({m['similaridade']}% via \"{m['match']}\") "
                f"— {m['speaker']}\n"
                f"  > {m['contexto']}\n\n"
            )


# ────────────────────────────────────────────────────────────────────────────
# Renomeação de speakers
# ────────────────────────────────────────────────────────────────────────────

def renomear_speakers(segs: list[Segmento], mapa: dict[str, str]) -> None:
    for s in segs:
        s.speaker = mapa.get(s.speaker, s.speaker)


# ────────────────────────────────────────────────────────────────────────────
# Pipeline principal — agora aceita progress_cb opcional
# ────────────────────────────────────────────────────────────────────────────

def processar(pasta: Path, titulo: str, modelo: str,
              diarizar_flag: bool, hotwords: list[str],
              progress_cb: Optional[Callable[[str], None]] = None,
              idioma: Optional[str] = None) -> dict:
    """
    Orquestra transcrição + diarização + hotwords.
    progress_cb recebe mensagens curtas de status (pra UI).
    Retorna dict com paths gerados.
    """
    p_mic, p_loop, p_mix = caminhos_audio(pasta)

    def report(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)
        else:
            print(f"   {msg}")

    # Resolve configuração e idioma efetivo
    cfg = config.carregar()
    if idioma is None:
        idioma = cfg.get("idioma", "auto")
    idioma_efetivo = None if idioma == "auto" else idioma

    # Recompõe wavs se reunião já foi comprimida anteriormente
    wavs_temp = garantir_wavs(pasta)

    try:
        if not recuperar_audio(pasta, progress_cb):
            raise RuntimeError(
                "Nenhum arquivo de áudio utilizável encontrado nesta pasta. "
                "A gravação pode ter sido interrompida antes de qualquer dado ser salvo."
            )

        idioma_detectado: Optional[str] = None

        if diarizar_flag:
            report("Transcrevendo sua voz...")
            segs_mic, _ = transcrever(p_mic, modelo, label_speaker="Eu",
                                      idioma=idioma_efetivo)
            report("Transcrevendo demais participantes...")
            segs_loop, idi = transcrever(p_loop, modelo, idioma=idioma_efetivo)
            idioma_detectado = idi
            report("Identificando falantes...")
            turnos = diarizar(p_loop)
            segs_loop = aplicar_diarizacao(segs_loop, turnos)
            speakers_unicos = sorted(set(s.speaker for s in segs_loop
                                         if s.speaker != "?"))
            mapa = {sp: f"Pessoa {i+1}" for i, sp in enumerate(speakers_unicos)}
            renomear_speakers(segs_loop, mapa)
            todos_segs = segs_mic + segs_loop
        else:
            report("Transcrevendo...")
            todos_segs, idioma_detectado = transcrever(p_mix, modelo,
                                                       idioma=idioma_efetivo)

        p_txt = pasta / "transcricao.txt"
        escrever_transcricao(todos_segs, p_txt, titulo, modelo)

        p_hw = None
        if hotwords:
            report("Buscando hotwords...")
            matches = encontrar_hotwords(todos_segs, hotwords)
            p_hw = pasta / "hotwords.md"
            escrever_hotwords(matches, p_hw, hotwords)

        # Grava meta.json com informações da transcrição
        fonte = _fonte_audio(pasta, "audio") or p_mix
        speakers = sorted({s.speaker for s in todos_segs})
        meta.escrever(
            pasta,
            titulo=titulo,
            modelo=modelo,
            idioma=idioma_detectado,
            num_segmentos=len(todos_segs),
            num_speakers=len(speakers),
            speakers=speakers,
            duracao_s=meta.duracao_audio(fonte),
            status="concluido",
            tem_transcricao=True,
            tem_hotwords=bool(hotwords),
            tem_resumo=False,
            audio_comprimido=bool(cfg.get("comprimir_audio")),
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )

        # Resumo automático
        if cfg.get("resumo_automatico"):
            try:
                report("Gerando resumo...")
                if resumo.gerar_e_salvar(pasta):
                    meta.escrever(pasta, tem_resumo=True)
            except Exception as e:
                report(f"Resumo não gerado: {e}")

        # Compressão de áudio ao final
        if cfg.get("comprimir_audio"):
            report("Comprimindo áudio...")
            comprimir_audios(pasta, cfg.get("bitrate_opus", "32k"))
        else:
            # Reprocessamento: limpa wavs temporários que foram recriados de .opus
            for w in wavs_temp:
                w.unlink(missing_ok=True)

    except Exception:
        # Garante limpeza dos wavs temporários em caso de erro
        for w in wavs_temp:
            w.unlink(missing_ok=True)
        raise

    report("Pronto!")
    return {"transcricao": str(p_txt), "hotwords": str(p_hw) if p_hw else None}


# ────────────────────────────────────────────────────────────────────────────
# CLI (compatibilidade com versão anterior)
# ────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Grava, transcreve, diariza e analisa reuniões.",
    )
    p.add_argument("titulo", nargs="?", default="reuniao")
    p.add_argument("--modelo", default="medium",
                   choices=["small", "medium", "large-v3"])
    p.add_argument("--diarizar", action="store_true")
    p.add_argument("--hotwords", default="")
    p.add_argument("--so-gravar", action="store_true")
    p.add_argument("--so-processar", type=Path, metavar="PASTA")
    args = p.parse_args()

    hotwords = [h.strip() for h in args.hotwords.split(",") if h.strip()]

    if args.so_processar:
        if not (args.so_processar / "audio.wav").exists():
            sys.exit(f"❌ {args.so_processar}/audio.wav não encontrado.")
        processar(args.so_processar, args.so_processar.name,
                  args.modelo, args.diarizar, hotwords)
        return

    agora = datetime.now()
    titulo_limpo = args.titulo.replace(" ", "-").lower()
    pasta = BASE_DIR / agora.strftime("%Y-%m-%d") / f"{agora.strftime('%H-%M')}-{titulo_limpo}"
    pasta.mkdir(parents=True, exist_ok=True)

    monitor, mic = detectar_dispositivos()
    print(f"📡 Sistema: {monitor}")
    print(f"🎤 Microfone: {mic}")

    gravar_cli(pasta, monitor, mic)

    if args.so_gravar:
        print(f"\n💾 Áudio salvo em: {pasta}/")
        return

    print()
    processar(pasta, args.titulo, args.modelo, args.diarizar, hotwords)
    print(f"\n✅ Resultado em: {pasta}/")


if __name__ == "__main__":
    main()
