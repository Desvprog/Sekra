#!/usr/bin/env python3
"""
Relatório de horas por cliente, para faturamento de trabalho por hora.

Agrega DUAS fontes:
  - reuniões: duracao_s do meta.json, lidas do filesystem (comportamento
    original, inalterado);
  - apontamentos: lançamentos manuais/timer do timetracking (backend/horas.py),
    já filtrados para "encerrados" (fim preenchido) e não deletados.

Este módulo não acessa filesystem nem SQLite diretamente: recebe as listas já
montadas pelo caller (server.py), evitando import circular e mantendo o
módulo fácil de testar.

Tarifa (valor/hora): resolvida preferencialmente pela tabela `clientes`
(por nome, para reuniões — que guardam cliente como string solta; por
cliente_id, para apontamentos), com fallback em config.valores_hora (mapa
nome -> valor, compat com o formato antigo).
"""

import csv
import io
import re
from datetime import date
from typing import Optional

SEM_CLIENTE = "(sem cliente)"

_RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def mes_corrente() -> str:
    return date.today().strftime("%Y-%m")


def _data_valida(valor: Optional[str]) -> bool:
    return bool(valor) and bool(_RE_DATA.match(valor))


def intervalo_valido(inicio: Optional[str], fim: Optional[str]) -> bool:
    """True quando `inicio` e `fim` (AAAA-MM-DD) são ambos strings válidas.
    Formato inválido em qualquer um dos dois faz o intervalo ser ignorado
    (o filtro cai de volta para `mes`) — usado tanto aqui quanto em
    server.py para decidir se busca apontamentos do mês ou de todo o
    período."""
    return _data_valida(inicio) and _data_valida(fim)


def _monta_valores_por_nome(clientes: Optional[list[dict]], valores_hora: Optional[dict]) -> dict:
    """Mapa nome -> valor_hora. Tabela `clientes` tem prioridade; config.valores_hora
    é usado como fallback (compat) para nomes que não estejam na tabela."""
    saida = dict(valores_hora or {})
    for c in (clientes or []):
        nome = (c.get("nome") or "").strip()
        if nome:
            saida[nome] = c.get("valor_hora")
    return saida


def _resolve_nome_cliente(apontamento: dict, clientes_por_id: dict, projetos_por_id: dict) -> str:
    """Resolve o nome do cliente de um apontamento (modelo Toggl): cliente
    direto no apontamento tem prioridade; na ausência, cai para o cliente do
    projeto vinculado; sem nenhum dos dois, agrupa como SEM_CLIENTE. Um
    `cliente_id`/`projeto.cliente_id` apontando para um cliente inexistente
    (deletado) é distinto de "sem cliente": vira "(cliente removido)"."""
    cliente_id = apontamento.get("cliente_id")
    if cliente_id:
        cli = clientes_por_id.get(cliente_id)
        return cli["nome"] if cli else "(cliente removido)"

    projeto = projetos_por_id.get(apontamento.get("projeto_id"))
    projeto_cliente_id = projeto.get("cliente_id") if projeto else None
    if projeto_cliente_id:
        cli = clientes_por_id.get(projeto_cliente_id)
        return cli["nome"] if cli else "(cliente removido)"

    return SEM_CLIENTE


