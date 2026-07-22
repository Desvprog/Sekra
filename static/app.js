// ────────────────────────────────────────────────────────────────────
// Estado local da UI
// ────────────────────────────────────────────────────────────────────
let reuniaoSelecionada = null;
let ultimoEstadoBackend = { gravando: false, processando: false };

// Mapa de speaker_nomes para a reunião atual (ex: {"SPEAKER_00": "Daniel"})
let speakerNomesAtual = {};

// Texto cru da transcrição atual (para copiar)
let transcricaoTextoAtual = "";

// Guarda se estamos exibindo resultado de busca (não lista normal)
let emModoBusca = false;

// Feature 12: cliente da reunião selecionada e cache de clientes conhecidos
let clienteAtualReuniao = "";
let projetoAtualReuniao = "";
let _clientesCache = [];

// F1: cliente usado na gravação em andamento (só conhecido nesta sessão —
// é resolvido localmente ao chamar iniciarGravacao(), não persistido até o
// fim do processamento) e última sugestão de cliente vinda do backend.
let _gravacaoClienteAtual = "";
let _gravacaoProjetoAtual = "";
let _sugestaoClienteAtual = null;

// F6: modo de sincronização atual ("tudo" | "selecionados"), usado para
// esmaecer os ícones de nuvem quando o modo é "tudo" (a flag por item não
// afeta o push nesse caso). Hidratado por carregarSyncModo() no boot e
// atualizado sempre que a tela de Config carrega/salva o status de sync.
let _syncModo = "tudo";

// Horas: caches de id → objeto para clientes/projetos (usados para exibir nomes)
// e estado do timer em execução.
let _clientesMapId = {};
let _projetosMapId = {};
let _timerAtivo = null; // apontamento em andamento, ou null
let _timerIntervalId = null;

// Instâncias do pickerProjeto() (ver definição mais abaixo), montadas no
// boot do app: topbar (início direto do timer), cronômetro (tela Timer) e
// lançamento retroativo ("Esqueci de ativar o timer").
let _pickerTopbar = null;
let _pickerHoras = null;
let _pickerApontEdit = null;

// Lembretes: vínculo com reunião ao criar via botão da tela de detalhe
let _lembreteReuniaoVinculada = null; // { ref: "data/slug", label: "titulo" }
let _mostrarConcluidosLembretes = false;

// Navegação: tela atual do shell (painel/gravacoes/timer/clientes/lembretes/config)
const TELAS = ["painel", "gravacoes", "timer", "clientes", "lembretes", "config"];
let telaAtual = localStorage.getItem("sekra_tela") || "gravacoes";
if (!TELAS.includes(telaAtual)) telaAtual = "gravacoes";

// ────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

function mostrar(id, display = "block") {
  const el = $(id);
  el.removeAttribute("hidden");
  el.style.display = display;
}

function ocultar(id) {
  const el = $(id);
  el.setAttribute("hidden", "");
  el.style.display = "none";
}

function fmtCronometro(s) {
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    const erro = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(erro.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// ────────────────────────────────────────────────────────────────────
// Feature 1: Banner de erro inline (substitui alert())
// ────────────────────────────────────────────────────────────────────
let _toastTimer = null;

function mostrarErro(msg) {
  // Exibe no banner-erro permanente (para erros críticos do polling)
  // E também mostra um toast temporário para erros de ação
  mostrarToast(msg, "erro");
}

function mostrarToast(msg, tipo = "info", duracao = 4000) {
  const toast = $("toast");
  toast.textContent = msg;
  toast.className = `toast toast-${tipo}`;
  toast.removeAttribute("hidden");
  toast.style.display = "block";
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => {
    toast.setAttribute("hidden", "");
    toast.style.display = "none";
  }, duracao);
}

// ────────────────────────────────────────────────────────────────────
// Modal genérico in-app (substitui confirm()/prompt() nativos — o
// Electron não suporta window.prompt() e o confirm() nativo abre um
// diálogo do SO sem estilo).
// ────────────────────────────────────────────────────────────────────
let _appModalModo = null; // "confirm" | "prompt"
let _appModalResolver = null;

function _appModalFechar(valor) {
  ocultar("app-modal");
  const resolver = _appModalResolver;
  _appModalResolver = null;
  _appModalModo = null;
  if (resolver) resolver(valor);
}

function confirmarAcao(mensagem, { titulo = "Confirmar", okLabel = "Excluir", perigo = true } = {}) {
  return new Promise((resolve) => {
    _appModalModo = "confirm";
    _appModalResolver = resolve;
    $("app-modal-titulo").textContent = titulo;
    $("app-modal-msg").textContent = mensagem;
    ocultar("app-modal-msg");
    mostrar("app-modal-msg", "block");
    ocultar("app-modal-input");
    const ok = $("app-modal-ok");
    ok.textContent = okLabel;
    ok.className = perigo ? "danger" : "primary";
    mostrar("app-modal", "flex");
    ok.focus();
  });
}

function promptTexto(titulo, valorAtual = "", { okLabel = "Salvar" } = {}) {
  return new Promise((resolve) => {
    _appModalModo = "prompt";
    _appModalResolver = resolve;
    $("app-modal-titulo").textContent = titulo;
    ocultar("app-modal-msg");
    const input = $("app-modal-input");
    input.value = valorAtual;
    mostrar("app-modal-input", "block");
    const ok = $("app-modal-ok");
    ok.textContent = okLabel;
    ok.className = "primary";
    mostrar("app-modal", "flex");
    input.focus();
    input.select();
  });
}

$("app-modal-ok").addEventListener("click", () => {
  if (_appModalModo === "prompt") {
    _appModalFechar($("app-modal-input").value);
  } else {
    _appModalFechar(true);
  }
});
$("app-modal-cancelar").addEventListener("click", () => {
  _appModalFechar(_appModalModo === "prompt" ? null : false);
});
$("app-modal").addEventListener("click", (e) => {
  if (e.target.id === "app-modal") {
    _appModalFechar(_appModalModo === "prompt" ? null : false);
  }
});
$("app-modal-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    _appModalFechar($("app-modal-input").value);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && _appModalResolver) {
    _appModalFechar(_appModalModo === "prompt" ? null : false);
  }
});

// ────────────────────────────────────────────────────────────────────
// Polling de status
// ────────────────────────────────────────────────────────────────────
async function limparErro() {
  await api("/api/erro/limpar", { method: "POST" }).catch(() => {});
  $("banner-erro").hidden = true;
  const badge = $("status-badge");
  if (badge.className === "status-error") {
    badge.textContent = "ocioso";
    badge.className = "status-idle";
  }
}

async function atualizarStatus() {
  try {
    const s = await api("/api/status");
    const mudouEstado =
      s.gravando !== ultimoEstadoBackend.gravando ||
      s.processando !== ultimoEstadoBackend.processando;

    // Erro persistente — sincroniza com o estado do backend
    if (s.erro) {
      $("banner-erro-msg").textContent = "Erro no processamento: " + s.erro;
      mostrar("banner-erro", "flex");
    } else {
      ocultar("banner-erro");
    }

    // Badge no header — prioridade: gravando > processando/fila > erro > ocioso
    const badge = $("status-badge");
    if (s.gravando) {
      badge.textContent = "● gravando";
      badge.className = "status-recording";
    } else if (s.processando) {
      const filaExtra = s.fila_tamanho > 0 ? ` (+${s.fila_tamanho} na fila)` : "";
      badge.textContent = `⚙ processando${filaExtra}`;
      badge.className = "status-processing";
    } else if (s.fila_tamanho > 0) {
      badge.textContent = `${s.fila_tamanho} na fila`;
      badge.className = "status-processing";
    } else if (s.erro) {
      badge.textContent = "erro";
      badge.className = "status-error";
    } else {
      badge.textContent = "ocioso";
      badge.className = "status-idle";
    }

    // Botões e cronômetro — INICIAR habilitado mesmo durante processamento/fila
    s.gravando ? ocultar("btn-iniciar") : mostrar("btn-iniciar", "inline-block");
    s.gravando ? mostrar("btn-parar", "inline-block") : ocultar("btn-parar");
    $("btn-iniciar").disabled = false;
    s.gravando ? mostrar("cronometro", "inline") : ocultar("cronometro");
    $("cronometro").textContent = fmtCronometro(s.duracao_s);

    // F1: estado visual da tela Gravações (ocioso com dica vs. círculo
    // pulsando + timer grande + cliente da gravação em andamento).
    if (s.gravando) {
      ocultar("grav-estado-idle");
      mostrar("grav-estado-ativa", "flex");
      $("cronometro-grande").textContent = fmtCronometro(s.duracao_s);
      // Hidrata cliente/projeto a partir do /api/status: cobre o caso de
      // reload da página durante uma gravação em andamento, quando as
      // variáveis locais (só setadas em iniciarGravacao()) ainda estão vazias.
      if (s.gravacao_cliente) _gravacaoClienteAtual = s.gravacao_cliente;
      if (s.gravacao_projeto) _gravacaoProjetoAtual = s.gravacao_projeto;
      const linha = [`Cliente: ${_gravacaoClienteAtual || "—"}`];
      if (_gravacaoProjetoAtual) linha.push(`Projeto: ${_gravacaoProjetoAtual}`);
      $("grav-cliente-linha").textContent = linha.join(" · ");
    } else {
      mostrar("grav-estado-idle", "flex");
      ocultar("grav-estado-ativa");
    }

    // Mensagem de status
    $("msg-status").textContent = s.msg || "";

    // Topbar: pill de Gravar (outline danger ocioso → preenchido piscando gravando)
    const gravarPill = $("btn-gravar-pill");
    if (gravarPill) {
      if (s.gravando) {
        gravarPill.classList.remove("pill-danger-outline");
        gravarPill.classList.add("pill-filled-danger");
        gravarPill.textContent = "● Gravando";
      } else {
        gravarPill.classList.remove("pill-filled-danger");
        gravarPill.classList.add("pill-danger-outline");
        gravarPill.textContent = "● Gravar";
      }
    }
    atualizarTopbarTempo();

    // Badge de detecção de reunião — só quando detectado e não gravando
    const det = s.deteccao || {};
    if (det.detectado && !s.gravando) {
      $("badge-deteccao").textContent =
        `Reunião detectada${det.app ? ` (${det.app})` : ""} — Gravar?`;
      mostrar("badge-deteccao", "inline-block");
    } else {
      ocultar("badge-deteccao");
    }

    // Painel de fila/processamento
    const painelProc = $("status-processamento");
    if (s.processando || s.fila_tamanho > 0) {
      let linhas = [];
      if (s.processando) {
        const titulo = s.titulo_processando ? escapeHtml(s.titulo_processando) : "(sem título)";
        const msg = s.msg ? ` — ${escapeHtml(s.msg)}` : "";
        linhas.push(`<span class="proc-label">Processando:</span> ${titulo}${msg}`);
      }
      if (s.fila && s.fila.length > 0) {
        const itens = s.fila.map(escapeHtml).join(", ");
        linhas.push(`<span class="proc-label">Na fila:</span> ${itens}`);
      }
      painelProc.innerHTML = linhas.join("<br>");
      mostrar("status-processamento", "block");
    } else {
      ocultar("status-processamento");
    }

    // Se acabou de parar de processar, recarrega lista e mostra resultado
    if (mudouEstado && !s.processando && ultimoEstadoBackend.processando) {
      await carregarLista();
      if (reuniaoSelecionada) {
        await selecionarReuniao(reuniaoSelecionada);
        if (!s.erro) ativarTab("transcricao");
      }
    }

    ultimoEstadoBackend = { gravando: s.gravando, processando: s.processando };
  } catch (e) {
    console.error("Erro no status:", e);
  }
}

// ────────────────────────────────────────────────────────────────────
// Lista de reuniões
// ────────────────────────────────────────────────────────────────────
let _listaCache = []; // cache para filtro local

async function carregarLista() {
  const ul = $("ul-reunioes");
  try {
    const lista = await api("/api/reunioes");
    _listaCache = lista;
    if (!lista.length) {
      ul.innerHTML = '<li class="vazio">nenhuma reunião ainda</li>';
      return;
    }
    renderizarLista(lista);
  } catch (e) {
    ul.innerHTML = `<li class="vazio">erro: ${e.message}</li>`;
  }
}

// Renderiza a lista de reuniões no <ul>
function renderizarLista(lista) {
  const ul = $("ul-reunioes");
  ul.innerHTML = lista.map((r) => `
    <li data-id="${r.id}" class="${r.id === reuniaoSelecionada ? 'selecionada' : ''}${r.audio_incompleto ? ' incompleta' : ''}">
      <div class="r-linha">
        ${r.cliente ? `<div class="avatar-quad" title="${escapeHtml(r.cliente)}">${escapeHtml(_iniciaisCliente(r.cliente))}</div>` : ''}
        <div class="r-linha-corpo">
          <div class="r-titulo">${r.audio_incompleto ? '⚠️ ' : ''}${escapeHtml(r.titulo)}</div>
          <div class="r-meta">
            <span>${r.data} ${r.hora}</span>
            ${r.tamanho_mb ? `<span>${r.tamanho_mb} MB</span>` : ''}
            ${r.duracao_fmt ? `<span>⏱ ${escapeHtml(r.duracao_fmt)}</span>` : ''}
            ${r.audio_incompleto ? '<span class="tag audio-parcial">gravação incompleta</span>' : ''}
            ${r.tem_transcricao ? '<span class="tag tem-trans">📝 trans</span>' : ''}
            ${r.tem_hotwords ? '<span class="tag tem-hw">🔍 hw</span>' : ''}
            ${r.tem_resumo ? '<span class="tag tem-resumo">📄 resumo</span>' : ''}
            ${r.cliente ? `<span class="tag tem-cliente">👤 ${escapeHtml(r.cliente)}</span>` : ''}
            ${r.projeto ? `<span class="tag tem-projeto">📁 ${escapeHtml(r.projeto)}</span>` : ''}
          </div>
        </div>
      </div>
    </li>
  `).join("");
  ul.querySelectorAll("li[data-id]").forEach((li) => {
    li.addEventListener("click", () => selecionarReuniao(li.dataset.id));
  });
}

// ────────────────────────────────────────────────────────────────────
// Feature 2: Filtro local + busca full-text
// ────────────────────────────────────────────────────────────────────
function inicializarFiltro() {
  const input = $("filtro");
  const btnLimpar = $("btn-limpar-filtro");

  input.addEventListener("input", () => {
    const q = input.value.trim();
    btnLimpar.hidden = !q;
    if (emModoBusca && !q) {
      // Sai do modo busca ao apagar o texto
      sairModoBusca();
      return;
    }
    if (!emModoBusca) {
      // Filtro local por título (case-insensitive)
      const filtrado = _listaCache.filter((r) =>
        r.titulo.toLowerCase().includes(q.toLowerCase())
      );
      renderizarLista(filtrado);
    }
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const q = input.value.trim();
      if (q) executarBusca(q);
    }
  });

  btnLimpar.addEventListener("click", () => {
    input.value = "";
    btnLimpar.hidden = true;
    sairModoBusca();
  });
}

async function executarBusca(q) {
  const ul = $("ul-reunioes");
  emModoBusca = true;
  mostrar("btn-limpar-filtro", "inline-block");
  ul.innerHTML = '<li class="vazio">buscando…</li>';
  try {
    const resultados = await api(`/api/buscar?q=${encodeURIComponent(q)}`);
    if (!resultados.length) {
      ul.innerHTML = '<li class="vazio">nenhum resultado</li>';
      return;
    }
    ul.innerHTML = resultados.map((r) => {
      const trechos = (r.trechos || []).slice(0, 3).map((t) =>
        `<div class="busca-trecho"><span class="timestamp">[${escapeHtml(t.timestamp || "")}]</span> <span class="busca-speaker">${escapeHtml(t.speaker || "")}</span>: ${escapeHtml(t.texto || "")}</div>`
      ).join("");
      return `
        <li data-id="${r.id}" class="busca-item${r.id === reuniaoSelecionada ? ' selecionada' : ''}">
          <div class="r-titulo">${escapeHtml(r.titulo)}</div>
          <div class="r-meta">
            <span>${r.data} ${r.hora}</span>
            <span class="tag tem-trans">${r.total_ocorrencias} ocorrência(s)</span>
          </div>
          ${trechos}
        </li>
      `;
    }).join("");
    ul.querySelectorAll("li[data-id]").forEach((li) => {
      li.addEventListener("click", () => {
        selecionarReuniao(li.dataset.id);
        ativarTab("transcricao");
      });
    });
  } catch (e) {
    ul.innerHTML = `<li class="vazio">erro: ${e.message}</li>`;
  }
}

function sairModoBusca() {
  emModoBusca = false;
  $("btn-limpar-filtro").hidden = true;
  renderizarLista(_listaCache);
}

