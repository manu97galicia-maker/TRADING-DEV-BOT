# Estrategia actual

Estado a **19 de agosto de 2026**. Esto es lo que está corriendo en el servidor
ahora mismo, no lo que se planea.

---

## Resumen en una tabla

Tres bots en paralelo sobre **la misma wallet**. Lo único que los separa es
**qué compran**: el tamaño y la salida son idénticos en los tres.

```
                MC entrada   volumen    creador   últimas   edad    stop
DEFINITIVA      ≤ 2.800 $    ≥ 3 SOL     ≥10%        2      90 s    80%
ELITE           ≤ 2.800 $    ≥ 3 SOL     ≥25%        3      90 s    80%
RAPIDA          ≤ 2.800 $    ≥ 3 SOL     ≥ 1%        3       3 s    sin
```

Común a los tres: **≥4 carteras distintas**, **≥4 operaciones previas**, sin
mayhem, slippage 12%, `max_posiciones: 1` por bot, reserva 0,08 SOL.

---

## La compra

```
base                 0,15 SOL
crece un 35%         por cada SOL ganado de VERDAD
```

El bote es **común a los tres bots** — comparten wallet, así que comparten
caja — y solo cuenta P&L leído de la cadena.

```
   ganado        compra
   +0 SOL        0,150
   +1 SOL        0,203
   +2 SOL        0,255
   +5 SOL        0,413
  +10 SOL        0,675
```

**En negativo no baja.** El multiplicador se queda en 1 y se sigue con el
tamaño base. Reducir la apuesta tras una racha mala suena prudente, pero con la
mitad de tamaño hacen falta el doble de aciertos para recuperar: convierte un
bache en un agujero del que no se sale.

**Por qué el P&L real y no el del bot.** El teórico ha llegado a equivocarse de
signo — reportó **+229 SOL** en una operación que en la cadena dio **−0,02**.
Un bote alimentado con ese número subiría el tamaño justo cuando se pierde.

---

## La salida

```
TP1   al +50% de subida       vende el 50%   → el suelo sube al PRECIO DE COMPRA
MC     6.800 $                vende el 35%
MC    18.000 $                vende el 7,5%
MC    34.000 $                vende el 3,75%
      3,75% se queda dentro para siempre
```

Las fracciones van sobre la **compra original**, no sobre lo que queda. Los
escalones de market cap **solo se arman cuando ha saltado el TP1**, así que
nunca se adelantan.

**El trinquete es lo importante.** Al cobrar el TP1 el suelo sube al precio de
compra y lo que queda dentro ya no puede acabar en pérdida. Funciona incluso en
RAPIDA, que no lleva stop-loss configurado.

**Por qué +50% y no x2.** Al +40% llega el 79% de las monedas; a x2, el 57%.
Las configuraciones anteriores pedían x2, x3 o x5 y por eso casi ninguna
cobraba nada.

**Aviso sobre 18K y 34K.** Una moneda migra a los 30.867 $, así que el escalón
de 34.000 $ cae **después de la migración**: ya no se vende por la curva sino
por Jupiter. Las mayhem no se pueden vender por agregadores, y de las 308
monedas del análisis del 14-ago, 63 se quedaron sin vender por pool migrado —
el 58% de la pérdida. Ese escalón probablemente necesite rescate manual.

---

## Lo que está medido (19-ago, 288.984 evaluaciones)

### La latencia no es el problema

```
p25 0,3 s · mediana 1,6 s · p75 3,7 s
MC al disparar: mediana 2.599 $
```

Dispara a 1,6 segundos y a 2.599 $. Eso ya está donde hay que estar.

### El filtro del creador sí lo es

```
                cumplen   tumbadas por el dev   compras
DEFINITIVA        1.127            850  (75%)       40
ELITE               223            218  (98%)        3
RAPIDA              178             63  (35%)       15
```

**ELITE está muerta.** El 85,2% de las monedas con arranque limpio las lanza
alguien con **0 migraciones**; exigir historial deja fuera casi todo el flujo.

La prueba: **WIW**, la única ganadora del 18-ago, cumplió a los 2,1 s con MC
2.262 $ y 12 carteras distintas. Su creador llevaba 7% de migración. La
bloquearon DEFINITIVA (7% < 10%) y ELITE (7% < 25%); solo la cogió RAPIDA, y
cobró el +50%.

**Recomendación pendiente de decisión**: bajar DEFINITIVA a ≥1% y ELITE a ≥5%.

### El volumen y el market cap están acoplados

La curva es aritmética exacta:

```
MC 2.400 $ → caben 2,09 SOL netos
MC 2.800 $ → caben 4,67 SOL netos
MC 3.500 $ → caben 8,76 SOL netos
```

Y el volumen cuenta compras **y** ventas. Así que exigir 900 $ (12 SOL) con el
techo en 2.800 $ obligaba a que ya hubiera **3,67 SOL de ventas**: el filtro
seleccionaba monedas donde la gente ya estaba soltando. Por eso está en 3 SOL.

---

## Bugs arreglados esta sesión

**El programa de token** (18-ago). El 10% de las ventas — 37 de 373 — acababa
en "no quedan tokens que vender" sin haber vendido nada. La dirección de la
cuenta sale de wallet + mint + **programa**; con el programa equivocado no
existe, el saldo se lee como cero y la posición se cierra en falso. Ahora se
miran los dos programas y se **corrige el campo**, porque la venta construye
sus cuentas con ese mismo campo.

**El moonbag del stop-loss** (18-ago). Se calculaba solo con los escalones de
porcentaje, así que con un único tramo daba un moonbag del 50%: al saltar el
stop se dejaba media posición sin vender. Pasó con WIW: −0,0220 SOL cobrados y
0,0720 colgando en una moneda que ya había dado la vuelta. Ahora descuenta
también los escalones de market cap: 3,75% en vez de 50%.

---

## Lo que sigue roto

- **El P&L teórico miente, y de signo.** Solo es cosmético (lo que decide sale
  de los deltas de la wallet), pero no fiarse de lo que llegue por Telegram.
- **`venta_muerta` se mira una sola vez a los 120 s.**
- **Los tres bots no se coordinan** y pueden comprar la misma moneda.
- **`Custom 6024`**: curva vacía, no hay contrapartida a ningún precio.
- **El saldo no da para los tres.** Con 0,15 por posición hacen falta 0,53 SOL
  (0,45 + reserva) y hay menos: solo dos bots pueden tener posición a la vez.

---

## Operación

```bash
bash desplegar.sh              # sube y reinicia esperando a que no haya posiciones
bash desplegar.sh --solo-subir # sube sin reiniciar
bash desplegar.sh --ya         # reinicia abandonando posiciones (avisa de cuáles)
```

**Nunca reiniciar con una posición abierta.** El bot no las reanuda: detecta la
marca, avisa de "bolsa huérfana" y la deja para vender a mano. Deja de
vigilarse el take-profit, el stop y el anti-rug.

Los tres servicios van con `Restart=always`, `RestartSec=10`,
`StartLimitIntervalSec=0` y `enabled`: arrancan solos al reiniciar el servidor
y se levantan si se caen. **24/7 sin intervención.**

Servidor: `root@159.89.19.12`, clave en `~/.ssh/id_droplet_bot`. La clave
privada no se copia en ningún documento.
