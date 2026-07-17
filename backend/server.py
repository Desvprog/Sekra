#!/usr/bin/env python3
"""
Servidor web local para gerenciar gravação e transcrição de reuniões.
Acesse em http://localhost:8765 após iniciar.
"""

import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import reuniao
import config
import meta
import busca
import exportar
import resumo
import monitor
import relatorio

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
        # Campos de gravação
        self.gravando = False
        self.titulo: Optional[str] = None
        self.inicio: Optional[float] = None
        self.pasta: Optional[Path] = None
        self.proc = None
        self.opcoes: dict = {}
        # Campos de processamento (worker)
        self.processando: bool = False
        self.titulo_processando: Optional[str] = None
        self.pasta_processando: Optional[Path] = None
        self.fila: queue.Queue = queue.Queue()
        self.pendentes: list[dict] = []  # cada item: {"titulo": str, "id": str}
        # Mensagens
        self.msg = ""
        self.erro: Optional[str] = None

    def snapshot(self) -> dict:
        det = monitor.snapshot()
        cfg_det = config.carregar().get("deteccao", {})
        with self.lock:
            duracao = int(time.time() - self.inicio) if self.gravando and self.inicio else 0
            return {
                "gravando": self.gravando,
                "duracao_s": duracao,
                "titulo": self.titulo,
                "processando": self.processando,
                "titulo_processando": self.titulo_processando,
                "fila": [p["titulo"] for p in self.pendentes],
                "fila_tamanho": len(self.pendentes),
                "msg": self.msg,
                "erro": self.erro,
                "deteccao": {
                    "detectado": det["detectado"],
                    "app": det["app"],
                    "ativa": bool(cfg_det.get("ativa", True)),
                    "auto_iniciar": bool(cfg_det.get("auto_iniciar", False)),
                },
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
# Fila de processamento serial + worker thread
# ────────────────────────────────────────────────────────────────────────────

def enfileirar(pasta: Path, titulo: str, modelo: str,
               diarizar: bool, hotwords: list, idioma: Optional[str] = None,
               cliente: Optional[str] = None) -> int:
    """Adiciona um item à fila de processamento. Retorna a posição (nº de pendentes)."""
    item = {
        "pasta": pasta,
        "titulo": titulo,
        "modelo": modelo,
        "diarizar": diarizar,
        "hotwords": hotwords,
        "idioma": idioma,
        "cliente": cliente,
        "id": str(pasta),
    }
    with estado.lock:
        estado.pendentes.append({"titulo": titulo, "id": str(pasta)})
        n = len(estado.pendentes)
    estado.fila.put(item)
    return n


def _worker_loop():
    while True:
        item = estado.fila.get()  # bloqueia até haver item

        # Gravação tem prioridade: espera enquanto houver gravação ativa.
        # Um item já em andamento NÃO é interrompido — apenas não se inicia
        # um novo item enquanto estiver gravando.
        while True:
            with estado.lock:
                gravando = estado.gravando
            if not gravando:
                break
            time.sleep(1)

        # Remove este item dos pendentes e marca processando
        with estado.lock:
            estado.pendentes = [p for p in estado.pendentes if p["id"] != item["id"]]
            estado.processando = True
            estado.titulo_processando = item["titulo"]
            estado.pasta_processando = item["pasta"]
            estado.erro = None
            estado.msg = "Processando..."

        try:
            reuniao.processar(
                item["pasta"], item["titulo"], item["modelo"],
                item["diarizar"], item["hotwords"],
                progress_cb=estado.set_msg, idioma=item["idioma"],
                cliente=item.get("cliente"),
            )
        except BaseException as e:
            estado.set_erro(str(e) or type(e).__name__)
        finally:
            with estado.lock:
                estado.processando = False
                estado.titulo_processando = None
                estado.pasta_processando = None
                estado.msg = ""
            estado.fila.task_done()


# Inicia o worker (daemon) uma única vez ao carregar o módulo
threading.Thread(target=_worker_loop, daemon=True).start()


# ────────────────────────────────────────────────────────────────────────────
# Schemas de entrada
# ────────────────────────────────────────────────────────────────────────────

class IniciarBody(BaseModel):
    titulo: str = "reuniao"
    modelo: str = "medium"
    diarizar: bool = False
    hotwords: str = ""
    idioma: Optional[str] = None
    cliente: Optional[str] = None


class ReprocessarBody(BaseModel):
    modelo: str = "medium"
    diarizar: bool = False
    hotwords: str = ""
    idioma: Optional[str] = None


class ConfigBody(BaseModel):
    model_config = {"extra": "allow"}


class ChaveBody(BaseModel):
    provider: str
    chave: str = ""


class TestarChaveBody(BaseModel):
    provider: str
    modelo: str = ""


class PatchReuniaoBody(BaseModel):
    titulo: Optional[str] = None
    speaker_nomes: Optional[dict] = None
    cliente: Optional[str] = None
    arquivada: Optional[bool] = None


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def parsear_hotwords(s: str) -> list[str]:
    return [h.strip() for h in s.split(",") if h.strip()]


def _info_audio(pasta: Path) -> Optional[dict]:
    """Retorna info de áudio da pasta ou None se não houver nenhum arquivo."""
    # Suporta .wav e .opus para cada base
    extensoes = [".wav", ".opus"]
    bases = ["audio", "audio-mic", "audio-loopback"]

    def _encontrar(base: str):
        for ext in extensoes:
            p = pasta / f"{base}{ext}"
            if p.exists():
                return p
        return None

    p_mix = _encontrar("audio")

    if p_mix is not None and p_mix.stat().st_size > reuniao.MIN_AUDIO_BYTES:
        return {"tamanho_mb": round(p_mix.stat().st_size / 1024 / 1024, 1),
                "audio_incompleto": False}

    # Gravação interrompida — verifica arquivos parciais
    parciais = []
    for base in bases:
        p = _encontrar(base)
        if p is not None and p.stat().st_size > 0:
            parciais.append(p)

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
            titulo_parsed = "-".join(partes[2:]) if len(partes) > 2 else r.name

            m = meta.ler(r)
            titulo = m.get("titulo") if m.get("titulo") else titulo_parsed
            duracao_s = m.get("duracao_s")
            duracao_fmt = meta.fmt_duracao(duracao_s) if duracao_s is not None else None

            out.append({
                "id": f"{dia.name}/{r.name}",
                "data": dia.name,
                "hora": hora,
                "titulo": titulo,
                "tamanho_mb": info["tamanho_mb"],
                "audio_incompleto": info["audio_incompleto"],
                "tem_transcricao": (r / "transcricao.txt").exists(),
                "tem_hotwords": (r / "hotwords.md").exists(),
                "tem_resumo": (r / "resumo.md").exists(),
                "duracao_s": duracao_s,
                "duracao_fmt": duracao_fmt,
                "idioma": m.get("idioma"),
                "num_speakers": m.get("num_speakers"),
                "cliente": m.get("cliente") or "",
                "arquivada": bool(m.get("arquivada")),
            })
    return out


def pasta_da_reuniao(data: str, slug: str) -> Path:
    p = reuniao.BASE_DIR / data / slug
    if not p.is_dir():
        raise HTTPException(404, f"Reunião não encontrada: {data}/{slug}")
    return p


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
    pasta = pasta_da_reuniao(data, slug)
    p_wav = pasta / "audio.wav"
    p_opus = pasta / "audio.opus"
    if p_wav.exists():
        return FileResponse(p_wav, media_type="audio/wav")
    if p_opus.exists():
        return FileResponse(p_opus, media_type="audio/ogg")
    raise HTTPException(404)


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


@app.get("/api/reunioes/{data}/{slug}/meta")
def meta_endpoint(data: str, slug: str):
    pasta = pasta_da_reuniao(data, slug)
    return meta.ler(pasta)


@app.patch("/api/reunioes/{data}/{slug}")
def patch_reuniao(data: str, slug: str, body: PatchReuniaoBody):
    pasta = pasta_da_reuniao(data, slug)
    campos = {}
    if body.titulo is not None:
        campos["titulo"] = body.titulo
    if body.speaker_nomes is not None:
        campos["speaker_nomes"] = body.speaker_nomes
    if body.cliente is not None:
        campos["cliente"] = body.cliente.strip()
    if body.arquivada is not None:
        campos["arquivada"] = bool(body.arquivada)
    if campos:
        return meta.escrever(pasta, **campos)
    return meta.ler(pasta)


@app.get("/api/reunioes/{data}/{slug}/resumo")
def get_resumo(data: str, slug: str):
    p = pasta_da_reuniao(data, slug) / "resumo.md"
    if not p.exists():
        return JSONResponse({"texto": None}, status_code=404)
    return {"texto": p.read_text(encoding="utf-8")}


@app.post("/api/reunioes/{data}/{slug}/resumo")
def post_resumo(data: str, slug: str):
    pasta = pasta_da_reuniao(data, slug)
    try:
        p = resumo.gerar_e_salvar(pasta)
    except Exception as e:
        raise HTTPException(503, str(e))
    if p is None:
        raise HTTPException(404, "Sem transcrição para resumir")
    meta.escrever(pasta, tem_resumo=True)
    return {"texto": p.read_text(encoding="utf-8")}


@app.post("/api/reunioes/{data}/{slug}/exportar")
def exportar_endpoint(data: str, slug: str):
    cfg = config.carregar()
    destino = cfg.get("export_dir", "")
    if not destino:
        raise HTTPException(400, "Configure o diretório de export nas Configurações")
    pasta = pasta_da_reuniao(data, slug)
    try:
        arq = exportar.exportar(pasta, f"{data}/{slug}", Path(destino))
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"destino": str(arq)}


