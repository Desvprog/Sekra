#!/usr/bin/env python3
"""
Sincronização opcional de lembretes e timetracking (clientes/projetos/
apontamentos) com Supabase, via PostgREST puro (httpx). Também sincroniza
(push-only) reuniões marcadas individualmente pelo usuário.

Desativada por padrão (config["sync"]["ativo"] = False) — o app funciona
100% offline sem isso. Quando ativada, faz merge last-write-wins (LWW) por
`atualizado_em`, respeitando tombstones (deletado_em) para propagar exclusões.
Cada tabela é sincronizada de forma independente (mesmo algoritmo,
generalizado em `_sincronizar_tabela`); o resultado agregado soma
enviados/recebidos de todas as tabelas.

Controle de escopo (config["sync"]["modo"]):
  - "tudo" (padrão): envia todos os registros das 4 tabelas, ignorando a
    flag `sync_habilitado` — é o comportamento histórico, preservado.
  - "selecionados": só envia (push) registros com `sync_habilitado`=True.
    O pull/merge das 4 tabelas continua idêntico em ambos os modos — a flag
    só afeta o que ESTE app envia, nunca o que ele recebe.

Reuniões (função `_sincronizar_reunioes`): diferente das 4 tabelas acima,
reuniões vivem no filesystem (meta.json por pasta), não no SQLite, e o sync
aqui é estritamente PUSH-ONLY — não há pull nem merge, a fonte de verdade é
sempre o filesystem local. Apenas reuniões com `sync_habilitado`=True no
meta.json são enviadas, e nunca o áudio: só metadados + transcrição + resumo
(quando os arquivos existem). Isso é opt-in duplo: exige tanto
config["sync"]["ativo"]=True quanto a marcação individual da reunião.

Chave de API: mesmo padrão de llm.py — variável de ambiente > arquivo
~/.config/reunioes/supabase_key (0600). Nunca logada nem devolvida.

Qualquer chamada de rede é lazy (import httpx dentro da função) e nunca deve
propagar exceção não tratada para o caller HTTP: falhas viram
{"ok": False, "erro": "..."}.
"""

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import config
import lembretes
import horas
import meta
import reuniao

_CONFIG_DIR = Path.home() / ".config" / "reunioes"
_CHAVE_ARQUIVO = "supabase_key"
_CHAVE_ENV = "SUPABASE_KEY"

# Tabelas sincronizadas: (nome_tabela_postgrest, listar_incluindo_deletados, upsert_bruto).
# Adicionar uma tabela nova é só acrescentar uma tupla aqui.
_TABELAS: list[tuple[str, Callable[[], list[dict]], Callable[[dict], bool]]] = [
    ("lembretes", lembretes.todos_incluindo_deletados, lembretes.upsert_bruto),
    ("clientes", horas.clientes_todos_incluindo_deletados, horas.clientes_upsert_bruto),
    ("projetos", horas.projetos_todos_incluindo_deletados, horas.projetos_upsert_bruto),
    ("apontamentos", horas.apontamentos_todos_incluindo_deletados, horas.apontamentos_upsert_bruto),
]

_TABELA_REUNIOES = "reunioes"

# Colunas local-only nunca enviadas ao Supabase (não existem no schema remoto).
_COLUNAS_NAO_ENVIAR = {"sync_habilitado", "notificado_nivel", "notificado_em"}

# Estado do último sync, mantido em memória (não persiste entre reinícios).
_ultimo_sync: dict = {"quando": None, "resultado": None}
_lock = threading.Lock()


class SyncError(Exception):
    pass


def _int_ou_none(valor) -> Optional[int]:
    """Coerção segura para coluna integer do Supabase. None/inválido -> None."""
    if valor is None:
        return None
    try:
        return round(float(valor))
    except (TypeError, ValueError):
        return None


def _detalhe_erro(resp) -> str:
    """Extrai a mensagem de erro do PostgREST (campo `message`/`hint`) ou o
    corpo bruto truncado, para diagnóstico. O corpo do Supabase não contém
    segredos (só metadados do erro), então é seguro devolver ao caller."""
    try:
        corpo = resp.json()
        if isinstance(corpo, dict):
            partes = [corpo.get(k) for k in ("message", "details", "hint") if corpo.get(k)]
            if partes:
                return " | ".join(str(p) for p in partes)
    except Exception:
        pass
    texto = (resp.text or "").strip()
    return texto[:300] if texto else "sem corpo"


# ---------------------------------------------------------------------------
# Chave de API
# ---------------------------------------------------------------------------

