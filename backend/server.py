#!/usr/bin/env python3
"""
Servidor web local para gerenciar gravação e transcrição de reuniões.
Acesse em http://localhost:8765 após iniciar.
"""

import queue
import re
import sys
import threading
import time
from datetime import datetime, timedelta
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
import lembretes
import sync
import horas
import dashboard

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
            # Cliente/projeto da gravação em andamento, para hidratar a UI em
            # caso de reload durante a gravação (ver static/app.js,
            # atualizarStatus): cliente vem de estado.opcoes (definido em
            # iniciar_gravacao_servidor); projeto é gravado direto no
            # meta.json da pasta (não fica em opcoes) — ver docstring de
            # iniciar_gravacao_servidor.
            gravacao_cliente = None
            pasta_gravacao = None
            if self.gravando:
                gravacao_cliente = self.opcoes.get("cliente")
                pasta_gravacao = self.pasta
            snap = {
                "gravando": self.gravando,
                "duracao_s": duracao,
                "titulo": self.titulo,
                "gravacao_cliente": gravacao_cliente,
                "gravacao_projeto": None,
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
        # Leitura de disco fora do lock: meta.json é pequeno, mas segurar o
        # lock durante I/O a cada poll de 1s bloquearia parar/patch atrás dele.
        if pasta_gravacao is not None:
            try:
                snap["gravacao_projeto"] = meta.ler(pasta_gravacao).get("projeto") or None
            except Exception:
                pass
        return snap

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
    projeto: Optional[str] = None


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
    projeto: Optional[str] = None
    arquivada: Optional[bool] = None
    sync_habilitado: Optional[bool] = None


class LembreteBody(BaseModel):
    titulo: str
    descricao: Optional[str] = ""
    data_hora: Optional[str] = None
    reuniao: Optional[str] = None
    cliente: Optional[str] = None
    recorrencia: Optional[str] = ""


class PatchLembreteBody(BaseModel):
    titulo: Optional[str] = None
    descricao: Optional[str] = None
    data_hora: Optional[str] = None
    reuniao: Optional[str] = None
    cliente: Optional[str] = None
    concluido: Optional[bool] = None
    sync_habilitado: Optional[bool] = None
    recorrencia: Optional[str] = None


class AdiarBody(BaseModel):
    minutos: Optional[int] = None
    ate: Optional[str] = None


class SyncChaveBody(BaseModel):
    chave: str = ""


class ClienteBody(BaseModel):
    nome: str
    valor_hora: float = 0.0


class PatchClienteBody(BaseModel):
    nome: Optional[str] = None
    valor_hora: Optional[float] = None
    ativo: Optional[bool] = None
    sync_habilitado: Optional[bool] = None


class ProjetoBody(BaseModel):
    nome: str
    cliente_id: Optional[str] = None


class PatchProjetoBody(BaseModel):
    nome: Optional[str] = None
    # "" limpa o vínculo com cliente (seta NULL); omitido = não altera.
    cliente_id: Optional[str] = None
    ativo: Optional[bool] = None
    sync_habilitado: Optional[bool] = None


class ApontamentoBody(BaseModel):
    cliente_id: Optional[str] = None
    projeto_id: Optional[str] = None
    descricao: Optional[str] = ""
    inicio: str
    fim: str


class PatchApontamentoBody(BaseModel):
    cliente_id: Optional[str] = None
    projeto_id: Optional[str] = None
    descricao: Optional[str] = None
    inicio: Optional[str] = None
    fim: Optional[str] = None
    sync_habilitado: Optional[bool] = None


class TimerIniciarBody(BaseModel):
    cliente_id: Optional[str] = None
    projeto_id: Optional[str] = None
    descricao: Optional[str] = ""


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
                "projeto": m.get("projeto") or "",
                "arquivada": bool(m.get("arquivada")),
            })
    return out


