#!/usr/bin/env python3
"""
Dashboard semanal: tempo total (reuniões + apontamentos), nº de reuniões,
reuniões recentes e distribuição de tempo por cliente na semana.

Não acessa filesystem/SQLite diretamente: recebe as listas já montadas pelo
caller (server.py), no mesmo espírito de relatorio.py — evita import
circular e mantém o módulo fácil de testar.
"""

from datetime import date, datetime, timedelta
from typing import Optional


def _parse_data(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def semana_de(ref: Optional[str] = None) -> tuple[date, date]:
    """Retorna (inicio, fim) da semana (segunda a domingo) que contém `ref`
    (AAAA-MM-DD; padrão: hoje)."""
    d = _parse_data(ref) if ref else None
    if d is None:
        d = date.today()
    inicio = d - timedelta(days=d.weekday())  # weekday(): segunda=0
    fim = inicio + timedelta(days=6)
    return inicio, fim


def gerar(reunioes: list[dict], apontamentos: list[dict],
          ref: Optional[str] = None,
          clientes: Optional[list[dict]] = None) -> dict:
    """
    Monta o dashboard da semana (segunda a domingo) que contém `ref`.

    `reunioes`: saída de listar_reunioes_fs() (dicts com data, id, titulo,
    cliente, projeto, duracao_s).
    `apontamentos`: saída de horas.listar_apontamentos() (dicts com inicio,
    fim, duracao_s, cliente_id, deletado_em já filtrado).
    `clientes`: saída de horas.listar_clientes(), usada para resolver o nome
    do cliente de cada apontamento (que só guarda cliente_id).
    """
    inicio, fim = semana_de(ref)
    inicio_s, fim_s = inicio.isoformat(), fim.isoformat()

    por_cliente: dict[str, int] = {}
    tempo_semana_s = 0
    num_reunioes_semana = 0

    for r in reunioes:
        data_r = _parse_data(r.get("data"))
        if data_r is None or not (inicio <= data_r <= fim):
            continue
        duracao = r.get("duracao_s") or 0
        tempo_semana_s += duracao
        num_reunioes_semana += 1
        nome = (r.get("cliente") or "").strip()
        if nome:
            por_cliente[nome] = por_cliente.get(nome, 0) + duracao

    clientes_por_id = {c["id"]: (c.get("nome") or "").strip() for c in (clientes or [])}
    for a in apontamentos:
        # já vem filtrado por deletado_em IS NULL (listar_apontamentos);
        # aqui aplicamos fim preenchido + inicio na semana.
        if a.get("fim") is None:
            continue
        inicio_ap = str(a.get("inicio") or "")[:10]
        data_ap = _parse_data(inicio_ap)
        if data_ap is None or not (inicio <= data_ap <= fim):
            continue
        duracao = a.get("duracao_s") or 0
        tempo_semana_s += duracao
        cid = a.get("cliente_id")
        nome = clientes_por_id.get(cid)
        if nome:
            por_cliente[nome] = por_cliente.get(nome, 0) + duracao

    recentes = []
    for r in reunioes[:5]:
        recentes.append({
            "data": r.get("data"),
            "slug": r["id"].split("/", 1)[1] if "/" in (r.get("id") or "") else r.get("id"),
            "titulo": r.get("titulo"),
            "cliente": r.get("cliente") or "",
            "projeto": r.get("projeto") or "",
            "duracao_s": r.get("duracao_s") or 0,
        })

    por_cliente_saida = [
        {"cliente": nome, "tempo_s": tempo}
        for nome, tempo in sorted(por_cliente.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {
        "semana": {"inicio": inicio_s, "fim": fim_s},
        "tempo_semana_s": round(tempo_semana_s),
        "num_reunioes_semana": num_reunioes_semana,
        "reunioes_recentes": recentes,
        "por_cliente": por_cliente_saida,
    }