// ────────────────────────────────────────────────────────────────────
// Seleção e detalhes
// ────────────────────────────────────────────────────────────────────
async function selecionarReuniao(id) {
  reuniaoSelecionada = id;
  speakerNomesAtual = {};
  document.querySelectorAll("#ul-reunioes li").forEach((li) => {
    li.classList.toggle("selecionada", li.dataset.id === id);
  });

  ocultar("placeholder");
  mostrar("conteudo", "block");
  cancelarConfirmacaoExcluir();
  cancelarEdicaoTitulo();

  const [data, slug] = id.split("/");
  const partes = slug.split("-");
  const hora = `${partes[0]}:${partes[1]}`;
  const titulo = partes.slice(2).join("-");

  $("det-titulo").textContent = titulo;
  $("input-titulo").value = titulo;
  $("det-meta").textContent = `${data} às ${hora}`;
  $("player").src = `/api/reunioes/${data}/${slug}/audio`;

  // Feature 12: reset do cliente até o meta carregar; F1: idem para projeto
  clienteAtualReuniao = "";
  $("det-cliente").textContent = "Cliente: —";
  cancelarEdicaoCliente();
  projetoAtualReuniao = "";
  $("det-projeto").textContent = "Projeto: —";
  cancelarEdicaoProjeto();
  $("det-sync-toggle").checked = false;

  // Carrega meta (speaker_nomes e demais dados)
  try {
    const meta = await api(`/api/reunioes/${data}/${slug}/meta`);
    speakerNomesAtual = meta.speaker_nomes || {};
    // Atualiza título se vier no meta
    if (meta.titulo) {
      $("det-titulo").textContent = meta.titulo;
      $("input-titulo").value = meta.titulo;
    }
    // Atualiza meta com idioma/duracao se disponível
    let metaExtra = `${data} às ${hora}`;
    if (meta.idioma) metaExtra += ` · ${meta.idioma}`;
    if (meta.duracao_s) metaExtra += ` · ${fmtDuracao(meta.duracao_s)}`;
    $("det-meta").textContent = metaExtra;
    // Feature 12: preenche cliente a partir do meta; F1: idem para projeto
    clienteAtualReuniao = meta.cliente || "";
    $("det-cliente").textContent = clienteAtualReuniao ? `Cliente: ${clienteAtualReuniao}` : "Cliente: —";
    projetoAtualReuniao = meta.projeto || "";
    $("det-projeto").textContent = projetoAtualReuniao ? `Projeto: ${projetoAtualReuniao}` : "Projeto: —";
    // F6: estado atual de sync desta reunião (meta já devolve o campo completo)
    $("det-sync-toggle").checked = !!meta.sync_habilitado;
  } catch (_) {
    // meta é opcional, ignora 404
  }

  // Carrega transcrição
  try {
    const t = await api(`/api/reunioes/${data}/${slug}/transcricao`);
    transcricaoTextoAtual = t.texto || "";
    $("transcricao-render").innerHTML = renderizarTranscricao(t.texto, speakerNomesAtual);
    inicializarSpeakerEdits(data, slug);
  } catch (e) {
    transcricaoTextoAtual = "";
    $("transcricao-render").innerHTML = '<p class="dica">Sem transcrição. Use "Reprocessar".</p>';
  }

  // Carrega hotwords
  try {
    const h = await api(`/api/reunioes/${data}/${slug}/hotwords`);
    $("hotwords-render").innerHTML = renderizarHotwords(h.texto, speakerNomesAtual);
  } catch (e) {
    $("hotwords-render").innerHTML = '<p class="dica">Sem hotwords. Use "Reprocessar" com hotwords definidas.</p>';
  }

  // Feature 4: Carrega resumo
  await carregarResumo(data, slug);

  // Reset da aba
  ativarTab("transcricao");
}

// Formata duração em segundos como "1h23m" ou "45min"
function fmtDuracao(s) {
  if (!s) return "";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h${String(m).padStart(2, "0")}m`;
  return `${m}min`;
}

// ────────────────────────────────────────────────────────────────────
// Renderização da transcrição (formato do reuniao.py)
// ────────────────────────────────────────────────────────────────────
function renderizarTranscricao(texto, speakerNomes = {}) {
  if (!texto) return '<p class="dica">Vazio</p>';
  const linhas = texto.split("\n");
  const out = [];
  for (const linha of linhas) {
    if (linha.startsWith("# ")) continue;
    if (linha.startsWith("**") && linha.endsWith("**")) {
      const speakerOriginal = linha.slice(2, -2);
      const speakerDisplay = speakerNomes[speakerOriginal] || speakerOriginal;
      const cls = speakerOriginal === "Eu" ? "speaker-line speaker-eu" : "speaker-line";
      // Feature 6: speaker clicável para renomear
      out.push(`<div class="${cls}" data-speaker="${escapeHtml(speakerOriginal)}" title="Clique para renomear">${escapeHtml(speakerDisplay)}</div>`);
    } else if (linha.match(/^\[\d/)) {
      const m = linha.match(/^(\[[\d:]+\])\s*(.*)$/);
      if (m) {
        out.push(`<div><span class="timestamp">${m[1]}</span>${escapeHtml(m[2])}</div>`);
      }
    }
  }
  if (!out.length) return '<p class="dica">Nenhum segmento de fala identificado. Tente reprocessar com outro modelo ou verifique se o áudio possui voz audível.</p>';
  return out.join("");
}

// ────────────────────────────────────────────────────────────────────
// Renderização das hotwords (com suporte a speakerNomes)
// ────────────────────────────────────────────────────────────────────
function renderizarHotwords(texto, speakerNomes = {}) {
  if (!texto) return '<p class="dica">Vazio</p>';
  const blocos = texto.split(/\n(?=- \*\*\[)/);
  const out = [];
  // Cabeçalho
  const header = blocos[0].split("\n").filter((l) => l && !l.startsWith("#"));
  out.push(`<p class="dica">${escapeHtml(header.join(" "))}</p>`);
  // Matches
  for (const bloco of blocos.slice(1)) {
    const m = bloco.match(/^- \*\*\[([^\]]+)\]\*\*\s+`([^`]+)`\s+\(([^)]+)\)\s+—\s+(.+?)\n\s*>\s*(.*)$/s);
    if (m) {
      const [, ts, kw, sim, speaker, ctx] = m;
      const speakerDisplay = speakerNomes[speaker] || speaker;
      out.push(`
        <div class="hw-match">
          <span class="timestamp">${escapeHtml(ts)}</span>
          <span class="hw-keyword">${escapeHtml(kw)}</span>
          <span style="color:var(--text-2);font-size:11px;"> (${escapeHtml(sim)}) — ${escapeHtml(speakerDisplay)}</span>
          <div class="hw-context">${escapeHtml(ctx.trim())}</div>
        </div>
      `);
    }
  }
  return out.join("");
}

// ────────────────────────────────────────────────────────────────────
// Tabs
// ────────────────────────────────────────────────────────────────────
function ativarTab(nome) {
  document.querySelectorAll(".tab").forEach((b) => {
    b.classList.toggle("ativa", b.dataset.tab === nome);
  });
  document.querySelectorAll(".tab-content").forEach((d) => {
    d.hidden = d.id !== `tab-${nome}`;
  });
}

// ────────────────────────────────────────────────────────────────────
// Ações
// ────────────────────────────────────────────────────────────────────
async function iniciarGravacao() {
  const clienteVal = $("cliente").value.trim();
  const projetoVal = $("grav-projeto").value.trim();
  try {
    await api("/api/gravar/iniciar", {
      method: "POST",
      body: JSON.stringify({
        titulo: $("titulo").value || "reuniao",
        modelo: $("modelo").value,
        // Feature 11: inclui idioma
        idioma: $("idioma").value,
        diarizar: $("diarizar").checked,
        hotwords: $("hotwords").value,
        // Feature 12: cliente para faturamento; F1: + projeto
        cliente: clienteVal,
        projeto: projetoVal,
      }),
    });
    _gravacaoClienteAtual = clienteVal;
    _gravacaoProjetoAtual = projetoVal;
    ocultar("grav-sugestao-cliente");
    await atualizarStatus();
  } catch (e) {
    // Feature 1: substitui alert()
    mostrarErro(`Erro ao iniciar: ${e.message}`);
  }
}

async function pararGravacao() {
  try {
    await api("/api/gravar/parar", { method: "POST" });
    _gravacaoClienteAtual = "";
    _gravacaoProjetoAtual = "";
    await atualizarStatus();
  } catch (e) {
    // Feature 1: substitui alert()
    mostrarErro(`Erro ao parar: ${e.message}`);
  }
}

// ────────────────────────────────────────────────────────────────────
// F1: resolução do cliente da gravação — cliente do projeto selecionado na
// topbar (_pickerTopbar) tem prioridade; senão consulta a heurística do
// backend e mostra como sugestão aceitável com 1 clique (nunca aplica
// sozinha); senão vazio. O campo #cliente do painel é a exibição/override
// manual dessa resolução.
// ────────────────────────────────────────────────────────────────────
function _nomeClienteTopbar() {
  if (!_pickerTopbar) return "";
  const sel = _pickerTopbar.getSelecionado();
  if (!sel.cliente_id) return "";
  const c = _clientesMapId[sel.cliente_id];
  return c ? c.nome : "";
}

async function atualizarSugestaoClienteGravacao() {
  const topbarNome = _nomeClienteTopbar();
  if (topbarNome) {
    if (!$("cliente").value.trim()) {
      $("cliente").value = topbarNome;
      await atualizarListaProjetosGravacao();
    }
    _sugestaoClienteAtual = null;
    ocultar("grav-sugestao-cliente");
    return;
  }
  if ($("cliente").value.trim()) {
    ocultar("grav-sugestao-cliente");
    return;
  }
  try {
    const r = await api("/api/gravacao/cliente-sugerido");
    if (r.cliente) {
      _sugestaoClienteAtual = r;
      $("grav-sugestao-nome").textContent = r.cliente;
      mostrar("grav-sugestao-cliente", "flex");
    } else {
      _sugestaoClienteAtual = null;
      ocultar("grav-sugestao-cliente");
    }
  } catch (_) {
    ocultar("grav-sugestao-cliente");
  }
}

function aceitarSugestaoCliente() {
  if (!_sugestaoClienteAtual) return;
  $("cliente").value = _sugestaoClienteAtual.cliente;
  ocultar("grav-sugestao-cliente");
  atualizarListaProjetosGravacao();
}

// Popula o datalist de projetos do painel de gravação a partir do cliente
// digitado em #cliente, quando ele corresponde a um cliente cadastrado
// (com id conhecido). Sem correspondência, o campo de projeto segue livre.
async function atualizarListaProjetosGravacao() {
  const nome = $("cliente").value.trim();
  const clienteObj = Object.values(_clientesMapId).find((c) => c.nome === nome);
  const dl = $("lista-projetos-gravacao");
  if (!clienteObj) {
    dl.innerHTML = "";
    return;
  }
  try {
    const lista = await api(`/api/projetos?cliente_id=${encodeURIComponent(clienteObj.id)}`);
    dl.innerHTML = lista.filter((p) => p.ativo !== false)
      .map((p) => `<option value="${escapeHtml(p.nome)}"></option>`).join("");
  } catch (_) {
    dl.innerHTML = "";
  }
}

function _iniciaisCliente(nome) {
  if (!nome) return "";
  const partes = nome.trim().split(/\s+/).filter(Boolean);
  if (!partes.length) return "";
  if (partes.length === 1) return partes[0].slice(0, 2).toUpperCase();
  return (partes[0][0] + partes[1][0]).toUpperCase();
}

function cancelarConfirmacaoExcluir() {
  mostrar("btn-excluir", "inline-block");
  ocultar("confirm-excluir");
}

function pedirConfirmacaoExcluir() {
  ocultar("btn-excluir");
  mostrar("confirm-excluir", "flex");
}

async function confirmarExcluir() {
  if (!reuniaoSelecionada) return;
  const [data, slug] = reuniaoSelecionada.split("/");
  try {
    await api(`/api/reunioes/${data}/${slug}/excluir`, { method: "POST" });
    reuniaoSelecionada = null;
    ocultar("conteudo");
    mostrar("placeholder", "flex");
    await carregarLista();
  } catch (e) {
    cancelarConfirmacaoExcluir();
    // Feature 1: substitui alert()
    mostrarErro(`Erro ao excluir: ${e.message}`);
  }
}

async function reprocessar() {
  if (!reuniaoSelecionada) return;
  const [data, slug] = reuniaoSelecionada.split("/");
  try {
    await api(`/api/reunioes/${data}/${slug}/reprocessar`, {
      method: "POST",
      body: JSON.stringify({
        modelo: $("re-modelo").value,
        diarizar: $("re-diarizar").checked,
        // Feature 11: inclui idioma no reprocessar
        idioma: $("re-idioma").value,
        hotwords: $("re-hotwords").value,
      }),
    });
    await atualizarStatus();
  } catch (e) {
    // Feature 1: substitui alert()
    mostrarErro(`Erro ao reprocessar: ${e.message}`);
  }
}

// ────────────────────────────────────────────────────────────────────
// Feature 4: Aba Resumo
// ────────────────────────────────────────────────────────────────────
async function carregarResumo(data, slug) {
  const render = $("resumo-render");
  const btn = $("btn-gerar-resumo");

  render.innerHTML = '<p class="dica">Carregando resumo…</p>';
  ocultar("btn-gerar-resumo");

  try {
    const r = await api(`/api/reunioes/${data}/${slug}/resumo`);
    render.innerHTML = markdownParaHtml(r.texto || "");
    ocultar("btn-gerar-resumo");
  } catch (e) {
    if (e.message.includes("404") || e.message.includes("Not Found")) {
      render.innerHTML = '<p class="dica">Nenhum resumo gerado ainda.</p>';
      mostrar("btn-gerar-resumo", "inline-block");
    } else {
      render.innerHTML = `<p class="dica">Erro ao carregar resumo: ${escapeHtml(e.message)}</p>`;
      mostrar("btn-gerar-resumo", "inline-block");
    }
  }

  // Bind do botão gerar (substitui node para remover listener anterior)
  const btnNovo = btn.cloneNode(true);
  btn.parentNode.replaceChild(btnNovo, btn);
  $("btn-gerar-resumo").addEventListener("click", () => gerarResumo(data, slug));
}

async function gerarResumo(data, slug) {
  const render = $("resumo-render");
  const btn = $("btn-gerar-resumo");
  btn.disabled = true;
  btn.textContent = "Gerando… (pode levar alguns segundos)";
  render.innerHTML = '<p class="dica">Aguarde, gerando resumo via LLM…</p>';
  try {
    const r = await api(`/api/reunioes/${data}/${slug}/resumo`, { method: "POST" });
    render.innerHTML = markdownParaHtml(r.texto || "");
    ocultar("btn-gerar-resumo");
    // Atualiza a lista para refletir tem_resumo
    await carregarLista();
  } catch (e) {
    if (e.message.includes("503")) {
      render.innerHTML = `<p class="dica" style="color:var(--warning)">LLM não disponível: ${escapeHtml(e.message)}</p>`;
    } else {
      render.innerHTML = `<p class="dica">Erro: ${escapeHtml(e.message)}</p>`;
    }
    btn.disabled = false;
    btn.textContent = "✦ Gerar resumo";
  }
}

// Renderização simples de Markdown → HTML (sem libs externas)
function markdownParaHtml(md) {
  if (!md) return '<p class="dica">Vazio</p>';
  const linhas = md.split("\n");
  const out = [];
  for (let i = 0; i < linhas.length; i++) {
    let l = linhas[i];
    // Headings ## e ###
    if (l.startsWith("### ")) {
      out.push(`<h4 class="md-h">${escapeHtml(l.slice(4))}</h4>`);
    } else if (l.startsWith("## ")) {
      out.push(`<h3 class="md-h">${escapeHtml(l.slice(3))}</h3>`);
    } else if (l.startsWith("# ")) {
      out.push(`<h2 class="md-h">${escapeHtml(l.slice(2))}</h2>`);
    } else if (l.startsWith("- ") || l.startsWith("* ")) {
      // Bullet
      const txt = aplicarInline(l.slice(2));
      out.push(`<div class="md-bullet">• ${txt}</div>`);
    } else if (l.trim() === "") {
      out.push("<br>");
    } else {
      out.push(`<p class="md-p">${aplicarInline(l)}</p>`);
    }
  }
  return out.join("");
}

// Aplica inline markdown: **negrito** e _itálico_
function aplicarInline(s) {
  return escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/_(.+?)_/g, "<em>$1</em>");
}

// ────────────────────────────────────────────────────────────────────
// Feature 5: Edição de título
// ────────────────────────────────────────────────────────────────────
function cancelarEdicaoTitulo() {
  mostrar("titulo-view", "flex");
  $("titulo-edit").style.display = "none";
}

function iniciarEdicaoTitulo() {
  $("titulo-view").style.display = "none";
  mostrar("titulo-edit", "flex");
  const inp = $("input-titulo");
  inp.value = $("det-titulo").textContent;
  inp.focus();
  inp.select();
}

async function salvarTitulo() {
  if (!reuniaoSelecionada) return;
  const novoTitulo = $("input-titulo").value.trim();
  if (!novoTitulo) return;
  const [data, slug] = reuniaoSelecionada.split("/");
  try {
    await api(`/api/reunioes/${data}/${slug}`, {
      method: "PATCH",
      body: JSON.stringify({ titulo: novoTitulo }),
    });
    $("det-titulo").textContent = novoTitulo;
    cancelarEdicaoTitulo();
    await carregarLista();
    mostrarToast("Título atualizado", "ok");
  } catch (e) {
    mostrarErro(`Erro ao salvar título: ${e.message}`);
  }
}

// ────────────────────────────────────────────────────────────────────
// Feature 12: Edição de cliente (faturamento)
// ────────────────────────────────────────────────────────────────────
function cancelarEdicaoCliente() {
  mostrar("det-cliente-view", "block");
  $("det-cliente-edit").style.display = "none";
}

function iniciarEdicaoCliente() {
  $("det-cliente-view").style.display = "none";
  mostrar("det-cliente-edit", "flex");
  const inp = $("input-cliente");
  inp.value = clienteAtualReuniao;
  inp.focus();
  inp.select();
}

async function salvarCliente() {
  // Já cancelado (ex: via Escape) — ignora o blur que segue
  if ($("det-cliente-edit").style.display === "none") return;
  if (!reuniaoSelecionada) return cancelarEdicaoCliente();
  const novoCliente = $("input-cliente").value.trim();
  cancelarEdicaoCliente();
  if (novoCliente === clienteAtualReuniao) return;
  const [data, slug] = reuniaoSelecionada.split("/");
  try {
    await api(`/api/reunioes/${data}/${slug}`, {
      method: "PATCH",
      body: JSON.stringify({ cliente: novoCliente }),
    });
    clienteAtualReuniao = novoCliente;
    $("det-cliente").textContent = novoCliente ? `Cliente: ${novoCliente}` : "Cliente: —";
    mostrarToast("Cliente atualizado", "ok");
    await carregarLista();
  } catch (e) {
    mostrarErro(`Erro ao salvar cliente: ${e.message}`);
  }
}

// ────────────────────────────────────────────────────────────────────
// F1: Edição de projeto no painel de detalhe (mesmo padrão do cliente)
// ────────────────────────────────────────────────────────────────────
function cancelarEdicaoProjeto() {
  mostrar("det-projeto-view", "block");
  $("det-projeto-edit").style.display = "none";
}