class GravacaoAtivaError(RuntimeError):
    """Já existe uma gravação ativa (mapeada para HTTP 409 no endpoint)."""


def iniciar_gravacao_servidor(titulo: str = "reuniao", modelo: str = "medium",
                              diarizar: bool = False, hotwords: str = "",
                              idioma: Optional[str] = None,
                              cliente: Optional[str] = None) -> Path:
    """
    Inicia uma gravação (usada pelo endpoint e pelo monitor de detecção).
    Levanta GravacaoAtivaError se já houver gravação; RuntimeError/SystemExit
    se os dispositivos de áudio falharem. Retorna a pasta criada.
    """
    with estado.lock:
        # Bloqueia APENAS se já houver gravação ativa — nunca por processamento
        if estado.gravando:
            raise GravacaoAtivaError("Já existe uma gravação ativa")

        agora = datetime.now()
        titulo_limpo = titulo.replace(" ", "-").lower() or "reuniao"
        pasta = (reuniao.BASE_DIR / agora.strftime("%Y-%m-%d")
                 / f"{agora.strftime('%H-%M')}-{titulo_limpo}")
        pasta.mkdir(parents=True, exist_ok=True)

        monitor_disp, mic = reuniao.detectar_dispositivos()
        proc = reuniao.iniciar_gravacao(pasta, monitor_disp, mic)

        # Fallback de hotwords para hotwords_padrao da config
        hotwords_lista = parsear_hotwords(hotwords)
        if not hotwords_lista:
            hotwords_lista = config.carregar().get("hotwords_padrao", [])

        estado.gravando = True
        estado.titulo = titulo
        estado.inicio = time.time()
        estado.pasta = pasta
        estado.proc = proc
        estado.opcoes = {
            "diarizar": diarizar,
            "hotwords": hotwords_lista,
            "modelo": modelo,
            "idioma": idioma,
            "cliente": (cliente or "").strip() or None,
        }
        estado.msg = "Gravando..."

    return pasta