def _ler_chave() -> Optional[str]:
    chave = os.environ.get(_CHAVE_ENV)
    if chave and chave.strip():
        return chave.strip()
    caminho = _CONFIG_DIR / _CHAVE_ARQUIVO
    try:
        conteudo = caminho.read_text(encoding="utf-8").strip()
        if conteudo:
            return conteudo
    except (OSError, PermissionError):
        pass
    return None


def salvar_chave(chave: str) -> None:
    """Grava a chave em ~/.config/reunioes/supabase_key (0600). Vazia remove o arquivo."""
    caminho = _CONFIG_DIR / _CHAVE_ARQUIVO
    chave = (chave or "").strip()
    if not chave:
        caminho.unlink(missing_ok=True)
        return
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, chave.encode("utf-8"))
    finally:
        os.close(fd)


def chave_configurada() -> bool:
    return bool(_ler_chave())


def _cfg_sync() -> dict:
    return config.carregar().get("sync", {})


# ---------------------------------------------------------------------------
# Contrato público
# ---------------------------------------------------------------------------

def status() -> dict:
    cfg = _cfg_sync()
    with _lock:
        ultimo = dict(_ultimo_sync)
    return {
        "ativo": bool(cfg.get("ativo")),
        "modo": cfg.get("modo") or "tudo",
        "url_configurada": bool(cfg.get("url")),
        "chave_configurada": chave_configurada(),
        "reunioes_marcadas": _contar_reunioes_marcadas(),
        "ultimo_sync": ultimo,
    }


def testar() -> dict:
    """GET mínimo (limit=1) para validar url + chave. Nunca levanta exceção."""
    cfg = _cfg_sync()
    url = (cfg.get("url") or "").strip().rstrip("/")
    if not url:
        return {"ok": False, "erro": "URL do Supabase não configurada."}
    chave = _ler_chave()
    if not chave:
        return {"ok": False, "erro": "Chave do Supabase não configurada."}

    try:
        import httpx
    except ImportError:
        return {"ok": False, "erro": "Dependência httpx não disponível."}

    try:
        resp = httpx.get(
            f"{url}/rest/v1/lembretes",
            params={"select": "id", "limit": "1"},
            headers=_headers(chave),
            timeout=10.0,
        )
        if resp.status_code >= 400:
            return {"ok": False, "erro": f"Supabase retornou {resp.status_code}."}
        return {"ok": True, "erro": ""}
    except Exception as e:
        return {"ok": False, "erro": f"Falha ao conectar: {type(e).__name__}."}


def _parse_dt(valor: Optional[str]) -> datetime:
    """Converte `atualizado_em` (ISO 8601, geralmente com sufixo Z) em datetime
    timezone-aware, para comparação correta no merge LWW. None ou valor
    inválido vira datetime.min (UTC), sempre "mais antigo" que qualquer
    timestamp real."""
    if not valor:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _headers(chave: str) -> dict:
    return {
        "apikey": chave,
        "Authorization": f"Bearer {chave}",
        "Content-Type": "application/json",
    }


def _filtrar_por_modo(registros: list[dict], modo: str) -> list[dict]:
    """Filtro de escopo do push: em modo "selecionados", só passam registros
    com `sync_habilitado` truthy. Em qualquer outro modo (padrão "tudo"),
    passa tudo sem filtrar — preserva o comportamento histórico. Função pura,
    sem I/O, para ser testável isoladamente (ver smoke test)."""
    if modo != "selecionados":
        return registros
    return [r for r in registros if r.get("sync_habilitado")]


