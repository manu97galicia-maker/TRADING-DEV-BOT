# ESTRATEGIA BANDA 3-4.2K — para revisar

Estado a 3-ago-2026. Fichero de configuración: `bots/bot-sniper-banda38.yaml`.

Cada número lleva **por qué está ahí** y, sobre todo, si está **medido** o es una
**suposición**. Lo que no esté medido, dilo y lo medimos antes de arrancar.

---

## 1. Qué compra

Tienen que cumplirse **todas** las condiciones. Se evalúan en este orden, y el
orden importa: primero lo gratis, al final lo que cuesta una llamada de red.

| # | Filtro | Valor | Estado |
|---|---|---|---|
| 1 | Nombre no repetido | 5 min | **tu decisión** |
| 2 | No mayhem | — | medido |
| 3 | Dev no vetado | 2 rugs | medido |
| 4 | Edad al comprar | < 20 s | **tu decisión** |
| 5 | SOL en curva | ≥ 7 | medido |
| 6 | Market cap | $3.000 – $4.200 | **tu decisión** |
| 7 | Volumen negociado | ≥ 28 SOL | medido, **sin estrenar** |
| 8 | Titulares (GMGN) | ≥ 8 | medido |
| 9 | Dev tiene | < 10% del supply | heredado |
| 10 | Ningún holder | > 15% del supply | heredado |

### El detalle de cada uno

**1. Nombre no repetido (5 min).** Si salió otra moneda con el mismo símbolo en
los últimos 5 minutos, se descarta. Es el patrón del que lanza veinte clones a
ver cuál prende. El 3-ago rechazó 5 de 44 monedas.

**4. Edad < 20 s.** Se mira a los 12 segundos y se exige que al comprar no haya
pasado de 20. Ojo: el bot NO tenía este filtro — `max_token_age` se comprueba al
detectarla, cuando tiene ~1 segundo, así que no acotaba nada. Se añadió
`max_buy_age`.

**5. Curva ≥ 7 SOL.** Descarta las que nacen vacías: el 3-ago tumbó 16 de 44.
**No es un filtro independiente del market cap** — en una bonding curve son la
misma magnitud. 7 SOL equivale justo al suelo de $3.000, así que en la práctica
manda la banda y esto solo quita las curvas muertas.

**6. Market cap $3.000 – $4.200.** Equivalencia medida el 3-ago:

```
MC $3.000  =   6,6 SOL en curva   (~$478)
MC $4.200  =  13,2 SOL            (~$950)
```

**7. Volumen ≥ 28 SOL (~$2.016).** Lo pediste tres veces y hasta hoy te decía que
no se podía medir. Era cierto para las APIs (ni GMGN ni pump.fun lo dan a los 20
segundos), pero **el bot ya recibía el dato y lo tiraba**: está suscrito a todos
los logs de pump.fun y cada operación emite un `TradeEvent` con su `sol_amount`.
Ahora los cuenta (`src/monitoring/volumen.py`).

Sale de la cadena, no de un indexador: es el único dato de dinero que nadie
puede falsear desde fuera. Y cuesta **cero llamadas de red**.

Medido sobre 141 monedas nuevas a los 20 segundos exactos:

```
mediana   10,62 SOL
media     22,65
máximo   192,78

umbral 10 SOL →  51,1% pasan
umbral 20 SOL →  31,2%
umbral 28 SOL →  25,5%   ← el actual
umbral 40 SOL →  17,7%
```

**8. Titulares ≥ 8.** Empezó en 25 (lo que pediste), bajó a 20, a 12 y a 8. La
razón está en la sección 4: los resultados dicen que no predice nada.

---

## 2. Cuánto sale a cuenta: el cruce de filtros

Aquí está el riesgo real de esta configuración, y es donde me equivoqué dos
veces hoy. Sobre 168 monedas medidas a los 20 segundos:

```
 14  en la banda $3.000–$4.200      ( 8,3%)
 23  con volumen ≥ 28 SOL           (13,7%)
  3  LAS DOS COSAS A LA VEZ         ( 1,8%)
```

A ~1.700 monedas por hora, ese 1,8% son unas **30 candidatas por hora** antes de
aplicar nombre, titulares y dev. Estrecho pero viable.

**Por qué esto casi sale mal dos veces.** Puse curva ≥12 SOL con techo de $4.500,
y luego volumen ≥$2.000 con techo de $3.500. En ambos casos los dos límites
medían lo mismo y se anulaban: el bot no habría comprado NUNCA, sin dar un solo
error. El volumen se salva porque suma compras **y** ventas, así que no está
atado al market cap como sí lo está la curva.

---

## 3. Cuándo vende