function iniciarEdicaoProjeto() {
  $("det-projeto-view").style.display = "none";
  mostrar("det-projeto-edit", "flex");
  const inp = $("input-projeto");
  inp.value = projetoAtualReuniao;
  inp.focus();
  inp.select();
}

async function salvarProjeto() {
  if ($("det-projeto-edit").style.display === "none") return;
  if (!reuniaoSelecionada) return cancelarEdicaoProjeto();
  const novoProjeto = $("input-projeto").value.trim();
  cancelarEdicaoProjeto();
  if (novoProjeto === projetoAtualReuniao) return;
  const [data, slug] = reuniaoSelecionada.split("/");
  try {
    await api(`/api/reunioes/${data}/${slug}`, {
      method: "PATCH",
      body: JSON.stringify({ projeto: novoProjeto }),
    });
    projetoAtualReuniao = novoProjeto;
    $("det-projeto").textContent = novoProjeto ? `Projeto: ${novoProjeto}` : "Projeto: —";
    mostrarToast("Projeto atualizado", "ok");
    await carregarLista();
  } catch (e) {
    mostrarErro(`Erro ao salvar projeto: ${e.message}`);
  }
}

// ────────────────────────────────────────────────────────────────────
// F6: toggle "Sincronizar esta reunião na nuvem" (push-only, sem áudio)
// ────────────────────────────────────────────────────────────────────
async function toggleSyncReuniao() {
  if (!reuniaoSelecionada) return;
  const habilitado = $("det-sync-toggle").checked;
  const [data, slug] = reuniaoSelecionada.split("/");
  try {
    await api(`/api/reunioes/${data}/${slug}`, {
      method: "PATCH",
      body: JSON.stringify({ sync_habilitado: habilitado }),
    });
    mostrarToast(habilitado ? "Reunião marcada para sincronizar" : "Reunião marcada como só local", "ok");
  } catch (e) {
    mostrarErro(`Erro ao atualizar sincronização da reunião: ${e.message}`);
    $("det-sync-toggle").checked = !habilitado;
  }
}

// ────────────────────────────────────────────────────────────────────
// Feature 6: Renomear speakers inline
// ────────────────────────────────────────────────────────────────────
function inicializarSpeakerEdits(data, slug) {
  document.querySelectorAll("#transcricao-render .speaker-line").forEach((el) => {
    el.style.cursor = "pointer";
    el.addEventListener("click", () => abrirRenomearSpeaker(el, data, slug));
  });
}

function abrirRenomearSpeaker(el, data, slug) {
  // Se já tem um input aberto, fecha
  const jaAberto = el.querySelector("input.speaker-rename-input");
  if (jaAberto) return;

  const speakerOriginal = el.dataset.speaker;
  const nomeAtual = speakerNomesAtual[speakerOriginal] || speakerOriginal;

  const inp = document.createElement("input");
  inp.type = "text";
  inp.className = "speaker-rename-input";
  inp.value = nomeAtual;
  inp.style.cssText = "font-size:13px;padding:2px 6px;margin-left:8px;width:140px;background:var(--input-bg);border:1px solid var(--accent);color:var(--text-1);border-radius:4px;";

  el.appendChild(inp);
  inp.focus();
  inp.select();

  const confirmar = async () => {
    const novoNome = inp.value.trim();
    inp.remove();
    if (!novoNome || novoNome === nomeAtual) {
      // Restaura exibição sem mudança
      el.childNodes[0].textContent = speakerNomesAtual[speakerOriginal] || speakerOriginal;
      return;
    }
    speakerNomesAtual[speakerOriginal] = novoNome;
    el.childNodes[0].textContent = novoNome;
    // Aplica em todos os speakers com o mesmo rótulo
    document.querySelectorAll(`#transcricao-render .speaker-line[data-speaker="${escapeHtml(speakerOriginal)}"]`).forEach((s) => {
      s.childNodes[0].textContent = novoNome;
    });
    // Salva via PATCH
    try {
      await api(`/api/reunioes/${data}/${slug}`, {
        method: "PATCH",
        body: JSON.stringify({ speaker_nomes: speakerNomesAtual }),
      });
      mostrarToast(`Speaker "${escapeHtml(speakerOriginal)}" → "${escapeHtml(novoNome)}"`, "ok");
      // Recarrega hotwords com os novos nomes
      try {
        const h = await api(`/api/reunioes/${data}/${slug}/hotwords`);
        $("hotwords-render").innerHTML = renderizarHotwords(h.texto, speakerNomesAtual);
      } catch (_) {}
    } catch (e) {
      mostrarErro(`Erro ao salvar speaker: ${e.message}`);
    }
  };

  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmar(); }
    if (e.key === "Escape") { inp.remove(); }
  });
  inp.addEventListener("blur", confirmar);
}

// ────────────────────────────────────────────────────────────────────
// Feature 7: Copiar transcrição
// ────────────────────────────────────────────────────────────────────
async function copiarTranscricao() {
  if (!transcricaoTextoAtual) return;
  try {
    await navigator.clipboard.writeText(transcricaoTextoAtual);
    const btn = $("btn-copiar-trans");
    const original = btn.textContent;
    btn.textContent = "✓ Copiado";
    setTimeout(() => { btn.textContent = original; }, 2000);
  } catch (e) {
    mostrarErro("Não foi possível acessar a área de transferência.");
  }
}

// ────────────────────────────────────────────────────────────────────
// Feature 8: Exportar reunião
// ────────────────────────────────────────────────────────────────────
async function exportarReuniao() {
  if (!reuniaoSelecionada) return;
  const [data, slug] = reuniaoSelecionada.split("/");
  const btn = $("btn-exportar");
  btn.disabled = true;
  try {
    const r = await api(`/api/reunioes/${data}/${slug}/exportar`, { method: "POST" });
    mostrarToast(`Exportado para: ${r.destino}`, "ok", 6000);
  } catch (e) {
    if (e.message.includes("400")) {
      mostrarErro("Configure o diretório de exportação nas Configurações (⚙️) antes de exportar.");
    } else {
      mostrarErro(`Erro ao exportar: ${e.message}`);
    }
  } finally {
    btn.disabled = false;
  }
}

// ────────────────────────────────────────────────────────────────────
// Feature 9: Painel de Configurações
// ────────────────────────────────────────────────────────────────────
async function abrirConfig() {
  // Carrega config atual
  try {
    const cfg = await api("/api/config");
    $("cfg-idioma").value = cfg.idioma || "auto";
    $("cfg-modelo").value = cfg.modelo_padrao || "medium";
    $("cfg-comprimir").checked = !!cfg.comprimir_audio;
    $("cfg-resumo-auto").checked = !!cfg.resumo_automatico;
    $("cfg-export-dir").value = cfg.export_dir || "";
    const det = cfg.deteccao || {};
    $("cfg-det-ativa").checked = det.ativa !== false;
    $("cfg-det-auto").checked = !!det.auto_iniciar;
    $("cfg-det-apps").value = (det.apps || []).join(", ");
    if (cfg.llm) {
      $("cfg-llm-provider").value = cfg.llm.provider || "none";
      $("cfg-llm-modelo").value = cfg.llm.modelo || "";
    }
    const tr = cfg.transcricao || {};
    $("cfg-transc-provider").value = tr.provider || "local";
    $("cfg-transc-modelo").value = tr.modelo || "";
    const notif = cfg.notificacoes || {};
    $("cfg-notif-ativo").checked = notif.ativo !== false;
    $("cfg-notif-dia").value = notif.antecedencia_dia_min ?? 1440;
    $("cfg-notif-hora").value = notif.antecedencia_hora_min ?? 60;
    $("cfg-notif-vencido").value = notif.vencido_repetir || "diario";
    // Campo de chave nunca é pré-preenchido; só limpamos o input
    $("cfg-llm-chave").value = "";
    // Sincronização Supabase
    const sync = cfg.sync || {};
    $("cfg-sync-ativo").checked = !!sync.ativo;
    $("cfg-sync-url").value = sync.url || "";
    $("cfg-sync-chave").value = "";
    $("cfg-sync-modo").value = sync.modo || "tudo";
    _syncModo = sync.modo || "tudo";
  } catch (e) {
    mostrarErro(`Erro ao carregar configurações: ${e.message}`);
  }
  await carregarStatusSync();
  // Carrega status LLM (inclui se a chave já está configurada, sem expô-la)
  try {
    const st = await api("/api/llm/status");
    const display = $("llm-status-display");
    if (st.disponivel) {
      display.innerHTML = `<span class="llm-ok">✓ ${escapeHtml(st.provider)} — ${escapeHtml(st.modelo)}</span>`;
    } else {
      display.innerHTML = `<span class="llm-warn">⚠ ${escapeHtml(st.motivo || "LLM não disponível")}</span>`;
    }
    const cs = $("chave-status-display");
    cs.innerHTML = st.chave_configurada
      ? '<span class="llm-ok">✓ Chave configurada</span>'
      : '<span class="llm-warn">⚠ Nenhuma chave salva</span>';
  } catch (_) {
    $("llm-status-display").innerHTML = '<span class="llm-warn">⚠ Não foi possível verificar o status do LLM</span>';
  }
}

// Salva a chave de API do provedor selecionado (nunca é lida de volta ao input)
async function salvarChaveLLM() {
  const provider = $("cfg-llm-provider").value;
  const chave = $("cfg-llm-chave").value;
  if (provider === "none") {
    mostrarToast("Selecione um provedor antes de salvar a chave", "erro");
    return;
  }
  const btn = $("btn-salvar-chave");
  btn.disabled = true;
  try {
    const r = await api("/api/llm/chave", {
      method: "POST",
      body: JSON.stringify({ provider, chave }),
    });
    $("cfg-llm-chave").value = "";
    $("chave-status-display").innerHTML = r.chave_configurada
      ? '<span class="llm-ok">✓ Chave configurada</span>'
      : '<span class="llm-warn">⚠ Nenhuma chave salva</span>';
    mostrarToast(chave.trim() ? "Chave salva" : "Chave removida", "ok");
  } catch (e) {
    mostrarToast(`Erro ao salvar chave: ${e.message}`, "erro");
  } finally {
    btn.disabled = false;
  }
}

// Testa a chave do provedor com uma chamada mínima
async function testarChaveLLM() {
  const provider = $("cfg-llm-provider").value;
  const modelo = $("cfg-llm-modelo").value.trim();
  if (provider === "none") {
    mostrarToast("Selecione um provedor antes de testar", "erro");
    return;
  }
  const btn = $("btn-testar-chave");
  btn.disabled = true;
  const cs = $("chave-status-display");
  cs.innerHTML = '<span class="llm-warn">Testando…</span>';
  try {
    const r = await api("/api/llm/testar", {
      method: "POST",
      body: JSON.stringify({ provider, modelo }),
    });
    cs.innerHTML = r.ok
      ? '<span class="llm-ok">✓ Conexão OK</span>'
      : `<span class="llm-warn">⚠ ${escapeHtml(r.erro || "Falha no teste")}</span>`;
  } catch (e) {
    cs.innerHTML = `<span class="llm-warn">⚠ ${escapeHtml(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}

function fecharConfig() {
  mostrarTela("gravacoes");
}

async function salvarConfig() {
  const patch = {
    idioma: $("cfg-idioma").value,
    modelo_padrao: $("cfg-modelo").value,
    comprimir_audio: $("cfg-comprimir").checked,
    resumo_automatico: $("cfg-resumo-auto").checked,
    export_dir: $("cfg-export-dir").value.trim(),
    deteccao: {
      ativa: $("cfg-det-ativa").checked,
      auto_iniciar: $("cfg-det-auto").checked,
      apps: $("cfg-det-apps").value.split(",").map((s) => s.trim()).filter(Boolean),
    },
    llm: {
      provider: $("cfg-llm-provider").value,
      modelo: $("cfg-llm-modelo").value.trim(),
    },
    transcricao: {
      provider: $("cfg-transc-provider").value,
      modelo: $("cfg-transc-modelo").value.trim(),
    },
    notificacoes: {
      ativo: $("cfg-notif-ativo").checked,
      antecedencia_dia_min: parseInt($("cfg-notif-dia").value, 10) || 1440,
      antecedencia_hora_min: parseInt($("cfg-notif-hora").value, 10) || 60,
      vencido_repetir: $("cfg-notif-vencido").value,
    },
    sync: {
      ativo: $("cfg-sync-ativo").checked,
      url: $("cfg-sync-url").value.trim(),
      modo: $("cfg-sync-modo").value,
    },
  };
  const btn = $("btn-salvar-config");
  btn.disabled = true;
  try {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify(patch),
    });
    _syncModo = patch.sync.modo;
    fecharConfig();
    mostrarToast("Configurações salvas", "ok");
    // Feature 12: repopula o datalist de clientes com os novos valores
    await carregarClientesDatalist();
  } catch (e) {
    mostrarErro(`Erro ao salvar configurações: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

// ────────────────────────────────────────────────────────────────────
// Horas: helpers de clientes (datalist/selects) — API /api/clientes com
// fallback silencioso para cfg.clientes (compatibilidade) se o endpoint falhar.
// ────────────────────────────────────────────────────────────────────
async function _listarNomesClientes() {
  try {
    const clientes = await api("/api/clientes?incluir_inativos=false");
    return clientes.map((c) => c.nome);
  } catch (_) {
    try {
      const cfg = await api("/api/config");
      return cfg.clientes || [];
    } catch (__) {
      return [];
    }
  }
}

async function carregarClientesDatalist() {
  _clientesCache = await _listarNomesClientes();
  $("lista-clientes").innerHTML = _clientesCache
    .map((c) => `<option value="${escapeHtml(c)}"></option>`).join("");
}

// Repopula TODOS os selects/datalists de cliente do app (picker da topbar,
// gravação, timer/lançamento, relatório, lembretes), preservando o valor
// selecionado quando ele ainda existir. Chamado após qualquer
// criação/edição/exclusão de cliente (a lista de nomes/ids pode ter mudado).
async function atualizarTodosSelectsClientes() {
  await Promise.all([
    _atualizarClienteTopbar(),
    atualizarSelectsClientes(),
    carregarClientesDatalist(),
    popularSelectClientesLembrete(),
    popularSelectClientesRelatorio(),
  ]);
}

// Recarrega os dados (projetos/clientes) das instâncias do pickerProjeto()
// do Timer e do Lançamento retroativo, e o mapa global de projetos.
// Chamado após qualquer criação/toggle/exclusão de projeto ou cliente.
async function atualizarProjetosDependentes() {
  await carregarProjetosTodos();
  await Promise.all([
    _pickerHoras && _pickerHoras.recarregar(),
    _pickerApontEdit && _pickerApontEdit.recarregar(),
    _pickerTopbar && _pickerTopbar.recarregar(),
    atualizarListaProjetosGravacao(),
  ]);
}

// ────────────────────────────────────────────────────────────────────
// pickerProjeto: componente de seleção de projeto estilo Toggl Track —
// substitui os pares de <select cliente/projeto> do popover do Timer, do
// formulário do cronômetro e do lançamento retroativo ("Esqueci de ativar
// o timer"). O projeto é escolhido primeiro; o cliente vem embutido nele
// (projeto.cliente_id) e é derivado automaticamente — não há mais select
// de cliente nesses 3 fluxos. Permite criar projeto (com/sem cliente,
// incl. cliente novo) sem sair do fluxo, e também iniciar sem projeto.
//
// containerEl: elemento vazio onde o componente é montado.
// opts.placeholder: texto do input quando vazio.
// opts.onChange({projeto_id, cliente_id}): chamado a cada seleção/limpeza.
//
// Retorna um controlador: { getSelecionado(), selecionarProjeto(id),
// limpar(), recarregar() }.
// ────────────────────────────────────────────────────────────────────
function pickerProjeto(containerEl, opts = {}) {
  const onChange = typeof opts.onChange === "function" ? opts.onChange : () => {};
  const placeholder = opts.placeholder || "Projeto (opcional)";

  const state = { projetoId: null, clienteId: null };
  let projetos = [];
  let clientes = [];
  let carregado = false;
  let aberto = false;
  let modoCriar = false;
  let highlightIndex = 0;
  let itensAtuais = [];

  containerEl.innerHTML = `
    <div class="pp-root">
      <div class="pp-chip" hidden>
        <span class="pp-chip-text"></span>
        <button type="button" class="pp-chip-x" title="Limpar projeto">✕</button>
      </div>
      <input type="text" class="pp-input" placeholder="${escapeHtml(placeholder)}" autocomplete="off">
      <div class="pp-dropdown" hidden></div>
    </div>
  `;
  const elChip = containerEl.querySelector(".pp-chip");
  const elChipText = containerEl.querySelector(".pp-chip-text");
  const elChipX = containerEl.querySelector(".pp-chip-x");
  const elInput = containerEl.querySelector(".pp-input");
  const elDrop = containerEl.querySelector(".pp-dropdown");

  function nomeCliente(clienteId) {
    if (!clienteId) return null;
    const c = clientes.find((x) => String(x.id) === String(clienteId));
    return c ? c.nome : null;
  }

  async function carregarDados() {
    try {
      const [p, c] = await Promise.all([
        api("/api/projetos"),
        api("/api/clientes?incluir_inativos=false"),
      ]);
      projetos = p.filter((x) => x.ativo !== false);
      clientes = c;
      projetos.forEach((x) => { _projetosMapId[x.id] = x; });
      clientes.forEach((x) => { _clientesMapId[x.id] = x; });
    } catch (_) {
      projetos = [];
      clientes = [];
    }
    carregado = true;
  }

  function listaFiltrada() {
    const f = elInput.value.trim().toLowerCase();
    const projs = projetos.filter((p) => {
      if (!f) return true;
      const cliNome = (nomeCliente(p.cliente_id) || "").toLowerCase();
      return p.nome.toLowerCase().includes(f) || cliNome.includes(f);
    });
    const itens = [{ tipo: "sem" }, ...projs.map((p) => ({ tipo: "projeto", projeto: p }))];
    if (f && !projetos.some((p) => p.nome.toLowerCase() === f)) {
      itens.push({ tipo: "criar", texto: elInput.value.trim() });
    }
    return itens;
  }

  function renderLista() {
    itensAtuais = listaFiltrada();
    if (highlightIndex >= itensAtuais.length) highlightIndex = itensAtuais.length - 1;
    if (highlightIndex < 0) highlightIndex = 0;
    elDrop.innerHTML = itensAtuais.map((item, i) => {
      const hl = i === highlightIndex ? " pp-item-hl" : "";
      if (item.tipo === "sem") {
        return `<div class="pp-item pp-item-sem${hl}" data-idx="${i}">Sem projeto</div>`;
      }
      if (item.tipo === "criar") {
        return `<div class="pp-item pp-item-criar${hl}" data-idx="${i}">➕ Criar projeto "${escapeHtml(item.texto)}"</div>`;
      }
      const cliNome = nomeCliente(item.projeto.cliente_id);
      return `<div class="pp-item${hl}" data-idx="${i}"><span>${escapeHtml(item.projeto.nome)}</span><span class="pp-item-sub">· ${cliNome ? escapeHtml(cliNome) : "(sem cliente)"}</span></div>`;
    }).join("");
    elDrop.querySelectorAll("[data-idx]").forEach((el) => {
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        selecionarItem(itensAtuais[Number(el.dataset.idx)]);
      });
    });
  }

  function renderMiniForm(textoNovoProjeto) {
    const opts = clientes
      .map((c) => `<option value="${escapeHtml(String(c.id))}">${escapeHtml(c.nome)}</option>`)
      .join("");
    elDrop.innerHTML = `
      <div class="pp-miniform">
        <div class="pp-miniform-title">Criar projeto "${escapeHtml(textoNovoProjeto)}"</div>
        <select class="pp-mf-cliente">
          <option value="">(sem cliente)</option>
          ${opts}
          <option value="__novo__">➕ novo cliente…</option>
        </select>
        <input type="text" class="pp-mf-novo-cliente" placeholder="Nome do novo cliente" hidden>
        <div class="pp-mf-actions">
          <button type="button" class="pp-mf-cancelar btn-icon-label">Cancelar</button>
          <button type="button" class="pp-mf-confirmar primary">Criar projeto</button>
        </div>
      </div>
    `;
    const selCli = elDrop.querySelector(".pp-mf-cliente");
    const inpNovoCli = elDrop.querySelector(".pp-mf-novo-cliente");
    const btnCancelar = elDrop.querySelector(".pp-mf-cancelar");
    const btnConfirmar = elDrop.querySelector(".pp-mf-confirmar");

    selCli.addEventListener("change", () => {
      const ehNovo = selCli.value === "__novo__";
      inpNovoCli.hidden = !ehNovo;
      if (ehNovo) inpNovoCli.focus();
    });
    btnCancelar.addEventListener("mousedown", (e) => {
      e.preventDefault();
      modoCriar = false;
      renderLista();
    });
    btnConfirmar.addEventListener("mousedown", (e) => {
      e.preventDefault();
      confirmarCriacao(textoNovoProjeto, selCli, inpNovoCli, btnConfirmar);
    });
  }

  async function confirmarCriacao(nomeProjeto, selCli, inpNovoCli, btnConfirmar) {
    btnConfirmar.disabled = true;
    try {
      let clienteId = selCli.value || null;
      if (clienteId === "__novo__") {
        const nomeCli = inpNovoCli.value.trim();
        if (!nomeCli) {
          mostrarToast("Informe o nome do novo cliente", "erro");
          btnConfirmar.disabled = false;
          return;
        }
        const clienteCriado = await api("/api/clientes", {
          method: "POST",
          body: JSON.stringify({ nome: nomeCli }),
        });
        clienteId = clienteCriado.id;
      }
      const projetoCriado = await api("/api/projetos", {
        method: "POST",
        body: JSON.stringify({ nome: nomeProjeto, cliente_id: clienteId || null }),
      });
      mostrarToast("Projeto criado", "ok");
      modoCriar = false;
      await Promise.all([
        atualizarTodosSelectsClientes(),
        atualizarProjetosDependentes(),
      ]);
      await carregarDados();
      aplicarSelecao(projetoCriado);
      fecharDropdown();
    } catch (e) {
      mostrarErro(`Erro ao criar projeto: ${e.message}`);
      btnConfirmar.disabled = false;
    }
  }

  function render() {
    if (modoCriar) return; // já renderizado pelo abrirMiniForm
    renderLista();
  }

  function abrirDropdown() {
    aberto = true;
    highlightIndex = 0;
    elDrop.hidden = false;
    render();
    document.addEventListener("mousedown", onDocMouseDown);
  }

  function fecharDropdown() {
    aberto = false;
    modoCriar = false;
    elDrop.hidden = true;
    document.removeEventListener("mousedown", onDocMouseDown);
  }

  function onDocMouseDown(e) {
    if (containerEl.contains(e.target)) return;
    fecharDropdown();
  }

  function selecionarItem(item) {
    if (!item) return;
    if (item.tipo === "sem") {
      limparSelecao();
      fecharDropdown();
    } else if (item.tipo === "projeto") {
      aplicarSelecao(item.projeto);
      fecharDropdown();
    } else if (item.tipo === "criar") {
      modoCriar = true;
      renderMiniForm(item.texto);
    }
  }

  function aplicarSelecao(projeto) {
    state.projetoId = projeto.id;
    state.clienteId = projeto.cliente_id || null;
    const cliNome = nomeCliente(state.clienteId);
    elChipText.textContent = `${projeto.nome} · ${cliNome || "(sem cliente)"}`;
    elChip.hidden = false;
    elInput.hidden = true;
    elInput.value = "";
    onChange({ projeto_id: state.projetoId, cliente_id: state.clienteId });
  }

  function limparSelecao() {
    state.projetoId = null;
    state.clienteId = null;
    elChip.hidden = true;
    elInput.hidden = false;
    elInput.value = "";
    onChange({ projeto_id: null, cliente_id: null });
  }

  elInput.addEventListener("focus", async () => {
    if (!carregado) await carregarDados();
    modoCriar = false;
    abrirDropdown();
  });
  elInput.addEventListener("input", () => {
    modoCriar = false;
    highlightIndex = 0;
    if (!aberto) abrirDropdown(); else render();
  });
  elInput.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!aberto) { abrirDropdown(); return; }
      if (modoCriar) return;
      highlightIndex = Math.min(highlightIndex + 1, itensAtuais.length - 1);
      renderLista();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (modoCriar) return;
      highlightIndex = Math.max(highlightIndex - 1, 0);
      renderLista();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!aberto) { abrirDropdown(); return; }
      if (modoCriar) return;
      selecionarItem(itensAtuais[highlightIndex]);
    } else if (e.key === "Escape") {
      if (modoCriar) { modoCriar = false; renderLista(); }
      else fecharDropdown();
    }
  });
  elChipX.addEventListener("click", () => {
    limparSelecao();
    elInput.focus();
  });

  return {
    getSelecionado() {
      return { projeto_id: state.projetoId, cliente_id: state.clienteId };
    },
    async selecionarProjeto(projetoId) {
      if (!carregado) await carregarDados();
      if (!projetoId) { limparSelecao(); return; }
      const p = projetos.find((x) => String(x.id) === String(projetoId));
      if (p) aplicarSelecao(p); else limparSelecao();
    },
    limpar() {
      limparSelecao();
    },
    async recarregar() {
      await carregarDados();
    },
  };
}