def _sincronizar_tabela(httpx_mod, url: str, chave: str, tabela: str,
                         listar_fn: Callable[[], list[dict]],
                         upsert_fn: Callable[[dict], bool],
                         modo: str = "tudo") -> dict:
    """Pull + merge LWW + push de uma única tabela. Levanta exceção em caso de
    falha de rede/HTTP — capturada pelo caller (sincronizar()).

    `modo`: controla apenas o PUSH (o que este app envia); pull/merge é
    sempre completo, independente do modo — ver docstring do módulo."""
    # 1. Pull remoto
    resp = httpx_mod.get(
        f"{url}/rest/v1/{tabela}",
        params={"select": "*"},
        headers=_headers(chave),
        timeout=20.0,
    )
    if resp.status_code >= 400:
        raise SyncError(f"Falha no pull ({tabela}): {resp.status_code} — {_detalhe_erro(resp)}")
    remotos = resp.json()

    locais = {r["id"]: r for r in listar_fn()}
    remotos_por_id = {r["id"]: r for r in remotos}

    # 2. Merge LWW: remoto mais novo que local -> aplica localmente
    recebidos = 0
    for id_, remoto in remotos_por_id.items():
        local = locais.get(id_)
        if local is None or _parse_dt(remoto.get("atualizado_em")) > _parse_dt(local.get("atualizado_em")):
            if upsert_fn(remoto):
                recebidos += 1

    # 3. Push: locais mais novos que o remoto (ou ausentes no remoto), filtrado por modo
    locais_atualizados = {r["id"]: r for r in listar_fn()}
    a_enviar = []
    for id_, local in locais_atualizados.items():
        remoto = remotos_por_id.get(id_)
        if remoto is None or _parse_dt(local.get("atualizado_em")) > _parse_dt(remoto.get("atualizado_em")):
            a_enviar.append(local)
    a_enviar = _filtrar_por_modo(a_enviar, modo)

    enviados = 0
    if a_enviar:
        # Colunas local-only: `sync_habilitado` (controla o que ESTE app envia)
        # e o estado do agendador de notificações (`notificado_nivel`/
        # `notificado_em`). Nenhuma existe nas tabelas remotas, então são
        # removidas do payload para não quebrar o push (ver notificacoes.md#sync-colunas).
        payload = [{k: v for k, v in r.items() if k not in _COLUNAS_NAO_ENVIAR} for r in a_enviar]
        resp = httpx_mod.post(
            f"{url}/rest/v1/{tabela}",
            json=payload,
            headers={**_headers(chave), "Prefer": "resolution=merge-duplicates,return=minimal"},
            timeout=20.0,
        )
        if resp.status_code >= 400:
            raise SyncError(f"Falha no push ({tabela}): {resp.status_code} — {_detalhe_erro(resp)}")
        enviados = len(a_enviar)

    return {"enviados": enviados, "recebidos": recebidos}


# ---------------------------------------------------------------------------
# Reuniões (push-only, filesystem -> Supabase; ver docstring do módulo)
# ---------------------------------------------------------------------------

def _pastas_de_reunioes():
    """Itera todas as pastas de reunião em reuniao.BASE_DIR (<data>/<slug>)."""
    base = reuniao.BASE_DIR
    if not base.exists():
        return
    for dia in base.iterdir():
        if not dia.is_dir():
            continue
        for pasta in dia.iterdir():
            if pasta.is_dir():
                yield dia, pasta


def _contar_reunioes_marcadas() -> int:
    """Conta reuniões com `sync_habilitado`=True no meta.json, para status()."""
    total = 0
    for _dia, pasta in _pastas_de_reunioes():
        try:
            if meta.ler(pasta).get("sync_habilitado"):
                total += 1
        except Exception:
            continue
    return total


def _ler_arquivo_seguro(caminho: Path) -> Optional[str]:
    """Lê um arquivo texto se existir; None se ausente. Erro de leitura vira
    skip silencioso (None), nunca propaga — reunião ainda é enviada, só sem
    esse campo, em vez de derrubar o sync inteiro."""
    if not caminho.exists():
        return None
    try:
        return caminho.read_text(encoding="utf-8")
    except OSError:
        return None


def _mtime_mais_recente(caminhos: list[Path]) -> Optional[float]:
    mtimes = []
    for c in caminhos:
        try:
            if c.exists():
                mtimes.append(c.stat().st_mtime)
        except OSError:
            continue
    return max(mtimes) if mtimes else None


def _coletar_reunioes_a_enviar() -> list[tuple[Path, dict]]:
    """Varre o filesystem e retorna [(pasta, payload)] para cada reunião
    marcada (`sync_habilitado`=True) que tem novidade desde o último envio
    (mtime dos arquivos > meta["sync_enviado_em"]). PUSH-ONLY: nunca lê nada
    do Supabase para reuniões — a fonte de verdade é sempre o filesystem
    local. Nunca inclui áudio; transcrição/resumo só se os arquivos existirem."""
    a_enviar: list[tuple[Path, dict]] = []
    for dia, pasta in _pastas_de_reunioes():
        try:
            m = meta.ler(pasta)
        except Exception:
            continue
        if not m.get("sync_habilitado"):
            continue
        sync_id = m.get("sync_id")
        if not sync_id:
            # Marcada mas sem sync_id (não deveria ocorrer via endpoint normal) -> pula.
            continue

        arq_transcricao = pasta / "transcricao.txt"
        arq_resumo = pasta / "resumo.md"
        arq_meta = meta.caminho(pasta)
        mtime = _mtime_mais_recente([arq_transcricao, arq_resumo, arq_meta])
        if mtime is None:
            continue

        enviado_em = m.get("sync_enviado_em")
        if enviado_em and datetime.fromtimestamp(mtime, tz=timezone.utc) <= _parse_dt(enviado_em):
            continue  # nada novo desde o último envio bem-sucedido

        atualizado_em = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        payload = {
            "id": sync_id,
            "titulo": m.get("titulo") or pasta.name,
            "data": dia.name,
            "cliente": m.get("cliente") or "",
            "projeto": m.get("projeto") or "",
            # Coluna Supabase é integer; meta.json guarda duracao_s como float
            # (ex.: 617.8). Arredonda para int; None permanece None.
            "duracao_s": _int_ou_none(m.get("duracao_s")),
            "idioma": m.get("idioma"),
            "transcricao": _ler_arquivo_seguro(arq_transcricao),
            "resumo": _ler_arquivo_seguro(arq_resumo),
            "atualizado_em": atualizado_em,
        }
        a_enviar.append((pasta, payload))
    return a_enviar


