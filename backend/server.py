#!/usr/bin/env python3
"""
Servidor web local para gerenciar gravação e transcrição de reuniões.
Acesse em http://localhost:8765 após iniciar.
"""

import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import reuniao

PORT = 8654
# Quando empacotado pelo PyInstaller, arquivos de dados ficam em sys._MEIPASS
_BASE = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).parent.parent
STATIC_DIR = _BASE / "static"

app = FastAPI(title="Reuniões")


# ────────────────────────────────────────────────────────────────────────────
# Estado global (singleton, thread-safe)
# ────────────────────────────────────────────────────────────────────────────

class Estado:
    def __init__(self):
        self.lock = threading.Lock()
        self.gravando = False
        self.processando = False
        self.titulo: Optional[str] = None
        self.inicio: Optional[float] = None
        self.pasta: Optional[Path] = None
        self.proc = None
        self.opcoes: dict = {}
        self.msg = ""
        self.erro: Optional[str] = None

    def snapshot(self) -> dict:
        with self.lock:
            duracao = int(time.time() - self.inicio) if self.gravando and self.inicio else 0
            return {
                "gravando": self.gravando,
                "processando": self.processando,
                "titulo": self.titulo,
                "duracao_s": duracao,
                "msg": self.msg,
                "erro": self.erro,
            }

    def set_msg(self, msg: str) -> None:
        with self.lock:
            self.msg = msg

    def set_erro(self, msg: str) -> None:
        with self.lock:
            self.erro = msg

    def limpar_erro(self) -> None:
        with self.lock:
            self.erro = None


estado = Estado()


# ────────────────────────────────────────────────────────────────────────────
# Schemas de entrada
# ────────────────────────────────────────────────────────────────────────────

class IniciarBody(BaseModel):
    titulo: str = "reuniao"
    modelo: str = "medium"
    diarizar: bool = False
    hotwords: str = ""


class ReprocessarBody(BaseModel):
    modelo: str = "medium"
    diarizar: bool = False
    hotwords: str = ""


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def parsear_hotwords(s: str) -> list[str]:
    return [h.strip() for h in s.split(",") if h.strip()]


def _info_audio(pasta: Path) -> Optional[dict]:
    """Retorna info de áudio da pasta ou None se não houver nenhum arquivo."""
    p_mix = pasta / "audio.wav"
    p_mic = pasta / "audio-mic.wav"
    p_loop = pasta / "audio-loopback.wav"

    if p_mix.exists() and p_mix.stat().st_size > reuniao.MIN_AUDIO_BYTES:
        return {"tamanho_mb": round(p_mix.stat().st_size / 1024 / 1024, 1),
                "audio_incompleto": False}

    # Gravação interrompida — verifica arquivos parciais
    parciais = [p for p in (p_mix, p_mic, p_loop) if p.exists() and p.stat().st_size > 0]
    if not parciais:
        return None
    tamanho = max(p.stat().st_size for p in parciais)
    return {"tamanho_mb": round(tamanho / 1024 / 1024, 1), "audio_incompleto": True}


def listar_reunioes_fs() -> list[dict]:
    """Lê ~/reunioes/ e retorna metadados de cada reunião."""
    base = reuniao.BASE_DIR
    if not base.exists():
        return []
    out = []
    for dia in sorted(base.iterdir(), reverse=True):
        if not dia.is_dir():
            continue
        for r in sorted(dia.iterdir(), reverse=True):
            if not r.is_dir():
                continue
            info = _info_audio(r)
            if info is None:
                continue
            partes = r.name.split("-")
            hora = f"{partes[0]}:{partes[1]}" if len(partes) >= 2 else "??:??"
            titulo = "-".join(partes[2:]) if len(partes) > 2 else r.name
            out.append({
                "id": f"{dia.name}/{r.name}",
                "data": dia.name,
                "hora": hora,
                "titulo": titulo,
                "tamanho_mb": info["tamanho_mb"],
                "audio_incompleto": info["audio_incompleto"],
                "tem_transcricao": (r / "transcricao.txt").exists(),
                "tem_hotwords": (r / "hotwords.md").exists(),
            })
    return out


def pasta_da_reuniao(data: str, slug: str) -> Path:
    p = reuniao.BASE_DIR / data / slug
    if not p.is_dir():
        raise HTTPException(404, f"Reunião não encontrada: {data}/{slug}")
    return p


