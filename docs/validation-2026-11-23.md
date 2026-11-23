# Validación del sesgo contra retornos futuros (23-nov-2026)

**Pregunta:** ¿el sesgo de la cesta de ballenas predice el retorno de las siguientes 4 / 24 / 72 h?

**Datos:** 45 wallets (selección de la app: direccionales, sin market makers), fills de los últimos 90 días
(hasta ~10k por wallet), posiciones reconstruidas hora a hora, velas de 1 h de Hyperliquid.
Muestras **no solapadas** (una cada H horas). Reproducir: `python -m whale_tracking.validate`.

| coin | señal | H | n | IC | p(IC) | acierto | p | Sharpe seguir | Sharpe B&H | IC 1ª / 2ª mitad |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTC | nivel | 4 | 521 | -0.01 | 0.85 | 52% | 0.49 | 2.61 | -1.93 | 0.02 / -0.02 |
| BTC | cambio 24h | 4 | 521 | 0.08 | 0.06 | 56% | 0.31 | 1.64 | -1.93 | 0.07 / 0.07 |
| BTC | nivel | 24 | 86 | -0.13 | 0.23 | 48% | 0.81 | 3.29 | -2.28 | -0.13 / -0.17 |
| BTC | cambio 24h | 24 | 86 | 0.19 | 0.08 | 62% | 0.58 | 0.19 | -2.28 | 0.12 / 0.22 |
| BTC | nivel | 72 | 28 | -0.15 | 0.44 | 59% | 0.52 | 3.10 | -2.32 | -0.06 / -0.42 |
| BTC | cambio 24h | 72 | 28 | 0.28 | 0.16 | 83% | 0.22 | 2.96 | -2.32 | 0.44 / 0.24 |
| ETH | nivel | 4 | 439 | 0.01 | 0.79 | 49% | 0.75 | 3.94 | -3.31 | 0.09 / 0.01 |
| ETH | cambio 24h | 24 | 71 | 0.18 | 0.15 | 50% | 1.00 | 2.91 | -3.47 | -0.10 / 0.04 |
| SOL | nivel | 24 | 60 | -0.18 | 0.17 | 63% | 0.05 | 5.64 | -5.64 | -0.34 / -0.22 |
| SOL | nivel | 72 | 19 | -0.26 | 0.30 | 84% | 0.00 | 7.96 | -7.96 | — |
| HYPE | nivel | 24 | 86 | 0.00 | 0.97 | 57% | 0.27 | 1.77 | -1.03 | -0.03 / 0.15 |
| HYPE | cambio 24h | 4 | 521 | 0.12 | 0.01 | 61% | 0.05 | 4.90 | -0.73 | 0.15 / 0.06 |

(Tabla abreviada; las 24 combinaciones completas están en `validation-2026-11-23.json`.)

## Lectura honesta

1. **El nivel del sesgo no predice.** IC ≈ 0 o ligeramente negativo en BTC, ETH y HYPE, y negativo en SOL.
2. **El Sharpe de "seguir la señal" engaña.** El periodo fue bajista (comprar y mantener: Sharpe de -1 a -8) y las
   ballenas estuvieron cortas casi todo el rato. "Seguirlas" equivale a estar corto en un mercado que cae:
   en SOL a 72 h, Sharpe 7.96 = exactamente el opuesto de comprar y mantener. No es habilidad de timing demostrada.
3. **El cambio del sesgo (24 h) es lo único con un hint:** IC positivo en BTC en los tres horizontes y en las dos
   mitades (0.07/0.07, 0.12/0.22, 0.44/0.24). Pero ningún p llega a significar nada tras corregir por 24 pruebas
   (esperaríamos ~1 resultado con p < 0.05 solo por azar; el HYPE 4 h con p = 0.01 es ese caso).
4. **Sesgo de supervivencia:** las wallets se eligen por su PnL actual en el leaderboard, lo que tiende a *inflar*
   cualquier resultado favorable. Que aun así el nivel no muestre nada pesa en contra de la señal.
5. **Una sola fase de mercado** (90 días bajistas) y pocas muestras a 24/72 h (28–86): baja potencia estadística.

## Qué implica para la app

- Tratar `LARGO/CORTO` como **descripción del posicionamiento**, no como señal de entrada.
- La `confianza` sigue sin calibrar: mide cuánta cesta respalda la lectura, no la probabilidad de acierto.
- Lo más prometedor para seguir investigando es el **cambio** del sesgo, no su nivel.
- Para una conclusión firme hacen falta más meses (incluyendo un tramo alcista), más wallets y un control con
  wallets elegidas sin mirar su PnL actual.
