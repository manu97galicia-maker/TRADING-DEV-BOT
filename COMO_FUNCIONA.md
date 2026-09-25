# Bot Definitivo — cómo funciona

Nota para quien llega nuevo. No hace falta saber programar para entenderla.

Esto es un bot que compra y vende monedas de **pump.fun** en la red Solana,
solo. Corre en un servidor encendido las 24 horas (un droplet de DigitalOcean)
y opera con **dinero real**.

---

## 1. La idea en una frase

Una moneda de pump.fun nace con una "curva": hay que meterle unos **85 SOL**
para que se llene. Cuando se llena, la moneda **migra** a un mercado mayor y
ahí es cuando entra la gente que no estaba mirando pump.fun.

El bot **no compra monedas recién nacidas**. Espera a las que ya han recorrido
casi toda la curva y están cerca de conseguirlo, porque para llegar ahí han
tenido que pasar por mucha gente.

```
nace ─────────────────────────▶ 71,0% ───────────────▶ migra
                                  ▲
                             aquí compra
                     (le faltan ~52 SOL, dos tercios de curva)
```

Son pocas al día: la mayoría de las monedas mueren mucho antes de llegar. Que
compre poco **no significa que esté roto**.

---

## 2. Qué tiene que cumplir una moneda para que la compre

Todo esto, sin saltarse nada:

| | |
|---|---|
| **Punto de la curva** | entre el 71,0% y el 99,5% (pasado eso ya está migrando) |
| **Gente de verdad** | mínimo **30 carteras distintas** la han comprado |
| **Sin bundle** | como mucho 3 carteras en su bloque de nacimiento, y máximo 2 SOL |
| **Sin concentración** | los mayores tenedores no pueden tener más del 50% |
| **Sin vela verde** | si ha **doblado de precio en 10 segundos**, no entra |

Los dos últimos son los importantes, y están ahí por algo que pasó:

> El 24 de septiembre el bot compró una moneda con **cinco tenedores** justo en
> la punta de una vela vertical. Cuarenta segundos después valía un 92% menos y
> se llevó 45 $. Era un *pump and dump* de manual.

La protección de la vela verde **ya existía**, pero estaba guardada dentro de
una estrategia que se había apagado, así que no se aplicaba. Ahora vale para
todas las entradas, venga de donde venga la moneda.

---

## 3. Cuánto pone en cada compra

**El 80% de la cartera.** Cartera = todo lo que tienes, el SOL de la wallet
más lo que ya vale lo que hay metido en monedas.

Con 1 SOL de saldo salen así:

```
0,800 · 0,125 · 0,029     (suma 0,954 = el 95% del saldo)
```

**Una compra de verdad y dos restos.** La primera se lleva ocho décimas de
todo tu dinero; la segunda, lo que quedaba libre; la tercera son 6,4 $ de los
que 1,12 $ se van en comisión — el 18%.

> **La primera compra se lleva el 80% de tu patrimonio, y no es la mejor
> moneda del día: es la primera que pasa los filtros.** El bot no elige entre
> candidatas, compra la que llega. Con el stop en el 35%, esa sola posición
> arriesga el **28% de todo tu dinero**.

Subir el porcentaje **no arriesga más dinero en total** — desde el 25% para
arriba se compromete siempre el mismo 95%, porque el límite lo pone el saldo,
no el porcentaje. Lo que cambia es que todo se juega en una sola carta.

Con un 32% de aciertos necesarios y el 70% en una moneda, **el resultado del
mes lo decide un puñado de operaciones**. La suerte pesa más que la
estrategia.

Si lo que se busca es concentrar, la forma limpia es bajar el número de
posiciones a 1 o 2: el dinero va entero a una o dos compras y no se pagan
comisiones por restos de 10 $.

### Y un techo de 2 SOL por compra

Pase lo que pase, una compra **nunca pasa de 2 SOL**.

