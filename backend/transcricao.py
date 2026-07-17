#!/usr/bin/env python3
"""
Transcrição plugável: provedor local (faster-whisper, offline) ou API (OpenAI).

Contrato único:
    transcrever(audio, idioma, modelo, provider) -> (segmentos, idioma_detectado)
onde segmentos é list[tuple[start, end, text]] (tempos em segundos).

Este módulo NÃO importa reuniao (evita import circular): devolve tuplas cruas
e quem chama monta os objetos de domínio. A chave da API é lida de llm.py,
nunca logada. O provedor local é o padrão e mantém o app 100% offline.
"""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# Tempos em segundos
Trecho = tuple[float, float, str]


class TranscricaoError(Exception):
    pass


# ────────────────────────────────────────────────────────────────────────────
# Provedor local (faster-whisper)
# ────────────────────────────────────────────────────────────────────────────

_modelo_cache: dict = {"nome": None, "obj": None}


def _get_whisper(modelo: str):
    """Retorna WhisperModel do cache; recria apenas se o modelo mudou."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise TranscricaoError("faster-whisper não instalado (pip install faster-whisper)")

    if _modelo_cache["nome"] != modelo or _modelo_cache["obj"] is None:
        _modelo_cache["obj"] = WhisperModel(modelo, device="cpu", compute_type="int8")
        _modelo_cache["nome"] = modelo
    return _modelo_cache["obj"]


def _transcrever_local(audio: Path, idioma: Optional[str],
                       modelo: str) -> tuple[list[Trecho], Optional[str]]:
    model = _get_whisper(modelo)
    segments_iter, info = model.transcribe(
        str(audio),
        language=idioma,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
    )
    idioma_detectado = getattr(info, "language", None)
    trechos = [(s.start, s.end, s.text.strip()) for s in segments_iter]
    return trechos, idioma_detectado


# ────────────────────────────────────────────────────────────────────────────
# Provedor OpenAI (API)
# ────────────────────────────────────────────────────────────────────────────

# Limite prático da API de áudio da OpenAI é 25 MB/arquivo. Cortamos em blocos
# de tempo para nunca chegar perto disso mesmo em reuniões longas.
_BLOCO_SEGUNDOS = 600  # 10 min ≈ 19 MB em WAV 16 kHz mono 16-bit
_OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"


def _duracao_segundos(audio: Path) -> float:
    """Duração via ffprobe; 0.0 se indisponível."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
            capture_output=True, text=True, timeout=60,
        )
        return float(r.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return 0.0


def _cortar_bloco(audio: Path, inicio: float, dur: float, destino: Path) -> bool:
    """Extrai [inicio, inicio+dur) de audio para destino (WAV). True se ok."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(inicio), "-t", str(dur),
             "-i", str(audio), "-ac", "1", "-ar", "16000", str(destino)],
            capture_output=True, timeout=600, check=False,
        )
        return destino.exists() and destino.stat().st_size > 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _openai_um_bloco(bloco: Path, idioma: Optional[str], modelo: str,
                     chave: str) -> tuple[list[Trecho], Optional[str]]:
    """Transcreve um bloco via API. Tempos relativos ao início do bloco."""
    import httpx

    data = {"model": modelo, "response_format": "verbose_json"}
    if idioma:
        data["language"] = idioma

    try:
        with open(bloco, "rb") as f:
            arquivos = {"file": (bloco.name, f, "audio/wav")}
            with httpx.Client(timeout=600) as client:
                resp = client.post(
                    _OPENAI_URL,
                    headers={"Authorization": f"Bearer {chave}"},
                    data=data,
                    files=arquivos,
                )
    except httpx.HTTPError as e:
        raise TranscricaoError(f"Falha de conexão com a OpenAI: {type(e).__name__}: {e}") from e

    if resp.status_code != 200:
        # Mensagem da API sem a chave
        raise TranscricaoError(
            f"Erro na API da OpenAI (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    dados = resp.json()
    idioma_detectado = dados.get("language")
    trechos: list[Trecho] = []
    for seg in dados.get("segments", []):
        trechos.append((
            float(seg.get("start", 0.0)),
            float(seg.get("end", 0.0)),
            (seg.get("text") or "").strip(),
        ))
    # Sem segments (formato inesperado): cai para o texto plano num único trecho
    if not trechos and dados.get("text"):
        trechos.append((0.0, 0.0, dados["text"].strip()))
    return trechos, idioma_detectado


def _transcrever_openai(audio: Path, idioma: Optional[str],
                        modelo: str) -> tuple[list[Trecho], Optional[str]]:
    import llm
    chave = llm._ler_chave("openai")
    if not chave:
        raise TranscricaoError(
            "Chave da OpenAI ausente. Configure em Configurações > Chave de API."
        )
    modelo = modelo or "whisper-1"

    dur_total = _duracao_segundos(audio)
    todos: list[Trecho] = []
    idioma_detectado: Optional[str] = None

    with tempfile.TemporaryDirectory(prefix="transc-") as tmp:
        tmp_dir = Path(tmp)
        # Se não conseguimos medir a duração, tenta o arquivo inteiro num bloco.
        if dur_total <= 0 or dur_total <= _BLOCO_SEGUNDOS:
            trechos, idi = _openai_um_bloco(audio, idioma, modelo, chave)
            return trechos, idi

        inicio = 0.0
        while inicio < dur_total:
            dur = min(_BLOCO_SEGUNDOS, dur_total - inicio)
            bloco = tmp_dir / f"bloco-{int(inicio)}.wav"
            if not _cortar_bloco(audio, inicio, dur, bloco):
                raise TranscricaoError(
                    f"Falha ao cortar bloco de áudio em {inicio:.0f}s (ffmpeg)."
                )
            trechos, idi = _openai_um_bloco(bloco, idioma, modelo, chave)
            if idioma_detectado is None:
                idioma_detectado = idi
            # Reposiciona os tempos do bloco na linha do tempo completa
            todos.extend((s + inicio, e + inicio, t) for (s, e, t) in trechos)
            inicio += _BLOCO_SEGUNDOS

    return todos, idioma_detectado


# ────────────────────────────────────────────────────────────────────────────
# Despacho
# ────────────────────────────────────────────────────────────────────────────

def transcrever(audio: Path, idioma: Optional[str], modelo: str,
                provider: str = "local") -> tuple[list[Trecho], Optional[str]]:
    """Transcreve audio com o provedor indicado.

    idioma=None → detecção automática. Retorna (trechos, idioma_detectado),
    trechos = list[(start, end, text)] em segundos.
    """
    if provider == "openai":
        return _transcrever_openai(audio, idioma, modelo)
    if provider in ("local", "", None):
        return _transcrever_local(audio, idioma, modelo)
    raise TranscricaoError(f"Provedor de transcrição desconhecido: '{provider}'.")
