const { app, BrowserWindow, dialog } = require("electron");

app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-software-rasterizer");
app.commandLine.appendSwitch("log-level", "3"); // suprime warnings do Chromium/GTK
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const PORT = 8654;
const POLL_INTERVAL_MS = 300;
const POLL_TIMEOUT_MS = 30_000;

let serverProcess = null;
let mainWindow = null;
let serverStderr = "";

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
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 800,
    minHeight: 600,
    title: "Reuniões",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.loadURL(`http://127.0.0.1:${PORT}`);
  mainWindow.on("closed", () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  startServer();

  try {
    await waitForServer(Date.now() + POLL_TIMEOUT_MS);
    createWindow();
  } catch (err) {
    dialog.showErrorBox("Falha ao iniciar", err.message);
    app.quit();
  }
});

app.on("window-all-closed", () => {
  stopServer();
  app.quit();
});

app.on("before-quit", stopServer);
