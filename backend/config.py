#!/usr/bin/env python3
"""
Configurações persistentes do app, em ~/.config/reunioes/config.json.

Centraliza preferências do usuário (idioma, modelo padrão, compressão,
diretório de export e provedor de LLM). Chaves de API NUNCA são exigidas
aqui: são lidas de variável de ambiente ou de arquivo dedicado por provedor
(ver llm.py), para não vazarem em backups do config.
"""

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "reunioes"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Valores padrão. Qualquer chave ausente no arquivo cai para cá.
PADROES: dict[str, Any] = {
    "idioma": "auto",            # "auto" deixa o Whisper detectar; ou "pt", "en", ...
    "modelo_padrao": "medium",   # small | medium | large-v3
    "hotwords_padrao": [],       # aplicadas automaticamente em toda reunião
    "comprimir_audio": True,     # converte WAV -> Opus após processar
    "bitrate_opus": "32k",       # 32k é ótimo para voz; reduz ~40x vs WAV PCM
    "resumo_automatico": False,  # gera resumo via LLM ao final do processamento
    "export_dir": "",            # destino do export Markdown (ex.: vault Obsidian)
    "clientes": [],               # nomes de clientes p/ seletor no início da gravação
    "valores_hora": {},           # mapa cliente -> valor/hora, p/ cálculo no relatório
    "llm": {
        "provider": "claude",    # claude | openai | gemini | none
        # Padrão claude-opus-4-8 (mais capaz). Troque por claude-sonnet-4-6 /
        # claude-haiku-4-5 nas configurações se quiser reduzir custo.
        "modelo": "claude-opus-4-8",
    },
    "deteccao": {
        "ativa": True,           # monitora o PulseAudio em busca de reuniões
        "auto_iniciar": False,   # inicia gravação sozinho ao detectar
        # Termos casados (substring, case-insensitive) contra o nome/binário
        # do app que abriu o microfone
        "apps": ["chrome", "chromium", "firefox", "brave", "edge", "opera",
                 "vivaldi", "teams", "zoom", "discord", "slack", "skype"],
    },
}


def _merge(base: dict, extra: dict) -> dict:
    """Merge raso com um nível de profundidade para o sub-dict 'llm'."""
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def carregar() -> dict:
    """Lê o config do disco, preenchendo padrões para chaves ausentes."""
    if not CONFIG_FILE.exists():
        return json.loads(json.dumps(PADROES))  # cópia profunda
    try:
        dados = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(PADROES))
    return _merge(PADROES, dados if isinstance(dados, dict) else {})


def salvar(patch: dict) -> dict:
    """Aplica um patch parcial sobre o config atual e persiste. Retorna o config final."""
    atual = carregar()
    novo = _merge(atual, patch)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(novo, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return novo


def get(chave: str, default: Any = None) -> Any:
    return carregar().get(chave, default)
