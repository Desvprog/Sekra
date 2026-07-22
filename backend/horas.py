#!/usr/bin/env python3
"""
Timetracking (clientes, projetos e apontamentos de horas) com persistência
local em SQLite (~/.config/reunioes/reunioes.db) — mesmo banco de lembretes.py.

Segue o padrão de lembretes.py: conexão por chamada (sem pool), UUID text PK,
`atualizado_em` UTC ISO 8601 usado como critério "mais novo" no merge LWW
(ver sync.py), soft delete via `deletado_em`.

`inicio`/`fim` de apontamentos são wall-clock local (sem timezone), no mesmo
espírito de `data_hora` em lembretes.py — nunca alterar esse significado sem
revisar sync.py junto. `fim` nulo indica timer em execução.

Migração automática (idempotente): na primeira conexão do processo, se a
tabela `clientes` estiver totalmente vazia (nenhuma linha, nem tombstone) e
a config tiver `clientes`/`valores_hora`, popula um cliente por nome. Só
acontece uma vez por vida útil do banco: depois que existir ao menos 1 linha
(mesmo soft-deletada), nunca mais roda.
"""

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import config

DB_FILE = config.CONFIG_DIR / "reunioes.db"

_CAMPOS_CLIENTE = ("nome", "valor_hora", "ativo", "sync_habilitado")
_CAMPOS_PROJETO = ("nome", "cliente_id", "ativo", "sync_habilitado")
_CAMPOS_APONTAMENTO = ("cliente_id", "projeto_id", "descricao", "inicio", "fim",
                       "duracao_s", "reuniao_ref", "sync_habilitado")

_SCHEMA_CLIENTES = """
CREATE TABLE IF NOT EXISTS clientes (
  id TEXT PRIMARY KEY,
  nome TEXT NOT NULL,
  valor_hora REAL NOT NULL DEFAULT 0,
  ativo INTEGER NOT NULL DEFAULT 1,
  criado_em TEXT NOT NULL,
  atualizado_em TEXT NOT NULL,
  deletado_em TEXT,
  sync_habilitado INTEGER NOT NULL DEFAULT 1
)
"""

_SCHEMA_PROJETOS = """
CREATE TABLE IF NOT EXISTS projetos (
  id TEXT PRIMARY KEY,
  cliente_id TEXT,
  nome TEXT NOT NULL,
  ativo INTEGER NOT NULL DEFAULT 1,
  criado_em TEXT NOT NULL,
  atualizado_em TEXT NOT NULL,
  deletado_em TEXT,
  sync_habilitado INTEGER NOT NULL DEFAULT 1
)
"""

_SCHEMA_APONTAMENTOS = """
CREATE TABLE IF NOT EXISTS apontamentos (
  id TEXT PRIMARY KEY,
  cliente_id TEXT,
  projeto_id TEXT,
  descricao TEXT NOT NULL DEFAULT '',
  inicio TEXT NOT NULL,
  fim TEXT,
  duracao_s INTEGER,
  origem TEXT NOT NULL DEFAULT 'manual',
  reuniao_ref TEXT,
  criado_em TEXT NOT NULL,
  atualizado_em TEXT NOT NULL,
  deletado_em TEXT,
  sync_habilitado INTEGER NOT NULL DEFAULT 1
)
"""

_migracao_verificada = False
_migracao_lock = threading.Lock()
_timer_lock = threading.Lock()


def _agora_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agora_local() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _coluna_not_null(conn: sqlite3.Connection, tabela: str, coluna: str) -> bool:
    """PRAGMA table_info: coluna (cid, name, type, notnull, dflt_value, pk).
    Retorna True se a coluna existir e tiver NOT NULL (notnull=1)."""
    for row in conn.execute(f"PRAGMA table_info({tabela})"):
        if row[1] == coluna:
            return bool(row[3])
    return False


