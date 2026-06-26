"""
resumo.py — Geração de ata/resumo estruturado de reunião via LLM plugável.

Lê a transcrição de uma reunião (transcricao.txt) e produz um arquivo
markdown (resumo.md) com seções padronizadas: resumo executivo, decisões,
pendências/próximos passos e tópicos discutidos.

Depende do módulo llm (llm.py) já presente no backend.
"""

from pathlib import Path
from typing import Optional

import llm


# ---------------------------------------------------------------------------
# Prompt do sistema — papel de secretário executivo de atas
# ---------------------------------------------------------------------------

PROMPT_SYSTEM = (
    "Você é um secretário executivo especializado em elaboração de atas de reunião. "
    "Redija documentos claros, objetivos e acionáveis em português do Brasil. "
    "Seja fiel ao conteúdo da transcrição: não invente fatos, decisões nem compromissos "
    "que não tenham sido mencionados. "
    "Preserve com exatidão os nomes de pessoas, projetos e sistemas citados. "
    "Seja conciso — evite repetir a transcrição literal ou incluir detalhes irrelevantes."
)


# ---------------------------------------------------------------------------
# Funções públicas
# ---------------------------------------------------------------------------

def gerar_resumo(transcricao_texto: str, titulo: str) -> str:
    """Monta o prompt e chama llm.gerar(). Retorna o markdown do resumo.

    Levanta llm.LLMError se o LLM estiver indisponível ou falhar.
    """
    # Verifica disponibilidade antes de montar o prompt completo
    if not llm.disponivel():
        motivo = llm.info().get("motivo", "LLM indisponível")
        raise llm.LLMError(motivo)

    prompt = (
        f"Reunião: {titulo}\n\n"
        "Abaixo está a transcrição completa da reunião. "
        "Produza uma ata em markdown com EXATAMENTE as seguintes seções e headings:\n\n"
        "## Resumo executivo\n"
        "De 3 a 6 bullets resumindo os pontos centrais da reunião.\n\n"
        "## Decisões\n"
        "Liste as decisões tomadas durante a reunião. "
        "Se nenhuma decisão explícita foi tomada, escreva apenas: _Nenhuma decisão explícita._\n\n"
        "## Pendências e próximos passos\n"
        "Liste os itens de ação identificados. "
        "Quando possível, indique o responsável entre parênteses e o prazo mencionado.\n\n"
        "## Tópicos discutidos\n"
        "Lista dos principais temas abordados na reunião.\n\n"
        "NÃO repita trechos literais da transcrição. Seja conciso e direto.\n\n"
        "---\n"
        "TRANSCRIÇÃO:\n\n"
        f"{transcricao_texto}"
    )

    return llm.gerar(prompt, system=PROMPT_SYSTEM, max_tokens=4000)


def gerar_e_salvar(pasta: Path) -> Optional[Path]:
    """Lê pasta/transcricao.txt; se não existir, retorna None.

    Gera o resumo via LLM e grava em pasta/resumo.md.
    Retorna o Path do resumo.md criado.
    Propaga llm.LLMError se o LLM estiver indisponível ou falhar.
    """
    arquivo_transcricao = pasta / "transcricao.txt"

    # Se a transcrição não existir, não há o que resumir
    if not arquivo_transcricao.exists():
        return None

    texto = arquivo_transcricao.read_text(encoding="utf-8")

    # Extrai o título da primeira linha (formato: # titulo-slug)
    titulo = pasta.name  # fallback: nome da pasta
    for linha in texto.splitlines():
        linha = linha.strip()
        if linha.startswith("#") and not linha.startswith("##"):
            titulo = linha.lstrip("#").strip()
            break

    # Gera o resumo — propaga LLMError ao chamador
    markdown = gerar_resumo(texto, titulo)

    # Grava o resultado
    arquivo_resumo = pasta / "resumo.md"
    arquivo_resumo.write_text(markdown, encoding="utf-8")

    return arquivo_resumo