def rodar_processamento_em_thread(pasta: Path, titulo: str, modelo: str,
                                   diarizar: bool, hotwords: list[str]) -> None:
    """Roda processar() em thread separada, atualizando estado."""
    def alvo():
        try:
            estado.limpar_erro()
            reuniao.processar(pasta, titulo, modelo, diarizar, hotwords,
                              progress_cb=estado.set_msg)
        except BaseException as e:
            estado.set_erro(str(e) or type(e).__name__)
        finally:
            with estado.lock:
                estado.processando = False
                estado.titulo = None
                estado.msg = ""

    threading.Thread(target=alvo, daemon=True).start()


# ────────────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def status():
    return estado.snapshot()


@app.post("/api/erro/limpar")
def limpar_erro():
    estado.limpar_erro()
    return {"ok": True}


@app.get("/api/reunioes")
def listar():
    return listar_reunioes_fs()


@app.get("/api/reunioes/{data}/{slug}/audio")
def audio(data: str, slug: str):
    p = pasta_da_reuniao(data, slug) / "audio.wav"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/wav")


@app.get("/api/reunioes/{data}/{slug}/transcricao")
def transcricao(data: str, slug: str):
    p = pasta_da_reuniao(data, slug) / "transcricao.txt"
    if not p.exists():
        return JSONResponse({"texto": None}, status_code=404)
    return {"texto": p.read_text(encoding="utf-8")}


@app.get("/api/reunioes/{data}/{slug}/hotwords")
def hotwords_endpoint(data: str, slug: str):
    p = pasta_da_reuniao(data, slug) / "hotwords.md"
    if not p.exists():
        return JSONResponse({"texto": None}, status_code=404)
    return {"texto": p.read_text(encoding="utf-8")}


@app.post("/api/gravar/iniciar")
def iniciar(body: IniciarBody):
    with estado.lock:
        if estado.gravando or estado.processando:
            raise HTTPException(409, "Já existe gravação ou processamento ativo")

        agora = datetime.now()
        titulo_limpo = body.titulo.replace(" ", "-").lower() or "reuniao"
        pasta = (reuniao.BASE_DIR / agora.strftime("%Y-%m-%d")
                 / f"{agora.strftime('%H-%M')}-{titulo_limpo}")
        pasta.mkdir(parents=True, exist_ok=True)

        try:
            monitor, mic = reuniao.detectar_dispositivos()
            proc = reuniao.iniciar_gravacao(pasta, monitor, mic)
        except SystemExit as e:
            raise HTTPException(500, str(e))

        estado.gravando = True
        estado.titulo = body.titulo
        estado.inicio = time.time()
        estado.pasta = pasta
        estado.proc = proc
        estado.opcoes = {
            "diarizar": body.diarizar,
            "hotwords": parsear_hotwords(body.hotwords),
            "modelo": body.modelo,
        }
        estado.msg = "Gravando..."

    return {"ok": True, "pasta": str(pasta)}


@app.post("/api/gravar/parar")
def parar():
    with estado.lock:
        if not estado.gravando:
            raise HTTPException(409, "Nenhuma gravação ativa")
        proc = estado.proc
        pasta = estado.pasta
        opcoes = estado.opcoes
        titulo = estado.titulo
        estado.gravando = False
        estado.proc = None
        estado.processando = True
        estado.msg = "Finalizando gravação..."

    reuniao.parar_gravacao(proc)

    rodar_processamento_em_thread(
        pasta, titulo, opcoes["modelo"],
        opcoes["diarizar"], opcoes["hotwords"]
    )
    return {"ok": True}


@app.post("/api/reunioes/{data}/{slug}/excluir")
def excluir(data: str, slug: str):
    import shutil
    pasta = pasta_da_reuniao(data, slug)
    with estado.lock:
        if estado.pasta == pasta and (estado.gravando or estado.processando):
            raise HTTPException(409, "Não é possível excluir uma reunião em andamento")
    shutil.rmtree(pasta)
    return {"ok": True}


@app.post("/api/reunioes/{data}/{slug}/reprocessar")
def reprocessar(data: str, slug: str, body: ReprocessarBody):
    pasta = pasta_da_reuniao(data, slug)
    with estado.lock:
        if estado.gravando or estado.processando:
            raise HTTPException(409, "Já existe operação ativa")
        estado.processando = True
        estado.titulo = slug
        estado.msg = "Reprocessando..."

    rodar_processamento_em_thread(
        pasta, slug, body.modelo, body.diarizar,
        parsear_hotwords(body.hotwords)
    )
    return {"ok": True}


# ────────────────────────────────────────────────────────────────────────────
# Static files (a UI)
# ────────────────────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


# ────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ────────────────────────────────────────────────────────────────────────────

def abrir_navegador():
    time.sleep(1.2)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    threading.Thread(target=abrir_navegador, daemon=True).start()
    print(f"\n🌐 Servidor em http://localhost:{PORT}\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
