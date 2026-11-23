# Avisos de terceros

Este proyecto adapta ideas y fragmentos de los siguientes repositorios, todos con licencia MIT.
Se reescribió el código (no es una copia literal) pero se conserva la atribución.

| Origen | Qué se adaptó | Dónde |
|---|---|---|
| [hollowc2/Hyperliquid](https://github.com/hollowc2/Hyperliquid) — `wallet_tracker/rank_sharpe.py` (© 2026 Corey) | Retornos por PnL descontando depósitos/retiros, Sharpe anualizado, drawdown | `whale_tracking/ranking.py` |
| [KonScanner/hyperliquid-trader-tracker](https://github.com/KonScanner/hyperliquid-trader-tracker) (© 2026 KonScanner) | Convención `users == [comprador, vendedor]`, libro de posiciones con guarda de época, dedupe por `tid`, bucle de reconexión | `whale_tracking/resolve.py`, `state.py`, `engine.py` |

Texto de la licencia MIT (aplica a ambos orígenes):

```
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## Descartados por licencia

`Niketion/flowdepth` y `uzillion/crypto-whale-watcher` son GPL-3.0: no se incorporó código suyo.
`azseza/smallfish_` (Apache-2.0) está pendiente: sus detectores de ráfagas, barridos e icebergs
requieren el libro L2 y se plantean como fase 2 (si se usa, añadir aquí su aviso NOTICE).
