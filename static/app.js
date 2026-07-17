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
let _clientesCache = [];

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

    // Mensagem de status
    $("msg-status").textContent = s.msg || "";

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

  // Feature 12: reset do cliente até o meta carregar
  clienteAtualReuniao = "";
  $("det-cliente").textContent = "Cliente: —";
  cancelarEdicaoCliente();

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
    // Feature 12: preenche cliente a partir do meta
    clienteAtualReuniao = meta.cliente || "";
    $("det-cliente").textContent = clienteAtualReuniao ? `Cliente: ${clienteAtualReuniao}` : "Cliente: —";
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
        // Feature 12: cliente para faturamento
        cliente: $("cliente").value.trim(),
      }),
    });
    await atualizarStatus();
  } catch (e) {
    // Feature 1: substitui alert()
    mostrarErro(`Erro ao iniciar: ${e.message}`);
  }
}

async function pararGravacao() {
  try {
    await api("/api/gravar/parar", { method: "POST" });
    await atualizarStatus();
  } catch (e) {
    // Feature 1: substitui alert()
    mostrarErro(`Erro ao parar: ${e.message}`);
  }
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
  inp.style.cssText = "font-size:13px;padding:2px 6px;margin-left:8px;width:140px;background:var(--bg);border:1px solid var(--accent);color:var(--text);border-radius:4px;";

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
  mostrar("overlay-config", "flex");
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
    // Feature 12: clientes e valores/hora
    $("cfg-clientes").value = (cfg.clientes || []).join(", ");
    $("cfg-valores-hora").value = Object.entries(cfg.valores_hora || {})
      .map(([k, v]) => `${k}=${v}`).join("\n");
    if (cfg.llm) {
      $("cfg-llm-provider").value = cfg.llm.provider || "none";
      $("cfg-llm-modelo").value = cfg.llm.modelo || "";
    }
    const tr = cfg.transcricao || {};
    $("cfg-transc-provider").value = tr.provider || "local";
    $("cfg-transc-modelo").value = tr.modelo || "";
    // Campo de chave nunca é pré-preenchido; só limpamos o input
    $("cfg-llm-chave").value = "";
  } catch (e) {
    mostrarErro(`Erro ao carregar configurações: ${e.message}`);
  }
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
  ocultar("overlay-config");
}

async function salvarConfig() {
  // Feature 12: parseia clientes (separados por vírgula)
  const clientes = $("cfg-clientes").value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  // Feature 12: parseia valores/hora ("nome=valor", um por linha)
  const valoresHora = {};
  $("cfg-valores-hora").value.split("\n").forEach((linha) => {
    const l = linha.trim();
    if (!l) return;
    const i = l.indexOf("=");
    if (i === -1) return;
    const nome = l.slice(0, i).trim();
    const valor = parseFloat(l.slice(i + 1).trim().replace(",", "."));
    if (!nome || isNaN(valor)) return;
    valoresHora[nome] = valor;
  });

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
    clientes,
    valores_hora: valoresHora,
    llm: {
      provider: $("cfg-llm-provider").value,
      modelo: $("cfg-llm-modelo").value.trim(),
    },
    transcricao: {
      provider: $("cfg-transc-provider").value,
      modelo: $("cfg-transc-modelo").value.trim(),
    },
  };
  const btn = $("btn-salvar-config");
  btn.disabled = true;
  try {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify(patch),
    });
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
// Feature 12: Clientes (datalist) e Relatório de horas
// ────────────────────────────────────────────────────────────────────
async function carregarClientesDatalist() {
  try {
    const cfg = await api("/api/config");
    _clientesCache = cfg.clientes || [];
    $("lista-clientes").innerHTML = _clientesCache
      .map((c) => `<option value="${escapeHtml(c)}"></option>`).join("");
  } catch (_) {
    // datalist é opcional, ignora falha
  }
}

async function abrirRelatorio() {
  mostrar("overlay-relatorio", "flex");
  if (!$("rel-mes").value) {
    const hoje = new Date();
    $("rel-mes").value = `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, "0")}`;
  }
  await popularSelectClientesRelatorio();
  await carregarRelatorio();
}

function fecharRelatorio() {
  ocultar("overlay-relatorio");
}

async function popularSelectClientesRelatorio() {
  try {
    const cfg = await api("/api/config");
    const sel = $("rel-cliente");
    const atual = sel.value;
    sel.innerHTML = '<option value="">todos os clientes</option>' +
      (cfg.clientes || []).map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    sel.value = atual || "";
  } catch (_) {
    // opcional
  }
}

