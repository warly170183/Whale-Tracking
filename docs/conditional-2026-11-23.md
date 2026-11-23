# Backtest condicional: entradas a favor de tendencia según alineación con las ballenas (23-nov-2026)

**Pregunta:** dado un trigger de tendencia que NO usa datos de ballenas, ¿rinden mejor las entradas cuando el
cambio del sesgo a 24 h las acompaña ("alineada") y peor cuando va en contra ("en contra")?

**Protocolo fijado antes de ver resultados** (detalle en `whale_tracking/conditional.py`): trigger EMA por
horizonte (4 h: 6/24 · 24 h: 24/72 · 72 h: 72/240, descartando |diferencia| < 0.3 %), un punto de decisión cada H horas
por moneda, la misma función de alineación que usa la app en vivo, test de permutación unilateral con etiquetas
barajadas dentro de cada moneda. Datos: 45 wallets, 90 días, BTC/ETH/SOL/HYPE. Reproducir: `python -m whale_tracking.conditional`.

| H | grupo | n | retorno medio | acierto |
|--:|---|--:|--:|--:|
| 4 h | alineada | 82 | +0.01 % | 54 % |
| 4 h | neutral | 1025 | -0.01 % | 48 % |
| 4 h | **en contra** | 76 | **-0.47 %** | **37 %** |
| 24 h | alineada | 14 | +0.16 % | 50 % |
| 24 h | neutral | 202 | +0.23 % | 53 % |
| 24 h | **en contra** | 17 | **-1.73 %** | **12 %** (2 de 17) |
| 72 h | alineada / neutral / en contra | 5 / 88 / 3 | — | muestras insuficientes |

| contraste (unilateral) | 4 h | 24 h |
|---|---|---|
| alineada − resto | +0.05 %, p = 0.48 | +0.08 %, p = 0.44 |
| alineada − en contra | +0.48 %, p = 0.013 | +1.89 %, p = 0.007 |

## Lectura honesta

1. **"Alineada" no añade nada.** Rinde igual que "neutral" (p ≈ 0.45). No hay evidencia para confiar más en una
   entrada, ni para subir tamaño, porque las ballenas la acompañen.
2. **"En contra" sí funciona como señal de evitar.** Las entradas a favor de tendencia que van contra el cambio de
   posicionamiento rindieron claramente peor. Es consistente: negativo en las dos mitades del periodo
   (4 h: -0.47 / -0.47; 24 h: -1.91 / -1.62), en 3 de 4 monedas a 4 h (SOL, con solo 6 casos, fue la excepción) y en
   las 4 a 24 h. Los casos no son un solo día repetido: 76 muestras en 35 días distintos (24 negativos) y 17 en
   15 días (13 negativos).
3. **Cautelas:** (a) con 6 contrastes, la corrección de Bonferroni exige p < 0.008: solo el de 24 h lo roza y el de 4 h
   no lo cumple; (b) "en contra" a 24 h son solo 17 casos; (c) las monedas están correlacionadas, así que la
   muestra efectiva es menor que n; (d) un único tramo bajista de 90 días, en el que la propia estrategia de
   tendencia rindió ≈ 0; (e) las wallets se eligen por su PnL actual (sesgo de supervivencia); (f) el sesgo pudo
   cambiar *porque* el precio ya se había movido, así que parte del efecto podría ser reversión a la media y no
   información de las ballenas.
4. **Conclusión práctica:** usar la alineación solo como **veto o reducción de tamaño** cuando sale "EN CONTRA", no
   como confirmación. Es una hipótesis razonable y todavía no demostrada: el diario de entradas está para confirmarla hacia
   delante, sin sesgo de supervivencia.