Con la cartera de hoy eso no cambia nada — el 70% de 1 SOL son 0,70. El techo
empieza a mandar cuando la cartera pasa de **2,86 SOL**:

| cartera | el 70% sería | se compra |
|---|---|---|
| 1 SOL | 0,70 | 0,70 |
| 2 SOL | 1,40 | 1,40 |
| 3 SOL | 2,10 | **2,00** |
| 10 SOL | 7,00 | **2,00** |

Está por dos motivos. Uno: si la cuenta crece, el porcentaje efectivo baja
solo (2 de 10 es un 20%). Y dos, el que importa: es el último freno contra un
fallo de cálculo. El 12 de agosto la cadena registró compras de 0,25 y 0,30
SOL con 0,15 configurado, y nunca se supo por qué.

Puede tener **5 posiciones abiertas a la vez**.

---

## 4. Cómo vende

Dos escaleras, una detrás de la otra.

### Un solo objetivo

| sube | vende | market cap aprox. |
|---|---|---|
| +55% | 100% | 85k $ |

Eso es todo. No hay escalones, no hay moonbag: o la moneda llega al +55% y se
vende entera, o no llega.

**Lo que de verdad decide si se cobra no es el +55%, sino a qué punto de la
curva equivale.** La curva acelera, así que el mismo +55% queda más lejos
cuanto más arriba se compra:

| se compra al | para cobrar hay que llegar al |
|---|---|
| **71%** | **83,7% de curva** |
| 79% | 90,1% |
| 84% | 94,1% |
| 90% | 98,9% ← casi migrar |

Por eso se compra abajo: el objetivo pasa de pedir *casi migrar* a pedir un
83,7%, por donde pasan muchas más monedas.