def pasta_da_reuniao(data: str, slug: str) -> Path:
    # Segmentos vêm da URL: restringir charset impede escapar de BASE_DIR
    # (ex.: data="..") antes de qualquer acesso ao filesystem.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data) or not re.fullmatch(r"[\w-]+", slug):
        raise HTTPException(404, f"Reunião não encontrada: {data}/{slug}")
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
def listar(cliente: str = ""):
    reunioes = listar_reunioes_fs()
    cliente = cliente.strip()
    if cliente:
        alvo = cliente.lower()
        reunioes = [r for r in reunioes if (r.get("cliente") or "").lower() == alvo]
    return reunioes


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
    if body.projeto is not None:
        campos["projeto"] = body.projeto.strip()
    if body.arquivada is not None:
        campos["arquivada"] = bool(body.arquivada)
    if body.sync_habilitado is not None:
        campos["sync_habilitado"] = bool(body.sync_habilitado)
        # Marcação para sync (push-only, ver sync.py): a reunião ganha um
        # `sync_id` estável (uuid4) na primeira vez que é marcada, usado como
        # PK na tabela remota `reunioes`. Nunca regenerado depois disso.
        if campos["sync_habilitado"] and not meta.ler(pasta).get("sync_id"):
            import uuid
            campos["sync_id"] = str(uuid.uuid4())
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
                              cliente: Optional[str] = None,
                              projeto: Optional[str] = None) -> Path:
    """
    Inicia uma gravação (usada pelo endpoint e pelo monitor de detecção).
    Levanta GravacaoAtivaError se já houver gravação; RuntimeError/SystemExit
    se os dispositivos de áudio falharem. Retorna a pasta criada.

    `projeto`: diferente de `cliente` (que só é persistido no meta.json ao fim
    do processamento, dentro de reuniao.processar), `projeto` é gravado aqui,
    logo após criar a pasta — reuniao.py não pode ser tocado nesta tarefa, e
    seu meta.escrever(...) final não referencia "projeto", então o merge feito
    por meta.escrever preserva este valor até a conclusão do processamento.
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

        projeto_limpo = (projeto or "").strip()
        if projeto_limpo:
            meta.escrever(pasta, projeto=projeto_limpo)

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


@app.get("/api/gravacao/cliente-sugerido")
def cliente_sugerido():
    """
    Heurística simples (sem ML, sem rede) para sugerir o cliente da próxima
    gravação:
      1) se a detecção estiver ativa e tiver um app/termo detectado agora,
         procura nas últimas 20 reuniões qual cliente mais aparece em
         títulos que casam com esse termo;
      2) senão, usa o cliente mais frequente nas reuniões dos últimos 7 dias;
      3) senão, None.
    """
    reunioes = listar_reunioes_fs()  # já ordenado do mais recente para o mais antigo

    det = estado.snapshot()["deteccao"]
    termo = (det.get("app") or "").strip().lower() if det.get("ativa") and det.get("detectado") else ""
    if termo:
        contagem: dict[str, int] = {}
        for r in reunioes[:20]:
            cli = (r.get("cliente") or "").strip()
            if not cli:
                continue
            if termo in (r.get("titulo") or "").lower():
                contagem[cli] = contagem.get(cli, 0) + 1
        if contagem:
            melhor = max(contagem.items(), key=lambda kv: kv[1])[0]
            return {"cliente": melhor, "origem": "deteccao"}

    limite = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    contagem = {}
    for r in reunioes:
        if (r.get("data") or "") < limite:
            continue
        cli = (r.get("cliente") or "").strip()
        if cli:
            contagem[cli] = contagem.get(cli, 0) + 1
    if contagem:
        melhor = max(contagem.items(), key=lambda kv: kv[1])[0]
        return {"cliente": melhor, "origem": "historico"}

    return {"cliente": None, "origem": None}


@app.post("/api/gravar/iniciar")
def iniciar(body: IniciarBody):
    try:
        pasta = iniciar_gravacao_servidor(body.titulo, body.modelo,
                                          body.diarizar, body.hotwords,
                                          body.idioma, body.cliente, body.projeto)
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


def _gerar_relatorio(mes: str, cliente: str, inicio: str = "", fim: str = "") -> dict:
    valores_hora = config.carregar().get("valores_hora", {})
    clientes = horas.listar_clientes(incluir_inativos=True)
    projetos = horas.listar_projetos(incluir_inativos=True)
    # Com intervalo válido, o filtro de data é feito em relatorio.gerar() sobre
    # TODOS os apontamentos (o prefixo de mês não cobriria um intervalo que
    # cruza meses); sem intervalo, mantém o pré-filtro por mês (retrocompat).
    if relatorio.intervalo_valido(inicio, fim):
        apontamentos = horas.todos_apontamentos_periodo(None)
    else:
        apontamentos = horas.todos_apontamentos_periodo(mes.strip() or relatorio.mes_corrente())
    return relatorio.gerar(listar_reunioes_fs(), apontamentos, mes, cliente,
                           clientes, projetos, valores_hora, inicio=inicio, fim=fim)


@app.get("/api/relatorio")
def relatorio_endpoint(mes: str = "", cliente: str = "", inicio: str = "", fim: str = ""):
    return _gerar_relatorio(mes, cliente, inicio, fim)


@app.get("/api/relatorio/csv")
def relatorio_csv_endpoint(mes: str = "", cliente: str = "", inicio: str = "", fim: str = ""):
    dados = _gerar_relatorio(mes, cliente, inicio, fim)
    csv_texto = relatorio.para_csv(dados)
    if dados.get("filtro_inicio") and dados.get("filtro_fim"):
        nome = f"relatorio-{dados['filtro_inicio']}_a_{dados['filtro_fim']}.csv"
    else:
        nome = f"relatorio-{dados['mes']}.csv"
    return Response(
        content=csv_texto,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@app.get("/api/dashboard")
def dashboard_endpoint(ref: str = ""):
    clientes = horas.listar_clientes(incluir_inativos=True)
    apontamentos = horas.listar_apontamentos()
    return dashboard.gerar(listar_reunioes_fs(), apontamentos, ref or None, clientes)


@app.get("/api/lembretes")
def listar_lembretes(incluir_concluidos: bool = False):
    return lembretes.listar(incluir_concluidos=incluir_concluidos)


@app.post("/api/lembretes")
def criar_lembrete(body: LembreteBody):
    try:
        criado = lembretes.criar(
            body.titulo, body.descricao or "", body.data_hora, body.reuniao, body.cliente,
            recorrencia=body.recorrencia or "",
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    sync.sincronizar_em_background()
    return criado


@app.patch("/api/lembretes/{id}")
def atualizar_lembrete(id: str, body: PatchLembreteBody):
    try:
        atualizado = lembretes.atualizar(id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if atualizado is None:
        raise HTTPException(404, "Lembrete não encontrado")
    sync.sincronizar_em_background()
    return atualizado


@app.post("/api/lembretes/{id}/excluir")
def excluir_lembrete(id: str):
    if not lembretes.excluir(id):
        raise HTTPException(404, "Lembrete não encontrado")
    sync.sincronizar_em_background()
    return {"ok": True}


@app.get("/api/lembretes/vencidos")
def lembretes_vencidos():
    return lembretes.vencidos()


@app.get("/api/lembretes/pendentes-notificacao")
def lembretes_pendentes_notificacao():
    """Autoridade do agendador: calcula e marca (idempotente) os marcos de
    notificação pendentes. Consumido pelo processo main do Electron."""
    cfg = config.carregar().get("notificacoes", {})
    return lembretes.pendentes_notificacao(cfg)


@app.post("/api/lembretes/{id}/adiar")
def adiar_lembrete(id: str, body: AdiarBody):
    try:
        atualizado = lembretes.adiar(id, minutos=body.minutos, ate=body.ate)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if atualizado is None:
        raise HTTPException(404, "Lembrete não encontrado")
    sync.sincronizar_em_background()
    return atualizado


@app.post("/api/sync/agora")
def sync_agora():
    cfg = config.carregar().get("sync", {})
    if not cfg.get("ativo") or not (cfg.get("url") or "").strip():
        raise HTTPException(400, "Sincronização desativada ou URL não configurada")
    return sync.sincronizar()


@app.post("/api/sync/chave")
def sync_salvar_chave(body: SyncChaveBody):
    sync.salvar_chave(body.chave)
    return {"configurada": sync.chave_configurada()}


@app.post("/api/sync/testar")
def sync_testar():
    return sync.testar()


@app.get("/api/sync/status")
def sync_status():
    return sync.status()


# ────────────────────────────────────────────────────────────────────────────
# Timetracking: clientes, projetos, apontamentos e timer
# ────────────────────────────────────────────────────────────────────────────

@app.get("/api/clientes")
def listar_clientes_endpoint(incluir_inativos: bool = False):
    return horas.listar_clientes(incluir_inativos=incluir_inativos)


@app.post("/api/clientes")
def criar_cliente_endpoint(body: ClienteBody):
    try:
        criado = horas.criar_cliente(body.nome, body.valor_hora)
    except ValueError as e:
        raise HTTPException(422, str(e))
    sync.sincronizar_em_background()
    return criado


@app.patch("/api/clientes/{id}")
def atualizar_cliente_endpoint(id: str, body: PatchClienteBody):
    try:
        atualizado = horas.atualizar_cliente(id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if atualizado is None:
        raise HTTPException(404, "Cliente não encontrado")
    sync.sincronizar_em_background()
    return atualizado


@app.post("/api/clientes/{id}/excluir")
def excluir_cliente_endpoint(id: str):
    if not horas.excluir_cliente(id):
        raise HTTPException(404, "Cliente não encontrado")
    sync.sincronizar_em_background()
    return {"ok": True}


@app.get("/api/projetos")
def listar_projetos_endpoint(cliente_id: str = "", incluir_inativos: bool = False):
    return horas.listar_projetos(cliente_id=cliente_id or None, incluir_inativos=incluir_inativos)


@app.post("/api/projetos")
def criar_projeto_endpoint(body: ProjetoBody):
    try:
        criado = horas.criar_projeto(body.nome, cliente_id=body.cliente_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    sync.sincronizar_em_background()
    return criado


@app.patch("/api/projetos/{id}")
def atualizar_projeto_endpoint(id: str, body: PatchProjetoBody):
    try:
        atualizado = horas.atualizar_projeto(id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if atualizado is None:
        raise HTTPException(404, "Projeto não encontrado")
    sync.sincronizar_em_background()
    return atualizado


@app.post("/api/projetos/{id}/excluir")
def excluir_projeto_endpoint(id: str):
    if not horas.excluir_projeto(id):
        raise HTTPException(404, "Projeto não encontrado")
    sync.sincronizar_em_background()
    return {"ok": True}


@app.get("/api/apontamentos")
def listar_apontamentos_endpoint(mes: str = "", cliente_id: str = "", projeto_id: str = ""):
    return horas.listar_apontamentos(mes=mes or None, cliente_id=cliente_id or None,
                                     projeto_id=projeto_id or None)


@app.post("/api/apontamentos")
def criar_apontamento_endpoint(body: ApontamentoBody):
    try:
        criado = horas.criar_apontamento(
            body.inicio, body.fim,
            cliente_id=body.cliente_id, projeto_id=body.projeto_id,
            descricao=body.descricao or "",
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    sync.sincronizar_em_background()
    return criado


@app.patch("/api/apontamentos/{id}")
def atualizar_apontamento_endpoint(id: str, body: PatchApontamentoBody):
    try:
        atualizado = horas.atualizar_apontamento(id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if atualizado is None:
        raise HTTPException(404, "Apontamento não encontrado")
    sync.sincronizar_em_background()
    return atualizado


@app.post("/api/apontamentos/{id}/excluir")
def excluir_apontamento_endpoint(id: str):
    if not horas.excluir_apontamento(id):
        raise HTTPException(404, "Apontamento não encontrado")
    sync.sincronizar_em_background()
    return {"ok": True}


@app.get("/api/horas/timer")
def timer_status_endpoint():
    ativo = horas.timer_ativo()
    return {"ativo": ativo is not None, "apontamento": ativo}


@app.post("/api/horas/timer/iniciar")
def timer_iniciar_endpoint(body: TimerIniciarBody):
    try:
        criado = horas.timer_iniciar(
            projeto_id=body.projeto_id, cliente_id=body.cliente_id,
            descricao=body.descricao or "",
        )
    except horas.TimerAtivoError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    sync.sincronizar_em_background()
    return criado


@app.post("/api/horas/timer/parar")
def timer_parar_endpoint():
    try:
        parado = horas.timer_parar()
    except horas.TimerInativoError as e:
        raise HTTPException(409, str(e))
    sync.sincronizar_em_background()
    return parado


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
