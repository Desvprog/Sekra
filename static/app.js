// ────────────────────────────────────────────────────────────────────
// Estado local da UI
// ────────────────────────────────────────────────────────────────────
let reuniaoSelecionada = null;
let ultimoEstadoBackend = { gravando: false, processando: false };

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

    // Badge no header
    const badge = $("status-badge");
    if (s.gravando) {
      badge.textContent = "● gravando";
      badge.className = "status-recording";
    } else if (s.processando) {
      badge.textContent = "processando";
      badge.className = "status-processing";
    } else if (s.erro) {
      badge.textContent = "erro";
      badge.className = "status-error";
    } else {
      badge.textContent = "ocioso";
      badge.className = "status-idle";
    }

    // Botões e cronômetro
    s.gravando ? ocultar("btn-iniciar") : mostrar("btn-iniciar", "inline-block");
    s.gravando ? mostrar("btn-parar", "inline-block") : ocultar("btn-parar");
    $("btn-iniciar").disabled = s.processando;
    s.gravando ? mostrar("cronometro", "inline") : ocultar("cronometro");
    $("cronometro").textContent = fmtCronometro(s.duracao_s);

    // Mensagem de status
    $("msg-status").textContent = s.msg || "";

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
async function carregarLista() {
  const ul = $("ul-reunioes");
  try {
    const lista = await api("/api/reunioes");
    if (!lista.length) {
      ul.innerHTML = '<li class="vazio">nenhuma reunião ainda</li>';
      return;
    }
    ul.innerHTML = lista.map((r) => `
      <li data-id="${r.id}" class="${r.id === reuniaoSelecionada ? 'selecionada' : ''}${r.audio_incompleto ? ' incompleta' : ''}">
        <div class="r-titulo">${r.audio_incompleto ? '⚠️ ' : ''}${escapeHtml(r.titulo)}</div>
        <div class="r-meta">
          <span>${r.data} ${r.hora}</span>
          <span>${r.tamanho_mb} MB</span>
          ${r.audio_incompleto ? '<span class="tag audio-parcial">gravação incompleta</span>' : ''}
          ${r.tem_transcricao ? '<span class="tag tem-trans">📝 trans</span>' : ''}
          ${r.tem_hotwords ? '<span class="tag tem-hw">🔍 hw</span>' : ''}
        </div>
      </li>
    `).join("");
    ul.querySelectorAll("li[data-id]").forEach((li) => {
      li.addEventListener("click", () => selecionarReuniao(li.dataset.id));
    });
  } catch (e) {
    ul.innerHTML = `<li class="vazio">erro: ${e.message}</li>`;
  }
}

// ────────────────────────────────────────────────────────────────────
// Seleção e detalhes
// ────────────────────────────────────────────────────────────────────
async function selecionarReuniao(id) {
  reuniaoSelecionada = id;
  document.querySelectorAll("#ul-reunioes li").forEach((li) => {
    li.classList.toggle("selecionada", li.dataset.id === id);
  });

  ocultar("placeholder");
  mostrar("conteudo", "block");
  cancelarConfirmacaoExcluir();

  const [data, slug] = id.split("/");
  const partes = slug.split("-");
  const hora = `${partes[0]}:${partes[1]}`;
  const titulo = partes.slice(2).join("-");

  $("det-titulo").textContent = titulo;
  $("det-meta").textContent = `${data} às ${hora}`;
  $("player").src = `/api/reunioes/${data}/${slug}/audio`;

  // Carrega transcrição
  try {
    const t = await api(`/api/reunioes/${data}/${slug}/transcricao`);
    $("transcricao-render").innerHTML = renderizarTranscricao(t.texto);
  } catch (e) {
    $("transcricao-render").innerHTML = '<p class="dica">Sem transcrição. Use "Reprocessar".</p>';
  }

  // Carrega hotwords
  try {
    const h = await api(`/api/reunioes/${data}/${slug}/hotwords`);
    $("hotwords-render").innerHTML = renderizarHotwords(h.texto);
  } catch (e) {
    $("hotwords-render").innerHTML = '<p class="dica">Sem hotwords. Use "Reprocessar" com hotwords definidas.</p>';
  }

  // Reset da aba
  ativarTab("transcricao");
}

// ────────────────────────────────────────────────────────────────────
// Renderização da transcrição (formato do reuniao.py)
// ────────────────────────────────────────────────────────────────────
function renderizarTranscricao(texto) {
  if (!texto) return '<p class="dica">Vazio</p>';
  const linhas = texto.split("\n");
  const out = [];
  for (const linha of linhas) {
    if (linha.startsWith("# ")) continue;
    if (linha.startsWith("**") && linha.endsWith("**")) {
      const speaker = linha.slice(2, -2);
      const cls = speaker === "Eu" ? "speaker-line speaker-eu" : "speaker-line";
      out.push(`<div class="${cls}">${escapeHtml(speaker)}</div>`);
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
// Renderização das hotwords
// ────────────────────────────────────────────────────────────────────
function renderizarHotwords(texto) {
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
      out.push(`
        <div class="hw-match">
          <span class="timestamp">${escapeHtml(ts)}</span>
          <span class="hw-keyword">${escapeHtml(kw)}</span>
          <span style="color:var(--text-2);font-size:11px;"> (${escapeHtml(sim)}) — ${escapeHtml(speaker)}</span>
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
        diarizar: $("diarizar").checked,
        hotwords: $("hotwords").value,
      }),
    });
    await atualizarStatus();
  } catch (e) {
    alert(`Erro ao iniciar: ${e.message}`);
  }
}

async function pararGravacao() {
  try {
    await api("/api/gravar/parar", { method: "POST" });
    await atualizarStatus();
  } catch (e) {
    alert(`Erro ao parar: ${e.message}`);
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
    alert(`Erro ao excluir: ${e.message}`);
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
        hotwords: $("re-hotwords").value,
      }),
    });
    await atualizarStatus();
  } catch (e) {
    alert(`Erro ao reprocessar: ${e.message}`);
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
// Init
// ────────────────────────────────────────────────────────────────────
$("btn-iniciar").addEventListener("click", iniciarGravacao);
$("btn-parar").addEventListener("click", pararGravacao);
$("btn-reprocessar").addEventListener("click", reprocessar);
$("btn-excluir").addEventListener("click", pedirConfirmacaoExcluir);
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

carregarLista();
atualizarStatus();
setInterval(atualizarStatus, 1000);
setInterval(() => {
  // Recarrega lista periodicamente caso arquivos mudem por fora
  if (!ultimoEstadoBackend.gravando) carregarLista();
}, 10000);
