const { app, BrowserWindow, dialog, Tray, Menu, Notification, globalShortcut } = require("electron");

app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-software-rasterizer");
app.commandLine.appendSwitch("log-level", "3"); // suprime warnings do Chromium/GTK
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");

const PORT = 8654;
const POLL_INTERVAL_MS = 300;
const POLL_TIMEOUT_MS = 30_000;
const STATUS_POLL_MS = 2000;
const ATALHO_GRAVAR = "Ctrl+Alt+R";

const AUTOSTART_DIR = path.join(os.homedir(), ".config", "autostart");
const AUTOSTART_FILE = path.join(AUTOSTART_DIR, "reunioes.desktop");

let serverProcess = null;
let mainWindow = null;
let serverStderr = "";
let tray = null;
let isQuitting = false;
let hiddenBoot = process.argv.includes("--hidden");

// Estado conhecido do backend (atualizado pelo polling)
let estadoAtual = { gravando: false, duracao_s: 0 };
let statusTimer = null;

function serverCommand() {
  if (app.isPackaged) {
    // Produção: binário gerado pelo PyInstaller
    const bin = path.join(process.resourcesPath, "server", "server");
    return { cmd: bin, args: [], cwd: process.resourcesPath };
  }

  // Dev: usa o venv criado na raiz do projeto
  const root = path.join(__dirname, "..");
  const python = path.join(root, ".venv", "bin", "python");
  return { cmd: python, args: [path.join(root, "backend", "server.py")], cwd: root };
}

function startServer() {
  const { cmd, args, cwd } = serverCommand();

  serverProcess = spawn(cmd, args, {
    detached: false,
    stdio: ["ignore", "ignore", "pipe"],
    cwd,
    env: { ...process.env },
  });

  serverProcess.stderr.on("data", (chunk) => {
    serverStderr += chunk.toString();
  });

  serverProcess.on("exit", (code, signal) => {
    if (isQuitting) return; // kill intencional no quit — não é erro
    if (mainWindow === null && code !== 0) {
      // Saiu antes da janela abrir — mostra o erro
      dialog.showErrorBox(
        "Servidor falhou ao iniciar",
        serverStderr.trim() || `Processo encerrou com código ${code}`
      );
      app.quit();
    }
  });

  serverProcess.on("error", (err) => {
    dialog.showErrorBox("Erro ao iniciar servidor", err.message);
    app.quit();
  });
}

function stopServer() {
  if (serverProcess) {
    serverProcess.kill();
    serverProcess = null;
  }
}

function waitForServer(deadline) {
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      if (Date.now() > deadline) {
        reject(new Error(
          `Servidor não respondeu em ${POLL_TIMEOUT_MS / 1000}s.\n\n` +
          (serverStderr.trim() || "Sem saída de erro disponível.")
        ));
        return;
      }
      http
        .get(`http://127.0.0.1:${PORT}/api/status`, (res) => {
          if (res.statusCode === 200) resolve();
          else setTimeout(tryOnce, POLL_INTERVAL_MS);
        })
        .on("error", () => setTimeout(tryOnce, POLL_INTERVAL_MS));
    };
    tryOnce();
  });
}

function createWindow() {
  if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
    return;
  }

  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 800,
    minHeight: 600,
    title: "Reuniões",
    // A SPA tem barra de janela própria (winbar); o menu padrão
    // File/Edit/View não tem função aqui.
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  mainWindow.setMenuBarVisibility(false);

  mainWindow.loadURL(`http://127.0.0.1:${PORT}`);

  mainWindow.on("close", (event) => {
    if (isQuitting) return;
    event.preventDefault();
    mainWindow.hide();
  });

  mainWindow.on("closed", () => { mainWindow = null; });
}

// --- Requisições HTTP simples ao backend (mesmo estilo de waitForServer) ---

function httpGetJson(caminho) {
  return new Promise((resolve, reject) => {
    http
      .get(`http://127.0.0.1:${PORT}${caminho}`, (res) => {
        let corpo = "";
        res.on("data", (chunk) => { corpo += chunk; });
        res.on("end", () => {
          if (res.statusCode !== 200) {
            reject(new Error(`HTTP ${res.statusCode}`));
            return;
          }
          try {
            resolve(JSON.parse(corpo));
          } catch (e) {
            reject(e);
          }
        });
      })
      .on("error", reject);
  });
}

function httpPostJson(caminho, dados) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(dados || {});
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port: PORT,
        path: caminho,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
        },
      },
      (res) => {
        let corpo = "";
        res.on("data", (chunk) => { corpo += chunk; });
        res.on("end", () => {
          if (res.statusCode < 200 || res.statusCode >= 300) {
            reject(new Error(corpo || `HTTP ${res.statusCode}`));
            return;
          }
          try {
            resolve(corpo ? JSON.parse(corpo) : {});
          } catch (e) {
            resolve({});
          }
        });
      }
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

function notificar(titulo, corpo) {
  new Notification({ title: titulo, body: corpo }).show();
}

// --- Tray ---

function iconePath(nome) {
  return path.join(__dirname, "assets", nome);
}

function formatarDuracao(segundos) {
  const min = Math.floor(segundos / 60);
  const seg = segundos % 60;
  return `${min}min${seg > 0 ? ` ${seg}s` : ""}`;
}

function estaAutostartAtivo() {
  return fs.existsSync(AUTOSTART_FILE);
}

