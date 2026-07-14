#!/usr/bin/env python3
"""
Detecção automática de reunião via PulseAudio.

Thread daemon que consulta `pactl list source-outputs` a cada 5s e detecta
quando um app de reunião (navegador, Teams, Zoom, Discord...) abre o
microfone. O server injeta callbacks via iniciar() para evitar import
circular; o estado detectado é lido pelo snapshot() no /api/status.

A captura do próprio app (ffmpeg abre um source-output no mic ao gravar)
é sempre ignorada.
"""

import re
import threading
import time
from typing import Callable, Optional

import config
import reuniao

POLL_S = 5

# Binários que nunca contam como reunião (nosso próprio gravador)
IGNORAR_BINARIOS = ("ffmpeg",)

# Estado do monitor (protegido por _lock)
_lock = threading.Lock()
_detectado = False
_app: Optional[str] = None
_desde: Optional[float] = None
_pausado = False  # quando True, não auto-inicia gravação (detecção segue ativa)

# Callbacks injetados pelo server (evita import circular)
_iniciar_gravacao: Optional[Callable[..., object]] = None
_esta_gravando: Optional[Callable[[], bool]] = None

_thread_iniciada = False


def snapshot() -> dict:
    """Estado atual da detecção, para compor o /api/status."""
    with _lock:
        return {"detectado": _detectado, "app": _app, "desde": _desde}


def pausar() -> None:
    """Suspende o auto-início de gravação (a detecção continua)."""
    global _pausado
    with _lock:
        _pausado = True


def retomar() -> None:
    global _pausado
    with _lock:
        _pausado = False


def _parse_source_outputs(saida: str) -> list[dict]:
    """
    Parseia a saída de `pactl list source-outputs` em blocos, extraindo
    application.name e application.process.binary das Properties.
    """
    blocos: list[dict] = []
    atual: Optional[dict] = None
    for linha in saida.splitlines():
        if linha.startswith("Source Output #"):
            atual = {"name": "", "binary": ""}
            blocos.append(atual)
            continue
        if atual is None:
            continue
        m = re.search(r'application\.name = "(.*)"', linha)
        if m:
            atual["name"] = m.group(1)
            continue
        m = re.search(r'application\.process\.binary = "(.*)"', linha)
        if m:
            atual["binary"] = m.group(1)
    return blocos


def _detectar(apps: list[str]) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Consulta o PulseAudio e retorna (detectado, nome_do_app, termo_casado).
    Levanta RuntimeError se pactl falhar (tratado no loop).
    """
    saida = reuniao.pactl("list", "source-outputs")
    for bloco in _parse_source_outputs(saida):
        binario = bloco["binary"].lower()
        # Ignora a captura do nosso próprio ffmpeg
        if any(ig in binario for ig in IGNORAR_BINARIOS):
            continue
        alvo = f"{bloco['name']} {bloco['binary']}".lower()
        for termo in apps:
            t = termo.strip().lower()
            if t and t in alvo:
                return True, bloco["name"] or bloco["binary"], t
    return False, None, None


def _loop() -> None:
    global _detectado, _app, _desde
    while True:
        try:
            cfg_full = config.carregar()
            cfg = cfg_full.get("deteccao", {})
            if not cfg.get("ativa", True):
                # Detecção desligada: zera estado e nem consulta o pactl
                with _lock:
                    _detectado, _app, _desde = False, None, None
                time.sleep(POLL_S)
                continue

            detectado_novo, nome, termo = _detectar(cfg.get("apps", []))

            with _lock:
                antes = _detectado
                pausado = _pausado
                if detectado_novo and not antes:
                    _detectado, _app, _desde = True, nome, time.time()
                elif not detectado_novo and antes:
                    # true→false: só atualiza estado — quem sugere parar é o
                    # Electron (a reunião pode continuar por outro caminho)
                    _detectado, _app, _desde = False, None, None

            # Transição false→true com auto-início habilitado
            if (detectado_novo and not antes
                    and cfg.get("auto_iniciar") and not pausado
                    and _iniciar_gravacao is not None
                    and _esta_gravando is not None
                    and not _esta_gravando()):
                try:
                    _iniciar_gravacao(f"reuniao-{termo}",
                                      cfg_full.get("modelo_padrao", "medium"))
                except Exception as e:
                    # Corrida com gravação manual ou erro de dispositivo —
                    # apenas registra; a thread nunca pode morrer
                    print(f"[monitor] Auto-início falhou: {e}")
        except RuntimeError:
            # pactl indisponível/falhou — tenta de novo no próximo ciclo
            pass
        except Exception as e:
            print(f"[monitor] Erro no loop de detecção: {e}")
        time.sleep(POLL_S)


def iniciar(iniciar_gravacao: Callable[..., object],
            esta_gravando: Callable[[], bool]) -> None:
    """
    Registra os callbacks do server e inicia a thread daemon (uma única vez).
    iniciar_gravacao(titulo) deve levantar RuntimeError se já houver gravação.
    """
    global _iniciar_gravacao, _esta_gravando, _thread_iniciada
    _iniciar_gravacao = iniciar_gravacao
    _esta_gravando = esta_gravando
    if not _thread_iniciada:
        _thread_iniciada = True
        threading.Thread(target=_loop, daemon=True).start()
