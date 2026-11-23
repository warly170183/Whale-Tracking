"""Diario de entradas etiquetadas con la alineación de las ballenas (validación hacia delante).

Cada entrada guarda el estado de las ballenas EN ESE MOMENTO, tal como lo vería la app en vivo: así los
datos acumulados no sufren el sesgo de supervivencia de la selección de wallets ni mirada hacia delante.
"""

from __future__ import annotations

import json
import random
import statistics
import time
import uuid
from pathlib import Path


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict] = []
        try:
            self.entries = json.loads(path.read_text())
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, indent=1))
        tmp.replace(self.path)

    def add(self, coin: str, direction: str, entry_px: float, alignment: dict, snapshot: dict | None,
            risk_pct: float | None = None, note: str = "") -> dict:
        e = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "coin": coin, "direction": direction,
             "entry_px": entry_px, "risk_pct": risk_pct, "note": note,
             "alignment": alignment["label"], "reasons": alignment["reasons"], "snapshot": snapshot,
             "status": "open", "exit_px": None, "closed_ts": None, "ret_pct": None, "r": None}
        self.entries.append(e)
        self._save()
        return e

    def close(self, entry_id: str, exit_px: float) -> dict | None:
        for e in self.entries:
            if e["id"] == entry_id and e["status"] == "open":
                d = 1 if e["direction"] == "long" else -1
                e["exit_px"], e["closed_ts"], e["status"] = exit_px, time.time(), "closed"
                e["ret_pct"] = d * (exit_px / e["entry_px"] - 1) * 100
                if e["risk_pct"]:
                    e["r"] = e["ret_pct"] / e["risk_pct"]
                self._save()
                return e
        return None

    def stats(self, n_perm: int = 5000, min_group: int = 10) -> dict:
        closed = [e for e in self.entries if e["status"] == "closed"]
        groups: dict[str, dict] = {}
        for label in ("aligned", "neutral", "against"):
            rs = [e["ret_pct"] for e in closed if e["alignment"] == label]
            groups[label] = {
                "n": len(rs),
                "win_rate": (sum(r > 0 for r in rs) / len(rs)) if rs else None,
                "mean_ret_pct": statistics.mean(rs) if rs else None,
                "median_ret_pct": statistics.median(rs) if rs else None,
                "mean_r": (statistics.mean([e["r"] for e in closed
                                            if e["alignment"] == label and e["r"] is not None])
                           if any(e["alignment"] == label and e["r"] is not None for e in closed) else None),
            }
        out = {"closed": len(closed), "open": len(self.entries) - len(closed), "groups": groups,
               "test": "insuficiente: se necesitan al menos "
                       f"{min_group} entradas cerradas alineadas y {min_group} no alineadas"}
        al = [e["ret_pct"] for e in closed if e["alignment"] == "aligned"]
        rest = [e["ret_pct"] for e in closed if e["alignment"] != "aligned"]
        if len(al) >= min_group and len(rest) >= min_group:
            obs = statistics.mean(al) - statistics.mean(rest)
            pool, k, rng, hits = al + rest, len(al), random.Random(11), 0
            for _ in range(n_perm):
                rng.shuffle(pool)
                if statistics.mean(pool[:k]) - statistics.mean(pool[k:]) >= obs:
                    hits += 1
            out["test"] = {"aligned_minus_rest_pct": obs, "p_one_sided": (hits + 1) / (n_perm + 1)}
        return out