def _sincronizar_reunioes(httpx_mod, url: str, chave: str) -> dict:
    """Push-only das reuniões marcadas. Levanta SyncError em falha de
    rede/HTTP (capturada pelo caller); nunca envia áudio nem reuniões não
    marcadas explicitamente pelo usuário."""
    pendentes = _coletar_reunioes_a_enviar()
    if not pendentes:
        return {"enviados": 0}

    payload = [p for _, p in pendentes]
    resp = httpx_mod.post(
        f"{url}/rest/v1/{_TABELA_REUNIOES}",
        json=payload,
        headers={**_headers(chave), "Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=30.0,
    )
    if resp.status_code >= 400:
        raise SyncError(f"Falha no push (reunioes): {resp.status_code} — {_detalhe_erro(resp)}")

    for pasta, p in pendentes:
        try:
            meta.escrever(pasta, sync_enviado_em=p["atualizado_em"])
        except OSError:
            continue  # falha ao gravar o marcador não deve derrubar o sync

    return {"enviados": len(pendentes)}


def sincronizar() -> dict:
    """Pull + merge LWW + push de todas as tabelas em _TABELAS, seguido do
    push-only de reuniões marcadas (ver docstring do módulo). Nunca levanta
    exceção não tratada para o caller HTTP."""
    resultado = {"ok": False, "enviados": 0, "recebidos": 0, "reunioes_enviadas": 0, "erro": ""}
    cfg = _cfg_sync()
    if not cfg.get("ativo"):
        resultado["erro"] = "Sincronização desativada."
        return _registrar(resultado)

    url = (cfg.get("url") or "").strip().rstrip("/")
    if not url:
        resultado["erro"] = "URL do Supabase não configurada."
        return _registrar(resultado)

    chave = _ler_chave()
    if not chave:
        resultado["erro"] = "Chave do Supabase não configurada."
        return _registrar(resultado)

    try:
        import httpx
    except ImportError:
        resultado["erro"] = "Dependência httpx não disponível."
        return _registrar(resultado)

    modo = cfg.get("modo") or "tudo"

    total_enviados = 0
    total_recebidos = 0
    erros = []
    for tabela, listar_fn, upsert_fn in _TABELAS:
        try:
            parcial = _sincronizar_tabela(httpx, url, chave, tabela, listar_fn, upsert_fn, modo)
            total_enviados += parcial["enviados"]
            total_recebidos += parcial["recebidos"]
        except SyncError as e:
            erros.append(str(e))
        except Exception as e:
            erros.append(f"Falha ao sincronizar {tabela}: {type(e).__name__}.")

    total_reunioes = 0
    try:
        total_reunioes = _sincronizar_reunioes(httpx, url, chave)["enviados"]
    except SyncError as e:
        erros.append(str(e))
    except Exception as e:
        erros.append(f"Falha ao sincronizar reunioes: {type(e).__name__}.")

    resultado.update(ok=not erros, enviados=total_enviados, recebidos=total_recebidos,
                      reunioes_enviadas=total_reunioes, erro="; ".join(erros))
    return _registrar(resultado)


def _registrar(resultado: dict) -> dict:
    import datetime
    with _lock:
        _ultimo_sync["quando"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _ultimo_sync["resultado"] = resultado
    return resultado


def sincronizar_em_background() -> None:
    """Dispara sincronizar() em thread daemon, best-effort. Não bloqueia o caller."""
    if not _cfg_sync().get("ativo"):
        return

    def _run():
        try:
            sincronizar()
        except BaseException:
            # Falha silenciosa: fica registrada em _ultimo_sync (via sincronizar) ou,
            # no pior caso, simplesmente não atualiza — nunca derruba a thread do worker.
            pass

    threading.Thread(target=_run, daemon=True).start()
