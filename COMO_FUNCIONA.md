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
nace ───────────────────────────────▶ 90,0% ──────▶ migra
                                        ▲
                                   aquí compra
                               (le faltan ~25 SOL)
```

Son pocas al día: la mayoría de las monedas mueren mucho antes de llegar. Que
compre poco **no significa que esté roto**.

---

## 2. Qué tiene que cumplir una moneda para que la compre

Todo esto, sin saltarse nada:

| | |
|---|---|
| **Punto de la curva** | entre el 90,0% y el 99,5% (pasado eso ya está migrando) |
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

**El 30% de la cartera.** Cartera = todo lo que tienes, el SOL de la wallet
más lo que ya vale lo que hay metido en monedas. Esa base **no baja al
comprar** (el dinero cambia de sitio, no desaparece), así que las compras
salen **iguales**.

Con 1 SOL de saldo salen así:

```
0,300 · 0,299 · 0,298 · 0,048     (suma 0,945 = el 95% del saldo)
```

**Solo se abren cuatro, no cinco** — y de esas, tres son compras de verdad y
la cuarta es calderilla. No es un fallo: tres del 30% ya son el 90% del
dinero, y para la quinta no queda nada. El tope de 5 posiciones sigue escrito
en la configuración, pero **el dinero no llega hasta él**.

> **Aquí no hay freno automático.** Antes cada compra era un porcentaje de lo
> que *sobraba* y menguaban solas, así que nunca se comprometía todo. Ahora lo
> único que para al bot es quedarse sin saldo. Con el stop en el 26%, las
> posiciones juntas ponen en riesgo un **25% de tu dinero**.

Subir del 25% al 30% **no arriesga más dinero** — el 95% se compromete igual,
porque el límite lo pone el saldo, no el porcentaje. Lo que cambia es el
reparto: **el mismo dinero en menos apuestas y más gordas**, así que un solo
rug duele más y la suerte pesa más en el resultado del día.

Puede tener **5 posiciones abiertas a la vez**.

---

## 4. Cómo vende

Dos escaleras, una detrás de la otra.

### Por porcentaje de subida

| sube | vende | acumulado | market cap aprox. |
|---|---|---|---|
| +18% | 10% | 10% | 65k $ |
| +38% | 15% | 25% | 76k $ |
| +55% | 15% | 40% | 85k $ |

**Antes de migrar solo se vende el 25%**, y es a propósito. Hasta el 24 de
septiembre se vendía el 60% ahí mismo: las monedas que iban a dispararse se
frenaban justo antes de empezar.

El primer peldaño es el más importante y **no por lo que cobra**. En cuanto se
toca el +18%, el suelo sube a +5% y la operación ya no puede acabar en
pérdida. Cuanto más bajo, más monedas llegan a activar esa protección.

**Entrando al 90,0% caben los tres.** Quedan +64,7% hasta migrar y el último
está en +55%, así que entra con 9,6 puntos de margen.

### Hasta dónde puede subir la entrada

Esta es la tabla más importante de la nota, y hay que mirarla **antes** de
tocar el punto de entrada. Cada peldaño deja de caber a partir de cierta
altura de curva:

| peldaño | deja de caber por encima de | vende |
|---|---|---|
| +18% | 96,96% | 10% |
| +38% | 93,83% | 15% |
| **+55%** | **91,34%** ← el acantilado | **15%** |

Se compra al **90,0%**, o sea **1,34 puntos por debajo del acantilado**.

> **Lo importante de esta tabla:** entre el 85% y el 91,34% caben los tres
> peldaños y el resultado es **idéntico** — lo único que cambia es cuántas
> monedas entran. Pasado el 91,34% el sistema se rompe a escalones:
>
> | dónde se compra | dentro de la curva | migra y muere | aciertos |
> |---|---|---|---|
> | 85% – 91,3% | los tres | +42,7% | **38%** |
> | 91,4% – 93,8% | dos | +25,4% | 51% |
> | por encima de 93,8% | uno | +6,3% | **80%** |
>
> Por eso se compra al 90% y no al 91%: **el mismo resultado, con margen.**

### Y a partir de ahí, cada venta un 35% más cara que la anterior

No son cifras fijas: cada escalón se calcula **sobre el precio del escalón
anterior**, no sobre lo que se pagó. Así todos piden el mismo esfuerzo.

| escalón | market cap | precio | vende | acumulado |
|---|---|---|---|---|
| 1 | **150k $** | x2,73 | 20% | 60% |
| 2 | **200k $** | x3,64 | 17% | 77% |
| 3 | 300k $ | x5,47 | 12% | 89% |
| 4 | 400k $ | x7,29 | 8% | 97% |

**Aquí es donde está ahora el grueso de la venta.** Más de la mitad de la
posición se cobra entre 150k y 400k, en vez de antes de migrar.

> En el fichero estos escalones están escritos como **múltiplos** del precio
> de compra, porque es lo único que el bot sabe mirar. Y el múltiplo que
> corresponde a "150k" **depende de dónde se entre**: al 90% se compra sobre
> 54,9k y 150k es un x2,73; al 93,5% se compraría sobre 64,5k y 150k sería un
> x2,33. Si se mueve la entrada hay que rehacerlos — hay un test que lo
> comprueba y salta solo.

Queda un **3% (el "moonbag")** que el bot **no vende nunca**. Ese lo vende una
persona a mano, si quiere.

> Los porcentajes son sobre la **compra original**, no sobre lo que queda
> dentro. Es la confusión que más caro puede salir aquí: "vender el 30%"
> significa el 30% de todo lo que se compró.

### Y el suelo que sube

Esto es lo más importante de todo el sistema:

```
al empezar        el suelo está en -26%      ← si baja de ahí, se vende TODO
tras cobrar +18%  el suelo sube a  +5%       ← ya no puede acabar en pérdida
tras cobrar +38%  el suelo sube a +23,9%
tras cobrar +55%  el suelo sube a +44,9%
tras cobrar 150k  el suelo sube a +62,9%
tras cobrar 200k  el suelo sube a +186,9%
```

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