def _esta_gravando() -> bool:
    with estado.lock:
        return estado.gravando


# Inicia o monitor de detecção de reunião (thread daemon, uma única vez)
monitor.iniciar(iniciar_gravacao_servidor, _esta_gravando)


@app.post("/api/gravar/iniciar")
def iniciar(body: IniciarBody):
    try:
        pasta = iniciar_gravacao_servidor(body.titulo, body.modelo,
                                          body.diarizar, body.hotwords,
                                          body.idioma, body.cliente)
    except GravacaoAtivaError as e:
        raise HTTPException(409, str(e))
    except (RuntimeError, SystemExit) as e:
        raise HTTPException(500, str(e))
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
        # Limpa APENAS o estado de gravação; não mexe em processando
        estado.gravando = False
        estado.proc = None
        estado.titulo = None
        estado.inicio = None
        estado.pasta = None
        estado.msg = "Finalizando gravação..."

    reuniao.parar_gravacao(proc)

    pos = enfileirar(
        pasta, titulo, opcoes["modelo"],
        opcoes["diarizar"], opcoes["hotwords"],
        opcoes.get("idioma"), opcoes.get("cliente")
    )
    return {"ok": True, "fila_tamanho": pos}


@app.post("/api/reunioes/{data}/{slug}/excluir")
def excluir(data: str, slug: str):
    import shutil
    pasta = pasta_da_reuniao(data, slug)
    with estado.lock:
        em_uso = (
            (estado.pasta == pasta and estado.gravando)
            or (estado.processando and estado.pasta_processando == pasta)
            or any(p["id"] == str(pasta) for p in estado.pendentes)
        )
    if em_uso:
        raise HTTPException(409, "Não é possível excluir uma reunião em gravação/processamento/fila")
    shutil.rmtree(pasta)
    return {"ok": True}


