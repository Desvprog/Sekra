#!/usr/bin/env python3
"""
Relatório de horas por cliente, para faturamento de trabalho por hora.

Agrupa as reuniões de um mês por cliente somando as durações (duracao_s do
meta.json). Se a config tiver valor/hora para o cliente, calcula também o
valor a faturar. Não lê o filesystem diretamente: recebe a lista de reuniões
já montada por server.listar_reunioes_fs() (evita import circular).
"""

import csv
import io
from datetime import date
from typing import Optional

SEM_CLIENTE = "(sem cliente)"


def mes_corrente() -> str:
    return date.today().strftime("%Y-%m")


def gerar(reunioes: list[dict], mes: Optional[str] = None,
          cliente: Optional[str] = None,
          valores_hora: Optional[dict] = None) -> dict:
    """
    Monta o relatório do mês (AAAA-MM; padrão: mês corrente), opcionalmente
    filtrado por cliente. Retorna:
    {
      "mes": "AAAA-MM",
      "filtro_cliente": str | None,
      "grupos": [{"cliente", "reunioes": [{"data","slug","titulo","duracao_s"}],
                  "total_s", "valor"}],
      "total_geral_s": int
    }
    "valor" é None quando não há valor/hora configurado para o cliente.
    """
    mes = (mes or "").strip() or mes_corrente()
    filtro = (cliente or "").strip() or None
    valores_hora = valores_hora or {}

    grupos: dict[str, dict] = {}
    for r in reunioes:
        if not r["data"].startswith(mes):
            continue
        nome = (r.get("cliente") or "").strip() or SEM_CLIENTE
        if filtro and nome != filtro:
            continue
        g = grupos.setdefault(nome, {"cliente": nome, "reunioes": [], "total_s": 0})
        duracao = r.get("duracao_s") or 0
        g["reunioes"].append({
            "data": r["data"],
            "slug": r["id"].split("/", 1)[1],
            "titulo": r["titulo"],
            "duracao_s": duracao,
        })
        g["total_s"] += duracao

    saida = []
    # Ordena por nome; "(sem cliente)" por último
    for nome in sorted(grupos, key=lambda n: (n == SEM_CLIENTE, n.lower())):
        g = grupos[nome]
        g["total_s"] = round(g["total_s"])
        valor_hora = valores_hora.get(nome)
        g["valor"] = (round(g["total_s"] / 3600 * valor_hora, 2)
                      if isinstance(valor_hora, (int, float)) else None)
        g["reunioes"].sort(key=lambda x: x["data"])
        saida.append(g)

    return {
        "mes": mes,
        "filtro_cliente": filtro,
        "grupos": saida,
        "total_geral_s": round(sum(g["total_s"] for g in saida)),
    }


def para_csv(dados: dict, valores_hora: Optional[dict] = None) -> str:
    """
    Converte o relatório de gerar() em CSV (separador ';'), uma linha por
    reunião: data;titulo;cliente;horas;valor. Valor fica vazio quando não há
    valor/hora configurado para o cliente.
    """
    valores_hora = valores_hora or {}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["data", "titulo", "cliente", "horas", "valor"])
    for g in dados["grupos"]:
        valor_hora = valores_hora.get(g["cliente"])
        for r in g["reunioes"]:
            horas = r["duracao_s"] / 3600
            valor = (f"{horas * valor_hora:.2f}"
                     if isinstance(valor_hora, (int, float)) else "")
            w.writerow([r["data"], r["titulo"], g["cliente"], f"{horas:.2f}", valor])
    return buf.getvalue()