function _relatorioParams() {
  const params = new URLSearchParams();
  const mes = $("rel-mes").value;
  const cliente = $("rel-cliente").value;
  if (mes) params.set("mes", mes);
  if (cliente) params.set("cliente", cliente);
  return params;
}

async function carregarRelatorio() {
  const render = $("rel-render");
  const params = _relatorioParams();
  $("btn-rel-csv").href = `/api/relatorio/csv?${params.toString()}`;
  render.innerHTML = '<p class="dica">Carregando…</p>';
  try {
    const r = await api(`/api/relatorio?${params.toString()}`);
    render.innerHTML = renderizarRelatorio(r);
  } catch (e) {
    render.innerHTML = `<p class="dica">Erro ao carregar relatório: ${escapeHtml(e.message)}</p>`;
  }
}

function renderizarRelatorio(r) {
  if (!r.grupos || !r.grupos.length) {
    return '<p class="dica">Nenhuma reunião encontrada no período.</p>';
  }
  const linhas = [];
  for (const g of r.grupos) {
    const valorTxt = g.valor != null ? `R$ ${g.valor.toFixed(2).replace(".", ",")}` : "—";
    linhas.push(`
      <tr class="rel-grupo">
        <td colspan="3">${escapeHtml(g.cliente || "(sem cliente)")} — ${g.reunioes.length} reunião(ões)</td>
        <td>${fmtDuracao(g.total_s)}</td>
        <td>${valorTxt}</td>
      </tr>
    `);
    for (const reu of g.reunioes) {
      linhas.push(`
        <tr>
          <td>${escapeHtml(reu.data)}</td>
          <td colspan="2">${escapeHtml(reu.titulo)}</td>
          <td>${fmtDuracao(reu.duracao_s)}</td>
          <td></td>
        </tr>
      `);
    }
  }
  return `
    <table class="rel-tabela">
      <thead>
        <tr><th colspan="3">Cliente / Título</th><th>Duração</th><th>Valor</th></tr>
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
// Init — event listeners
// ────────────────────────────────────────────────────────────────────
$("btn-iniciar").addEventListener("click", iniciarGravacao);
$("btn-parar").addEventListener("click", pararGravacao);
$("btn-reprocessar").addEventListener("click", reprocessar);
$("btn-excluir").addEventListener("click", pedirConfirmacaoExcluir);
$("btn-copiar-trans").addEventListener("click", copiarTranscricao);
$("btn-exportar").addEventListener("click", exportarReuniao);

// Detecção de reunião: badge no header aciona o botão de gravar existente
$("badge-deteccao").addEventListener("click", () => $("btn-iniciar").click());

// Feature 5: editar título
$("btn-editar-titulo").addEventListener("click", iniciarEdicaoTitulo);
$("btn-salvar-titulo").addEventListener("click", salvarTitulo);
$("btn-cancelar-titulo").addEventListener("click", cancelarEdicaoTitulo);
$("input-titulo").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); salvarTitulo(); }
  if (e.key === "Escape") cancelarEdicaoTitulo();
});

// Feature 9: configurações
$("btn-config").addEventListener("click", abrirConfig);
$("btn-fechar-config").addEventListener("click", fecharConfig);
$("btn-fechar-config2").addEventListener("click", fecharConfig);
$("btn-salvar-config").addEventListener("click", salvarConfig);
$("btn-salvar-chave").addEventListener("click", salvarChaveLLM);
$("btn-testar-chave").addEventListener("click", testarChaveLLM);
// Fechar ao clicar no overlay
$("overlay-config").addEventListener("click", (e) => {
  if (e.target === $("overlay-config")) fecharConfig();
});

// Feature 12: editar cliente
$("det-cliente").addEventListener("click", iniciarEdicaoCliente);
$("input-cliente").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); salvarCliente(); }
  if (e.key === "Escape") cancelarEdicaoCliente();
});
$("input-cliente").addEventListener("blur", salvarCliente);

// Feature 12: relatório de horas
$("btn-relatorio").addEventListener("click", abrirRelatorio);
$("btn-fechar-relatorio").addEventListener("click", fecharRelatorio);
$("btn-fechar-relatorio2").addEventListener("click", fecharRelatorio);
$("rel-mes").addEventListener("change", carregarRelatorio);
$("rel-cliente").addEventListener("change", carregarRelatorio);
$("overlay-relatorio").addEventListener("click", (e) => {
  if (e.target === $("overlay-relatorio")) fecharRelatorio();
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

carregarLista();
atualizarStatus();
setInterval(atualizarStatus, 1000);
setInterval(() => {
  // Recarrega lista periodicamente caso arquivos mudem por fora
  if (!ultimoEstadoBackend.gravando && !emModoBusca) carregarLista();
}, 10000);
