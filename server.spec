# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec para empacotar server.py + reuniao.py + static/.
Execute: pyinstaller server.spec
Gera: dist/server/  (diretório, não onefile — mais rápido para carregar ML)
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH)

# Coleta dependências do pyannote/faster-whisper de forma robusta, com fallback seguro.
# Se algum pacote não estiver instalado no ambiente de build, o bloco é ignorado.
from PyInstaller.utils.hooks import collect_all, collect_submodules
hidden_extra = []
datas_extra = []
binaries_extra = []
# faster_whisper carrega assets por caminho relativo (ex.: silero_vad_v6.onnx do
# filtro VAD). collect_all traz esses dados; sem isso o import funciona mas o
# arquivo some do bundle e só quebra em runtime ao chamar transcribe(vad_filter=True).
for _pkg in ("faster_whisper", "ctranslate2",
             "pyannote", "pyannote.audio", "lightning_fabric", "speechbrain", "asteroid_filterbanks"):
    try:
        d, b, h = collect_all(_pkg)
        datas_extra += d; binaries_extra += b; hidden_extra += h
    except Exception:
        pass

a = Analysis(
    [str(ROOT / "backend" / "server.py")],
    pathex=[str(ROOT / "backend")],
    binaries=binaries_extra,
    datas=[
        (str(ROOT / "static"), "static"),
        (str(ROOT / "backend" / "reuniao.py"), "."),
        (str(ROOT / "backend" / "config.py"), "."),
        (str(ROOT / "backend" / "meta.py"), "."),
        (str(ROOT / "backend" / "llm.py"), "."),
        (str(ROOT / "backend" / "resumo.py"), "."),
        (str(ROOT / "backend" / "busca.py"), "."),
        (str(ROOT / "backend" / "exportar.py"), "."),
        (str(ROOT / "backend" / "transcricao.py"), "."),
        (str(ROOT / "backend" / "monitor.py"), "."),
        (str(ROOT / "backend" / "relatorio.py"), "."),
        (str(ROOT / "backend" / "lembretes.py"), "."),
        (str(ROOT / "backend" / "sync.py"), "."),
        (str(ROOT / "backend" / "horas.py"), "."),
        (str(ROOT / "backend" / "dashboard.py"), "."),
    ] + datas_extra,
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "fastapi.staticfiles",
        "starlette",
        "starlette.staticfiles",
        "anyio",
        "anyio._backends._asyncio",
        "asyncio",
        # Novos módulos backend
        "config",
        "meta",
        "llm",
        "resumo",
        "busca",
        "exportar",
        "transcricao",
        "monitor",
        "relatorio",
        "lembretes",
        "sync",
        "horas",
        "dashboard",
        # Dependências LLM
        "anthropic",
        "httpx",
    ] + hidden_extra,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # sem janela de terminal
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="server",
)