```
+50%    vende la mitad
+125%   vende un cuarto
resto   trailing, sin objetivo fijo
stop    −35%
tiempo  10 minutos máximo
```

El tercer tramo se dejó sin objetivo fijo tras medirlo el 3-ago sobre seis
ganadoras: el objetivo de $14.000 solo lo alcanzó 1 de 6, y el 25-50% restante
acababa saliendo al stop porque éste se mide contra la ENTRADA y nunca se mueve.

```
stop -35% desde la entrada        +0,4752 SOL
stop a break-even tras el 1er TP  +0,5394
trailing al 80% del máximo        +0,6083   ← el mejor
```

---

## 4. Lo que los resultados reales dicen que NO funciona

Esto es lo más importante del documento. El 3-ago se operó en real con 0,573 SOL
y se perdió **el 100%**. De doce operaciones:

```
3  invendibles      (0 SOL recuperados de 0,45 invertidos)
5  cerradas por el otro bot del droplet
1  gestionada entera por nosotros (H3) — la única que salió bien
resto al stop-loss
```

**Los titulares no separan ganadoras de perdedoras:**

| moneda | titulares | resultado |
|---|---|---|
| Community | 15 | ganadora |
| WUBZY | 21 | stop |
| Edworm | 19 | stop |
| mask | 12 | stop |
| UPPAPA | 54 | **invendible** |
| Patch | 35 | ni destacó |

La ganadora tuvo menos titulares que las tres perdedoras, y la de 54 —la mejor
sobre el papel— no se pudo vender.

**Y lo que de verdad decide, medido en julio y confirmado en agosto:** solo el
4-6% de estas monedas tiene un pool con SOL suficiente para pagarte la salida.
Ningún filtro de entrada ni configuración de TP/SL arregla eso.

La hipótesis del filtro de volumen es que ataca justo ese problema —mide gente
operando ahora, no cuánto vale la moneda— pero **es una hipótesis, no un hecho.
Nunca se ha ejecutado.**

---

## 5. Dinero y protecciones

```
posición          0,15 SOL
reserva           0,05 SOL (intocable)
slippage          15% comprar · 90% vender
comisión prior.   20M µlamports  (~0,0017 SOL por transacción)
```

**No bajar de 0,15 por operación.** Las comisiones fijas (~0,0012 SOL por
transacción) se comen el 18,9% de una posición de 0,05 y solo el 4,8% de una de
0,15.

**La reserva de 0,05 no es negociable.** Sin gasolina, una posición abierta no se
puede cerrar y se pierde entera. Y si se toca aquí, hay que tocar también el
umbral de `esperar_fondos.py` — el 3-ago se me quedaron descuadrados y el bot
estuvo vivo y mudo sin comprar.

### Contra los gastos en intentos fallidos

El 1-ago se perdieron 0,642 SOL en 178 ventas fallidas. Tres defensas:

1. **Leer el saldo real** antes de vender y recortar a él. Nunca vender la cifra
   de la contabilidad interna.
2. **`skip_preflight=False`** en compra y venta. Una transacción que iba a fallar
   se rechaza antes de aterrizar y cuesta **0**. Verificado el 3-ago: una compra
   con blockhash caducado costó cero.
3. **Rendirse a los 3 fallos** seguidos.

---

## 6. Lo que sigue sin resolver

**El bot del droplet.** `159.89.19.12`, `/root/ai-bot`, con la MISMA clave
privada. No compite por el saldo: **ve los tokens en el monedero compartido y los
vende con sus reglas**. Cerró 5 de las 8 posiciones del 3-ago, incluida la única
ganadora justo cuando tocaba el +50%.

Y hace que los números del log dejen de ser ciertos: anotó "+0,1763 SOL" en una
operación en la que se ganaron ~0,035. Error de factor cinco.

```
ssh root@159.89.19.12
pkill -f bot_runner
```

**Mientras siga encendido, ninguna medición vale.**

**El límite de peticiones.** 240 errores 429 el 3-ago, algunos por consultar la
cadena mientras el bot operaba. Cada consulta que haces es una que él no puede
hacer para vender a tiempo. Mirar el log, no la cadena.

**Confirmaciones lentas.** Una compra se dio por fallida y **había aterrizado**.
El bot no le monta la salida a una posición que cree que no tiene: se queda sin
stop-loss. Pasó una vez y no costó dinero, pero está sin arreglar.

---

## 7. Mi recomendación

**Probarlo en papel antes de meter un euro.** No cuesta nada y responde las dos
preguntas que hoy no hemos contestado con datos limpios:

1. ¿Cuántas monedas pasan los filtros completos por hora?
2. **De las que pasan, ¿cuántas se pueden vender?**

La segunda es la única que importa. Todo lo demás son ajustes sobre un problema
que no sabemos si está resuelto.
