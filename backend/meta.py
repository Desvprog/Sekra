#!/usr/bin/env python3
"""
Metadados estruturados por reunião, em meta.json dentro da pasta da reunião.

Substitui o parsing frágil do nome da pasta e guarda informação que não dá
para inferir do filesystem: idioma detectado, nº de speakers, nomes reais dos
speakers (renomeados pelo usuário), duração, status e flags de conteúdo.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

META_FILE = "meta.json"


def caminho(pasta: Path) -> Path:
    return pasta / META_FILE


def ler(pasta: Path) -> dict:
    """Retorna o meta.json da pasta ou {} se ausente/inválido."""
    p = caminho(pasta)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def escrever(pasta: Path, **campos) -> dict:
    """Faz merge dos campos informados no meta.json existente e persiste."""
    dados = ler(pasta)
    dados.update({k: v for k, v in campos.items() if v is not None})
    caminho(pasta).write_text(
        json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dados


def duracao_audio(path: Path) -> Optional[float]:
    """Duração do áudio em segundos via ffprobe, ou None se indisponível."""
    if not path.exists():
        return None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(r.stdout.strip()), 1)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def fmt_duracao(segundos: Optional[float]) -> str:
    """Formata duração para exibição curta: '7min', '1h12'."""
    if not segundos:
        return ""
    total = int(segundos)
    h, resto = divmod(total, 3600)
    m, _ = divmod(resto, 60)
    if h:
        return f"{h}h{m:02d}"
    return f"{m}min" if m else f"{total}s"
