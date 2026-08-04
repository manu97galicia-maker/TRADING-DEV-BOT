# TÍO, HAZ ESTO CON ALGORITMO

Bro, léete esto antes de tocar nada. Es lo que aprendí el 3 de agosto de 2026
poniendo el bot en real con dinero de verdad, y me costó un día entero y varios
sustos.

El resumen es el título: **haz esto con algoritmo, no a ojo.** Todo lo que en
este juego "parece" verdad es mentira hasta que lo mides.

---

## 1. La regla madre: si no lo has medido, no lo sabes

Yo tenía una teoría preciosa. Comprar monedas de menos de 20 segundos, con el
market cap entre $3.000 y $4.500, con al menos 25 titulares. Sonaba perfecto.

Lo medí y **25 titulares no existían** en esas monedas. La mediana era 3. El bot
habría estado encendido toda la noche sin comprar ni una sola vez, y yo pensando
que "no había oportunidades".

Y al revés: yo estaba convencido de que más titulares = mejor moneda. Los
resultados reales del día:

| moneda | titulares | resultado |
|---|---|---|
| Community | 15 | **ganadora** |
| WUBZY | 21 | stop-loss |
| Edworm | 19 | stop-loss |
| mask | 12 | stop-loss |
| Patch | 35 | ni destacó |

**La única que ganó tenía menos titulares que las tres que perdieron.** Mi
intuición estaba exactamente del revés. Si no llego a medirlo, sigo apretando el
filtro en la dirección equivocada y me quedo sin comprar nada.

---

## 2. Los precios que ves son mentira si no hay liquidez

Esto es lo más caro que he aprendido, y me lo enseñó una moneda llamada TNOS:
marcaba **27 millones de market cap con 0,198 SOL en el pool**.

Podías "ganar" un x1000 en la pantalla y no poder sacar 20 euros.

**El 85% de las monedas que "suben" tienen la curva seca.** Cualquier backtest
que no compruebe el SOL real del pool antes de contar un acierto está
inventándose los resultados. Los míos lo hacían, y por eso durante un día entero
creí que tenía una estrategia ganadora.

Regla: **antes de contar una venta como buena, comprueba que la curva tenía SOL
para pagarla.**

---

## 3. Las comisiones se comen las posiciones pequeñas

Medido en cadena, no sacado de un blog:

- ~2,25% variable por operación (de ida **y** de vuelta)
- ~0,0012 SOL **fijos** por transacción, ganes o pierdas

Lo fijo es lo que mata. Con posiciones de 0,05 SOL se te va el **18,9%** solo en
comisiones. Con 0,15 baja al 4,8%.

**No bajes de 0,15 SOL por operación.** Por debajo, la estrategia puede ser
buena y aun así perder dinero.

---

## 4. Una transacción que falla te cobra igual

El 1 de agosto perdí **0,642 SOL en 178 ventas fallidas**. El bot intentaba
vender una cantidad que ya no tenía, la red lo rechazaba, y volvía a intentarlo.
Cada intento pagaba comisión de prioridad.

Tres defensas, y las tres hacen falta:

1. **Leer el saldo REAL de la cartera** antes de vender, y recortar a eso. Nunca
   vendas la cifra de tu contabilidad interna.
2. **`skip_preflight=False`.** Con el preflight activo, una transacción que iba a
   fallar se rechaza antes de aterrizar y **cuesta 0**. Sin él, pagas igual.
3. **Rendirse a los 3 fallos seguidos.** Si va a fallar, va a seguir fallando.

Lo del preflight cuesta ~100 milisegundos. Solo importa si compites por el primer
bloque. Si tu estrategia espera unos segundos antes de mirar la moneda, no
compites con nadie: actívalo siempre.

---

## 5. `finalized` te miente sobre el presente

Esto me tuvo el bot "vivo" y sin comprar nada, sin dar un solo error.

La librería de Solana lee por defecto en nivel **`finalized`**, que va **13-30
segundos por detrás**. Si lees la curva de una moneda de 12 segundos, te dice
*"esa cuenta no existe"*. Falló **40 de 44 veces**.

**Usa `commitment="processed"`** para leer curvas y precios de monedas recién
nacidas. Y en el monitor de salida también: con 30 segundos de retraso, tu
stop-loss del 35% se ejecuta en un precio que no tiene nada que ver con el que
marca tu regla.

---

## 6. Un solo bot por cartera. UNO.

Yo tenía uno corriendo en un servidor desde julio y otro nuevo en el PC, **con la
misma clave privada**. No es que compitan por el saldo — es que **cada uno ve los
tokens del otro en el monedero y los vende con sus propias reglas**.

Ese día el bot del servidor cerró 3 de mis 5 posiciones, incluida la única
ganadora, justo cuando había tocado el +50%.

Y lo peor: **los números del log dejan de ser ciertos.** Mi bot anotó "+0,1763
SOL" en una operación en la que de verdad gané ~0,035. Error de un factor de
cinco, porque contaba tokens que el otro bot ya había vendido.

Si ves esto en el log, tienes dos bots peleándose:

```
no quedan tokens que vender; la posicion ya estaba cerrada
```

---

## 7. Mide contra la cartera, nunca contra el log

Consecuencia de todo lo anterior. La contabilidad interna del bot se equivoca:
cuenta moonbags que ya no existen, da por buenas ventas que no hizo, y da por
fallidas compras que sí salieron (el RPC tardó en confirmar y el bot se rindió,
pero la transacción había aterrizado).

**El saldo de la cartera es la única verdad.** Todo lo demás es una estimación
del bot sobre sí mismo.

---

## 8. Cuidado con robarle internet a tu propio bot

Estuve consultando el saldo y auditando transacciones mientras el bot operaba.
Los dos contra el mismo endpoint de Helius. Resultado: **240 errores 429** y una
compra perdida por rate limit.

Si vas a mirar cosas, mira el log. Cada consulta que haces a la cadena es una que
tu bot no puede hacer para vender a tiempo.

---

## 9. Lo que de verdad decide

Después de un día ajustando titulares, curva mínima, banda de market cap, stop y
take-profit, la conclusión honesta:

**Todos esos filtros son secundarios.** Lo que manda es que solo el 4-6% de estas
monedas tiene un pool con SOL de verdad. Ningún ajuste de TP/SL arregla comprar
cosas que no se pueden vender.

Y una muestra de 5 operaciones no dice nada. Esa misma tarde el bot estuvo un
76% abajo y luego cerró el día en verde. Eso es varianza, no una señal.

**Si vas a sacar conclusiones, saca 30 operaciones limpias.** Con un solo bot en
la cartera, midiendo contra el saldo real, y comprobando la liquidez.

---

## Y por eso: hazlo con algoritmo

No mires una moneda y pienses "esta tiene buena pinta". Escribe la regla, mídela
contra datos reales, y deja que el número te diga si tu intuición valía algo.

La mía valía menos de lo que pensaba, y saberlo me ahorró la mitad del depósito.

— Manu
