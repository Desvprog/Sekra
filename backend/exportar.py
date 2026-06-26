"""
exportar.py — Exporta uma reunião (transcrição + resumo + hotwords) como
Markdown bem formatado para um diretório configurável (ex.: vault Obsidian).

Uso típico:
    from exportar import exportar
    caminho = exportar(pasta, reuniao_id, destino_dir)
"""

import re
from pathlib import Path
from typing import Optional

# Importa meta com fallback para robustez
try:
    import meta as _meta
    _ler_meta = _meta.ler
    _fmt_duracao = _meta.fmt_duracao
except ImportError:  # pragma: no cover
    def _ler_meta(pasta: Path) -> dict:  # type: ignore
        import json
        p = pasta / "meta.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _fmt_duracao(segundos) -> str:  # type: ignore
        if not segundos:
            return ""
        total = int(segundos)
        h, resto = divmod(total, 3600)
        m, _ = divmod(resto, 60)
        if h:
            return f"{h}h{m:02d}"
        return f"{m}min" if m else f"{total}s"


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _yaml_str(valor: str) -> str:
    """Escapa uma string YAML simples: envolve em aspas se necessário."""
    if not isinstance(valor, str):
        valor = str(valor)
    # Envolve em aspas duplas se contiver caracteres problemáticos
    if any(c in valor for c in (':', '"', "'", '#', '{', '}', '[', ']', '&', '*', '!', '|', '>', '%', '@', '`', '\n')):
        # Escapa aspas duplas internas
        escaped = valor.replace('"', '\\"')
        return f'"{escaped}"'
    return valor if valor else '""'


def _yaml_lista(itens) -> str:
    """Monta lista YAML inline: [a, b, c]."""
    if not itens:
        return "[]"
    partes = [_yaml_str(str(i)) for i in itens]
    return "[" + ", ".join(partes) + "]"


def _aplicar_speaker_nomes(texto: str, speaker_nomes: dict) -> str:
    """Substitui rótulos de speakers no texto conforme o dicionário de renomeação.

    Substitui:
    - **Pessoa X** -> **Nome Real**  (na transcrição)
    - — Pessoa X   -> — Nome Real    (em linhas de hotwords com atribuição)
    - ) — Pessoa X  no fim de item de hotword
    """
    if not speaker_nomes:
        return texto
    for original, novo in speaker_nomes.items():
        # Substituição em negrito: **Pessoa X**
        padrao_negrito = re.compile(
            r'\*\*' + re.escape(original) + r'\*\*'
        )
        texto = padrao_negrito.sub(f"**{novo}**", texto)
        # Substituição em linha de atribuição: — Pessoa X (no final de linha ou seguido de quebra)
        padrao_dash = re.compile(
            r'—\s*' + re.escape(original) + r'(?=\s*$|\s*\n)',
            re.MULTILINE
        )
        texto = padrao_dash.sub(f"— {novo}", texto)
    return texto


def _ler_opcional(pasta: Path, nome: str) -> Optional[str]:
    """Lê um arquivo opcional; retorna None se não existir."""
    p = pasta / nome
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return None
    return None


def _slug_titulo(pasta: Path, dados_meta: dict) -> str:
    """Extrai um título/slug legível: usa meta.titulo ou o nome da pasta."""
    titulo = dados_meta.get("titulo", "")
    if titulo:
        return titulo
    # Fallback: nome da pasta (ex.: "11-00-reuniao-fmo-25-06-warup")
    nome_pasta = pasta.name
    # Remove prefixo HH-MM- se existir
    nome_limpo = re.sub(r'^\d{2}-\d{2}-', '', nome_pasta)
    # Substitui hífens por espaços para leitura
    return nome_limpo.replace("-", " ")


def _extrair_data_hora_do_id(reuniao_id: str) -> tuple[str, str]:
    """Extrai data e hora do reuniao_id no formato 'AAAA-MM-DD/HH-MM-slug'."""
    partes = reuniao_id.split("/")
    data = partes[0] if partes else ""
    hora = ""
    if len(partes) > 1:
        slug = partes[1]
        # Pega os dois primeiros segmentos separados por '-' como HH-MM
        m = re.match(r'^(\d{2})-(\d{2})', slug)
        if m:
            hora = f"{m.group(1)}:{m.group(2)}"
    return data, hora


def _montar_frontmatter(dados_meta: dict, reuniao_id: str, titulo: str) -> str:
    """Monta o bloco frontmatter YAML."""
    data, hora = _extrair_data_hora_do_id(reuniao_id)

    # Duração formatada
    duracao_s = dados_meta.get("duracao_s")
    duracao = _fmt_duracao(duracao_s) if duracao_s else dados_meta.get("duracao", "")

    # Modelo e idioma
    modelo = dados_meta.get("modelo", "")
    idioma = dados_meta.get("idioma", "")

    # Speakers: lista final (aplicando renomeações se existirem)
    speakers_raw = dados_meta.get("speakers", [])
    speaker_nomes = dados_meta.get("speaker_nomes") or {}
    speakers = [speaker_nomes.get(s, s) for s in speakers_raw] if speakers_raw else []

    linhas = ["---"]
    linhas.append(f"titulo: {_yaml_str(titulo)}")
    if data:
        linhas.append(f"data: {_yaml_str(data)}")
    if hora:
        linhas.append(f"hora: {_yaml_str(hora)}")
    if duracao:
        linhas.append(f"duracao: {_yaml_str(duracao)}")
    if modelo:
        linhas.append(f"modelo: {_yaml_str(modelo)}")
    if idioma:
        linhas.append(f"idioma: {_yaml_str(idioma)}")
    if speakers:
        linhas.append(f"speakers: {_yaml_lista(speakers)}")
    linhas.append('tags: [reuniao]')
    linhas.append('origem: sekra')
    linhas.append(f"id: {_yaml_str(reuniao_id)}")
    linhas.append("---")
    return "\n".join(linhas)