# Colunas na ordem exata dos schemas acima — usadas para o rebuild de
# `cliente_id` NOT NULL -> nullable (SQLite não suporta ALTER COLUMN DROP
# NOT NULL). Mantidas em sincronia manualmente com _SCHEMA_PROJETOS /
# _SCHEMA_APONTAMENTOS.
_COLUNAS_PROJETOS = (
    "id", "cliente_id", "nome", "ativo", "criado_em", "atualizado_em",
    "deletado_em", "sync_habilitado",
)
_COLUNAS_APONTAMENTOS = (
    "id", "cliente_id", "projeto_id", "descricao", "inicio", "fim",
    "duracao_s", "origem", "reuniao_ref", "criado_em", "atualizado_em",
    "deletado_em", "sync_habilitado",
)


def _tornar_cliente_id_nullable(conn: sqlite3.Connection, tabela: str, schema_novo: str,
                                 colunas: tuple[str, ...]) -> None:
    """Migração idempotente: se `cliente_id` da tabela ainda for NOT NULL
    (schema anterior ao modelo Toggl), reconstrói a tabela com o schema atual
    (cliente_id nullable), copiando os dados. SQLite não tem `ALTER TABLE ...
    ALTER COLUMN ... DROP NOT NULL`, então o caminho é: cria tabela nova sob
    nome temporário, copia dados, dropa a antiga, renomeia. Chamado sob
    _migracao_lock (mesmo guarda de _migrar_de_config) e dentro de uma
    transação explícita para não deixar o banco pela metade em caso de erro.
    Rodar de novo é seguro: a checagem de notnull vira um no-op."""
    if not _coluna_not_null(conn, tabela, "cliente_id"):
        return

    tmp = f"{tabela}__rebuild_tmp"
    schema_tmp = schema_novo.replace(f"IF NOT EXISTS {tabela}", f"IF NOT EXISTS {tmp}", 1)
    cols_sql = ", ".join(colunas)
    conn.execute("BEGIN")
    try:
        conn.execute(f"DROP TABLE IF EXISTS {tmp}")
        conn.execute(schema_tmp)
        conn.execute(f"INSERT INTO {tmp} ({cols_sql}) SELECT {cols_sql} FROM {tabela}")
        conn.execute(f"DROP TABLE {tabela}")
        conn.execute(f"ALTER TABLE {tmp} RENAME TO {tabela}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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
    conn.execute(_SCHEMA_CLIENTES)
    conn.execute(_SCHEMA_PROJETOS)
    conn.execute(_SCHEMA_APONTAMENTOS)
    for tabela in ("clientes", "projetos", "apontamentos"):
        _adicionar_coluna_se_ausente(conn, tabela, "sync_habilitado", "INTEGER NOT NULL DEFAULT 1")

    global _migracao_verificada
    if not _migracao_verificada:
        with _migracao_lock:
            if not _migracao_verificada:
                _tornar_cliente_id_nullable(conn, "projetos", _SCHEMA_PROJETOS, _COLUNAS_PROJETOS)
                _tornar_cliente_id_nullable(conn, "apontamentos", _SCHEMA_APONTAMENTOS, _COLUNAS_APONTAMENTOS)
                _migrar_de_config(conn)
                _migracao_verificada = True

    return conn


def _migrar_de_config(conn: sqlite3.Connection) -> None:
    """Popula `clientes` a partir de config.clientes/valores_hora, uma única
    vez: só roda se a tabela estiver 100% vazia (nem tombstones). Chamado sob
    _migracao_lock (double-checked em _conectar); revalida o COUNT aqui para
    evitar corrida com outra thread que já tenha inserido."""
    total = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
    if total:
        return

    cfg = config.carregar()
    nomes = cfg.get("clientes") or []
    valores_hora = cfg.get("valores_hora") or {}
    if not nomes:
        return

    agora = _agora_utc()
    for nome in nomes:
        nome = str(nome).strip()
        if not nome:
            continue
        valor = valores_hora.get(nome)
        valor = float(valor) if isinstance(valor, (int, float)) else 0.0
        conn.execute(
            """INSERT INTO clientes (id, nome, valor_hora, ativo, criado_em, atualizado_em, deletado_em)
               VALUES (?, ?, ?, 1, ?, ?, NULL)""",
            (str(uuid.uuid4()), nome, valor, agora, agora),
        )
    conn.commit()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    if "ativo" in d:
        d["ativo"] = bool(d["ativo"])
    if "sync_habilitado" in d:
        d["sync_habilitado"] = bool(d["sync_habilitado"])
    return d


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------

def listar_clientes(incluir_inativos: bool = False) -> list[dict]:
    conn = _conectar()
    try:
        sql = "SELECT * FROM clientes WHERE deletado_em IS NULL"
        if not incluir_inativos:
            sql += " AND ativo = 1"
        sql += " ORDER BY nome COLLATE NOCASE ASC"
        return [_row(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def obter_cliente(id: str) -> Optional[dict]:
    conn = _conectar()
    try:
        row = conn.execute(
            "SELECT * FROM clientes WHERE id = ? AND deletado_em IS NULL", (id,)
        ).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def _nome_cliente_em_uso(conn: sqlite3.Connection, nome: str, ignorar_id: Optional[str] = None) -> bool:
    sql = "SELECT 1 FROM clientes WHERE deletado_em IS NULL AND LOWER(nome) = LOWER(?)"
    params: list[Any] = [nome]
    if ignorar_id:
        sql += " AND id != ?"
        params.append(ignorar_id)
    return conn.execute(sql, params).fetchone() is not None


def criar_cliente(nome: str, valor_hora: float = 0.0) -> dict:
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome é obrigatório.")

    id_ = str(uuid.uuid4())
    agora = _agora_utc()
    conn = _conectar()
    try:
        if _nome_cliente_em_uso(conn, nome):
            raise ValueError("Já existe um cliente com esse nome.")
        conn.execute(
            """INSERT INTO clientes (id, nome, valor_hora, ativo, criado_em, atualizado_em, deletado_em)
               VALUES (?, ?, ?, 1, ?, ?, NULL)""",
            (id_, nome, float(valor_hora or 0), agora, agora),
        )
        conn.commit()
    finally:
        conn.close()
    return obter_cliente(id_)


def atualizar_cliente(id: str, **campos: Any) -> Optional[dict]:
    existente = obter_cliente(id)
    if existente is None:
        return None

    conn = _conectar()
    try:
        sets = []
        valores: list[Any] = []
        for campo in _CAMPOS_CLIENTE:
            if campo in campos and campos[campo] is not None:
                valor = campos[campo]
                if campo == "nome":
                    valor = str(valor).strip()
                    if not valor:
                        raise ValueError("Nome é obrigatório.")
                    if _nome_cliente_em_uso(conn, valor, ignorar_id=id):
                        raise ValueError("Já existe um cliente com esse nome.")
                if campo == "valor_hora":
                    valor = float(valor)
                if campo in ("ativo", "sync_habilitado"):
                    valor = 1 if valor else 0
                sets.append(f"{campo} = ?")
                valores.append(valor)

        if not sets:
            return existente

        sets.append("atualizado_em = ?")
        valores.append(_agora_utc())
        valores.append(id)
        conn.execute(
            f"UPDATE clientes SET {', '.join(sets)} WHERE id = ? AND deletado_em IS NULL",
            valores,
        )
        conn.commit()
    finally:
        conn.close()
    return obter_cliente(id)


def excluir_cliente(id: str) -> bool:
    existente = obter_cliente(id)
    if existente is None:
        return False
    agora = _agora_utc()
    conn = _conectar()
    try:
        conn.execute(
            "UPDATE clientes SET deletado_em = ?, atualizado_em = ? WHERE id = ?",
            (agora, agora, id),
        )
        conn.commit()
    finally:
        conn.close()
    return True


# ---------------------------------------------------------------------------
# Projetos
# ---------------------------------------------------------------------------

def listar_projetos(cliente_id: Optional[str] = None, incluir_inativos: bool = False) -> list[dict]:
    conn = _conectar()
    try:
        sql = "SELECT * FROM projetos WHERE deletado_em IS NULL"
        params: list[Any] = []
        if cliente_id:
            sql += " AND cliente_id = ?"
            params.append(cliente_id)
        if not incluir_inativos:
            sql += " AND ativo = 1"
        sql += " ORDER BY nome COLLATE NOCASE ASC"
        return [_row(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def obter_projeto(id: str) -> Optional[dict]:
    conn = _conectar()
    try:
        row = conn.execute(
            "SELECT * FROM projetos WHERE id = ? AND deletado_em IS NULL", (id,)
        ).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def criar_projeto(nome: str, cliente_id: Optional[str] = None) -> dict:
    """Modelo Toggl: projeto pode existir sem cliente (`cliente_id=None`)."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome é obrigatório.")
    if cliente_id and obter_cliente(cliente_id) is None:
        raise ValueError("Cliente não encontrado.")

    id_ = str(uuid.uuid4())
    agora = _agora_utc()
    conn = _conectar()
    try:
        conn.execute(
            """INSERT INTO projetos (id, cliente_id, nome, ativo, criado_em, atualizado_em, deletado_em)
               VALUES (?, ?, ?, 1, ?, ?, NULL)""",
            (id_, cliente_id or None, nome, agora, agora),
        )
        conn.commit()
    finally:
        conn.close()
    return obter_projeto(id_)


def atualizar_projeto(id: str, **campos: Any) -> Optional[dict]:
    """`cliente_id`: omitido = não altera; `""` (string vazia) = limpa o
    vínculo (seta NULL); qualquer outro valor = novo cliente (validado).
    Sentinela de string vazia porque `None` já significa "não fornecido" no
    padrão desta função (`campos[campo] is not None`)."""
    existente = obter_projeto(id)
    if existente is None:
        return None

    if "cliente_id" in campos and campos["cliente_id"]:
        if obter_cliente(campos["cliente_id"]) is None:
            raise ValueError("Cliente não encontrado.")

    sets = []
    valores: list[Any] = []
    for campo in _CAMPOS_PROJETO:
        if campo == "cliente_id" and campo in campos:
            valor = campos[campo]
            sets.append("cliente_id = ?")
            valores.append(valor if valor else None)
            continue
        if campo in campos and campos[campo] is not None:
            valor = campos[campo]
            if campo == "nome":
                valor = str(valor).strip()
                if not valor:
                    raise ValueError("Nome é obrigatório.")
            if campo in ("ativo", "sync_habilitado"):
                valor = 1 if valor else 0
            sets.append(f"{campo} = ?")
            valores.append(valor)

    if not sets:
        return existente

    sets.append("atualizado_em = ?")
    valores.append(_agora_utc())
    valores.append(id)
    conn = _conectar()
    try:
        conn.execute(
            f"UPDATE projetos SET {', '.join(sets)} WHERE id = ? AND deletado_em IS NULL",
            valores,
        )
        conn.commit()
    finally:
        conn.close()
    return obter_projeto(id)


def excluir_projeto(id: str) -> bool:
    existente = obter_projeto(id)
    if existente is None:
        return False
    agora = _agora_utc()
    conn = _conectar()
    try:
        conn.execute(
            "UPDATE projetos SET deletado_em = ?, atualizado_em = ? WHERE id = ?",
            (agora, agora, id),
        )
        conn.commit()
    finally:
        conn.close()
    return True


# ---------------------------------------------------------------------------
# Apontamentos
# ---------------------------------------------------------------------------

def _parse_local(valor: str) -> datetime:
    return datetime.fromisoformat(str(valor))


def _calc_duracao_s(inicio: str, fim: Optional[str]) -> Optional[int]:
    if not fim:
        return None
    try:
        d = (_parse_local(fim) - _parse_local(inicio)).total_seconds()
    except ValueError:
        return None
    return int(round(d))


def listar_apontamentos(mes: Optional[str] = None, cliente_id: Optional[str] = None,
                         projeto_id: Optional[str] = None) -> list[dict]:
    conn = _conectar()
    try:
        sql = "SELECT * FROM apontamentos WHERE deletado_em IS NULL"
        params: list[Any] = []
        if mes:
            sql += " AND inicio LIKE ?"
            params.append(f"{mes}%")
        if cliente_id:
            sql += " AND cliente_id = ?"
            params.append(cliente_id)
        if projeto_id:
            sql += " AND projeto_id = ?"
            params.append(projeto_id)
        sql += " ORDER BY inicio DESC"
        return [_row(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def obter_apontamento(id: str) -> Optional[dict]:
    conn = _conectar()
    try:
        row = conn.execute(
            "SELECT * FROM apontamentos WHERE id = ? AND deletado_em IS NULL", (id,)
        ).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def todos_apontamentos_periodo(mes: Optional[str] = None) -> list[dict]:
    """Apontamentos não deletados e já encerrados (fim preenchido), usado por
    relatorio.py. `mes` opcional (AAAA-MM) filtra por prefixo de `inicio`."""
    conn = _conectar()
    try:
        sql = "SELECT * FROM apontamentos WHERE deletado_em IS NULL AND fim IS NOT NULL"
        params: list[Any] = []
        if mes:
            sql += " AND inicio LIKE ?"
            params.append(f"{mes}%")
        sql += " ORDER BY inicio ASC"
        return [_row(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def criar_apontamento(inicio: str, fim: Optional[str] = None,
                       cliente_id: Optional[str] = None, projeto_id: Optional[str] = None,
                       descricao: str = "", origem: str = "manual",
                       reuniao_ref: Optional[str] = None) -> dict:
    """Modelo Toggl: `cliente_id` é opcional. Se ausente e `projeto_id` for
    fornecido, o cliente é derivado do projeto (`projeto.cliente_id`) e
    persistido no apontamento. Sem cliente e sem projeto: lançamento livre,
    permitido."""
    projeto = None
    if projeto_id:
        projeto = obter_projeto(projeto_id)
        if projeto is None:
            raise ValueError("Projeto não encontrado.")
    if cliente_id:
        if obter_cliente(cliente_id) is None:
            raise ValueError("Cliente não encontrado.")
    elif projeto is not None:
        cliente_id = projeto.get("cliente_id")
    if not inicio:
        raise ValueError("Início é obrigatório.")
    try:
        inicio_dt = _parse_local(inicio)
    except ValueError:
        raise ValueError("Início inválido.")
    if fim:
        try:
            fim_dt = _parse_local(fim)
        except ValueError:
            raise ValueError("Fim inválido.")
        if fim_dt <= inicio_dt:
            raise ValueError("Fim deve ser posterior ao início.")

    id_ = str(uuid.uuid4())
    agora = _agora_utc()
    duracao_s = _calc_duracao_s(inicio, fim)
    conn = _conectar()
    try:
        conn.execute(
            """INSERT INTO apontamentos
               (id, cliente_id, projeto_id, descricao, inicio, fim, duracao_s,
                origem, reuniao_ref, criado_em, atualizado_em, deletado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (id_, cliente_id, projeto_id, descricao or "", inicio, fim, duracao_s,
             origem, reuniao_ref, agora, agora),
        )
        conn.commit()
    finally:
        conn.close()
    return obter_apontamento(id_)


def atualizar_apontamento(id: str, **campos: Any) -> Optional[dict]:
    existente = obter_apontamento(id)
    if existente is None:
        return None

    # Mesma sentinela de atualizar_projeto: "" limpa o vínculo (grava NULL).
    # Converter antes da validação de existência.
    for _campo_fk in ("cliente_id", "projeto_id"):
        if campos.get(_campo_fk) == "":
            campos[_campo_fk] = None
            campos["_limpar_" + _campo_fk] = True

    if "cliente_id" in campos and campos["cliente_id"] is not None:
        if obter_cliente(campos["cliente_id"]) is None:
            raise ValueError("Cliente não encontrado.")
    if "projeto_id" in campos and campos["projeto_id"] is not None:
        if obter_projeto(campos["projeto_id"]) is None:
            raise ValueError("Projeto não encontrado.")

    novo_inicio = campos.get("inicio") if campos.get("inicio") is not None else existente["inicio"]
    novo_fim = campos.get("fim") if "fim" in campos and campos["fim"] is not None else existente["fim"]
    if ("inicio" in campos or "fim" in campos) and novo_fim:
        try:
            inicio_dt = _parse_local(novo_inicio)
            fim_dt = _parse_local(novo_fim)
        except ValueError:
            raise ValueError("Data inválida.")
        if fim_dt <= inicio_dt:
            raise ValueError("Fim deve ser posterior ao início.")

    # Troca de projeto sem cliente explícito re-deriva o cliente do novo
    # projeto (ou NULL se o projeto não tem cliente) — evita apontamento
    # apontando pro cliente do projeto antigo.
    if campos.get("projeto_id") and "cliente_id" not in campos:
        proj = obter_projeto(campos["projeto_id"])
        if proj is None:
            raise ValueError("Projeto não encontrado.")
        campos["cliente_id"] = proj.get("cliente_id")
        campos.setdefault("_limpar_cliente_id", campos["cliente_id"] is None)

    sets = []
    valores: list[Any] = []
    for campo in _CAMPOS_APONTAMENTO:
        if campo in campos and (
            campos[campo] is not None or campos.get("_limpar_" + campo)
        ):
            valor = campos[campo]
            if campo == "sync_habilitado":
                valor = 1 if valor else 0
            sets.append(f"{campo} = ?")
            valores.append(valor)

    if "inicio" in campos or "fim" in campos:
        duracao_s = _calc_duracao_s(novo_inicio, novo_fim)
        sets.append("duracao_s = ?")
        valores.append(duracao_s)

    if not sets:
        return existente

    sets.append("atualizado_em = ?")
    valores.append(_agora_utc())
    valores.append(id)
    conn = _conectar()
    try:
        conn.execute(
            f"UPDATE apontamentos SET {', '.join(sets)} WHERE id = ? AND deletado_em IS NULL",
            valores,
        )
        conn.commit()
    finally:
        conn.close()
    return obter_apontamento(id)


def excluir_apontamento(id: str) -> bool:
    existente = obter_apontamento(id)
    if existente is None:
        return False
    agora = _agora_utc()
    conn = _conectar()
    try:
        conn.execute(
            "UPDATE apontamentos SET deletado_em = ?, atualizado_em = ? WHERE id = ?",
            (agora, agora, id),
        )
        conn.commit()
    finally:
        conn.close()
    return True


# ---------------------------------------------------------------------------
# Timer
# ---------------------------------------------------------------------------

def timer_ativo() -> Optional[dict]:
    """Apontamento em execução (fim NULL, não deletado), se houver."""
    conn = _conectar()
    try:
        row = conn.execute(
            "SELECT * FROM apontamentos WHERE deletado_em IS NULL AND fim IS NULL "
            "ORDER BY inicio DESC LIMIT 1"
        ).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def timer_iniciar(projeto_id: Optional[str] = None, cliente_id: Optional[str] = None,
                   descricao: str = "") -> dict:
    """Modelo Toggl: `cliente_id` opcional, derivado de `projeto_id` quando
    ausente (ver `criar_apontamento`)."""
    projeto = None
    if projeto_id:
        projeto = obter_projeto(projeto_id)
        if projeto is None:
            raise ValueError("Projeto não encontrado.")
    if cliente_id:
        if obter_cliente(cliente_id) is None:
            raise ValueError("Cliente não encontrado.")
    elif projeto is not None:
        cliente_id = projeto.get("cliente_id")

    with _timer_lock:
        if timer_ativo() is not None:
            raise TimerAtivoError("Já existe um timer em execução.")

        id_ = str(uuid.uuid4())
        agora = _agora_utc()
        inicio = _agora_local()
        conn = _conectar()
        try:
            conn.execute(
                """INSERT INTO apontamentos
                   (id, cliente_id, projeto_id, descricao, inicio, fim, duracao_s,
                    origem, reuniao_ref, criado_em, atualizado_em, deletado_em)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, 'timer', NULL, ?, ?, NULL)""",
                (id_, cliente_id, projeto_id, descricao or "", inicio, agora, agora),
            )
            conn.commit()
        finally:
            conn.close()
    return obter_apontamento(id_)


def timer_parar() -> dict:
    ativo = timer_ativo()
    if ativo is None:
        raise TimerInativoError("Não há timer em execução.")

    fim = _agora_local()
    duracao_s = _calc_duracao_s(ativo["inicio"], fim)
    agora = _agora_utc()
    conn = _conectar()
    try:
        conn.execute(
            "UPDATE apontamentos SET fim = ?, duracao_s = ?, atualizado_em = ? WHERE id = ?",
            (fim, duracao_s, agora, ativo["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return obter_apontamento(ativo["id"])


class TimerAtivoError(RuntimeError):
    """Já existe um timer em execução (mapeado para HTTP 409 no endpoint)."""


class TimerInativoError(RuntimeError):
    """Não há timer em execução (mapeado para HTTP 409 no endpoint)."""


# ---------------------------------------------------------------------------
# Suporte a sincronização (usado por sync.py)
# ---------------------------------------------------------------------------

def clientes_todos_incluindo_deletados() -> list[dict]:
    conn = _conectar()
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM clientes").fetchall()]
    finally:
        conn.close()


def clientes_upsert_bruto(registro: dict) -> bool:
    try:
        uuid.UUID(str(registro.get("id")))
    except (ValueError, AttributeError, TypeError):
        return False
    conn = _conectar()
    try:
        conn.execute(
            """INSERT INTO clientes (id, nome, valor_hora, ativo, criado_em, atualizado_em, deletado_em)
               VALUES (:id, :nome, :valor_hora, :ativo, :criado_em, :atualizado_em, :deletado_em)
               ON CONFLICT(id) DO UPDATE SET
                 nome=excluded.nome, valor_hora=excluded.valor_hora, ativo=excluded.ativo,
                 criado_em=excluded.criado_em, atualizado_em=excluded.atualizado_em,
                 deletado_em=excluded.deletado_em""",
            {**registro, "ativo": 1 if registro.get("ativo") else 0},
        )
        conn.commit()
    finally:
        conn.close()
    return True


def projetos_todos_incluindo_deletados() -> list[dict]:
    conn = _conectar()
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM projetos").fetchall()]
    finally:
        conn.close()


def projetos_upsert_bruto(registro: dict) -> bool:
    try:
        uuid.UUID(str(registro.get("id")))
    except (ValueError, AttributeError, TypeError):
        return False
    conn = _conectar()
    try:
        conn.execute(
            """INSERT INTO projetos (id, cliente_id, nome, ativo, criado_em, atualizado_em, deletado_em)
               VALUES (:id, :cliente_id, :nome, :ativo, :criado_em, :atualizado_em, :deletado_em)
               ON CONFLICT(id) DO UPDATE SET
                 cliente_id=excluded.cliente_id, nome=excluded.nome, ativo=excluded.ativo,
                 criado_em=excluded.criado_em, atualizado_em=excluded.atualizado_em,
                 deletado_em=excluded.deletado_em""",
            {**registro, "ativo": 1 if registro.get("ativo") else 0},
        )
        conn.commit()
    finally:
        conn.close()
    return True


def apontamentos_todos_incluindo_deletados() -> list[dict]:
    conn = _conectar()
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM apontamentos").fetchall()]
    finally:
        conn.close()


def apontamentos_upsert_bruto(registro: dict) -> bool:
    try:
        uuid.UUID(str(registro.get("id")))
    except (ValueError, AttributeError, TypeError):
        return False
    conn = _conectar()
    try:
        conn.execute(
            """INSERT INTO apontamentos
               (id, cliente_id, projeto_id, descricao, inicio, fim, duracao_s,
                origem, reuniao_ref, criado_em, atualizado_em, deletado_em)
               VALUES (:id, :cliente_id, :projeto_id, :descricao, :inicio, :fim, :duracao_s,
                       :origem, :reuniao_ref, :criado_em, :atualizado_em, :deletado_em)
               ON CONFLICT(id) DO UPDATE SET
                 cliente_id=excluded.cliente_id, projeto_id=excluded.projeto_id,
                 descricao=excluded.descricao, inicio=excluded.inicio, fim=excluded.fim,
                 duracao_s=excluded.duracao_s, origem=excluded.origem,
                 reuniao_ref=excluded.reuniao_ref, criado_em=excluded.criado_em,
                 atualizado_em=excluded.atualizado_em, deletado_em=excluded.deletado_em""",
            registro,
        )
        conn.commit()
    finally:
        conn.close()
    return True
