"""Charge et mémoire de l'hôte, lues depuis /proc (visibles depuis le conteneur).

Limite assumée (docs/ARCHITECTURE.md) : les disques de l'array Unraid et le
SMART ne sont pas accessibles sans privilèges — ils passeront par les capteurs
de Nova si une intégration les expose.
"""

from __future__ import annotations

import os


def read_system() -> dict:
    out: dict = {}
    try:
        load1, load5, load15 = os.getloadavg()
        out["charge"] = [round(load1, 2), round(load5, 2), round(load15, 2)]
        out["coeurs"] = os.cpu_count() or 1
    except OSError:
        out["charge"] = None

    try:
        meminfo: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                key, _, rest = line.partition(":")
                value = rest.strip().split()
                if value:
                    meminfo[key] = int(value[0])  # kB
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        if total:
            out["ram"] = {
                "totale_mo": round(total / 1024),
                "disponible_mo": round(available / 1024),
                "utilisee_pct": round(100 * (total - available) / total),
            }
    except OSError:
        pass
    return out