// ────────────────────────────────────────────────────────────────────
// Horas: modal central — Timer & Lançamentos / Clientes / Projetos / Relatório
// ────────────────────────────────────────────────────────────────────
// Escopo (bug corrigido): opera só dentro de `escopo` (ex.: um <section
// class="screen">) em vez do documento inteiro. As telas Timer e Clientes
// têm cada uma seu próprio grupo de tabs (tabh-timer/tabh-relatorio vs.
// tabh-clientes/tabh-projetos) — sem escopo, ativar uma aba em uma tela
// escondia o conteúdo (sem correspondência de id) da outra tela.
function ativarTabHoras(nome, escopo) {
  const root = escopo || document;
  root.querySelectorAll(".tab-horas").forEach((b) => {
    b.classList.toggle("ativa", b.dataset.tabHoras === nome);
  });
  root.querySelectorAll(".tab-content-horas").forEach((d) => {
    d.hidden = d.id !== `tabh-${nome}`;
  });
}

// Abre a tela Timer (Timer & Lançamentos / Relatório) — a tela Clientes tem
// seu próprio loader (carregarTelaClientes(), mestre-detalhe, F4).
async function abrirHoras() {
  _garantirIntervalTimer();
  ativarTabHoras("timer", $("screen-timer"));

  await atualizarSelectsClientes();
  await carregarProjetosTodos();
  if (_pickerHoras) await _pickerHoras.recarregar();
  if (_pickerApontEdit) await _pickerApontEdit.recarregar();

  await Promise.all([
    carregarTimerStatus(),
    carregarApontamentos(),
  ]);

  if (!$("rel-mes").value) {
    const hoje = new Date();
    $("rel-mes").value = `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, "0")}`;
  }
  await popularSelectClientesRelatorio();
  await carregarRelatorio();
}

function fecharHoras() {
  mostrarTela("gravacoes");
}

// Popula o mapa global _clientesMapId (usado por vários pontos do app para
// exibir nomes a partir de ids), a partir de /api/clientes (só ativos).
// Os selects de cliente do Timer/Lançamento/popover foram substituídos pelo
// pickerProjeto() (ver seção abaixo) — o cliente agora vem embutido no
// projeto escolhido, então não há mais select de cliente para repopular
// aqui além do mapa em si.
async function atualizarSelectsClientes() {
  try {
    const ativos = await api("/api/clientes?incluir_inativos=false");
    ativos.forEach((c) => { _clientesMapId[c.id] = c; });
  } catch (e) {
    mostrarErro(`Erro ao carregar clientes: ${e.message}`);
  }
}

// ────────────────────────────────────────────────────────────────────
// Horas: aba Timer & Lançamentos
// ────────────────────────────────────────────────────────────────────
function _mesAtual() {
  const hoje = new Date();
  return `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, "0")}`;
}

function _elapsedSince(isoStr) {
  const inicio = new Date(isoStr).getTime();
  if (isNaN(inicio)) return 0;
  return Math.max(0, Math.floor((Date.now() - inicio) / 1000));
}

function _tickTimerDisplay() {
  if (!_timerAtivo) return;
  const txt = fmtCronometro(_elapsedSince(_timerAtivo.inicio));
  const cron = $("horas-cronometro");
  if (cron) cron.textContent = txt;
  const badge = $("badge-timer");
  if (badge) badge.textContent = `⏱ ${txt}`;
  atualizarTopbarTempo();
}

// Atualiza o visual da pill de Timer no topbar (outline ocioso → preenchido ativo)
function _atualizarTimerPill() {
  const pill = $("btn-timer-pill");
  if (!pill) return;
  if (_timerAtivo) {
    pill.classList.remove("pill-outline");
    pill.classList.add("pill-filled");
    pill.textContent = "⏹ Timer";
  } else {
    pill.classList.remove("pill-filled");
    pill.classList.add("pill-outline");
    pill.textContent = "⏱ Timer";
  }
}

// Mantém o setInterval do cronômetro rodando só enquanto o modal está
// aberto ou o timer está de fato ativo (mesmo com o modal fechado).
function _garantirIntervalTimer() {
  const telaTimerAberta = telaAtual === "timer";
  const precisa = !!_timerAtivo || telaTimerAberta;
  if (precisa && !_timerIntervalId) {
    _timerIntervalId = setInterval(_tickTimerDisplay, 1000);
  } else if (!precisa && _timerIntervalId) {
    clearInterval(_timerIntervalId);
    _timerIntervalId = null;
  }
}

async function carregarTimerStatus() {
  try {
    const st = await api("/api/horas/timer");
    if (st.ativo && st.apontamento) {
      _timerAtivo = st.apontamento;
      mostrar("badge-timer", "inline-block");
      _tickTimerDisplay();
    } else {
      _timerAtivo = null;
      ocultar("badge-timer");
      $("horas-cronometro").textContent = "00:00";
    }
    _refletirEstadoBotaoTimer();
  } catch (_) {
    // status do timer é opcional; ignora falha
  }
  _atualizarTimerPill();
  atualizarTopbarTempo();
  _garantirIntervalTimer();
}

// True quando a faixa de horário manual está completa (data + início + fim) —
// nesse caso o botão principal vira "confirmar/salvar registro passado" em vez
// de iniciar o cronômetro (modelo Toggl: uma linha, ação contextual).
function _horarioManualPreenchido() {
  return Boolean($("lanc-data").value && $("lanc-hora-inicio").value && $("lanc-hora-fim").value);
}

// Reflete no botão único (#btn-timer-toggle) o estado atual: rodando (⏸ parar),
// faixa manual completa (✓ salvar) ou ocioso (▶ iniciar).
function _refletirEstadoBotaoTimer() {
  const btn = $("btn-timer-toggle");
  if (!btn) return;
  if (_timerAtivo) {
    btn.textContent = "⏸";
    btn.title = "Parar timer";
    btn.classList.remove("primary", "horas-play-confirmar");
    btn.classList.add("danger");
    return;
  }
  btn.classList.remove("danger");
  btn.classList.add("primary");
  if (_horarioManualPreenchido()) {
    btn.textContent = "✓";
    btn.title = "Salvar registro passado";
    btn.classList.add("horas-play-confirmar");
  } else {
    btn.textContent = "▶";
    btn.title = "Iniciar timer";
    btn.classList.remove("horas-play-confirmar");
  }
}

async function acaoTimerPrincipal() {
  if (_timerAtivo) {
    await pararTimer();
  } else if (_horarioManualPreenchido()) {
    await criarApontamento();
  } else {
    await iniciarTimer();
  }
}

// Requisição de início de timer, compartilhada pela tela Timer e pela
// topbar (ver seção "Topbar — iniciar timer direto").
async function _iniciarTimerRequisicao(clienteId, projetoId, descricao) {
  await api("/api/horas/timer/iniciar", {
    method: "POST",
    body: JSON.stringify({
      cliente_id: clienteId,
      projeto_id: projetoId || null,
      descricao: (descricao || "").trim(),
    }),
  });
  await carregarTimerStatus();
}

