"""
busca.py — Busca full-text em transcrições de reuniões salvas em disco.

Não requer dependências externas; usa apenas stdlib (pathlib, unicodedata, re).
"""

import unicodedata
import re
from pathlib import Path


def _normalizar(s: str) -> str:
    """Converte para minúsculas e remove acentos/marcas diacríticas."""
    nfkd = unicodedata.normalize("NFKD", s)
    sem_acentos = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return sem_acentos.lower()


_RE_TIMESTAMP = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)")
_RE_SPEAKER = re.compile(r"^\*\*(.+)\*\*\s*$")
_RE_HEADER = re.compile(r"^#\s")


def _parse_subpasta(nome: str) -> tuple[str, str]:
    """
    Deriva (hora, titulo) do nome da subpasta.
    Formato esperado: HH-MM-resto-do-slug
    Retorna hora no formato 'HH:MM' e titulo com hífens trocados por espaço.
    """
    partes = nome.split("-")
    if len(partes) >= 3:
        hora = f"{partes[0]}:{partes[1]}"
        titulo = " ".join(partes[2:])
    elif len(partes) == 2:
        hora = f"{partes[0]}:{partes[1]}"
        titulo = nome
    else:
        hora = "??:??"
        titulo = nome
    return hora, titulo


def _buscar_em_transcricao(
    transcricao: Path,
    query_norm: str,
    max_trechos: int,
) -> tuple[int, list[dict]]:
    """
    Lê um arquivo transcricao.txt e retorna (total_ocorrencias, trechos).
    Cada trecho é {'timestamp': str, 'speaker': str, 'texto': str}.
    """
    total = 0
    trechos: list[dict] = []
    speaker_atual = ""

    try:
        texto = transcricao.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0, []

    for linha in texto.splitlines():
        linha = linha.rstrip()

        # Ignora linhas de cabeçalho (começam com "# ")
        if _RE_HEADER.match(linha):
            continue

        # Detecta speaker
        m_speaker = _RE_SPEAKER.match(linha)
        if m_speaker:
            speaker_atual = m_speaker.group(1).strip()
            continue

        # Detecta fala com timestamp
        m_ts = _RE_TIMESTAMP.match(linha)
        if m_ts:
            timestamp = m_ts.group(1)
            fala = m_ts.group(2)

            if query_norm in _normalizar(fala):
                total += 1
                if len(trechos) < max_trechos:
                    trechos.append({
                        "timestamp": timestamp,
                        "speaker": speaker_atual,
                        "texto": fala,
                    })

    return total, trechos


def buscar(
    query: str,
    base_dir: Path,
    limite_reunioes: int = 50,
    max_trechos_por_reuniao: int = 5,
) -> list[dict]:
    """
    Varre transcricao.txt de todas as reuniões sob base_dir e retorna as que
    contêm `query` (case-insensitive, ignorando acentos). Retorno:
    [
      {
        "id": "AAAA-MM-DD/HH-MM-slug",
        "data": "AAAA-MM-DD",
        "hora": "HH:MM",
        "titulo": "titulo legível",
        "total_ocorrencias": int,
        "trechos": [
          {"timestamp": "MM:SS", "speaker": "Pessoa 3", "texto": "linha da fala"}
        ]
      }, ...
    ]
    Ordenado por total_ocorrencias desc. Limita a `limite_reunioes` reuniões e
    `max_trechos_por_reuniao` trechos por reunião.
    """
    query_norm = _normalizar(query)
    resultados: list[dict] = []

    if not base_dir.is_dir():
        return []

    # Pastas-dia em ordem reversa (mais recentes primeiro)
    pastas_dia = sorted(
        (p for p in base_dir.iterdir() if p.is_dir()),
        reverse=True,
    )

    for pasta_dia in pastas_dia:
        data = pasta_dia.name

        # Subpastas de reunião dentro da pasta-dia
        subpastas = sorted(
            (s for s in pasta_dia.iterdir() if s.is_dir()),
            reverse=True,
        )

        for subpasta in subpastas:
            transcricao = subpasta / "transcricao.txt"
            if not transcricao.exists():
                continue

            hora, titulo = _parse_subpasta(subpasta.name)

            try:
                total, trechos = _buscar_em_transcricao(
                    transcricao, query_norm, max_trechos_por_reuniao
                )
            except Exception:
                continue

            if total == 0:
                continue

            reuniao_id = f"{data}/{subpasta.name}"
            resultados.append({
                "id": reuniao_id,
                "data": data,
                "hora": hora,
                "titulo": titulo,
                "total_ocorrencias": total,
                "trechos": trechos,
            })

    # Ordena por total_ocorrencias desc; desempate por data desc (já embutida no id)
    resultados.sort(key=lambda r: (r["total_ocorrencias"], r["data"]), reverse=True)

    return resultados[:limite_reunioes]
