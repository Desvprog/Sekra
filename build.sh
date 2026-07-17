#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Garante que o venv existe e as dependências estão instaladas
if [ ! -d ".venv" ]; then
  echo "==> Criando ambiente virtual Python..."
  python3 -m venv .venv
fi

echo "==> [1/3] Instalando dependências Python..."
.venv/bin/pip install -r requirements.txt --quiet
.venv/bin/pip install pyinstaller --quiet

echo "==> [2/3] Empacotando servidor Python com PyInstaller..."
.venv/bin/pyinstaller server.spec --noconfirm --clean

echo "==> [3/3] Instalando dependências Electron e gerando AppImage..."
cd "$ROOT/electron"
pnpm install --silent
pnpm run build

echo ""
echo "✅ Pronto! Pacotes gerados em: electron/dist/"
ls "$ROOT/electron/dist/"*.AppImage "$ROOT/electron/dist/"*.deb 2>/dev/null || true