async function iniciarTimer() {
  const sel = _pickerHoras ? _pickerHoras.getSelecionado() : { projeto_id: null, cliente_id: null };
  const btn = $("btn-timer-toggle");
  btn.disabled = true;
  try {
    await _iniciarTimerRequisicao(sel.cliente_id, sel.projeto_id, $("horas-desc").value);
    mostrarToast("Timer iniciado", "ok");
  } catch (e) {
    mostrarErro(`Erro ao iniciar timer: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function pararTimer() {
  const btn = $("btn-timer-toggle");
  btn.disabled = true;
  try {
    await api("/api/horas/timer/parar", { method: "POST" });
    await carregarTimerStatus();
    await carregarApontamentos();
    mostrarToast("Timer parado", "ok");
  } catch (e) {
    mostrarErro(`Erro ao parar timer: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

// F2: bloco "Esqueci de ativar o timer" — data + hora início/fim separados
// (em vez de datetime-local), com atalhos Hoje/Ontem que só preenchem a data.
function _dataYMD(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function _isoLocal(dataStr, horaStr) {
  if (!dataStr || !horaStr) return "";
  return `${dataStr}T${horaStr}`;
}

function _addDiaYMD(dataStr) {
  const [y, m, d] = dataStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + 1);
  return _dataYMD(dt);
}

function preencherDataRetro(offsetDias) {
  const d = new Date();
  d.setDate(d.getDate() - offsetDias);
  $("lanc-data").value = _dataYMD(d);
  _refletirEstadoBotaoTimer();
}

async function criarApontamento() {
  const sel = _pickerHoras ? _pickerHoras.getSelecionado() : { projeto_id: null, cliente_id: null };
  const data = $("lanc-data").value;
  const horaIni = $("lanc-hora-inicio").value;
  const horaFim = $("lanc-hora-fim").value;
  const erroEl = $("lanc-erro");
  erroEl.hidden = true;
  if (!data || !horaIni || !horaFim) {
    mostrarToast("Preencha data e horários de início/fim", "erro");
    return;
  }
  const inicio = _isoLocal(data, horaIni);
  const dataFim = horaFim <= horaIni ? _addDiaYMD(data) : data;
  const fim = _isoLocal(dataFim, horaFim);
  if (new Date(fim).getTime() <= new Date(inicio).getTime()) {
    erroEl.textContent = "O horário de término deve ser depois do início. Dica: fim menor que início = dia seguinte.";
    erroEl.hidden = false;
    return;
  }
  const btn = $("btn-timer-toggle");
  btn.disabled = true;
  try {
    await api("/api/apontamentos", {
      method: "POST",
      body: JSON.stringify({
        cliente_id: sel.cliente_id,
        projeto_id: sel.projeto_id,
        descricao: $("horas-desc").value.trim(),
        inicio,
        fim,
      }),
    });
    $("horas-desc").value = "";
    $("lanc-data").value = "";
    $("lanc-hora-inicio").value = "";
    $("lanc-hora-fim").value = "";
    if (_pickerHoras) _pickerHoras.limpar();
    mostrarToast("Registro salvo", "ok");
    await carregarApontamentos();
  } catch (e) {
    mostrarErro(`Erro ao salvar registro: ${e.message}`);
  } finally {
    btn.disabled = false;
    _refletirEstadoBotaoTimer();
  }
}

async function carregarApontamentos() {
  const render = $("apont-lista");
  render.innerHTML = '<p class="dica">Carregando…</p>';
  try {
    const lista = await api(`/api/apontamentos?mes=${_mesAtual()}`);
    render.innerHTML = renderizarApontamentos(lista);
    render.querySelectorAll("[data-apont-excluir]").forEach((el) => {
      el.addEventListener("click", () => excluirApontamento(el.dataset.apontExcluir));
    });
    render.querySelectorAll("[data-apont-editar]").forEach((el) => {
      el.addEventListener("click", () => editarApontamento(el.dataset.apontEditar));
    });
    _bindSyncIcons(render, "/api/apontamentos", carregarApontamentos);
  } catch (e) {
    render.innerHTML = `<p class="dica">Erro ao carregar lançamentos: ${escapeHtml(e.message)}</p>`;
  }
}

// F2: lista agrupada por dia ("Hoje — 2h25", "Ontem — 3h10", depois datas),
// com total por grupo. Agrupamento é 100% client-side a partir do campo
// `inicio` (já filtrado por mês na API).
function _rotuloGrupoDia(dateKey) {
  const hoje = _dataYMD(new Date());
  const ontemD = new Date();
  ontemD.setDate(ontemD.getDate() - 1);
  const ontem = _dataYMD(ontemD);
  if (dateKey === hoje) return "Hoje";
  if (dateKey === ontem) return "Ontem";
  const [y, m, d] = dateKey.split("-");
  return `${d}/${m}/${y}`;
}

function agruparApontamentosPorDia(lista) {
  const grupos = {};
  for (const a of lista) {
    const dt = a.inicio ? new Date(a.inicio) : null;
    const key = dt && !isNaN(dt.getTime()) ? _dataYMD(dt) : "—";
    (grupos[key] = grupos[key] || []).push(a);
  }
  return Object.keys(grupos)
    .sort((a, b) => b.localeCompare(a))
    .map((key) => ({
      key,
      label: key === "—" ? "Sem data" : _rotuloGrupoDia(key),
      itens: grupos[key],
      total_s: grupos[key].reduce((acc, a) => acc + (a.duracao_s || 0), 0),
    }));
}

function renderizarApontamentos(lista) {
  if (!lista.length) return '<p class="dica">Nenhum lançamento neste mês.</p>';
  const grupos = agruparApontamentosPorDia(lista);
  return grupos.map((g) => `
    <div class="apont-grupo">
      <div class="apont-grupo-header">${escapeHtml(g.label)} — ${escapeHtml(fmtDuracao(g.total_s))}</div>
      ${g.itens.map(_renderizarApontamentoItem).join("")}
    </div>
  `).join("");
}

function _renderizarApontamentoItem(a) {
  const clienteObj = a.cliente_id ? _clientesMapId[a.cliente_id] : null;
  const cliente = !a.cliente_id
    ? "(sem cliente)"
    : (clienteObj ? clienteObj.nome : "(cliente removido)");
  const projetoObj = a.projeto_id ? _projetosMapId[a.projeto_id] : null;
  const projeto = a.projeto_id ? (projetoObj ? projetoObj.nome : "(projeto removido)") : "";
  const horaFmt = a.inicio ? new Date(a.inicio).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "";
  const origemTag = a.origem ? `<span class="tag">${escapeHtml(a.origem)}</span>` : "";
  const idAttr = escapeHtml(String(a.id));
  return `
    <div class="lem-item">
      <div class="lem-body">
        <div class="lem-titulo">${escapeHtml(a.descricao || cliente)}</div>
        <div class="r-meta">
          ${horaFmt ? `<span>${escapeHtml(horaFmt)}</span>` : ""}
          <span>${escapeHtml(cliente)}${projeto ? ` · ${escapeHtml(projeto)}` : ""}</span>
          <span>⏱ ${escapeHtml(fmtDuracao(a.duracao_s))}</span>
          ${origemTag}
        </div>
      </div>
      ${_syncIconHtml(a.id, a.sync_habilitado)}
      <button class="btn-icon" title="Editar descrição" data-apont-editar="${idAttr}">✏️</button>
      <button class="btn-icon" title="Excluir" data-apont-excluir="${idAttr}">🗑</button>
    </div>
  `;
}

async function excluirApontamento(id) {
  if (!(await confirmarAcao("Excluir este lançamento?"))) return;
  try {
    await api(`/api/apontamentos/${id}/excluir`, { method: "POST" });
    mostrarToast("Lançamento excluído", "ok");
    await carregarApontamentos();
  } catch (e) {
    mostrarErro(`Erro ao excluir lançamento: ${e.message}`);
  }
}

// Quebra um ISO wall-clock "AAAA-MM-DDTHH:MM[:SS]" em {data, hora} para
// preencher os inputs type=date/time do modal de edição. Tolera valor vazio.
function _splitIso(iso) {
  if (!iso || typeof iso !== "string" || !iso.includes("T")) return { data: "", hora: "" };
  const [d, h] = iso.split("T");
  return { data: d || "", hora: (h || "").slice(0, 5) };
}

let _apontEditId = null;

async function editarApontamento(id) {
  let atual = null;
  try {
    const lista = await api(`/api/apontamentos?mes=${_mesAtual()}`);
    atual = lista.find((a) => String(a.id) === String(id)) || null;
  } catch (_) {
    // segue com campos vazios se não conseguir buscar o valor atual
  }
  if (!atual) { mostrarErro("Lançamento não encontrado."); return; }

  _apontEditId = id;
  $("ae-desc").value = atual.descricao || "";
  const ini = _splitIso(atual.inicio);
  const fim = _splitIso(atual.fim);
  $("ae-data").value = ini.data;
  $("ae-inicio").value = ini.hora;
  $("ae-fim").value = fim.hora;
  $("ae-erro").hidden = true;
  if (_pickerApontEdit) await _pickerApontEdit.selecionarProjeto(atual.projeto_id || null);
  $("apont-edit").hidden = false;
  $("ae-desc").focus();
}

function _fecharEdicaoApontamento() {
  $("apont-edit").hidden = true;
  _apontEditId = null;
}

async function salvarEdicaoApontamento() {
  if (!_apontEditId) return;
  const data = $("ae-data").value;
  const horaIni = $("ae-inicio").value;
  const horaFim = $("ae-fim").value;
  const erroEl = $("ae-erro");
  erroEl.hidden = true;
  if (!data || !horaIni || !horaFim) {
    erroEl.textContent = "Preencha data e horários de início/fim.";
    erroEl.hidden = false;
    return;
  }
  const inicio = _isoLocal(data, horaIni);
  const dataFim = horaFim <= horaIni ? _addDiaYMD(data) : data;
  const fim = _isoLocal(dataFim, horaFim);
  if (new Date(fim).getTime() <= new Date(inicio).getTime()) {
    erroEl.textContent = "O horário de término deve ser depois do início. Dica: fim menor que início = dia seguinte.";
    erroEl.hidden = false;
    return;
  }
  const sel = _pickerApontEdit ? _pickerApontEdit.getSelecionado() : { projeto_id: null, cliente_id: null };
  const btn = $("ae-salvar");
  btn.disabled = true;
  try {
    await api(`/api/apontamentos/${_apontEditId}`, {
      method: "PATCH",
      body: JSON.stringify({
        descricao: $("ae-desc").value.trim(),
        projeto_id: sel.projeto_id === null ? "" : sel.projeto_id,
        inicio,
        fim,
      }),
    });
    _fecharEdicaoApontamento();
    mostrarToast("Lançamento atualizado", "ok");
    await carregarApontamentos();
  } catch (e) {
    erroEl.textContent = `Erro ao editar: ${e.message}`;
    erroEl.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

// ────────────────────────────────────────────────────────────────────
// Horas: mapa global de projetos por id (usado para exibir nomes na lista
// de apontamentos e no seletor de projeto do Timer) — independente da
// tela Clientes.
// ────────────────────────────────────────────────────────────────────
async function carregarProjetosTodos() {
  try {
    const lista = await api("/api/projetos");
    lista.forEach((p) => { _projetosMapId[p.id] = p; });
  } catch (_) {
    // usado só para o mapa de nomes; segue sem ele se falhar
  }
}

// ────────────────────────────────────────────────────────────────────
// Tela Clientes — mestre-detalhe (F4). Coluna esquerda: lista de clientes
// (ativos primeiro) com tempo total acumulado; painel direito: dados do
// cliente selecionado, projetos vinculados e gravações vinculadas.
//
// Tempo total por cliente: em vez de 1 chamada `/api/reunioes?cliente=`
// por cliente (N chamadas só para montar a lista), agregamos client-side
// a partir de 2 listas já buscadas uma vez: `/api/apontamentos` (sem
// filtro de mês — todo o histórico; soma por cliente_id) e a lista de
// reuniões já em cache (soma por nome de cliente, campo `duracao_s`).
// A chamada `/api/reunioes?cliente=nome` com filtro só é usada depois,
// sob demanda, para popular "Gravações vinculadas" do cliente selecionado.
// ────────────────────────────────────────────────────────────────────
let _clienteMdSelecionado = null;
let _clienteMdListaCache = [];

async function _calcularTotaisPorCliente() {
  const totais = {};
  try {
    const apontamentos = await api("/api/apontamentos");
    for (const a of apontamentos) {
      if (!a.cliente_id || !a.duracao_s) continue;
      totais[a.cliente_id] = (totais[a.cliente_id] || 0) + a.duracao_s;
    }
  } catch (_) {
    // segue com o que tiver (ex.: só reuniões) se apontamentos falhar
  }
  try {
    const reunioes = _listaCache.length ? _listaCache : await api("/api/reunioes");
    _listaCache = reunioes;
    for (const r of reunioes) {
      if (!r.cliente) continue;
      const cli = Object.values(_clientesMapId).find((c) => c.nome === r.cliente);
      if (!cli) continue;
      totais[cli.id] = (totais[cli.id] || 0) + (r.duracao_s || 0);
    }
  } catch (_) {
    // idem
  }
  return totais;
}

async function carregarTelaClientes() {
  const render = $("mcli-lista");
  try {
    const lista = await api("/api/clientes?incluir_inativos=true");
    lista.forEach((c) => { _clientesMapId[c.id] = c; });
    const totais = await _calcularTotaisPorCliente();
    _clienteMdListaCache = lista
      .map((c) => ({ ...c, _tempo_s: totais[c.id] || 0 }))
      .sort((a, b) => {
        if (a.ativo !== b.ativo) return a.ativo ? -1 : 1;
        return a.nome.localeCompare(b.nome, "pt-BR");
      });
    renderizarListaClientesMd();
    if (_clienteMdSelecionado && lista.some((c) => c.id === _clienteMdSelecionado)) {
      await selecionarClienteMd(_clienteMdSelecionado);
    }
  } catch (e) {
    render.innerHTML = `<p class="dica">Erro ao carregar clientes: ${escapeHtml(e.message)}</p>`;
  }
}

function renderizarListaClientesMd() {
  const render = $("mcli-lista");
  if (!_clienteMdListaCache.length) {
    render.innerHTML = '<p class="dica">Nenhum cliente cadastrado.</p>';
    return;
  }
  render.innerHTML = _clienteMdListaCache.map((c) => `
    <div class="mcli-item${c.ativo === false ? ' inativo' : ''}${c.id === _clienteMdSelecionado ? ' selecionado' : ''}" data-mcli-id="${escapeHtml(String(c.id))}">
      <div class="avatar-quad">${escapeHtml(_iniciaisCliente(c.nome))}</div>
      <div class="mcli-item-corpo">
        <div class="mcli-item-nome">${escapeHtml(c.nome)}</div>
        <div class="mcli-item-tempo">${escapeHtml(fmtDuracao(c._tempo_s))}</div>
      </div>
      ${_syncIconHtml(c.id, c.sync_habilitado)}
    </div>
  `).join("");
  render.querySelectorAll("[data-mcli-id]").forEach((el) => {
    el.addEventListener("click", () => selecionarClienteMd(el.dataset.mcliId));
  });
  _bindSyncIcons(render, "/api/clientes", carregarTelaClientes);
}

async function selecionarClienteMd(id) {
  _clienteMdSelecionado = id;
  document.querySelectorAll("#mcli-lista .mcli-item").forEach((el) => {
    el.classList.toggle("selecionado", el.dataset.mcliId === id);
  });
  const cacheEntry = _clienteMdListaCache.find((x) => x.id === id);
  const c = cacheEntry || _clientesMapId[id];
  if (!c) return;

  ocultar("mcli-vazio");
  mostrar("mcli-conteudo", "block");

  $("mcli-avatar").textContent = _iniciaisCliente(c.nome);
  $("mcli-nome-view").textContent = c.nome;
  $("mcli-total").textContent = fmtDuracao(cacheEntry ? cacheEntry._tempo_s : 0);
  $("mcli-edit-valor").value = c.valor_hora != null ? String(c.valor_hora) : "";
  $("mcli-edit-ativo").checked = c.ativo !== false;

  ocultar("mcli-form-projeto");
  await Promise.all([
    carregarProjetosClienteMd(id),
    carregarGravacoesClienteMd(c.nome),
  ]);
}

async function criarClienteMd() {
  const nome = $("mcli-nome").value.trim();
  if (!nome) {
    mostrarToast("Informe o nome do cliente", "erro");
    return;
  }
  const valorStr = $("mcli-valor").value.trim().replace(",", ".");
  const valor = valorStr ? parseFloat(valorStr) : undefined;
  const btn = $("btn-mcli-criar");
  btn.disabled = true;
  try {
    const body = { nome };
    if (valor !== undefined && !isNaN(valor)) body.valor_hora = valor;
    const criado = await api("/api/clientes", { method: "POST", body: JSON.stringify(body) });
    $("mcli-nome").value = "";
    $("mcli-valor").value = "";
    mostrarToast("Cliente criado", "ok");
    await atualizarTodosSelectsClientes();
    await carregarTelaClientes();
    await selecionarClienteMd(criado.id);
  } catch (e) {
    mostrarErro(`Erro ao criar cliente: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function salvarValorHoraMd() {
  if (!_clienteMdSelecionado) return;
  const valorStr = $("mcli-edit-valor").value.trim().replace(",", ".");
  const valor = valorStr ? parseFloat(valorStr) : 0;
  if (isNaN(valor)) {
    mostrarToast("Valor inválido", "erro");
    return;
  }
  try {
    await api(`/api/clientes/${_clienteMdSelecionado}`, {
      method: "PATCH",
      body: JSON.stringify({ valor_hora: valor }),
    });
    mostrarToast("Valor/hora atualizado", "ok");
    await carregarTelaClientes();
  } catch (e) {
    mostrarErro(`Erro ao atualizar valor/hora: ${e.message}`);
  }
}

async function toggleAtivoMd() {
  if (!_clienteMdSelecionado) return;
  const ativo = $("mcli-edit-ativo").checked;
  try {
    await api(`/api/clientes/${_clienteMdSelecionado}`, {
      method: "PATCH",
      body: JSON.stringify({ ativo }),
    });
    mostrarToast(ativo ? "Cliente ativado" : "Cliente desativado", "ok");
    await atualizarTodosSelectsClientes();
    await carregarTelaClientes();
  } catch (e) {
    mostrarErro(`Erro ao atualizar cliente: ${e.message}`);
    $("mcli-edit-ativo").checked = !ativo;
  }
}

async function excluirClienteMd() {
  if (!_clienteMdSelecionado) return;
  if (!(await confirmarAcao("Excluir este cliente?"))) return;
  try {
    await api(`/api/clientes/${_clienteMdSelecionado}/excluir`, { method: "POST" });
    mostrarToast("Cliente excluído", "ok");
    _clienteMdSelecionado = null;
    ocultar("mcli-conteudo");
    mostrar("mcli-vazio", "flex");
    await atualizarTodosSelectsClientes();
    await carregarTelaClientes();
  } catch (e) {
    mostrarErro(`Erro ao excluir cliente: ${e.message}`);
  }
}

// Projetos vinculados ao cliente selecionado
async function carregarProjetosClienteMd(clienteId) {
  const render = $("mcli-projetos-lista");
  render.innerHTML = '<p class="dica">Carregando…</p>';
  try {
    const lista = await api(`/api/projetos?cliente_id=${encodeURIComponent(clienteId)}&incluir_inativos=true`);
    lista.forEach((p) => { _projetosMapId[p.id] = p; });
    if (!lista.length) {
      render.innerHTML = '<p class="dica">Nenhum projeto vinculado.</p>';
      return;
    }
    render.innerHTML = lista.map((p) => {
      const inativo = p.ativo === false;
      const idAttr = escapeHtml(String(p.id));
      return `
        <div class="lem-item${inativo ? ' lem-concluido' : ''}">
          <div class="lem-body">
            <div class="lem-titulo">${escapeHtml(p.nome)}</div>
            <div class="r-meta"><span class="tag">${inativo ? "inativo" : "ativo"}</span></div>
          </div>
          ${_syncIconHtml(p.id, p.sync_habilitado)}
          <button class="btn-icon" title="${inativo ? 'Ativar' : 'Desativar'}" data-mproj-toggle="${idAttr}" data-mproj-ativo="${inativo ? '0' : '1'}">${inativo ? '▶' : '⏸'}</button>
          <button class="btn-icon" title="Excluir" data-mproj-excluir="${idAttr}">🗑</button>
        </div>
      `;
    }).join("");
    render.querySelectorAll("[data-mproj-toggle]").forEach((el) => {
      el.addEventListener("click", () => toggleProjetoAtivoMd(el.dataset.mprojToggle, el.dataset.mprojAtivo === "1"));
    });
    render.querySelectorAll("[data-mproj-excluir]").forEach((el) => {
      el.addEventListener("click", () => excluirProjetoMd(el.dataset.mprojExcluir));
    });
    _bindSyncIcons(render, "/api/projetos", () => carregarProjetosClienteMd(clienteId));
  } catch (e) {
    render.innerHTML = `<p class="dica">Erro ao carregar projetos: ${escapeHtml(e.message)}</p>`;
  }
}

function abrirFormNovoProjetoMd() {
  if (!_clienteMdSelecionado) return;
  mostrar("mcli-form-projeto", "flex");
  $("mcli-projeto-nome").value = "";
  $("mcli-projeto-nome").focus();
}

function cancelarFormNovoProjetoMd() {
  ocultar("mcli-form-projeto");
}

async function salvarNovoProjetoMd() {
  if (!_clienteMdSelecionado) return;
  const nome = $("mcli-projeto-nome").value.trim();
  if (!nome) {
    mostrarToast("Informe o nome do projeto", "erro");
    return;
  }
  try {
    await api("/api/projetos", {
      method: "POST",
      body: JSON.stringify({ cliente_id: _clienteMdSelecionado, nome }),
    });
    mostrarToast("Projeto vinculado", "ok");
    ocultar("mcli-form-projeto");
    await carregarProjetosClienteMd(_clienteMdSelecionado);
    await atualizarProjetosDependentes();
  } catch (e) {
    mostrarErro(`Erro ao criar projeto: ${e.message}`);
  }
}

async function toggleProjetoAtivoMd(id, ativoAtual) {
  try {
    await api(`/api/projetos/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ ativo: !ativoAtual }),
    });
    mostrarToast(ativoAtual ? "Projeto desativado" : "Projeto ativado", "ok");
    await carregarProjetosClienteMd(_clienteMdSelecionado);
    await atualizarProjetosDependentes();
  } catch (e) {
    mostrarErro(`Erro ao atualizar projeto: ${e.message}`);
  }
}

async function excluirProjetoMd(id) {
  if (!(await confirmarAcao("Excluir este projeto?"))) return;
  try {
    await api(`/api/projetos/${id}/excluir`, { method: "POST" });
    mostrarToast("Projeto excluído", "ok");
    await carregarProjetosClienteMd(_clienteMdSelecionado);
    await atualizarProjetosDependentes();
  } catch (e) {
    mostrarErro(`Erro ao excluir projeto: ${e.message}`);
  }
}

// Gravações vinculadas ao cliente selecionado (por nome, via /api/reunioes?cliente=)
async function carregarGravacoesClienteMd(nomeCliente) {
  const render = $("mcli-gravacoes-lista");
  render.innerHTML = '<p class="dica">Carregando…</p>';
  try {
    const lista = await api(`/api/reunioes?cliente=${encodeURIComponent(nomeCliente)}`);
    if (!lista.length) {
      render.innerHTML = '<p class="dica">Nenhuma gravação vinculada.</p>';
      return;
    }
    render.innerHTML = lista.map((r) => `
      <div class="painel-reuniao-item" data-mgrav-id="${escapeHtml(r.id)}">
        <div class="r-titulo">${escapeHtml(r.titulo)}</div>
        <div class="r-meta">
          <span>${escapeHtml(r.data)}</span>
          ${r.duracao_fmt ? `<span>⏱ ${escapeHtml(r.duracao_fmt)}</span>` : ""}
          ${r.projeto ? `<span class="tag tem-projeto">📁 ${escapeHtml(r.projeto)}</span>` : ""}
        </div>
      </div>
    `).join("");
    render.querySelectorAll("[data-mgrav-id]").forEach((el) => {
      el.addEventListener("click", () => {
        mostrarTela("gravacoes");
        selecionarReuniao(el.dataset.mgravId);
      });
    });
  } catch (e) {
    render.innerHTML = `<p class="dica">Erro: ${escapeHtml(e.message)}</p>`;
  }
}

// ────────────────────────────────────────────────────────────────────
// Horas: aba Relatório (mesmo relatório da Feature 12 original, adaptado
// ao novo shape com breakdown por origem/projeto)
// ────────────────────────────────────────────────────────────────────
async function popularSelectClientesRelatorio() {
  const nomes = await _listarNomesClientes();
  const sel = $("rel-cliente");
  const atual = sel.value;
  sel.innerHTML = '<option value="">todos os clientes</option>' +
    nomes.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
  sel.value = atual || "";
}

function _relatorioParams() {
  const params = new URLSearchParams();
  const mes = $("rel-mes").value;
  const cliente = $("rel-cliente").value;
  const inicio = $("rel-inicio").value;
  const fim = $("rel-fim").value;
  if (mes) params.set("mes", mes);
  if (cliente) params.set("cliente", cliente);
  if (inicio) params.set("inicio", inicio);
  if (fim) params.set("fim", fim);
  return params;
}

// Texto do período filtrado (intervalo de datas tem prioridade sobre mês),
// usado no cabeçalho exibido/impresso do relatório.
function _relatorioPeriodoTexto(r) {
  if (r.filtro_inicio && r.filtro_fim) {
    const fmt = (d) => new Date(`${d}T00:00`).toLocaleDateString("pt-BR");
    return `Período: ${fmt(r.filtro_inicio)} a ${fmt(r.filtro_fim)}`;
  }
  return `Mês: ${r.mes || ""}`;
}

async function carregarRelatorio() {
  const render = $("rel-render");
  const params = _relatorioParams();
  $("btn-rel-csv").href = `/api/relatorio/csv?${params.toString()}`;
  render.innerHTML = '<p class="dica">Carregando…</p>';
  try {
    const r = await api(`/api/relatorio?${params.toString()}`);
    const cabecalho = $("rel-cabecalho-impressao");
    if (cabecalho) {
      const cliente = r.filtro_cliente ? ` · Cliente: ${escapeHtml(r.filtro_cliente)}` : "";
      cabecalho.innerHTML = `<h3>Relatório de horas</h3><p>${escapeHtml(_relatorioPeriodoTexto(r))}${cliente}</p>`;
    }
    render.innerHTML = renderizarRelatorio(r);
  } catch (e) {
    render.innerHTML = `<p class="dica">Erro ao carregar relatório: ${escapeHtml(e.message)}</p>`;
  }
}

// Exporta o relatório renderizado como PDF via diálogo de impressão nativo
// (Electron/Chromium) — sem libs novas. A folha @media print (style.css)
// esconde tudo exceto #rel-print-area.
function exportarRelatorioPdf() {
  window.print();
}

// Renderiza o relatório agregado. Feito de forma defensiva: além do
// total por cliente, exibe (quando presentes na resposta) o breakdown por
// origem (reuniões x apontamentos manuais) e por projeto, além dos itens
// individuais de cada origem.
function renderizarRelatorio(r) {
  if (!r.grupos || !r.grupos.length) {
    return '<p class="dica">Nenhum registro encontrado no período.</p>';
  }
  const linhas = [];
  for (const g of r.grupos) {
    const valorTxt = g.valor != null ? `R$ ${Number(g.valor).toFixed(2).replace(".", ",")}` : "—";
    const qtdItens = (g.reunioes ? g.reunioes.length : 0) + (g.apontamentos ? g.apontamentos.length : 0);
    linhas.push(`
      <tr class="rel-grupo">
        <td colspan="3">${escapeHtml(g.cliente || "(sem cliente)")} — ${qtdItens} item(ns)</td>
        <td>${fmtDuracao(g.total_s)}</td>
        <td>${valorTxt}</td>
      </tr>
    `);

    // Breakdown por origem (reuniões x apontamentos), se vier no payload
    if (g.reunioes_s != null || g.apontamentos_s != null) {
      const partes = [];
      if (g.reunioes_s) partes.push(`Reuniões: ${fmtDuracao(g.reunioes_s)}`);
      if (g.apontamentos_s) partes.push(`Lançamentos: ${fmtDuracao(g.apontamentos_s)}`);
      if (partes.length) {
        linhas.push(`<tr><td colspan="5" style="color:var(--text-2);font-size:11px;padding-left:16px;">${escapeHtml(partes.join(" · "))}</td></tr>`);
      }
    }

    // Breakdown por projeto, se vier no payload
    if (g.projetos && g.projetos.length) {
      for (const p of g.projetos) {
        const pValorTxt = p.valor != null ? `R$ ${Number(p.valor).toFixed(2).replace(".", ",")}` : "—";
        linhas.push(`
          <tr>
            <td colspan="3" style="padding-left:24px;color:var(--text-2);">📁 ${escapeHtml(p.projeto || "(sem projeto)")}</td>
            <td>${fmtDuracao(p.total_s)}</td>
            <td>${pValorTxt}</td>
          </tr>
        `);
      }
    }

    for (const reu of (g.reunioes || [])) {
      linhas.push(`
        <tr>
          <td>${escapeHtml(reu.data || "")}</td>
          <td colspan="2">🎙️ ${escapeHtml(reu.titulo || "")}</td>
          <td>${fmtDuracao(reu.duracao_s)}</td>
          <td></td>
        </tr>
      `);
    }
    for (const ap of (g.apontamentos || [])) {
      const dataFmt = ap.inicio ? new Date(ap.inicio).toLocaleDateString("pt-BR") : "";
      linhas.push(`
        <tr>
          <td>${escapeHtml(dataFmt)}</td>
          <td colspan="2">⏱ ${escapeHtml(ap.descricao || "(sem descrição)")}</td>
          <td>${fmtDuracao(ap.duracao_s)}</td>
          <td></td>
        </tr>
      `);
    }
  }
  return `
    <table class="rel-tabela">
      <thead>
        <tr><th colspan="3">Cliente / Item</th><th>Duração</th><th>Valor</th></tr>
      </thead>
      <tbody>${linhas.join("")}</tbody>
      <tfoot>
        <tr class="rel-total">
          <td colspan="3">Total geral</td>
          <td>${fmtDuracao(r.total_geral_s)}</td>
          <td></td>
        </tr>
      </tfoot>
    </table>
  `;
}

// ────────────────────────────────────────────────────────────────────
// Lembretes
// ────────────────────────────────────────────────────────────────────
async function popularSelectClientesLembrete() {
  try {
    const nomes = await _listarNomesClientes();
    const sel = $("lem-cliente");
    const atual = sel.value;
    sel.innerHTML = '<option value="">sem cliente</option>' +
      nomes.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    sel.value = atual || "";
  } catch (_) {
    // opcional
  }
}

function _pedirPermissaoNotificacao() {
  if (typeof Notification === "undefined") return;
  if (Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

async function abrirLembretes() {
  _pedirPermissaoNotificacao();
  await popularSelectClientesLembrete();
  // Se aberto via botão da reunião, pré-vincula reunião e cliente
  if (_lembreteReuniaoVinculada) {
    $("lem-reuniao-texto").textContent = `Vinculado à reunião: ${_lembreteReuniaoVinculada.label}`;
    mostrar("lem-reuniao-info", "flex");
    if (_lembreteReuniaoVinculada.cliente) {
      $("lem-cliente").value = _lembreteReuniaoVinculada.cliente;
    }
    $("lem-titulo").focus();
  } else {
    ocultar("lem-reuniao-info");
  }
  $("chk-mostrar-concluidos").checked = _mostrarConcluidosLembretes;
  await carregarLembretesLista();
}

function fecharLembretes() {
  mostrarTela("gravacoes");
  limparFormLembrete();
}

function limparFormLembrete() {
  $("lem-titulo").value = "";
  $("lem-descricao").value = "";
  $("lem-data-hora").value = "";
  $("lem-cliente").value = "";
  $("lem-recorrencia").value = "";
  _lembreteReuniaoVinculada = null;
  ocultar("lem-reuniao-info");
}

function abrirLembreteParaReuniao() {
  if (!reuniaoSelecionada) return;
  const titulo = $("det-titulo").textContent || reuniaoSelecionada;
  _lembreteReuniaoVinculada = {
    ref: reuniaoSelecionada,
    label: titulo,
    cliente: clienteAtualReuniao || "",
  };
  mostrarTela("lembretes");
}

async function carregarLembretesLista() {
  const render = $("lembretes-lista");
  render.innerHTML = '<p class="dica">Carregando…</p>';
  try {
    const lista = await api(`/api/lembretes?incluir_concluidos=${_mostrarConcluidosLembretes ? "true" : "false"}`);
    render.innerHTML = renderizarLembretes(lista);
    render.querySelectorAll("[data-lem-concluir]").forEach((el) => {
      el.addEventListener("change", () => marcarConcluidoLembrete(el.dataset.lemConcluir, el.checked));
    });
    render.querySelectorAll("[data-lem-editar]").forEach((el) => {
      el.addEventListener("click", () => editarLembrete(el.dataset.lemEditar, lista));
    });
    render.querySelectorAll("[data-lem-excluir]").forEach((el) => {
      el.addEventListener("click", () => excluirLembrete(el.dataset.lemExcluir));
    });
    render.querySelectorAll("[data-lem-adiar]").forEach((el) => {
      el.addEventListener("click", () => {
        if (el.dataset.adiarAmanha) adiarLembrete(el.dataset.lemAdiar, { amanha: true });
        else adiarLembrete(el.dataset.lemAdiar, { minutos: parseInt(el.dataset.adiarMin, 10) });
      });
    });
    _bindSyncIcons(render, "/api/lembretes", carregarLembretesLista);
  } catch (e) {
    render.innerHTML = `<p class="dica">Erro ao carregar lembretes: ${escapeHtml(e.message)}</p>`;
  }
}

function _lembreteVencido(l) {
  if (l.concluido || !l.data_hora) return false;
  return new Date(l.data_hora).getTime() < Date.now();
}

function renderizarLembretes(lista) {
  if (!lista.length) return '<p class="dica">Nenhum lembrete.</p>';
  return lista.map((l) => {
    const vencido = _lembreteVencido(l);
    const cls = `lem-item${vencido ? ' lem-vencido' : ''}${l.concluido ? ' lem-concluido' : ''}`;
    const dataFmt = l.data_hora ? new Date(l.data_hora).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
    const tagsExtra = [];
    if (l.cliente) tagsExtra.push(`<span class="tag tem-cliente">👤 ${escapeHtml(l.cliente)}</span>`);
    if (l.reuniao) tagsExtra.push(`<span class="tag tem-trans" title="Vinculado a reunião">🎙️ ${escapeHtml(l.reuniao)}</span>`);
    return `
      <div class="${cls}">
        <label class="check lem-check">
          <input type="checkbox" data-lem-concluir="${escapeHtml(l.id)}" ${l.concluido ? "checked" : ""}>
        </label>
        <div class="lem-body">
          <div class="lem-titulo">${l.recorrencia ? '<span title="Lembrete recorrente">🔁</span> ' : ''}${escapeHtml(l.titulo)}${vencido ? ' <span class="lem-vencido-tag">vencido</span>' : ''}</div>
          ${l.descricao ? `<div class="lem-desc">${escapeHtml(l.descricao)}</div>` : ""}
          <div class="r-meta">
            ${dataFmt ? `<span>${escapeHtml(dataFmt)}</span>` : ""}
            ${tagsExtra.join("")}
          </div>
        </div>
        ${!l.concluido ? `
        <div class="lem-adiar-group">
          <button class="btn-icon" title="Adiar 10 minutos" data-lem-adiar="${escapeHtml(l.id)}" data-adiar-min="10">+10min</button>
          <button class="btn-icon" title="Adiar 1 hora" data-lem-adiar="${escapeHtml(l.id)}" data-adiar-min="60">+1h</button>
          <button class="btn-icon" title="Adiar para amanhã 09:00" data-lem-adiar="${escapeHtml(l.id)}" data-adiar-amanha="1">Amanhã</button>
        </div>` : ""}
        ${_syncIconHtml(l.id, l.sync_habilitado)}
        <button class="btn-icon" title="Editar" data-lem-editar="${escapeHtml(l.id)}">✏️</button>
        <button class="btn-icon" title="Excluir" data-lem-excluir="${escapeHtml(l.id)}">🗑</button>
      </div>
    `;
  }).join("");
}

async function marcarConcluidoLembrete(id, concluido) {
  try {
    await api(`/api/lembretes/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ concluido }),
    });
    await carregarLembretesLista();
    await pollLembretesVencidos();
  } catch (e) {
    mostrarErro(`Erro ao atualizar lembrete: ${e.message}`);
  }
}

// Adiar rápido: {minutos} adia relativo a agora, {amanha:true} adia para
// amanhã às 09:00 (wall-clock local, formato "AAAA-MM-DDTHH:MM").
async function adiarLembrete(id, { minutos, amanha } = {}) {
  try {
    let body;
    if (amanha) {
      const d = new Date();
      d.setDate(d.getDate() + 1);
      const ate = `${_dataYMD(d)}T09:00`;
      body = { ate };
    } else {
      body = { minutos };
    }
    await api(`/api/lembretes/${id}/adiar`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await carregarLembretesLista();
    await pollLembretesVencidos();
  } catch (e) {
    mostrarErro(`Erro ao adiar lembrete: ${e.message}`);
  }
}

async function excluirLembrete(id) {
  if (!(await confirmarAcao("Excluir este lembrete?"))) return;
  try {
    await api(`/api/lembretes/${id}/excluir`, { method: "POST" });
    await carregarLembretesLista();
    await pollLembretesVencidos();
  } catch (e) {
    mostrarErro(`Erro ao excluir lembrete: ${e.message}`);
  }
}

// Popula um <select> com os nomes de clientes (lembrete guarda o nome, não id).
async function _popularSelectNomesClientes(sel, valorAtual) {
  try {
    const nomes = await _listarNomesClientes();
    sel.innerHTML = '<option value="">sem cliente</option>' +
      nomes.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
  } catch (_) {
    sel.innerHTML = '<option value="">sem cliente</option>';
  }
  sel.value = valorAtual || "";
}

let _lemEditId = null;

async function editarLembrete(id, listaCache) {
  let atual = (listaCache || []).find((l) => String(l.id) === String(id)) || null;
  if (!atual) {
    try {
      const lista = await api("/api/lembretes?incluir_concluidos=true");
      atual = lista.find((l) => String(l.id) === String(id)) || null;
    } catch (_) {}
  }
  if (!atual) { mostrarErro("Lembrete não encontrado."); return; }

  _lemEditId = id;
  $("le-titulo").value = atual.titulo || "";
  $("le-descricao").value = atual.descricao || "";
  // data_hora é wall-clock naive "AAAA-MM-DDTHH:MM" — cabe direto no input.
  $("le-data-hora").value = (atual.data_hora || "").slice(0, 16);
  $("le-recorrencia").value = atual.recorrencia || "";
  $("le-erro").hidden = true;
  await _popularSelectNomesClientes($("le-cliente"), atual.cliente || "");
  $("lem-edit").hidden = false;
  $("le-titulo").focus();
}

function _fecharEdicaoLembrete() {
  $("lem-edit").hidden = true;
  _lemEditId = null;
}

async function salvarEdicaoLembrete() {
  if (!_lemEditId) return;
  const titulo = $("le-titulo").value.trim();
  const erroEl = $("le-erro");
  erroEl.hidden = true;
  if (!titulo) {
    erroEl.textContent = "Informe um título.";
    erroEl.hidden = false;
    return;
  }
  const btn = $("le-salvar");
  btn.disabled = true;
  try {
    await api(`/api/lembretes/${_lemEditId}`, {
      method: "PATCH",
      body: JSON.stringify({
        titulo,
        descricao: $("le-descricao").value.trim(),
        data_hora: $("le-data-hora").value || null,
        cliente: $("le-cliente").value || "",
        recorrencia: $("le-recorrencia").value,
      }),
    });
    _fecharEdicaoLembrete();
    mostrarToast("Lembrete atualizado", "ok");
    await carregarLembretesLista();
    await pollLembretesVencidos();
  } catch (e) {
    erroEl.textContent = `Erro ao editar: ${e.message}`;
    erroEl.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

async function criarLembrete() {
  const titulo = $("lem-titulo").value.trim();
  if (!titulo) {
    mostrarToast("Informe um título para o lembrete", "erro");
    return;
  }
  const btn = $("btn-criar-lembrete");
  btn.disabled = true;
  try {
    await api("/api/lembretes", {
      method: "POST",
      body: JSON.stringify({
        titulo,
        descricao: $("lem-descricao").value.trim(),
        data_hora: $("lem-data-hora").value || null,
        cliente: $("lem-cliente").value || "",
        reuniao: _lembreteReuniaoVinculada ? _lembreteReuniaoVinculada.ref : "",
        recorrencia: $("lem-recorrencia").value,
      }),
    });
    limparFormLembrete();
    mostrarToast("Lembrete criado", "ok");
    await carregarLembretesLista();
    await pollLembretesVencidos();
  } catch (e) {
    mostrarErro(`Erro ao criar lembrete: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

// Poll de lembretes: atualiza o badge (contagem de vencidos), a lista
// "Próximos" e o painel de tarefas de hoje. A notificação em si é responsabi-
// lidade do processo main do Electron (agendador central), não do renderer.
async function pollLembretesVencidos() {
  try {
    const lista = await api("/api/lembretes?incluir_concluidos=false");
    const agora = Date.now();
    let nVencidos = 0;
    for (const l of lista) {
      if (!l.data_hora) continue;
      const t = new Date(l.data_hora).getTime();
      if (isNaN(t)) continue;
      if (t - agora < 0) nVencidos++;
    }
    const badge = $("badge-lembretes");
    if (nVencidos > 0) {
      badge.textContent = nVencidos;
      mostrar("badge-lembretes", "inline-block");
    } else {
      ocultar("badge-lembretes");
    }
    _renderLembretesProximos(lista);
  } catch (e) {
    console.error("Erro ao verificar lembretes:", e);
  }
  await carregarTarefasHoje();
}

// Painel direito: lembretes com prazo FUTURO (a partir de amanhã) — os de
// hoje já aparecem em "Tarefas de hoje". Mostra data + hora; clique abre a
// tela de Lembretes.
function _renderLembretesProximos(lista) {
  const cont = $("lembretes-proximos");
  if (!cont) return;
  const agora = Date.now();
  const futuros = lista
    .filter((l) => l.data_hora && !l.concluido && !_dataHoraEhHoje(l.data_hora) &&
      new Date(l.data_hora).getTime() > agora)
    .sort((a, b) => new Date(a.data_hora) - new Date(b.data_hora))
    .slice(0, 6);
  if (!futuros.length) {
    cont.innerHTML = '<p class="dica">Nenhum lembrete futuro.</p>';
    return;
  }
  cont.innerHTML = futuros.map((l) => {
    const quando = new Date(l.data_hora).toLocaleString("pt-BR",
      { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    return `
      <div class="prox-item" data-prox-id="${escapeHtml(l.id)}" title="Abrir lembretes">
        <span class="prox-item-titulo">${escapeHtml(l.titulo)}</span>
        <span class="prox-item-quando">${escapeHtml(quando)}</span>
      </div>`;
  }).join("");
  cont.querySelectorAll("[data-prox-id]").forEach((el) => {
    el.addEventListener("click", () => mostrarTela("lembretes"));
  });
}

// ────────────────────────────────────────────────────────────────────
// Sincronização (Supabase) — configuração dentro do modal de Configurações
// ────────────────────────────────────────────────────────────────────
async function carregarStatusSync() {
  try {
    const st = await api("/api/sync/status");
    _syncModo = st.modo || "tudo";
    const el = $("sync-status-display");
    const partes = [];
    partes.push(st.chave_configurada
      ? '<span class="llm-ok">✓ Chave configurada</span>'
      : '<span class="llm-warn">⚠ Chave não configurada</span>');
    partes.push(st.ativo ? '<span class="llm-ok">Ativo</span>' : '<span class="llm-warn">Inativo (local)</span>');
    partes.push(`<span style="color:var(--text-2)">Modo: ${escapeHtml(st.modo === "selecionados" ? "só marcados" : "tudo")}</span>`);
    partes.push(`<span style="color:var(--text-2)">Reuniões marcadas: ${escapeHtml(String(st.reunioes_marcadas ?? 0))}</span>`);
    const quando = st.ultimo_sync && st.ultimo_sync.quando;
    const quandoFmt = quando ? new Date(quando).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "nunca sincronizado";
    const syncErro = st.ultimo_sync && st.ultimo_sync.resultado && !st.ultimo_sync.resultado.ok
      ? ` — ${st.ultimo_sync.resultado.erro || "erro"}`
      : "";
    partes.push(`<span style="color:var(--text-2)">Último sync: ${escapeHtml(quandoFmt)}${escapeHtml(syncErro)}</span>`);
    el.innerHTML = partes.join(" · ");
  } catch (_) {
    $("sync-status-display").innerHTML = '<span class="llm-warn">⚠ Não foi possível verificar o status</span>';
  }
}

async function salvarChaveSync() {
  const chave = $("cfg-sync-chave").value;
  const btn = $("btn-salvar-chave-sync");
  btn.disabled = true;
  try {
    await api("/api/sync/chave", {
      method: "POST",
      body: JSON.stringify({ chave }),
    });
    $("cfg-sync-chave").value = "";
    mostrarToast(chave.trim() ? "Chave de sincronização salva" : "Chave de sincronização removida", "ok");
    await carregarStatusSync();
  } catch (e) {
    mostrarToast(`Erro ao salvar chave: ${e.message}`, "erro");
  } finally {
    btn.disabled = false;
  }
}

async function testarSync() {
  const btn = $("btn-testar-sync");
  btn.disabled = true;
  const el = $("sync-status-display");
  el.innerHTML = '<span class="llm-warn">Testando…</span>';
  try {
    const r = await api("/api/sync/testar", { method: "POST" });
    el.innerHTML = r.ok
      ? '<span class="llm-ok">✓ Conexão OK</span>'
      : `<span class="llm-warn">⚠ ${escapeHtml(r.erro || "Falha no teste")}</span>`;
  } catch (e) {
    el.innerHTML = `<span class="llm-warn">⚠ ${escapeHtml(e.message)}</span>`;
  } finally {
    btn.disabled = false;
    setTimeout(carregarStatusSync, 2000);
  }
}

async function sincronizarAgora() {
  const btn = $("btn-sync-agora");
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Sincronizando…";
  try {
    const r = await api("/api/sync/agora", { method: "POST" });
    if (r.ok) {
      mostrarToast(`Sincronizado: ${r.enviados} enviado(s), ${r.recebidos} recebido(s), ${r.reunioes_enviadas || 0} reunião(ões) enviada(s)`, "ok");
    } else {
      mostrarErro(`Erro ao sincronizar: ${r.erro || "falha desconhecida"}`);
    }
    await carregarStatusSync();
  } catch (e) {
    mostrarErro(`Erro ao sincronizar: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

// ────────────────────────────────────────────────────────────────────
// F6: ícone de sincronização por item (lembretes, apontamentos, clientes,
// projetos) — reflete/alterna `sync_habilitado` via PATCH. Sempre visível;
// esmaecido quando o modo global é "tudo" (a flag não afeta o push nesse
// caso, mas o estado ainda é mostrado/editável para quando o modo mudar).
// ────────────────────────────────────────────────────────────────────
function _syncIconHtml(id, habilitado) {
  const on = habilitado !== false;
  const dim = _syncModo !== "selecionados" ? " sync-icon-dim" : "";
  const title = on
    ? "Sincroniza com a nuvem — clique para tornar só local"
    : "Só local — clique para sincronizar com a nuvem";
  return `<button type="button" class="btn-icon sync-icon${on ? "" : " sync-off"}${dim}" data-sync-id="${escapeHtml(String(id))}" data-sync-atual="${on ? "1" : "0"}" title="${escapeHtml(title)}">☁</button>`;
}

// Liga os cliques de todos os ícones de sync dentro de `root` (um elemento
// do DOM já renderizado), chamando PATCH `${endpointBase}/{id}` e, em caso
// de sucesso, `onDone()` (tipicamente a função que recarrega a lista).
function _bindSyncIcons(root, endpointBase, onDone) {
  root.querySelectorAll("[data-sync-id]").forEach((el) => {
    el.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const id = el.dataset.syncId;
      const atual = el.dataset.syncAtual === "1";
      el.disabled = true;
      try {
        await api(`${endpointBase}/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ sync_habilitado: !atual }),
        });
        if (onDone) await onDone();
      } catch (e) {
        mostrarErro(`Erro ao atualizar sincronização: ${e.message}`);
        el.disabled = false;
      }
    });
  });
}

async function carregarSyncModo() {
  try {
    const st = await api("/api/sync/status");
    _syncModo = st.modo || "tudo";
  } catch (_) {
    // segue com o padrão "tudo" se a chamada falhar
  }
}

// ────────────────────────────────────────────────────────────────────
// Util
// ────────────────────────────────────────────────────────────────────
function escapeHtml(s) {
  if (!s) return "";
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ────────────────────────────────────────────────────────────────────
// Navegação — shell com 6 telas (Painel, Gravações, Timer, Clientes,
// Lembretes, Config). Cada tela é uma <section class="screen">; a troca
// é feita mostrando/ocultando (mesmo padrão mostrar()/ocultar() já usado
// para os antigos modais) e persistida em localStorage.
// ────────────────────────────────────────────────────────────────────
function mostrarTela(nome) {
  if (!TELAS.includes(nome)) nome = "gravacoes";
  telaAtual = nome;
  localStorage.setItem("sekra_tela", nome);

  TELAS.forEach((t) => {
    if (t !== nome) ocultar(`screen-${t}`);
  });
  mostrar(`screen-${nome}`, "flex");

  document.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.toggle("ativo", b.dataset.screen === nome);
  });

  if (nome === "config") abrirConfig();
  else if (nome === "timer") abrirHoras();
  else if (nome === "clientes") carregarTelaClientes();
  else if (nome === "lembretes") abrirLembretes();
  else if (nome === "painel") carregarPainel();
  else if (nome === "gravacoes") atualizarSugestaoClienteGravacao();

  _garantirIntervalTimer();
}

// ────────────────────────────────────────────────────────────────────
// Tela Painel — KPIs da semana (GET /api/dashboard), reuniões recentes e
// distribuição de tempo por cliente (barras horizontais em CSS puro).
// ────────────────────────────────────────────────────────────────────
// Formata segundos como "18h20" (sem sufixo de minutos) para os KPIs
// grandes do painel — diferente de fmtDuracao(), que usa "1h23m"/"45min".
function _fmtHorasCurto(s) {
  s = s || 0;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h${String(m).padStart(2, "0")}`;
}

// "AAAA-MM-DD" → "DD/MM"
function _fmtDataCurta(iso) {
  if (!iso) return "";
  const partes = iso.split("-");
  if (partes.length !== 3) return iso;
  return `${partes[2]}/${partes[1]}`;
}

async function carregarPainel() {
  const kpiTempo = $("painel-kpi-tempo");
  if (!kpiTempo) return;
  const kpiReunioes = $("painel-kpi-reunioes");
  const kpiSemana = $("painel-kpi-semana");
  const kpiSemana2 = $("painel-kpi-semana2");
  const renderRecentes = $("painel-recentes");
  const renderPorCliente = $("painel-por-cliente");
  renderRecentes.innerHTML = '<p class="dica">Carregando…</p>';
  renderPorCliente.innerHTML = '<p class="dica">Carregando…</p>';
  try {
    const d = await api("/api/dashboard");

    kpiTempo.textContent = _fmtHorasCurto(d.tempo_semana_s);
    kpiReunioes.textContent = String(d.num_reunioes_semana);
    const semanaTxt = `${_fmtDataCurta(d.semana.inicio)} – ${_fmtDataCurta(d.semana.fim)}`;
    kpiSemana.textContent = semanaTxt;
    kpiSemana2.textContent = semanaTxt;

    const recentes = d.reunioes_recentes || [];
    if (!recentes.length) {
      renderRecentes.innerHTML = '<p class="dica">Nenhuma reunião ainda.</p>';
    } else {
      renderRecentes.innerHTML = recentes.map((r) => {
        const id = `${r.data}/${r.slug}`;
        return `
          <div class="painel-reuniao-item" data-id="${escapeHtml(id)}">
            <div class="r-linha">
              ${r.cliente ? `<div class="avatar-quad" title="${escapeHtml(r.cliente)}">${escapeHtml(_iniciaisCliente(r.cliente))}</div>` : ""}
              <div class="r-linha-corpo">
                <div class="r-titulo">${escapeHtml(r.titulo || "")}</div>
                <div class="r-meta">
                  <span>${escapeHtml(r.data || "")}</span>
                  ${r.duracao_s ? `<span>⏱ ${escapeHtml(fmtDuracao(r.duracao_s))}</span>` : ""}
                  ${r.cliente ? `<span class="tag tem-cliente">👤 ${escapeHtml(r.cliente)}</span>` : ""}
                  ${r.projeto ? `<span class="tag tem-projeto">📁 ${escapeHtml(r.projeto)}</span>` : ""}
                </div>
              </div>
            </div>
          </div>
        `;
      }).join("");
      renderRecentes.querySelectorAll("[data-id]").forEach((el) => {
        el.addEventListener("click", () => {
          mostrarTela("gravacoes");
          selecionarReuniao(el.dataset.id);
        });
      });
    }

    const porCliente = d.por_cliente || [];
    if (!porCliente.length) {
      renderPorCliente.innerHTML = '<p class="dica">Sem dados nesta semana.</p>';
    } else {
      const max = Math.max(...porCliente.map((c) => c.tempo_s || 0), 1);
      renderPorCliente.innerHTML = porCliente.map((c) => `
        <div class="painel-bar-row">
          <span class="painel-bar-nome" title="${escapeHtml(c.cliente)}">${escapeHtml(c.cliente)}</span>
          <div class="painel-bar-track"><div class="painel-bar-fill" style="width:${Math.max(4, Math.round(((c.tempo_s || 0) / max) * 100))}%"></div></div>
          <span class="painel-bar-tempo">${escapeHtml(fmtDuracao(c.tempo_s || 0))}</span>
        </div>
      `).join("");
    }
  } catch (e) {
    renderRecentes.innerHTML = `<p class="dica">Erro: ${escapeHtml(e.message)}</p>`;
    renderPorCliente.innerHTML = "";
  }
}

// ────────────────────────────────────────────────────────────────────
// Topbar — descrição + projeto (estilo Toggl), pill de Timer e pill de Gravar
// ────────────────────────────────────────────────────────────────────
// Recarrega os dados do picker de projeto da topbar e, se a tela de
// gravações estiver aberta, atualiza a sugestão/campo de cliente dela —
// chamado após qualquer criação/edição/exclusão de cliente ou projeto.
async function _atualizarClienteTopbar() {
  if (!_pickerTopbar) return;
  try {
    await _pickerTopbar.recarregar();
    if (telaAtual === "gravacoes") atualizarSugestaoClienteGravacao();
  } catch (e) {
    console.error("Erro ao atualizar picker da topbar:", e);
  }
}

function atualizarTopbarTempo() {
  const el = $("topbar-tempo");
  if (!el) return;
  if (ultimoEstadoBackend.gravando) {
    el.textContent = $("cronometro").textContent || "00:00";
    el.className = "mono-time tempo-gravando";
    mostrar("topbar-tempo", "inline");
  } else if (_timerAtivo) {
    el.textContent = fmtCronometro(_elapsedSince(_timerAtivo.inicio));
    el.className = "mono-time tempo-timer";
    mostrar("topbar-tempo", "inline");
  } else {
    ocultar("topbar-tempo");
  }
}

// Timer parado: inicia direto com descrição + projeto da topbar (entrada
// livre permitida se vazios). Timer rodando: continua parando direto.
function toggleTimerGlobal() {
  if (_timerAtivo) {
    pararTimer();
    return;
  }
  iniciarTimerTopbar();
}

// ────────────────────────────────────────────────────────────────────
// Topbar — iniciar timer direto: clique no botão "⏱ Timer" com o timer
// parado inicia imediatamente com a descrição (#topbar-desc) e o projeto
// escolhido no picker (_pickerTopbar) — sem popover intermediário. Reusa
// _iniciarTimerRequisicao() (mesma chamada da tela Cronômetro). O projeto
// é escolhido via pickerProjeto() — o cliente vem embutido nele. Default do
// picker: último projeto usado (localStorage), pré-selecionado no boot.
// ────────────────────────────────────────────────────────────────────
const LS_ULTIMO_PROJETO = "sekra_timer_ultimo_projeto";

async function iniciarTimerTopbar() {
  const sel = _pickerTopbar ? _pickerTopbar.getSelecionado() : { projeto_id: null, cliente_id: null };
  const btn = $("btn-timer-pill");
  btn.disabled = true;
  try {
    await _iniciarTimerRequisicao(sel.cliente_id, sel.projeto_id, $("topbar-desc").value);
    if (sel.projeto_id) localStorage.setItem(LS_ULTIMO_PROJETO, sel.projeto_id);
    $("topbar-desc").value = "";
    mostrarToast("Timer iniciado", "ok");
  } catch (e) {
    mostrarErro(`Erro ao iniciar timer: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function toggleGravarGlobal() {
  if (ultimoEstadoBackend.gravando) {
    await pararGravacao();
  } else {
    mostrarTela("gravacoes");
    await iniciarGravacao();
  }
}

// ────────────────────────────────────────────────────────────────────
// Painel direito "Tarefas de hoje" — lembretes de hoje ou sem data
// ────────────────────────────────────────────────────────────────────
function _mesmoDiaLocal(d, ref) {
  return d.getFullYear() === ref.getFullYear() &&
    d.getMonth() === ref.getMonth() &&
    d.getDate() === ref.getDate();
}

function _dataHoraEhHoje(dataHoraStr) {
  if (!dataHoraStr) return true; // sem data conta como tarefa de hoje
  const d = new Date(dataHoraStr);
  if (isNaN(d.getTime())) return true;
  return _mesmoDiaLocal(d, new Date());
}

// Tarefa concluída HOJE (para mantê-la visível no painel só até virar o dia).
// Usa `atualizado_em` (UTC ISO) — para um lembrete concluído, a última
// atualização é normalmente a própria conclusão. Nível 1-2: proxy suficiente.
function _concluidoHoje(l) {
  if (!l.concluido || !l.atualizado_em) return false;
  const d = new Date(l.atualizado_em);
  if (isNaN(d.getTime())) return false;
  return _mesmoDiaLocal(d, new Date());
}

// Ordena: vencidos primeiro, depois por hora (data_hora crescente), sem
// data por último. Não muta a lista recebida.
function _ordenarTarefasHoje(lista) {
  return [...lista].sort((a, b) => {
    // Concluídas sempre no fim.
    if (!!a.concluido !== !!b.concluido) return a.concluido ? 1 : -1;
    const aVenc = _lembreteVencido(a);
    const bVenc = _lembreteVencido(b);
    if (aVenc !== bVenc) return aVenc ? -1 : 1;
    if (!a.data_hora && !b.data_hora) return 0;
    if (!a.data_hora) return 1;
    if (!b.data_hora) return -1;
    return new Date(a.data_hora) - new Date(b.data_hora);
  });
}

// Segura re-renders concorrentes (polling 60s / criação rápida) enquanto a
// animação de conclusão de ~900ms está em curso.
let _tarefasHoldRender = false;

async function carregarTarefasHoje() {
  const render = $("tarefas-lista");
  const titulo = $("tarefas-titulo");
  if (!render || _tarefasHoldRender) return;
  try {
    // Inclui concluídos para manter na lista os que foram finalizados HOJE
    // (somem só no dia seguinte). Filtro: do dia (ou sem data) E, se concluído,
    // concluído hoje.
    const lista = await api("/api/lembretes?incluir_concluidos=true");
    const hoje = _ordenarTarefasHoje(lista.filter((l) =>
      _dataHoraEhHoje(l.data_hora) && (!l.concluido || _concluidoHoje(l))
    ));
    const pendentes = hoje.filter((l) => !l.concluido).length;
    if (titulo) titulo.textContent = `Tarefas de hoje${pendentes ? ` · ${pendentes}` : ""}`;
    if (!hoje.length) {
      render.innerHTML = '<p class="dica">Nada por hoje.</p>';
      return;
    }
    render.innerHTML = hoje.map((l) => {
      const vencido = !l.concluido && _lembreteVencido(l);
      const horaFmt = l.data_hora
        ? new Date(l.data_hora).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })
        : "";
      const cls = `tarefa-item${vencido ? ' tarefa-vencida' : ''}${l.concluido ? ' tarefa-concluida' : ''}`;
      const metaTxt = vencido ? "vencido" : horaFmt;
      return `
        <div class="${cls}" data-tarefa-id="${escapeHtml(l.id)}">
          <input type="checkbox" data-tarefa-concluir="${escapeHtml(l.id)}" ${l.concluido ? "checked" : ""}>
          <span class="tarefa-item-corpo" data-tarefa-expandir>
            <span class="tarefa-item-titulo">${escapeHtml(l.titulo)}</span>
            ${metaTxt ? `<span class="tarefa-item-hora">${escapeHtml(metaTxt)}</span>` : ""}
            ${l.descricao ? `<span class="tarefa-item-desc">${escapeHtml(l.descricao)}</span>` : ""}
            ${l.concluido ? "" : `
            <span class="tarefa-item-adiar">
              <button type="button" class="btn-icon btn-adiar" title="Adiar 10 minutos" data-adiar-min="10">+10min</button>
              <button type="button" class="btn-icon btn-adiar" title="Adiar 1 hora" data-adiar-min="60">+1h</button>
              <button type="button" class="btn-icon btn-adiar" title="Adiar para amanhã 09:00" data-adiar-amanha>Amanhã</button>
            </span>`}
          </span>
        </div>
      `;
    }).join("");
    render.querySelectorAll("[data-tarefa-concluir]").forEach((el) => {
      el.addEventListener("change", () => _concluirTarefaHojeComFeedback(el));
    });
    // Clique no corpo expande/recolhe o texto completo (não mexe no checkbox).
    render.querySelectorAll("[data-tarefa-expandir]").forEach((el) => {
      el.addEventListener("click", () => el.closest(".tarefa-item").classList.toggle("expandido"));
    });
    // Snooze (adiar) — stopPropagation para não recolher o item ao clicar.
    render.querySelectorAll(".btn-adiar").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = el.closest(".tarefa-item").dataset.tarefaId;
        if (el.hasAttribute("data-adiar-amanha")) adiarLembrete(id, { amanha: true });
        else adiarLembrete(id, { minutos: parseInt(el.dataset.adiarMin, 10) });
      });
    });
  } catch (e) {
    render.innerHTML = `<p class="dica">Erro: ${escapeHtml(e.message)}</p>`;
  }
}

