"""Alineación de una entrada (long/short) con el posicionamiento de las ballenas.

Definición FIJADA ANTES de validarla (para no ajustarla a los datos):

    alineada  = el cambio del sesgo a 24 h tiene el signo de la entrada y |Δ| >= MIN_DELTA
                y confianza >= MIN_CONF y nº de wallets >= MIN_WALLETS
    en contra = igual, pero con el signo contrario
    neutral   = cualquier otro caso (incluye "sin datos suficientes")

Se usa el CAMBIO del sesgo y no su nivel porque el nivel no mostró poder predictivo en la validación
(docs/validation-2026-11-23.md). Es un filtro de contexto, no una señal de entrada.
"""

from __future__ import annotations

MIN_DELTA = 0.05
MIN_CONF = 0.65
MIN_WALLETS = 5


def classify_alignment(direction: str, delta_24h: float | None, confidence: float | None,
                       n_wallets: int | None) -> dict:
    d = 1 if direction == "long" else -1 if direction == "short" else 0
    if d == 0:
        raise ValueError("direction debe ser 'long' o 'short'")
    reasons: list[str] = []
    if delta_24h is None:
        return {"label": "neutral", "reasons": ["sin 24 h de histórico del sesgo todavía"]}
    if n_wallets is None or n_wallets < MIN_WALLETS:
        return {"label": "neutral", "reasons": [f"pocas wallets con posición ({n_wallets or 0} < {MIN_WALLETS})"]}
    if confidence is None or confidence < MIN_CONF:
        return {"label": "neutral", "reasons": [f"confianza baja ({(confidence or 0) * 100:.0f}% < {MIN_CONF * 100:.0f}%)"]}
    if abs(delta_24h) < MIN_DELTA:
        return {"label": "neutral", "reasons": [f"el sesgo casi no cambió en 24 h ({delta_24h * 100:+.0f} pp)"]}
    same = (delta_24h > 0) == (d > 0)
    reasons.append(f"el sesgo cambió {delta_24h * 100:+.0f} pp en 24 h, "
                   + ("en la dirección de tu entrada" if same else "contra tu entrada"))
    return {"label": "aligned" if same else "against", "reasons": reasons}
