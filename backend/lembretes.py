#!/usr/bin/env python3
"""
Lembretes com persistência local em SQLite (~/.config/reunioes/reunioes.db).

Schema simples com soft delete (coluna `deletado_em`) para permitir
sincronização opcional com Supabase via estratégia last-write-wins (ver
sync.py). `atualizado_em` é sempre UTC ISO 8601 e é o campo usado como
critério de "mais novo" no merge de sincronização — nunca altere seu
significado sem revisar sync.py junto.

Uma conexão por chamada (sem pool nem thread compartilhada): simples e seguro
com os workers em thread já existentes no server.py.
"""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import config

DB_FILE = config.CONFIG_DIR / "reunioes.db"

_CAMPOS_ATUALIZAVEIS = ("titulo", "descricao", "data_hora", "reuniao", "cliente", "concluido",
                        "sync_habilitado", "recorrencia")

# Colunas de estado local do agendador de notificações — NUNCA sincronizadas
# com o Supabase (ver notificacoes.md#sync-colunas e sync.py). `recorrencia`,
# por ser dado de negócio, NÃO entra aqui: ela sincroniza normalmente.
COLUNAS_LOCAIS_NOTIFICACAO = ("notificado_nivel", "notificado_em")

_RECORRENCIAS = ("", "diaria", "semanal", "mensal")


def _agora_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_local(s: str) -> datetime:
    """Parseia um wall-clock naive 'AAAA-MM-DDTHH:MM[:SS]' (o formato de
    data_hora, vindo de <input datetime-local>)."""
    return datetime.fromisoformat(s)


def _proxima_ocorrencia(base: datetime, recorrencia: str, agora: datetime) -> datetime:
    """Avança `base` pela regra de recorrência até cair no futuro (>agora).
    Suporta diaria/semanal/mensal. Mensal usa passo de 30 dias (simplificação
    nível 1-2 — evita dependência de dateutil)."""
    passo = {"diaria": 1, "semanal": 7, "mensal": 30}.get(recorrencia)
    if not passo:
        return base
    prox = base
    while prox <= agora:
        prox = prox + timedelta(days=passo)
    return prox


def _adicionar_coluna_se_ausente(conn: sqlite3.Connection, tabela: str, coluna: str, definicao: str) -> None:
    """Migração idempotente de coluna: sqlite não tem `ADD COLUMN IF NOT
    EXISTS`, então checamos via PRAGMA antes de alterar. Seguro rodar em toda
    conexão (é apenas uma leitura de metadados quando a coluna já existe)."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({tabela})")}
    if coluna not in cols:
        try:
            conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")
        except sqlite3.OperationalError:
            pass


def _conectar() -> sqlite3.Connection:
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    _adicionar_coluna_se_ausente(conn, "lembretes", "sync_habilitado", "INTEGER NOT NULL DEFAULT 1")
    # Estado do agendador de notificações (local-only) + recorrência (sincronizável).
    _adicionar_coluna_se_ausente(conn, "lembretes", "notificado_nivel", "INTEGER NOT NULL DEFAULT 0")
    _adicionar_coluna_se_ausente(conn, "lembretes", "notificado_em", "TEXT")
    _adicionar_coluna_se_ausente(conn, "lembretes", "recorrencia", "TEXT NOT NULL DEFAULT ''")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS lembretes (
  id TEXT PRIMARY KEY,
  titulo TEXT NOT NULL,
  descricao TEXT NOT NULL DEFAULT '',
  data_hora TEXT,
  reuniao TEXT,
  cliente TEXT,
  concluido INTEGER NOT NULL DEFAULT 0,
  criado_em TEXT NOT NULL,
  atualizado_em TEXT NOT NULL,
  deletado_em TEXT,
  sync_habilitado INTEGER NOT NULL DEFAULT 1
)
"""


def _row_para_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["concluido"] = bool(d["concluido"])
    d["sync_habilitado"] = bool(d["sync_habilitado"])
    return d