// Feedback visual (check + risco) antes de o item sumir do painel: marca
// concluído no backend imediatamente, mas só re-renderiza a lista (fazendo
// o item desaparecer) depois de ~900ms.
async function _concluirTarefaHojeComFeedback(checkboxEl) {
  const id = checkboxEl.dataset.tarefaConcluir;
  const concluido = checkboxEl.checked;
  const item = checkboxEl.closest(".tarefa-item");
  checkboxEl.disabled = true;
  if (item) item.classList.toggle("tarefa-concluida", concluido);
  try {
    await api(`/api/lembretes/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ concluido }),
    });
  } catch (e) {
    mostrarErro(`Erro ao atualizar tarefa: ${e.message}`);
    checkboxEl.checked = !concluido;
    checkboxEl.disabled = false;
    if (item) item.classList.toggle("tarefa-concluida", !concluido);
    return;
  }
  // Concluída não some mais na hora — fica na lista (riscada) até o dia
  // seguinte. Segura o poll por um instante e re-renderiza para reordenar.
  _tarefasHoldRender = true;
  setTimeout(async () => {
    _tarefasHoldRender = false;
    await carregarTarefasHoje();
    await pollLembretesVencidos();
  }, 400);
}

// Ajusta a altura de um textarea ao conteúdo (o teto vem do max-height no CSS).
function _autoGrow(el) {
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

async function criarTarefaRapida() {
  const inp = $("tarefa-rapida-input");
  const titulo = inp.value.trim();
  if (!titulo) return;
  try {
    await api("/api/lembretes", { method: "POST", body: JSON.stringify({ titulo }) });
    inp.value = "";
    _autoGrow(inp);
    await carregarTarefasHoje();
    await pollLembretesVencidos();
  } catch (e) {
    mostrarErro(`Erro ao criar tarefa: ${e.message}`);
  }
}

// ────────────────────────────────────────────────────────────────────
// Init — event listeners
// ────────────────────────────────────────────────────────────────────
$("btn-iniciar").addEventListener("click", iniciarGravacao);
$("btn-parar").addEventListener("click", pararGravacao);
$("btn-reprocessar").addEventListener("click", reprocessar);
$("btn-excluir").addEventListener("click", pedirConfirmacaoExcluir);
$("btn-copiar-trans").addEventListener("click", copiarTranscricao);
$("btn-exportar").addEventListener("click", exportarReuniao);

// Detecção de reunião: badge no topbar aciona o botão de gravar existente
$("badge-deteccao").addEventListener("click", () => {
  mostrarTela("gravacoes");
  $("btn-iniciar").click();
});

// Feature 5: editar título
$("btn-editar-titulo").addEventListener("click", iniciarEdicaoTitulo);
$("btn-salvar-titulo").addEventListener("click", salvarTitulo);
$("btn-cancelar-titulo").addEventListener("click", cancelarEdicaoTitulo);
$("input-titulo").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); salvarTitulo(); }
  if (e.key === "Escape") cancelarEdicaoTitulo();
});

// Feature 9: configurações
$("btn-salvar-config").addEventListener("click", salvarConfig);
$("btn-salvar-chave").addEventListener("click", salvarChaveLLM);
$("btn-testar-chave").addEventListener("click", testarChaveLLM);
$("link-config-clientes").addEventListener("click", (e) => {
  e.preventDefault();
  mostrarTela("clientes");
});

// Feature 12: editar cliente
$("det-cliente").addEventListener("click", iniciarEdicaoCliente);
$("input-cliente").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); salvarCliente(); }
  if (e.key === "Escape") cancelarEdicaoCliente();
});
$("input-cliente").addEventListener("blur", salvarCliente);

// F1: editar projeto (mesmo padrão do cliente)
$("det-projeto").addEventListener("click", iniciarEdicaoProjeto);
$("input-projeto").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); salvarProjeto(); }
  if (e.key === "Escape") cancelarEdicaoProjeto();
});
$("input-projeto").addEventListener("blur", salvarProjeto);

// F6: toggle de sincronização da reunião selecionada
$("det-sync-toggle").addEventListener("change", toggleSyncReuniao);

// F1: sugestão de cliente para a gravação + datalist de projetos dependente
$("btn-aceitar-sugestao").addEventListener("click", aceitarSugestaoCliente);
$("cliente").addEventListener("input", () => {
  if ($("cliente").value.trim()) ocultar("grav-sugestao-cliente");
});
$("cliente").addEventListener("change", atualizarListaProjetosGravacao);
$("cliente").addEventListener("blur", atualizarListaProjetosGravacao);

// Horas: telas Timer & Clientes (timer, lançamentos, clientes, projetos, relatório)
$("badge-timer").addEventListener("click", () => mostrarTela("timer"));
document.querySelectorAll(".tab-horas").forEach((b) => {
  b.addEventListener("click", () => ativarTabHoras(b.dataset.tabHoras, b.closest(".screen")));
});
$("btn-timer-toggle").addEventListener("click", acaoTimerPrincipal);
_pickerHoras = pickerProjeto($("horas-picker"), { placeholder: "Projeto (opcional)" });
_pickerApontEdit = pickerProjeto($("ae-picker"), { placeholder: "Projeto (opcional)" });
$("ae-salvar").addEventListener("click", salvarEdicaoApontamento);
$("ae-cancelar").addEventListener("click", _fecharEdicaoApontamento);
$("apont-edit").addEventListener("click", (e) => {
  if (e.target.id === "apont-edit") _fecharEdicaoApontamento();
});
$("ae-desc").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); salvarEdicaoApontamento(); }
});
$("le-salvar").addEventListener("click", salvarEdicaoLembrete);
$("le-cancelar").addEventListener("click", _fecharEdicaoLembrete);
$("lem-edit").addEventListener("click", (e) => {
  if (e.target.id === "lem-edit") _fecharEdicaoLembrete();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("apont-edit").hidden) _fecharEdicaoApontamento();
  else if (!$("lem-edit").hidden) _fecharEdicaoLembrete();
});
$("btn-retro-hoje").addEventListener("click", () => preencherDataRetro(0));
$("btn-retro-ontem").addEventListener("click", () => preencherDataRetro(1));
["lanc-data", "lanc-hora-inicio", "lanc-hora-fim"].forEach((id) => {
  $(id).addEventListener("input", _refletirEstadoBotaoTimer);
});
$("rel-mes").addEventListener("change", carregarRelatorio);
$("rel-cliente").addEventListener("change", carregarRelatorio);
$("rel-inicio").addEventListener("change", carregarRelatorio);
$("rel-fim").addEventListener("change", carregarRelatorio);
$("btn-rel-pdf").addEventListener("click", exportarRelatorioPdf);

// Tela Clientes (mestre-detalhe, F4)
$("btn-mcli-criar").addEventListener("click", criarClienteMd);
$("mcli-nome").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); criarClienteMd(); }
});
$("btn-mcli-salvar-valor").addEventListener("click", salvarValorHoraMd);
$("mcli-edit-ativo").addEventListener("change", toggleAtivoMd);
$("btn-mcli-excluir").addEventListener("click", excluirClienteMd);
$("btn-mcli-novo-projeto").addEventListener("click", abrirFormNovoProjetoMd);
$("btn-mcli-salvar-projeto").addEventListener("click", salvarNovoProjetoMd);
$("btn-mcli-cancelar-projeto").addEventListener("click", cancelarFormNovoProjetoMd);
$("mcli-projeto-nome").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); salvarNovoProjetoMd(); }
});

document.querySelectorAll(".tab").forEach((b) => {
  b.addEventListener("click", () => ativarTab(b.dataset.tab));
});

// Toggle painel de gravação
$("btn-toggle-painel").addEventListener("click", () => {
  const painel = $("painel-gravar");
  const btn = $("btn-toggle-painel");
  const collapsed = painel.classList.toggle("collapsed");
  btn.textContent = collapsed ? "▼" : "▲";
  btn.title = collapsed ? "Mostrar painel de gravação" : "Ocultar painel de gravação";
});

// Feature 10: atalho de teclado (Espaço = iniciar/parar quando fora de input)
document.addEventListener("keydown", (e) => {
  if (e.key !== " ") return;
  const tag = e.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  e.preventDefault();
  if (ultimoEstadoBackend.gravando) {
    pararGravacao();
  } else {
    iniciarGravacao();
  }
});

// Feature 2: inicializa filtro
inicializarFiltro();

// Feature 12: popula o datalist de clientes
carregarClientesDatalist();

// Lembretes: tela e ações
$("btn-criar-lembrete").addEventListener("click", criarLembrete);
$("lem-titulo").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); criarLembrete(); }
});
$("chk-mostrar-concluidos").addEventListener("change", (e) => {
  _mostrarConcluidosLembretes = e.target.checked;
  carregarLembretesLista();
});
$("lem-reuniao-clear").addEventListener("click", () => {
  _lembreteReuniaoVinculada = null;
  ocultar("lem-reuniao-info");
});
$("btn-lembrete-reuniao").addEventListener("click", abrirLembreteParaReuniao);

// Sincronização (Supabase)
$("btn-salvar-chave-sync").addEventListener("click", salvarChaveSync);
$("btn-testar-sync").addEventListener("click", testarSync);
$("btn-sync-agora").addEventListener("click", sincronizarAgora);

// Navegação: itens da sidebar
document.querySelectorAll(".nav-item").forEach((b) => {
  b.addEventListener("click", () => {
    if (b.dataset.screen === "lembretes") _lembreteReuniaoVinculada = null;
    mostrarTela(b.dataset.screen);
  });
});

// Topbar: descrição + picker de projeto (início direto do timer), pill de
// Timer, pill de Gravar. O cliente vem embutido no projeto escolhido via
// pickerProjeto(); a seleção é persistida como último projeto usado.
_pickerTopbar = pickerProjeto($("topbar-picker"), {
  placeholder: "Projeto (opcional)",
  onChange: (sel) => {
    if (sel.projeto_id) localStorage.setItem(LS_ULTIMO_PROJETO, sel.projeto_id);
    if (telaAtual === "gravacoes") atualizarSugestaoClienteGravacao();
  },
});
$("btn-timer-pill").addEventListener("click", toggleTimerGlobal);
$("btn-gravar-pill").addEventListener("click", toggleGravarGlobal);
$("topbar-desc").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !_timerAtivo) { e.preventDefault(); iniciarTimerTopbar(); }
});

// Painel direito: tarefas de hoje (lembretes)
$("btn-tarefa-rapida").addEventListener("click", criarTarefaRapida);
$("tarefa-rapida-input").addEventListener("keydown", (e) => {
  // Enter envia; Shift+Enter quebra linha (textarea multilinha).
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); criarTarefaRapida(); }
});
// Auto-crescimento: acompanha o conteúdo até um teto (CSS max-height).
$("tarefa-rapida-input").addEventListener("input", (e) => _autoGrow(e.target));
$("tarefa-rapida-input").addEventListener("focus", (e) => _autoGrow(e.target));
$("tarefas-ver-todos").addEventListener("click", (e) => {
  e.preventDefault();
  mostrarTela("lembretes");
});

// Topbar: carrega os dados do picker e pré-seleciona o último projeto
// usado (localStorage), depois sincroniza a sugestão de cliente da
// gravação se a tela inicial for "gravacoes".
async function _bootPickerTopbar() {
  if (!_pickerTopbar) return;
  await _pickerTopbar.recarregar();
  const ultimoId = localStorage.getItem(LS_ULTIMO_PROJETO) || "";
  if (ultimoId) await _pickerTopbar.selecionarProjeto(ultimoId);
  if (telaAtual === "gravacoes") atualizarSugestaoClienteGravacao();
}

carregarLista();
atualizarStatus();
pollLembretesVencidos();
_bootPickerTopbar();
carregarSyncModo().then(carregarTarefasHoje);
// Horas: verifica se já existe um timer rodando (ex: aberto em outra sessão)
// para exibir o indicador no header mesmo com o modal fechado.
carregarTimerStatus();
// Sincroniza a UI da tela ativa (persistida) com o shell: mostra a seção
// certa, marca o item ativo na sidebar e dispara o loader correspondente.
mostrarTela(telaAtual);
setInterval(atualizarStatus, 1000);
setInterval(pollLembretesVencidos, 60000);
// Revalida o status do timer periodicamente (cobre início/parada por fora desta aba)
setInterval(() => { if (telaAtual !== "timer") carregarTimerStatus(); }, 30000);
setInterval(() => {
  // Recarrega lista periodicamente caso arquivos mudem por fora
  if (!ultimoEstadoBackend.gravando && !emModoBusca) carregarLista();
}, 10000);