def gerar(reunioes: list[dict], apontamentos: Optional[list[dict]] = None,
          mes: Optional[str] = None, cliente: Optional[str] = None,
          clientes: Optional[list[dict]] = None, projetos: Optional[list[dict]] = None,
          valores_hora: Optional[dict] = None,
          inicio: Optional[str] = None, fim: Optional[str] = None) -> dict:
    """
    Monta o relatório do mês (AAAA-MM; padrão: mês corrente), opcionalmente
    filtrado por cliente (nome). Alternativamente, quando `inicio` e `fim`
    (ambos AAAA-MM-DD, validados por regex) são fornecidos, filtra pelo
    intervalo de datas [inicio, fim] inclusive — nesse caso o intervalo tem
    precedência sobre `mes`. Formato inválido em `inicio`/`fim` faz o filtro
    de intervalo ser ignorado, caindo de volta para `mes` (retrocompatível).
    Retorna:
    {
      "mes": "AAAA-MM",
      "filtro_cliente": str | None,
      "filtro_inicio": str | None,
      "filtro_fim": str | None,
      "grupos": [{
        "cliente": str,
        "reunioes": [{"data","slug","titulo","duracao_s"}],
        "apontamentos": [{"data","descricao","projeto","origem","duracao_s"}],
        "reunioes_s": int, "apontamentos_s": int, "total_s": int,
        "projetos": [{"projeto": str, "total_s": int, "valor": float | None}],
        "valor": float | None,
        "valor_hora": float | None,
      }],
      "total_geral_s": int
    }
    "valor" é None quando não há valor/hora configurado para o cliente.
    """
    mes = (mes or "").strip() or mes_corrente()
    filtro = (cliente or "").strip() or None
    usa_intervalo = intervalo_valido(inicio, fim)
    filtro_inicio = inicio if usa_intervalo else None
    filtro_fim = fim if usa_intervalo else None
    apontamentos = apontamentos or []
    valores_por_nome = _monta_valores_por_nome(clientes, valores_hora)
    clientes_por_id = {c["id"]: c for c in (clientes or [])}
    projetos_por_id = {p["id"]: p for p in (projetos or [])}

    grupos: dict[str, dict] = {}

    def _grupo(nome: str) -> dict:
        return grupos.setdefault(nome, {
            "cliente": nome, "reunioes": [], "apontamentos": [],
            "reunioes_s": 0, "apontamentos_s": 0,
            "_projetos": {},
        })

    for r in reunioes:
        if usa_intervalo:
            if not (filtro_inicio <= r["data"] <= filtro_fim):
                continue
        elif not r["data"].startswith(mes):
            continue
        nome = (r.get("cliente") or "").strip() or SEM_CLIENTE
        if filtro and nome != filtro:
            continue
        g = _grupo(nome)
        duracao = r.get("duracao_s") or 0
        g["reunioes"].append({
            "data": r["data"],
            "slug": r["id"].split("/", 1)[1],
            "titulo": r["titulo"],
            "duracao_s": duracao,
        })
        g["reunioes_s"] += duracao

    for a in apontamentos:
        inicio_ap = a.get("inicio") or ""
        data_ap = inicio_ap.split("T", 1)[0]
        if usa_intervalo:
            if not (filtro_inicio <= data_ap <= filtro_fim):
                continue
        elif not inicio_ap.startswith(mes):
            continue
        nome = _resolve_nome_cliente(a, clientes_por_id, projetos_por_id)
        if filtro and nome != filtro:
            continue
        g = _grupo(nome)
        duracao = a.get("duracao_s") or 0
        proj = projetos_por_id.get(a.get("projeto_id"))
        nome_projeto = proj.get("nome") if proj else None
        g["apontamentos"].append({
            "data": data_ap,
            "descricao": a.get("descricao") or "",
            "projeto": nome_projeto,
            "origem": a.get("origem") or "manual",
            "duracao_s": duracao,
        })
        g["apontamentos_s"] += duracao
        if nome_projeto:
            g["_projetos"][nome_projeto] = g["_projetos"].get(nome_projeto, 0) + duracao

    saida = []
    # Ordena por nome; "(sem cliente)" por último
    for nome in sorted(grupos, key=lambda n: (n == SEM_CLIENTE, n.lower())):
        g = grupos[nome]
        g["reunioes_s"] = round(g["reunioes_s"])
        g["apontamentos_s"] = round(g["apontamentos_s"])
        g["total_s"] = g["reunioes_s"] + g["apontamentos_s"]
        valor_hora = valores_por_nome.get(nome)
        valor_hora = valor_hora if isinstance(valor_hora, (int, float)) else None
        g["valor_hora"] = valor_hora
        g["valor"] = (round(g["total_s"] / 3600 * valor_hora, 2)
                      if valor_hora is not None else None)
        g["reunioes"].sort(key=lambda x: x["data"])
        g["apontamentos"].sort(key=lambda x: x["data"])
        g["projetos"] = [
            {
                "projeto": p,
                "total_s": round(s),
                "valor": round(s / 3600 * valor_hora, 2) if valor_hora is not None else None,
            }
            for p, s in sorted(g["_projetos"].items())
        ]
        del g["_projetos"]
        saida.append(g)

    return {
        "mes": mes,
        "filtro_cliente": filtro,
        "filtro_inicio": filtro_inicio,
        "filtro_fim": filtro_fim,
        "grupos": saida,
        "total_geral_s": round(sum(g["total_s"] for g in saida)),
    }


def para_csv(dados: dict) -> str:
    """
    Converte o relatório de gerar() em CSV (separador ';'), uma linha por
    reunião/apontamento: data;titulo;cliente;origem;projeto;horas;valor.
    Valor fica vazio quando não há valor/hora configurado para o cliente
    (o mesmo valor/hora do grupo é aplicado a todas as suas linhas).
    """
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["data", "titulo", "cliente", "origem", "projeto", "horas", "valor"])
    for g in dados["grupos"]:
        valor_hora = g.get("valor_hora")

        def _valor(duracao_s: int) -> str:
            if valor_hora is None:
                return ""
            return f"{(duracao_s / 3600) * valor_hora:.2f}"

        for r in g["reunioes"]:
            horas = r["duracao_s"] / 3600
            w.writerow([r["data"], r["titulo"], g["cliente"], "reuniao", "",
                        f"{horas:.2f}", _valor(r["duracao_s"])])
        for a in g["apontamentos"]:
            horas = a["duracao_s"] / 3600
            titulo = a["descricao"] or "(apontamento)"
            w.writerow([a["data"], titulo, g["cliente"], a["origem"], a["projeto"] or "",
                        f"{horas:.2f}", _valor(a["duracao_s"])])
    return buf.getvalue()