function ativarAutostart() {
  const exePath = process.env.APPIMAGE || process.execPath;
  const args = ["--hidden"];
  // Em dev, process.execPath é o binário do Electron — precisa do path do app
  if (!app.isPackaged) args.unshift(`"${__dirname}"`, "--no-sandbox");

  const conteudo = `[Desktop Entry]
Type=Application
Name=Reuniões
Exec="${exePath}" ${args.join(" ")}
Icon=${iconePath("icon.png")}
X-GNOME-Autostart-enabled=true
Terminal=false
`;

  fs.mkdirSync(AUTOSTART_DIR, { recursive: true });
  fs.writeFileSync(AUTOSTART_FILE, conteudo, "utf-8");
}

function desativarAutostart() {
  if (fs.existsSync(AUTOSTART_FILE)) {
    fs.unlinkSync(AUTOSTART_FILE);
  }
}

function construirMenu() {
  const template = [
    {
      label: "Abrir",
      click: () => createWindow(),
    },
    {
      label: estadoAtual.gravando ? "Parar gravação" : "Iniciar gravação",
      click: () => alternarGravacao(),
    },
    { type: "separator" },
    {
      label: "Iniciar com o sistema",
      type: "checkbox",
      checked: estaAutostartAtivo(),
      click: (item) => {
        if (item.checked) {
          ativarAutostart();
        } else {
          desativarAutostart();
        }
      },
    },
    { type: "separator" },
    {
      label: "Sair",
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ];
  return Menu.buildFromTemplate(template);
}

function atualizarTray(reconstruirMenu) {
  if (!tray) return;
  tray.setToolTip(
    estadoAtual.gravando
      ? `Gravando — ${formatarDuracao(estadoAtual.duracao_s)}`
      : "Reuniões"
  );
  // Ícone e menu só mudam quando o estado de gravação vira — reconstruir o
  // menu a cada poll fecharia um menu aberto pelo usuário.
  if (reconstruirMenu) {
    tray.setImage(iconePath(estadoAtual.gravando ? "tray-rec.png" : "tray-idle.png"));
    tray.setContextMenu(construirMenu());
  }
}

function criarTray() {
  tray = new Tray(iconePath("tray-idle.png"));
  tray.on("click", () => createWindow());
  atualizarTray(true);
}

async function alternarGravacao() {
  try {
    if (estadoAtual.gravando) {
      await httpPostJson("/api/gravar/parar");
      notificar("Reuniões", "Gravação parada — processando");
    } else {
      await httpPostJson("/api/gravar/iniciar", {});
      notificar("Reuniões", "Gravação iniciada");
    }
    await consultarStatus();
  } catch (err) {
    notificar("Erro", err.message || String(err));
  }
}

// --- Detecção automática de reunião ---

// Último bloco `deteccao` visto no status — usado para detectar transições
// e não repetir a notificação da mesma transição.
let deteccaoAnterior = null;

function notificarClicavel(titulo, corpo, aoClicar) {
  const n = new Notification({ title: titulo, body: corpo });
  n.on("click", aoClicar);
  n.show();
}

function tratarDeteccao(novo) {
  const det = novo.deteccao;
  if (!det) return;
  const anterior = deteccaoAnterior;
  deteccaoAnterior = det;
  if (anterior === null) return; // primeiro poll — sem transição conhecida

  if (det.detectado && !anterior.detectado) {
    // false→true
    if (det.auto_iniciar && novo.gravando) {
      notificar("Reuniões", `Gravação iniciada automaticamente (${det.app || "app"})`);
    } else if (!det.auto_iniciar && !novo.gravando) {
      notificarClicavel(
        "Reuniões",
        `Reunião detectada (${det.app || "app"}) — clique para gravar`,
        () => alternarGravacao()
      );
    }
  } else if (!det.detectado && anterior.detectado && novo.gravando) {
    // true→false durante gravação — sugere parar, não para sozinho
    notificarClicavel(
      "Reuniões",
      "Reunião parece ter terminado — clique para parar",
      () => { if (estadoAtual.gravando) alternarGravacao(); }
    );
  }
}

async function consultarStatus() {
  try {
    const novo = await httpGetJson("/api/status");
    tratarDeteccao(novo);
    const virouGravacao = novo.gravando !== estadoAtual.gravando;
    const mudou = virouGravacao || novo.duracao_s !== estadoAtual.duracao_s;
    if (mudou) {
      estadoAtual = novo;
      atualizarTray(virouGravacao);
    }
  } catch (err) {
    // Servidor pode estar temporariamente indisponível — ignora silenciosamente
  }
}

function iniciarPollingStatus() {
  statusTimer = setInterval(consultarStatus, STATUS_POLL_MS);
}

// --- Single instance ---

const obteveLock = app.requestSingleInstanceLock();

if (!obteveLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    createWindow();
  });

  app.whenReady().then(async () => {
    startServer();

    try {
      await waitForServer(Date.now() + POLL_TIMEOUT_MS);

      criarTray();
      iniciarPollingStatus();
      await consultarStatus();

      if (!hiddenBoot) {
        createWindow();
      }

      globalShortcut.register(ATALHO_GRAVAR, () => {
        alternarGravacao();
      });
    } catch (err) {
      dialog.showErrorBox("Falha ao iniciar", err.message);
      app.quit();
    }
  });

  app.on("window-all-closed", () => {
    // Não encerra o app — segue rodando no tray.
  });

  app.on("before-quit", () => {
    isQuitting = true;
    if (statusTimer) clearInterval(statusTimer);
    globalShortcut.unregisterAll();
    stopServer();
  });
}
