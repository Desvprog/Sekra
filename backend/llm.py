"""
Módulo de LLM plugável para geração de resumos de reuniões.

Suporta três provedores: Claude (padrão, via SDK oficial), OpenAI e Gemini
(ambos via httpx REST). O provedor e modelo são lidos do config do usuário.
Chaves de API nunca são logadas; lidas de variável de ambiente ou arquivo
em ~/.config/reunioes/<provider>_key.

Funções públicas: info(), disponivel(), gerar().
"""

import os
from pathlib import Path
from typing import Optional

import config


class LLMError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

_CHAVE_ENV = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_CHAVE_ARQUIVO = {
    "claude": "anthropic_key",
    "openai": "openai_key",
    "gemini": "gemini_key",
}

_CONFIG_DIR = Path.home() / ".config" / "reunioes"


def _ler_chave(provider: str) -> Optional[str]:
    """Retorna a chave de API para o provedor, sem logar.

    Precedência: variável de ambiente > arquivo em ~/.config/reunioes/.
    Retorna None se nenhuma fonte estiver disponível.
    """
    # 1. variável de ambiente
    chave = os.environ.get(_CHAVE_ENV.get(provider, ""))
    if chave and chave.strip():
        return chave.strip()

    # 2. arquivo dedicado
    nome_arquivo = _CHAVE_ARQUIVO.get(provider)
    if nome_arquivo:
        caminho = _CONFIG_DIR / nome_arquivo
        try:
            conteudo = caminho.read_text(encoding="utf-8").strip()
            if conteudo:
                return conteudo
        except (OSError, PermissionError):
            pass

    return None


def _cfg_llm() -> dict:
    """Retorna o sub-dict llm do config."""
    return config.carregar().get("llm", {})


# ---------------------------------------------------------------------------
# Contrato público
# ---------------------------------------------------------------------------

def info() -> dict:
    """Retorna metadados sobre o provedor configurado.

    Não realiza chamadas de rede — apenas lê config e presença de chave.

    Retorno: {"provider": str, "modelo": str, "disponivel": bool, "motivo": str}
    """
    cfg = _cfg_llm()
    provider = cfg.get("provider", "none")
    modelo = cfg.get("modelo", "")

    if provider == "none":
        return {
            "provider": "none",
            "modelo": modelo,
            "disponivel": False,
            "motivo": "Provedor configurado como 'none'.",
        }

    if provider not in _CHAVE_ENV:
        return {
            "provider": provider,
            "modelo": modelo,
            "disponivel": False,
            "motivo": f"Provedor desconhecido: '{provider}'.",
        }

    chave = _ler_chave(provider)
    if not chave:
        env_var = _CHAVE_ENV[provider]
        arquivo = _CHAVE_ARQUIVO[provider]
        return {
            "provider": provider,
            "modelo": modelo,
            "disponivel": False,
            "motivo": (
                f"Chave de API ausente para '{provider}'. "
                f"Defina {env_var} ou crie ~/.config/reunioes/{arquivo}."
            ),
        }

    return {
        "provider": provider,
        "modelo": modelo,
        "disponivel": True,
        "motivo": "",
    }


def disponivel() -> bool:
    """True se há provedor configurado com chave de API presente."""
    return info()["disponivel"]


def gerar(prompt: str, system: str = "", max_tokens: int = 8000) -> str:
    """Chama o provedor configurado e retorna o texto da resposta.

    Levanta LLMError se indisponível ou em caso de falha de API.
    A chave de API nunca é incluída na mensagem de erro.
    """
    dados = info()
    if not dados["disponivel"]:
        raise LLMError(f"LLM indisponível: {dados['motivo']}")

    provider = dados["provider"]
    modelo = dados["modelo"]
    chave = _ler_chave(provider)  # já validada por info()

    if provider == "claude":
        return _chamar_claude(prompt, system, max_tokens, modelo, chave)
    elif provider == "openai":
        return _chamar_openai(prompt, system, max_tokens, modelo, chave)
    elif provider == "gemini":
        return _chamar_gemini(prompt, system, max_tokens, modelo, chave)
    else:
        raise LLMError(f"Provedor não suportado: '{provider}'.")


# ---------------------------------------------------------------------------
# Implementações por provedor
# ---------------------------------------------------------------------------

def _chamar_claude(
    prompt: str,
    system: str,
    max_tokens: int,
    modelo: str,
    chave: str,
) -> str:
    """Chama a API do Claude via SDK oficial anthropic."""
    try:
        import anthropic
    except ImportError:
        raise LLMError("SDK anthropic não instalado: pip install anthropic")

    try:
        client = anthropic.Anthropic(api_key=chave)
        resp = client.messages.create(
            model=modelo,
            max_tokens=max_tokens,
            system=system or None,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = "".join(b.text for b in resp.content if b.type == "text")
        return texto
    except anthropic.APIError as e:
        # Mensagem da API sem incluir a chave
        raise LLMError(f"Erro na API do Claude: {e.message if hasattr(e, 'message') else str(e)}") from e
    except Exception as e:
        raise LLMError(f"Falha ao chamar o Claude: {type(e).__name__}: {e}") from e


def _chamar_openai(
    prompt: str,
    system: str,
    max_tokens: int,
    modelo: str,
    chave: str,
) -> str:
    """Chama a API do OpenAI via httpx REST."""
    import httpx

    mensagens = []
    if system:
        mensagens.append({"role": "system", "content": system})
    mensagens.append({"role": "user", "content": prompt})

    payload = {
        "model": modelo,
        "max_tokens": max_tokens,
        "messages": mensagens,
    }

    try:
        with httpx.Client(timeout=120) as client:
            resposta = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {chave}"},
                json=payload,
            )
        if resposta.status_code != 200:
            raise LLMError(
                f"Erro na API do OpenAI (HTTP {resposta.status_code}): "
                f"{resposta.text[:300]}"
            )
        data = resposta.json()
        return data["choices"][0]["message"]["content"]
    except LLMError:
        raise
    except httpx.HTTPError as e:
        raise LLMError(f"Falha de conexão com o OpenAI: {type(e).__name__}: {e}") from e
    except Exception as e:
        raise LLMError(f"Falha ao chamar o OpenAI: {type(e).__name__}: {e}") from e


def _chamar_gemini(
    prompt: str,
    system: str,
    max_tokens: int,
    modelo: str,
    chave: str,
) -> str:
    """Chama a API do Gemini via httpx REST."""
    import httpx

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{modelo}:generateContent?key={chave}"
    )

    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}

    try:
        with httpx.Client(timeout=120) as client:
            resposta = client.post(url, json=payload)
        if resposta.status_code != 200:
            raise LLMError(
                f"Erro na API do Gemini (HTTP {resposta.status_code}): "
                f"{resposta.text[:300]}"
            )
        data = resposta.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except LLMError:
        raise
    except httpx.HTTPError as e:
        raise LLMError(f"Falha de conexão com o Gemini: {type(e).__name__}: {e}") from e
    except Exception as e:
        raise LLMError(f"Falha ao chamar o Gemini: {type(e).__name__}: {e}") from e
