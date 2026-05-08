"""Hardware detection + model recommendation.

Used by Settings to pre-fill the Local-mode model picker with a sensible
default for the user's machine. Also exposed so the UI can tag the
recommended model with a star.
"""
from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from typing import Optional


@dataclass
class Hardware:
    cpu: str
    cpu_cores: int
    ram_gb: float
    has_gpu: bool
    gpu_name: Optional[str]
    gpu_vram_gb: Optional[float]


def detect() -> Hardware:
    """Best-effort hardware probe — never raises, returns reasonable
    defaults if a probe fails."""
    cpu_name = platform.processor() or platform.machine() or "unknown"
    cpu_cores = os.cpu_count() or 1
    ram_gb = _ram_gb()
    gpu = _gpu_probe()
    return Hardware(
        cpu=cpu_name,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        has_gpu=gpu is not None,
        gpu_name=gpu[0] if gpu else None,
        gpu_vram_gb=gpu[1] if gpu else None,
    )


def _ram_gb() -> float:
    # Try psutil (commonly available with PyQt6 stack via pip-installed deps).
    try:
        import psutil

        return round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        pass
    # Fallback: parse /proc/meminfo on Linux.
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024**2), 1)
    except OSError:
        pass
    return 0.0


def _gpu_probe() -> Optional[tuple[str, float]]:
    """Return (name, vram_gb) if a CUDA GPU is detected, else None."""
    # Prefer torch.cuda — it's already a transitive dep through faster-whisper
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            i = 0
            name = torch.cuda.get_device_name(i)
            props = torch.cuda.get_device_properties(i)
            vram_gb = round(props.total_memory / (1024**3), 1)
            return (name, vram_gb)
    except Exception:
        pass
    # Fallback: shell out to nvidia-smi if present.
    nv = shutil.which("nvidia-smi")
    if nv:
        try:
            import subprocess

            out = subprocess.check_output(
                [nv, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                text=True,
                timeout=3,
            ).strip()
            line = out.splitlines()[0]
            name, mem_mb = (s.strip() for s in line.split(","))
            return (name, round(int(mem_mb) / 1024, 1))
        except Exception:
            pass
    return None


def recommend_local_model(hw: Optional[Hardware] = None) -> str:
    """Pick the largest faster-whisper model the hardware can comfortably run.

    GPU tiers:
        ≥6 GB VRAM   → large-v3   (top quality, ~3GB on disk, ~5-10x realtime)
        ≥3 GB VRAM   → medium     (~1.5GB on disk)
        any GPU      → small      (~0.5GB)
    CPU-only tiers:
        ≥16 GB RAM   → small      (still snappy on a modern CPU)
        ≥8 GB RAM    → base       (default — fast)
        otherwise    → base
    """
    hw = hw or detect()
    if hw.has_gpu and hw.gpu_vram_gb:
        if hw.gpu_vram_gb >= 6.0:
            return "large-v3"
        if hw.gpu_vram_gb >= 3.0:
            return "medium"
        return "small"
    if hw.ram_gb >= 16.0:
        return "small"
    return "base"


def describe(hw: Optional[Hardware] = None) -> str:
    """One-line human-readable summary for Settings."""
    hw = hw or detect()
    parts = [f"{hw.cpu_cores} cores"]
    if hw.ram_gb:
        parts.append(f"{hw.ram_gb:.0f} GB RAM")
    if hw.has_gpu and hw.gpu_name:
        vram = f"{hw.gpu_vram_gb:.0f} GB" if hw.gpu_vram_gb else "?"
        parts.append(f"{hw.gpu_name} ({vram})")
    else:
        parts.append("CPU only")
    return " · ".join(parts)
