#!/usr/bin/env python3
"""
Migração retroativa das reuniões já gravadas:
  1. Comprime os WAVs antigos (mic, loopback, mesclado) para Opus, liberando disco
     (Opus ~32 kbps reduz o áudio de voz em ~10-15x vs WAV PCM 16 kHz).
  2. Gera um meta.json para reuniões que ainda não têm, inferindo o que for possível
     do nome da pasta e do cabeçalho do transcricao.txt + duração via ffprobe.

Uso:
    python migrar_reunioes.py            # DRY-RUN: só mostra o que faria
    python migrar_reunioes.py --executar # aplica de fato

Seguro de rodar várias vezes (idempotente): pula o que já está comprimido / com meta.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))
import reuniao  # noqa: E402  (compressão + BASE_DIR)
import meta as meta_mod  # noqa: E402


def _tamanho_mb(p: Path) -> float:
    return round(p.stat().st_size / 1024 / 1024, 1) if p.exists() else 0.0


def _parse_cabecalho_transcricao(pasta: Path) -> dict:
    """Extrai modelo e nº de segmentos do cabeçalho do transcricao.txt, se houver."""
    txt = pasta / "transcricao.txt"
    if not txt.exists():
        return {}
    info = {}
    for linha in txt.read_text(encoding="utf-8").splitlines()[:3]:
        m = re.search(r"Modelo:\s*([^\s|]+)", linha)
        if m:
            info["modelo"] = m.group(1)
        m = re.search(r"Segmentos:\s*(\d+)", linha)
        if m:
            info["num_segmentos"] = int(m.group(1))
    return info


def _parse_pasta(dia: str, nome: str) -> tuple[str, str]:
    partes = nome.split("-")
    hora = f"{partes[0]}:{partes[1]}" if len(partes) >= 2 else "??:??"
    titulo = "-".join(partes[2:]) if len(partes) > 2 else nome
    return hora, titulo


def migrar(executar: bool) -> None:
    base = reuniao.BASE_DIR
    if not base.exists():
        print(f"Nada a migrar — {base} não existe.")
        return

    total_antes = total_depois = 0.0
    n_comprimidas = n_meta = 0

    for dia in sorted(base.iterdir()):
        if not dia.is_dir():
            continue
        for pasta in sorted(dia.iterdir()):
            if not pasta.is_dir():
                continue

            wavs = [pasta / f"{b}.wav" for b in ("audio", "audio-mic", "audio-loopback")]
            wavs = [w for w in wavs if w.exists()]
            mb = sum(_tamanho_mb(w) for w in wavs)

            # 1. Compressão (só se ainda houver WAVs)
            if wavs:
                total_antes += mb
                print(f"[{pasta.relative_to(base)}] {mb:.0f} MB em WAV "
                      f"→ {'comprimindo' if executar else 'comprimiria'} para Opus")
                if executar:
                    reuniao.comprimir_audios(pasta)
                    depois = sum(_tamanho_mb(pasta / f"{b}.opus")
                                 for b in ("audio", "audio-mic", "audio-loopback"))
                    total_depois += depois
                n_comprimidas += 1

            # 2. meta.json retroativo (se ausente)
            if not meta_mod.caminho(pasta).exists():
                hora, titulo = _parse_pasta(dia.name, pasta.name)
                campos = {
                    "titulo": titulo,
                    "data": dia.name,
                    "hora": hora,
                    "status": "concluido" if (pasta / "transcricao.txt").exists() else "sem_transcricao",
                    "tem_transcricao": (pasta / "transcricao.txt").exists(),
                    "tem_hotwords": (pasta / "hotwords.md").exists(),
                    "tem_resumo": (pasta / "resumo.md").exists(),
                    **_parse_cabecalho_transcricao(pasta),
                }
                fonte = reuniao._fonte_audio(pasta, "audio")
                if fonte:
                    campos["duracao_s"] = meta_mod.duracao_audio(fonte)
                print(f"[{pasta.relative_to(base)}] {'gerando' if executar else 'geraria'} meta.json")
                if executar:
                    meta_mod.escrever(pasta, **campos)
                n_meta += 1

    print("\n" + "=" * 60)
    verbo = "Migradas" if executar else "Seriam migradas"
    print(f"{verbo}: {n_comprimidas} reunião(ões) comprimida(s), {n_meta} meta.json")
    if executar and total_antes:
        print(f"Disco: {total_antes:.0f} MB → {total_depois:.0f} MB "
              f"(economia de {total_antes - total_depois:.0f} MB)")
    elif total_antes:
        print(f"Disco em WAV a comprimir: {total_antes:.0f} MB "
              f"(estimativa pós-Opus: ~{total_antes/12:.0f} MB)")
    if not executar:
        print("\nDRY-RUN. Rode com --executar para aplicar.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Migra reuniões antigas (compressão Opus + meta.json).")
    ap.add_argument("--executar", action="store_true", help="Aplica de fato (sem isto, é dry-run).")
    migrar(ap.parse_args().executar)