def _montar_secao_resumo(resumo_txt: Optional[str]) -> str:
    """Monta a seção ## Resumo, ou string vazia se não houver conteúdo."""
    if not resumo_txt or not resumo_txt.strip():
        return ""
    conteudo = resumo_txt.strip()
    return f"\n## Resumo\n\n{conteudo}\n"


def _montar_secao_hotwords(hotwords_txt: Optional[str], speaker_nomes: dict) -> str:
    """Monta a seção ## Hotwords removendo o cabeçalho original; retorna '' se vazio."""
    if not hotwords_txt or not hotwords_txt.strip():
        return ""
    # Remove o primeiro cabeçalho # (ex.: "# Hotwords detectadas")
    linhas = hotwords_txt.split("\n")
    linhas_filtradas = []
    removeu_cabecalho = False
    for linha in linhas:
        if not removeu_cabecalho and linha.startswith("# "):
            removeu_cabecalho = True
            continue
        linhas_filtradas.append(linha)
    conteudo = "\n".join(linhas_filtradas).strip()
    if not conteudo:
        return ""
    # Aplica renomeação de speakers nas hotwords
    conteudo = _aplicar_speaker_nomes(conteudo, speaker_nomes)
    return f"\n## Hotwords\n\n{conteudo}\n"


def _montar_secao_transcricao(transcricao_txt: Optional[str], speaker_nomes: dict) -> str:
    """Monta a seção ## Transcrição com conteúdo processado."""
    if not transcricao_txt or not transcricao_txt.strip():
        return "\n## Transcrição\n\n_Sem transcrição._\n"
    # Aplica renomeação de speakers
    conteudo = _aplicar_speaker_nomes(transcricao_txt.strip(), speaker_nomes)
    return f"\n## Transcrição\n\n{conteudo}\n"


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def montar_markdown(pasta: Path, reuniao_id: str) -> str:
    """Monta o conteúdo Markdown completo de uma reunião.

    Seções:
    - Frontmatter YAML com titulo, data, hora, duracao, modelo, idioma, speakers,
      tags: [reuniao], origem: sekra, id.
    - # Título
    - ## Resumo  (se resumo.md existir)
    - ## Hotwords (se hotwords.md existir, sem o cabeçalho original)
    - ## Transcrição  (transcricao.txt convertido; ou nota '_Sem transcrição._')

    Aplica speaker_nomes (do meta.json) substituindo rótulos na transcrição e hotwords.
    """
    dados_meta = _ler_meta(pasta)
    speaker_nomes: dict = dados_meta.get("speaker_nomes") or {}
    titulo = _slug_titulo(pasta, dados_meta)

    # Lê arquivos opcionais
    resumo_txt = _ler_opcional(pasta, "resumo.md")
    hotwords_txt = _ler_opcional(pasta, "hotwords.md")
    transcricao_txt = _ler_opcional(pasta, "transcricao.txt")

    # Monta blocos
    frontmatter = _montar_frontmatter(dados_meta, reuniao_id, titulo)
    secao_resumo = _montar_secao_resumo(resumo_txt)
    secao_hotwords = _montar_secao_hotwords(hotwords_txt, speaker_nomes)
    secao_transcricao = _montar_secao_transcricao(transcricao_txt, speaker_nomes)

    # Título principal
    cabecalho = f"\n# {titulo}"

    partes = [frontmatter, cabecalho]
    if secao_resumo:
        partes.append(secao_resumo)
    if secao_hotwords:
        partes.append(secao_hotwords)
    partes.append(secao_transcricao)

    return "\n".join(partes)


def slug_arquivo(pasta: Path, reuniao_id: str) -> str:
    """Nome do arquivo .md de saída.

    Formato: "AAAA-MM-DD título-legível.md"
    Ex.: "2026-06-25 reuniao-fmo-warup.md"
    """
    data, _ = _extrair_data_hora_do_id(reuniao_id)

    # Título legível: usa o slug da pasta (sem prefixo HH-MM-)
    nome_pasta = pasta.name
    slug = re.sub(r'^\d{2}-\d{2}-', '', nome_pasta)

    if data:
        return f"{data} {slug}.md"
    return f"{slug}.md"


def exportar(pasta: Path, reuniao_id: str, destino_dir: Path) -> Path:
    """Escreve o Markdown da reunião em destino_dir.

    Cria destino_dir se não existir.
    Levanta ValueError se destino_dir for None ou vazio.
    Retorna o Path do arquivo escrito.
    """
    if not destino_dir:
        raise ValueError("destino_dir não pode ser vazio ou None.")

    destino_dir = Path(destino_dir)
    destino_dir.mkdir(parents=True, exist_ok=True)

    nome = slug_arquivo(pasta, reuniao_id)
    caminho_saida = destino_dir / nome

    conteudo = montar_markdown(pasta, reuniao_id)
    caminho_saida.write_text(conteudo, encoding="utf-8")

    return caminho_saida
