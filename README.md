# Reuniões

App desktop para gravar, transcrever, diarizar e analisar reuniões. Roda 100% offline.

## Estrutura

```
record-app/
├── backend/
│   ├── server.py        # backend FastAPI (porta 8654)
│   └── reuniao.py       # núcleo: gravação, transcrição, diarização
├── electron/
│   ├── main.js          # processo principal Electron
│   └── package.json
├── static/              # frontend (HTML/CSS/JS)
├── requirements.txt     # dependências Python
├── server.spec          # configuração PyInstaller
└── build.sh             # gera o AppImage/deb distribuível
```

As gravações ficam em `reunioes/` dentro do próprio projeto (dev) ou em `~/reunioes/` (build distribuído).

## Pré-requisitos do sistema

```bash
sudo apt install ffmpeg pulseaudio-utils python3-venv nodejs npm python3-venv python3-pip
```

## Setup (desenvolvimento)

```bash
# 1. Ambiente Python
python3 -m venv .venv
source .venv/bin/activate
.venv/bin/pip install -r requirements.txt
pip install pyannote.audio

# 2. Huggingface                                                                                                                   
  - Acesse https://huggingface.co/pyannote/speaker-diarization-3.1 e aceite os termos                                                                                                         
  - Crie um token em https://huggingface.co/settings/tokens                                                                                                                                   

 — Salvar o token:                                                                                                                                                                   
  mkdir -p ~/.config/reunioes                                                                                                                                                                 
  echo "hf_SEU_TOKEN_AQUI" > ~/.config/reunioes/hf_token

# 3. Dependências Electron
cd electron && npm install
```

## Rodar

```bash
cd electron && npm start
```

## Build (AppImage distribuível)

```bash
./build.sh
# gera: electron/dist/Reunioes-*.AppImage e .deb
```

## Diarização (opcional)

Requer token Hugging Face com acesso a:
- [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
- [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)

Descomente `pyannote.audio` em `requirements.txt` e salve o token:

```bash
mkdir -p ~/.config/reunioes
echo "hf_xxxxxxxxxx" > ~/.config/reunioes/hf_token
chmod 600 ~/.config/reunioes/hf_token
```

## Uso da interface

1. Preencha título, modelo Whisper e opções de diarização/hotwords
2. Clique **INICIAR GRAVAÇÃO**
3. Clique **PARAR** — transcrição começa automaticamente em background
4. Selecione uma reunião na lista para ver transcrição, hotwords e player de áudio
5. Use a aba **Reprocessar** para retranscrever com outras opções

## Saída de cada gravação

```
reunioes/2026-05-05/14-30-daily/
├── audio.wav              # mesclado (mic + sistema)
├── audio-mic.wav          # só microfone
├── audio-loopback.wav     # só áudio do sistema
├── transcricao.txt        # transcrição com timestamps e speakers
└── hotwords.md            # menções detectadas (se configurado)
```

## Troubleshooting

**Sem microfone ou áudio do sistema**
```bash
pactl get-default-source   # microfone
pactl get-default-sink     # saída de áudio
```

**Token HF inválido mesmo configurado**
Confirme que aceitou os termos dos dois modelos acima na página do Hugging Face.

**Nenhum segmento transcrito**
O Whisper usa VAD (detecção de voz). Áudio muito baixo ou silencioso resulta em 0 segmentos — tente com modelo `large-v3` ou verifique o volume do microfone.
