# Whale Tracking

Seguimiento de ballenas en perpetuales de **Hyperliquid**: sesgo largo/corto agregado por activo, eventos
de apertura/cierre en vivo y ranking de wallets por Sharpe. Datos 100 % públicos, sin claves.

> Es **contexto**, no una señal: muestra dónde están posicionadas las wallets seguidas, no lo que harán.
> Pensado como un filtro más junto a estructura, régimen y derivados.

## Arranque

```bash
pip install -r requirements.txt
python -m whale_tracking          # http://localhost:8000
pytest                            # tests
```

## Cómo funciona

1. **Selección** (`ranking.py`): descarga el leaderboard y se queda con las N cuentas más grandes con
   volumen reciente y PnL histórico positivo (+ tus wallets en `WT_EXTRA_WALLETS`).
2. **Semilla** (`resolve.py`): lee las posiciones actuales de cada wallet (`clearinghouseState`).
3. **En vivo** (`engine.py`): un único WebSocket con el feed público de trades de todos los perps. Cada
   trade trae a las dos contrapartes; se filtran las vigiladas y se pliegan en un libro de posiciones
   (`state.py`) que emite eventos open / add / reduce / close.
4. **Reconciliación**: cada 5 min se vuelve a leer la cadena (con guarda de época para no pisar fills vivos).
5. **Sharpe** (`ranking.py`): en segundo plano, Sharpe a 30 d y total por wallet (retornos por PnL, sin
   contar depósitos). Se traduce a un peso 0.2–1.0 (sin datos = 0.5), con caché de 24 h en `data/`.
6. **Sesgo** (`aggregate.py`): por activo, `bias = Σ peso·signo·nocional / Σ peso·nocional ∈ [−1, 1]`,
   amplitud (wallets largas − cortas) y cambio a 1 h / 4 h / 24 h. `LARGO/CORTO` si |sesgo| ≥ 0.25.

## API

| Ruta | Qué devuelve |
|---|---|
| `GET /api/state` | estado, sesgo por activo y deltas |
| `GET /api/wallets` | wallets seguidas con Sharpe, peso y exposición |
| `GET /api/events?limit=` | últimos eventos |
| `GET /api/stream` | SSE con eventos en vivo |
| `GET /api/history/{coin}` | serie del sesgo |

## Configuración (`WT_*`)

`WATCH_SIZE` (100), `MIN_ACCOUNT` (1e6), `MIN_MONTH_VLM` (1e7), `EXTRA_WALLETS` (coma),
`COINS` (vacío = todos), `RECONCILE_S` (300), `EVENT_MIN_USD` (100000), `BIAS_THRESHOLD` (0.25),
`SHARPE` (0 para desactivar), `DATA_DIR`, `HOST`, `PORT`.

## Limitaciones conocidas

- Solo posiciones de la cuenta principal de cada wallet (no subcuentas ni vaults).
- Los market makers hedgeados aportan poco sesgo neto; el peso por Sharpe no los distingue de los direccionales.
- El leaderboard no es "todas las ballenas": es un muestreo por tamaño y actividad.
- Los eventos en vivo dependen del WebSocket; si se corta, se resincroniza en la siguiente reconciliación.
- Sin persistencia de posiciones/eventos: al reiniciar se reconstruye desde la cadena.

## Hoja de ruta

- Detectores de microestructura (ráfagas de trades grandes, barridos, icebergs) con libro L2 — ver `THIRD_PARTY_NOTICES.md`.
- Mapa de liquidaciones de las posiciones seguidas (`liq_px` ya se guarda).
- Exportar el sesgo como módulo/JSON para integrarlo en otras apps de análisis (filtro de confianza junto al régimen HMM).
- Clasificación de estilo de wallet (swing / market maker) y filtro direccional.

Atribuciones de código de terceros en [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