> **Ojo con el nombre.** Esta estrategia se llamaba "la curva casi llena" y
> nació comprando al 96%. Al 71% le faltan **52 SOL para migrar — dos
> tercios de la curva**. Ya no son monedas a punto de conseguirlo: son
> monedas que apenas han empezado. La premisa original ("si ha llegado hasta
> aquí es que ha pasado por mucha gente") ya casi no se sostiene, y los
> filtros que quedan están escritos para monedas avanzadas.

### Lo que cuesta tener un solo objetivo

Antes había un peldaño pequeño al +18%. **No estaba para cobrar: estaba para
apagar el stop.** Al tocarlo, el suelo subía a +5% y la operación ya no podía
acabar en pérdida.

Sin él, todo lo que no llegue al objetivo cuesta el stop entero:

| la moneda llega a | con la escalera de antes | con un solo TP |
|---|---|---|
| +20% | +6,3% | **−35,0%** |
| +30% | +6,3% | **−35,0%** |
| +45% | +25,4% | **−35,0%** |
| +54% | +25,4% | **−35,0%** |
| +55% | +42,7% | +55,1% |
| x2,73 | +75,6% | +55,1% |
| x7,29 | +213,5% | +55,1% |

> Está elegido así a propósito, con esa tabla delante. El razonamiento fue:
> *buscamos proyectos con mucha presión compradora y que se disparen*, así que
> la mayoría debería llegar al objetivo y los finales intermedios importan
> poco. **Esa es la apuesta, y todavía no está medida.**

**Hacen falta 39 aciertos de cada 100** para no perder dinero.

> El stop está en −35%. Cuanto más ancho, más aciertos hacen falta — pero
> también te sales de menos sustos normales. Con el 70% en una sola moneda,
> **tres pérdidas seguidas se llevan la mitad de la cuenta**, y no hay ningún
> freno que las pare.

### Lo que se ahorra

Cada venta paga 0,046 $ de comisión. Siete ventas eran 0,32 $ y una es
0,046 $: **se ahorran 0,28 $ por posición**, el 0,25% de una posición de
110 $. La comisión de *comprar* —1,12 $— no cambia.

### Y el suelo que sube

Esto es lo más importante de todo el sistema:

```
al empezar        el suelo está en -35%
si llega al +55%  se vende TODO y se acabó
```

**Ya no hay suelo que suba.** Con un único objetivo no hay nada que proteger
a mitad de camino: mientras la posición está abierta, lo único que la cierra
es el stop en −35% o el objetivo en +55%.

Si algún día quieres recuperar esa protección sin volver a la escalera
entera, basta **una venta más**: un peldaño al +18% que venda el 5%. Cuesta
0,046 $ y convierte ese −35% en +5,6% en todo el tramo del +18% al +54%.

El suelo se pone un peldaño por detrás del que acabas de cobrar, y un 5% por
encima de ese. Un 5% *del precio*, no cinco puntos: por eso tras el +38% queda
en +23,9% y no en +23%.

**En cuanto toca el primer peldaño, la operación ya no puede perder dinero.**
Ese es el verdadero trabajo del primer escalón — lo que cobra es secundario.

### La salida de emergencia

Aparte, hay un **anti-rug**: si en 4 segundos se venden 12 SOL o más y las
ventas triplican a las compras, sale de la posición entera sin esperar al
stop. Es para los volcados, no para los respiros normales.

---

## 5. Lo que cuesta operar

```
comisión al comprar            0,005 SOL   (rápida, para entrar antes)
comisión al vender (normal)    0,00012 SOL (los take profit pueden esperar)
comisión al vender (urgente)   0,005 SOL   (stop y anti-rug: no pueden esperar)
```

En Solana el **slippage** no es una preferencia, es una condición escrita en
la transacción: si el precio se pasa de ahí, **la venta no se ejecuta**. Por
eso las ventas que protegen llevan un margen del 50% — ahí no se negocia el
precio, se sale.

---

## 6. Los comandos, desde el móvil

Todos se escriben desde cualquier carpeta, por SSH:

```
bot                     ¿está encendido? ¿está comprando? ¿tiene el código al día?
porque                  por qué NO está comprando (el embudo entero)
porque_cerro <mint>     la vida entera de una posición: por qué se cerró
cuanto_compra           de cuánto están siendo las compras y si cuadran
cazador ver             cuántas monedas lleva apuntadas para medir
```

Para arrancar o traer cambios nuevos, **una sola línea**:

```
cd /root/ai-bot && bash deploy/arranca.sh
```

Después, `bot`. Tiene que decir `compras: ACTIVAS`, el código `al dia`, y
**una sola** línea de fuente (`curva96`). Si salen dos, el código nuevo no
entró.

---

## 7. Lo que hay que saber antes de fiarse

**No hay ni una medición todavía.** Toda la configuración actual sale de
aritmética, no de resultados reales. Los números pueden estar bien razonados y
aun así no funcionar.

Lo que hay que hacer es dejarlo correr **20 o 30 operaciones sin tocar nada** y
después:

```
python3 deploy/optimizar_escalera.py
```

Eso prueba todas las escaleras posibles contra lo que de verdad hicieron las
monedas y dice cuál habría dejado más. Esa respuesta vale más que cualquier
cálculo teórico.

**Cuidado con cambiar cosas a menudo.** Si se tocan tres parámetros al día,
cada operación se hace con una configuración distinta y ninguna enseña nada:
si se gana no se sabe por qué, y si se pierde tampoco.

**Dos cosas que mandan más que la estrategia:**

1. **Que llegue la cadena.** Si el nodo (Helius) se queda sin créditos, el bot
   no ve ninguna moneda y no compra — y eso se parece mucho a "los filtros son
   muy exigentes". `bot` lo avisa con un cartel bien grande.
2. **Que la venta se ejecute.** En un desplome el precio puede saltarse el
   suelo entre dos miradas. Ninguna configuración gana dinero si las salidas
   fallan.

**La clave privada de la cartera** vive solo en el fichero `.env` del servidor.
No se pega en ningún chat, ni se manda por Telegram, ni se sube a GitHub. La
dirección pública sí se puede compartir: no es un secreto.