def listar(incluir_concluidos: bool = False) -> list[dict]:
    """Lista lembretes não deletados, ordenados por data_hora (nulos por último)."""
    conn = _conectar()
    try:
        sql = "SELECT * FROM lembretes WHERE deletado_em IS NULL"
        if not incluir_concluidos:
            sql += " AND concluido = 0"
        sql += " ORDER BY (data_hora IS NULL), data_hora ASC, criado_em ASC"
        cur = conn.execute(sql)
        return [_row_para_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def obter(id: str) -> Optional[dict]:
    conn = _conectar()
    try:
        cur = conn.execute(
            "SELECT * FROM lembretes WHERE id = ? AND deletado_em IS NULL", (id,)
        )
        row = cur.fetchone()
        return _row_para_dict(row) if row else None
    finally:
        conn.close()


def criar(
    titulo: str,
    descricao: str = "",
    data_hora: Optional[str] = None,
    reuniao: Optional[str] = None,
    cliente: Optional[str] = None,
    recorrencia: str = "",
) -> dict:
    titulo = (titulo or "").strip()
    if not titulo:
        raise ValueError("Título é obrigatório.")
    recorrencia = recorrencia if recorrencia in _RECORRENCIAS else ""

    id_ = str(uuid.uuid4())
    agora = _agora_utc()
    conn = _conectar()
    try:
        conn.execute(
            """INSERT INTO lembretes
               (id, titulo, descricao, data_hora, reuniao, cliente, concluido,
                criado_em, atualizado_em, deletado_em, recorrencia)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, ?)""",
            (id_, titulo, descricao or "", data_hora, reuniao, cliente, agora, agora, recorrencia),
        )
        conn.commit()
    finally:
        conn.close()
    return obter(id_)


def atualizar(id: str, **campos: Any) -> Optional[dict]:
    """Atualiza somente os campos passados com valor não-None. Sempre atualiza atualizado_em."""
    existente = obter(id)
    if existente is None:
        return None

    # Recorrência: concluir um lembrete recorrente com prazo não o encerra —
    # avança para a próxima ocorrência e o reabre (nível de notificação zerado
    # via reset de data_hora abaixo). Para encerrar de vez, remova a
    # recorrência antes (ou edite o prazo manualmente).
    recor = existente.get("recorrencia") or ""
    if campos.get("concluido") and recor and existente.get("data_hora"):
        try:
            prox = _proxima_ocorrencia(_parse_local(existente["data_hora"]), recor, datetime.now())
            campos = dict(campos)
            campos["concluido"] = False
            campos["data_hora"] = prox.isoformat(timespec="minutes")
        except ValueError:
            pass  # data_hora inválida: conclui normalmente

    if campos.get("recorrencia") is not None and campos["recorrencia"] not in _RECORRENCIAS:
        raise ValueError("Recorrência inválida.")

    sets = []
    valores: list[Any] = []
    for campo in _CAMPOS_ATUALIZAVEIS:
        if campo in campos and campos[campo] is not None:
            valor = campos[campo]
            if campo == "titulo":
                valor = str(valor).strip()
                if not valor:
                    raise ValueError("Título é obrigatório.")
            if campo in ("concluido", "sync_habilitado"):
                valor = 1 if valor else 0
            sets.append(f"{campo} = ?")
            valores.append(valor)

    # Mudança de prazo reavalia os marcos: zera o estado de notificação.
    if "data_hora" in campos:
        sets.append("notificado_nivel = 0")
        sets.append("notificado_em = NULL")

    sets.append("atualizado_em = ?")
    valores.append(_agora_utc())
    valores.append(id)

    conn = _conectar()
    try:
        conn.execute(
            f"UPDATE lembretes SET {', '.join(sets)} WHERE id = ? AND deletado_em IS NULL",
            valores,
        )
        conn.commit()
    finally:
        conn.close()
    return obter(id)


def excluir(id: str) -> bool:
    """Soft delete: marca deletado_em (tombstone), preservado para sincronização."""
    existente = obter(id)
    if existente is None:
        return False
    agora = _agora_utc()
    conn = _conectar()
    try:
        conn.execute(
            "UPDATE lembretes SET deletado_em = ?, atualizado_em = ? WHERE id = ?",
            (agora, agora, id),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def vencidos() -> list[dict]:
    """Lembretes com prazo (data_hora) já vencido, não concluídos, não deletados."""
    agora = datetime.now().isoformat()
    conn = _conectar()
    try:
        cur = conn.execute(
            """SELECT * FROM lembretes
               WHERE deletado_em IS NULL AND concluido = 0
                 AND data_hora IS NOT NULL AND data_hora <= ?
               ORDER BY data_hora ASC""",
            (agora,),
        )
        return [_row_para_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def adiar(id: str, minutos: Optional[int] = None, ate: Optional[str] = None) -> Optional[dict]:
    """Snooze: empurra o prazo para agora+minutos (ou para o instante `ate`,
    wall-clock 'AAAA-MM-DDTHH:MM'), reabre o lembrete e reseta os marcos de
    notificação (via reset de data_hora em atualizar())."""
    if obter(id) is None:
        return None
    if ate:
        nova = ate
    else:
        m = int(minutos or 0)
        if m <= 0:
            raise ValueError("Informe minutos > 0 ou uma data/hora.")
        nova = (datetime.now() + timedelta(minutes=m)).isoformat(timespec="minutes")
    return atualizar(id, data_hora=nova, concluido=False)


# ---------------------------------------------------------------------------
# Agendador de notificações (autoridade) — consumido pelo processo main do
# Electron via GET /api/lembretes/pendentes-notificacao. Ver notificacoes.md.
# ---------------------------------------------------------------------------

def _data_de(iso: Optional[str]):
    try:
        return datetime.fromisoformat(iso).date() if iso else None
    except (ValueError, TypeError):
        return None


def pendentes_notificacao(cfg: Optional[dict] = None) -> dict:
    """Calcula E marca (atômico, idempotente) os lembretes que cruzaram um
    marco de notificação ainda não avisado. Retorna grupos por nível para o
    caller compor a mensagem consolidada.

    Marcos (relativos ao prazo, antecedências configuráveis):
      1=antes (padrão 1 dia) · 2=antes (padrão 1 h) · 3=no horário · 4=vencido.
    Avanço é monotônico: se o app ficou fechado e o prazo já passou, dispara
    só o marco mais recente (nível salta direto), evitando enxurrada. Nível 4
    re-lembra 1x/dia (config `vencido_repetir`)."""
    cfg = cfg or {}
    if not cfg.get("ativo", True):
        return {"grupos": []}
    ant_dia = int(cfg.get("antecedencia_dia_min", 1440))
    ant_hora = int(cfg.get("antecedencia_hora_min", 60))
    repetir = cfg.get("vencido_repetir", "diario")
    agora = datetime.now()
    hoje = agora.date()

    a_notificar: list[tuple[int, str, dict]] = []
    conn = _conectar()
    try:
        cur = conn.execute(
            "SELECT * FROM lembretes WHERE deletado_em IS NULL AND concluido = 0 "
            "AND data_hora IS NOT NULL"
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    for l in rows:
        try:
            prazo = _parse_local(l["data_hora"])
        except (ValueError, TypeError):
            continue
        nivel = int(l.get("notificado_nivel") or 0)
        antes_dia = prazo - timedelta(minutes=ant_dia)
        antes_hora = prazo - timedelta(minutes=ant_hora)

        if agora < antes_dia:
            continue
        elif agora < antes_hora:
            target, categoria = 1, "dia"
        elif agora < prazo:
            target, categoria = 2, "hora"
        elif (agora - prazo) <= timedelta(hours=1) and nivel < 3:
            target, categoria = 3, "agora"
        else:
            target, categoria = 4, "vencido"

        if target < 4:
            disparar = nivel < target
        elif nivel < 4:
            disparar = True
        elif repetir == "diario":
            d = _data_de(l.get("notificado_em"))
            disparar = (d is None) or (d < hoje)
        else:
            disparar = False
        if disparar:
            a_notificar.append((target, categoria, l))

    if a_notificar:
        conn = _conectar()
        try:
            marca = agora.isoformat(timespec="seconds")
            for target, _cat, l in a_notificar:
                # "No horário" (3) e "vencido" (4) ancoram o estado em nível 4 +
                # timestamp: evita que "agora" escale para "vencido" no tick
                # seguinte; o re-lembrete de vencido só reaparece no dia seguinte.
                if target >= 3:
                    conn.execute(
                        "UPDATE lembretes SET notificado_nivel = 4, notificado_em = ? WHERE id = ?",
                        (marca, l["id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE lembretes SET notificado_nivel = ? WHERE id = ?",
                        (target, l["id"]),
                    )
            conn.commit()
        finally:
            conn.close()

    grupos: dict[int, dict] = {}
    for target, categoria, l in a_notificar:
        g = grupos.setdefault(target, {"nivel": target, "categoria": categoria, "itens": []})
        g["itens"].append({"id": l["id"], "titulo": l["titulo"], "data_hora": l["data_hora"]})
    saida = []
    for target in sorted(grupos, reverse=True):
        g = grupos[target]
        g["count"] = len(g["itens"])
        saida.append(g)
    return {"grupos": saida}


# ---------------------------------------------------------------------------
# Suporte a sincronização (usado por sync.py)
# ---------------------------------------------------------------------------

def todos_incluindo_deletados() -> list[dict]:
    """Retorna todos os registros (inclusive tombstones), para o merge de sync."""
    conn = _conectar()
    try:
        cur = conn.execute("SELECT * FROM lembretes")
        return [_row_para_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def upsert_bruto(registro: dict) -> bool:
    """Insere/substitui um registro vindo do servidor remoto, sem validação de negócio.

    Usado apenas pelo merge de sincronização (sync.py), que já decidiu que este
    registro remoto é mais novo (LWW por atualizado_em).

    Defesa em profundidade: registros remotos com `id` que não seja um UUID
    bem formado são ignorados silenciosamente (skip), pois `id` é usado
    diretamente em SQL/HTML no restante do app. Retorna False nesse caso
    (o caller deve contar como "não recebido"), True se persistido.
    """
    try:
        uuid.UUID(str(registro.get("id")))
    except (ValueError, AttributeError, TypeError):
        return False
    conn = _conectar()
    try:
        conn.execute(
            """INSERT INTO lembretes
               (id, titulo, descricao, data_hora, reuniao, cliente, concluido,
                criado_em, atualizado_em, deletado_em, recorrencia)
               VALUES (:id, :titulo, :descricao, :data_hora, :reuniao, :cliente, :concluido,
                       :criado_em, :atualizado_em, :deletado_em, :recorrencia)
               ON CONFLICT(id) DO UPDATE SET
                 titulo=excluded.titulo, descricao=excluded.descricao,
                 data_hora=excluded.data_hora, reuniao=excluded.reuniao,
                 cliente=excluded.cliente, concluido=excluded.concluido,
                 criado_em=excluded.criado_em, atualizado_em=excluded.atualizado_em,
                 deletado_em=excluded.deletado_em, recorrencia=excluded.recorrencia""",
            {**registro, "concluido": 1 if registro.get("concluido") else 0,
             "recorrencia": registro.get("recorrencia") or ""},
        )
        conn.commit()
    finally:
        conn.close()
    return True