@app.post("/api/reunioes/{data}/{slug}/reprocessar")
def reprocessar(data: str, slug: str, body: ReprocessarBody):
    pasta = pasta_da_reuniao(data, slug)
    pos = enfileirar(
        pasta, slug, body.modelo, body.diarizar,
        parsear_hotwords(body.hotwords),
        body.idioma
    )
    return {"ok": True, "fila_tamanho": pos}


@app.get("/api/config")
def get_config():
    return config.carregar()


@app.post("/api/config")
def post_config(body: ConfigBody):
    return config.salvar(body.model_dump(exclude_unset=True))


@app.get("/api/llm/status")
def llm_status():
    import llm
    return llm.info()


@app.post("/api/llm/chave")
def llm_salvar_chave(body: ChaveBody):
    """Grava a chave de API do provedor (arquivo 0600). Nunca devolve a chave."""
    import llm
    try:
        llm.salvar_chave(body.provider, body.chave)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "chave_configurada": llm.chave_configurada(body.provider)}


@app.post("/api/llm/testar")
def llm_testar(body: TestarChaveBody):
    """Testa a chave do provedor com uma chamada mínima. Não altera o config."""
    import llm
    return llm.testar(body.provider, body.modelo)


@app.get("/api/buscar")
def buscar_endpoint(q: str = ""):
    if not q:
        return []
    return busca.buscar(q, reuniao.BASE_DIR)


@app.get("/api/relatorio")
def relatorio_endpoint(mes: str = "", cliente: str = ""):
    valores_hora = config.carregar().get("valores_hora", {})
    return relatorio.gerar(listar_reunioes_fs(), mes, cliente, valores_hora)


@app.get("/api/relatorio/csv")
def relatorio_csv_endpoint(mes: str = "", cliente: str = ""):
    valores_hora = config.carregar().get("valores_hora", {})
    dados = relatorio.gerar(listar_reunioes_fs(), mes, cliente, valores_hora)
    csv_texto = relatorio.para_csv(dados, valores_hora)
    nome = f"relatorio-{dados['mes']}.csv"
    return Response(
        content=csv_texto,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# ────────────────────────────────────────────────────────────────────────────
# Static files (a UI)
# ────────────────────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


# ────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n🌐 Servidor em http://localhost:{PORT}\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
