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
   volumen reciente y PnL histórico positivo, **sin market makers** (volumen 30 d / cuenta ≥ 60×) y con
   tus wallets de `WT_EXTRA_WALLETS`. Estilo por wallet en `classify.py`: direccional / cubierta / sin posiciones.
2. **Semilla** (`resolve.py`): lee las posiciones actuales de cada wallet (`clearinghouseState`).
3. **En vivo** (`engine.py`): un único WebSocket con el feed público de trades de todos los perps. Cada
   trade trae a las dos contrapartes; se filtran las vigiladas y se pliegan en un libro de posiciones
   (`state.py`) que emite eventos open / add / reduce / close.
4. **Reconciliación**: cada 5 min se vuelve a leer la cadena (con guarda de época para no pisar fills vivos).
5. **Sharpe** (`ranking.py`): en segundo plano, Sharpe a 30 d y total por wallet (retornos por PnL, sin
   contar depósitos). Se traduce a un peso 0.2–1.0 (sin datos = 0.5), con caché de 24 h en `data/`.
6. **Sesgo** (`aggregate.py`): por activo, `bias = Σ peso·signo·nocional / Σ peso·nocional ∈ [−1, 1]`,
   amplitud (wallets largas − cortas) y cambio a 1 h / 4 h / 24 h. `LARGO/CORTO` si |sesgo| ≥ 0.25.
   Cada activo lleva una **confianza** 0–1 (nº de wallets, concentración, acuerdo amplitud/sesgo, fuerza;
   heurística **sin calibrar**) y un aviso si una sola wallet pesa ≥ 50 %.
7. **Mapa de liquidaciones** (`build_liq_map`): liquidaciones pendientes de las posiciones seguidas por
   precio (largos bajo el mark, cortos sobre él). Clic en un activo del panel. Se refresca con la reconciliación.
8. **Histórico persistente** en `data/history.json`: los deltas de 1 h/4 h/24 h sobreviven a un reinicio.

## API

| Ruta | Qué devuelve |
|---|---|
| `GET /api/state` | estado, sesgo por activo y deltas |
| `GET /api/wallets` | wallets seguidas con Sharpe, peso y exposición |
| `GET /api/events?limit=` | últimos eventos |
| `GET /api/stream` | SSE con eventos en vivo |
| `GET /api/history/{coin}` | serie del sesgo |
| `GET /api/signals?min_wallets=3` | **señales exportables** por activo (sesgo, confianza, deltas, `stale`) para otras apps |
| `GET /api/signal/{coin}` | la misma señal de un solo activo (404 si no hay posiciones) |
| `GET /api/liqmap/{coin}` | liquidaciones pendientes agrupadas por precio |

## Configuración (`WT_*`)

`WATCH_SIZE` (100), `MIN_ACCOUNT` (1e6), `MIN_MONTH_VLM` (1e7), `EXTRA_WALLETS` (coma),
`COINS` (vacío = todos), `RECONCILE_S` (300), `EVENT_MIN_USD` (100000), `BIAS_THRESHOLD` (0.25),
`MM_TURNOVER` (60), `INCLUDE_MM` (1 para incluir market makers), `SHARPE` (0 para desactivar), `DATA_DIR`, `HOST`, `PORT`.

## Validar una entrada (alineación) y diario

`GET /api/alignment/{coin}?direction=long|short` responde si tu entrada va **a favor, en contra o neutral**
respecto al *cambio* del sesgo a 24 h (reglas fijas en `alignment.py`: |Δ| ≥ 5 pp, confianza ≥ 65 %, ≥ 5 wallets),
más el contexto de liquidaciones cercanas (±2/5/10 %). En el panel: sección «Validar entrada».

- **Diario** (`POST /api/journal`, `/api/journal/{id}/close`, `GET /api/journal/stats`): registra cada entrada con la
  alineación de ese instante y compara resultados por grupo con un test de permutación cuando hay ≥ 10 cerradas por lado.
- **Registro horario** en `data/snapshots.jsonl`: estado de las ballenas por activo, para construir un dataset propio.
- **Backtest condicional** (`python -m whale_tracking.conditional`): resultado del 23-nov-2026 en
  [`docs/conditional-2026-11-23.md`](docs/conditional-2026-11-23.md): *alineada* no mejora las entradas a favor de
  tendencia, pero *en contra* rindió claramente peor (hipótesis razonable, aún sin confirmar). Úsalo como **veto**, no como confirmación.

## Validación

`python -m whale_tracking.validate` reconstruye las posiciones de las wallets hora a hora desde sus fills y
mide si el sesgo anticipa retornos (IC de Spearman con p-valor por permutación, acierto, Sharpe, estabilidad
entre mitades). Resultado del 23-nov-2026 (45 wallets, 90 días, periodo bajista): **el nivel del sesgo no
predijo retornos**; solo el *cambio* a 24 h muestra un IC positivo en BTC, sin significancia estadística.
Detalle y salvedades en [`docs/validation-2026-11-23.md`](docs/validation-2026-11-23.md).

## Limitaciones conocidas

- Solo posiciones de la cuenta principal de cada wallet (no subcuentas ni vaults).
- La detección de market makers es heurística (volumen/cuenta del leaderboard, que no equivale a la equity perp).
- El valor de cuenta del leaderboard es mucho mayor que la equity perp en muchas wallets: la cesta se elige por riqueza total, no por capital en perps.
- La confianza del sesgo es una heurística sin calibrar; no está validada contra retornos.
- Los precios de liquidación del mapa pueden tener hasta ~5 min de retraso.
- El leaderboard no es "todas las ballenas": es un muestreo por tamaño y actividad.
- Los eventos en vivo dependen del WebSocket; si se corta, se resincroniza en la siguiente reconciliación.
- Sin persistencia de posiciones/eventos: al reiniciar se reconstruye desde la cadena.

## Hoja de ruta

- Validar el sesgo contra retornos futuros (IC de Spearman, como `basket_positioning.py` de hollowc2).
- Detectores de microestructura (ráfagas de trades grandes, barridos, icebergs) con libro L2 — ver `THIRD_PARTY_NOTICES.md`.
- Cliente Python mínimo para consumir `/api/signals` desde tus otras apps.
- Estilo de wallet por fills (swing vs scalper) y subcuentas.

Atribuciones de código de terceros en [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
