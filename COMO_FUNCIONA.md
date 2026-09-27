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
nace ──────────────▶ 59,0% ──────────────────────────▶ migra
                       ▲
                  aquí compra
          (le faltan ~62 SOL, casi tres cuartos de curva)
```

Son pocas al día: la mayoría de las monedas mueren mucho antes de llegar. Que
compre poco **no significa que esté roto**.

---

## 2. Qué tiene que cumplir una moneda para que la compre

Todo esto, sin saltarse nada:

| | |
|---|---|
| **Punto de la curva** | entre el 59,0% y el 99,5% (pasado eso ya está migrando) |
| **Gente de verdad** | mínimo **9 carteras distintas** la han comprado |
| **Sin bundle** | como mucho 3 carteras en su bloque de nacimiento, y máximo 2 SOL |
| **Sin concentración** | los mayores tenedores no pueden tener más del 50% |
| **Sin vela verde** | si ha subido más de un **80% en 30 segundos**, no entra |

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
más lo que ya vale lo que hay metido en monedas.

Con 1 SOL de saldo salen así:

```
0,300 · 0,299     y se para
```

**Dos compras iguales.** No se para porque se acabe el dinero — se para porque
sólo hay **2 plazas**. Y ahí está lo que hay que entender de este número:

> **30% × 2 plazas = 60% de la cartera. El 40% que sobra no opera nunca.**

No es un fallo, es una elección. Ese dinero quieto no puede perderse y está ahí
si mañana quieres subir el tamaño o el número de plazas. Pero no lo confundas
con "estoy arriesgando el 30%": se arriesga el 30% *por operación*, y sólo el
60% del dinero opera.

Si quisieras usarla entera, las dos palancas son subir las plazas a 3 (tres
compras del 30% son el 90%), o subir otra vez este porcentaje. Ninguna está
tocada.

### Lo que se arriesga

Con el stop en el 30%:

| | una posición al stop | las dos a la vez |
|---|---|---|
| con el 50% | −15,0% de la cartera | −30,0% |
| con el 25% | −7,5% | −15,0% |
| **con el 30% (hoy)** | **−9,0%** | **−18,0%** |

Y como **no hay freno de rachas**, tres pérdidas seguidas son −24,6% y diez
son −61,3%. Nada para al bot por el camino.

> **La primera compra no es la mejor moneda del día: es la primera que pasa
> los filtros.** El bot no elige entre candidatas, compra la que llega. Eso no
> cambia con el tamaño.

### La comisión

La comisión es **fija** (unos 0,93 $ entre comprar y vender), así que cuanto
más pequeña la compra, más muerde:

| cartera | con el 25% | con el 30% |
|---|---|---|
| 0,50 SOL | 11,4 $ → 8,1% | 13,7 $ → **6,8%** |
| 0,83 SOL | 18,9 $ → 4,9% | 22,7 $ → 4,1% |
| 1,00 SOL | 22,8 $ → 4,1% | 27,4 $ → 3,4% |
| 2,00 SOL | 45,6 $ → 2,0% | 54,7 $ → 1,7% |

Subir el tamaño la diluye. Con cartera pequeña sigue doliendo, pero menos.

### Y un techo de 2 SOL por compra

Pase lo que pase, una compra **nunca pasa de 2 SOL**.

Con el 30%, ese techo no manda hasta que la cartera pasa de **6,67 SOL**:

| cartera | el 30% sería | se compra |
|---|---|---|
| 1 SOL | 0,30 | 0,30 |
| 3 SOL | 0,90 | 0,90 |
| 6,67 SOL | 2,00 | 2,00 |
| 20 SOL | 6,00 | **2,00** |

Está por dos motivos. Uno: si la cuenta crece, el porcentaje efectivo baja solo.
Y dos, el que importa: es el último freno contra un fallo de cálculo. El 12 de
agosto la cadena registró compras de 0,25 y 0,30 SOL con 0,15 configurado, y
nunca se supo por qué.

### Dos posiciones a la vez

Puede tener **2 posiciones abiertas** como mucho, y con el 30% por compra eso
es lo que decide cuánto dinero trabaja: el 60% de la cartera.

Antes el techo eran 5, pero era mentira: con el 50% por compra, la tercera y la
cuarta salían de 8 $ y 1,7 $, y la comisión se llevaba el 12% y el 54% de ellas
antes de que la moneda se moviera. Eso no es diversificar, es pagar por tener
una posición que no puede ganar nada.

Con el 30% las dos salen **iguales** (0,300 y 0,299), que es diversificación de
verdad: dos posiciones del mismo tamaño, no una grande y una a medias.

---

## 4. Cómo vende

Dos escaleras, una detrás de la otra.

### Por porcentaje: cuatro peldaños, con el peso en el x3

Lo que sube la moneda **desde el precio al que la compró el bot**.

| sube | vende | acumulado | hay que llegar al |
|---|---|---|---|
| +25% | 10% | 10% | 67,1% de la curva |
| +60% | 20% | 30% | 75,0% de la curva |
| +100% | 20% | 50% | 81,3% de la curva |
| **x3 (+200%)** | 35% | 85% | **91,2% de la curva** |

**El x3 sólo cabe porque la entrada bajó al 59%.** Comprando al 71% el x3 caía
al 98,2% de la curva — prácticamente la migración, o sea un peldaño que casi
nunca cobraría. Al 59% cae al 91,2%, con sitio de sobra. Bajar la curva y
enfocar el take profit en el x3 son **el mismo cambio**.

El +25% casi no cobra a propósito: su trabajo no es recoger dinero, es
**apagar el stop** (sube el suelo a compra +5%), y eso lo hace igual vendiendo
un 10% que un 20%.

#### Qué cambia respecto de ayer

| la moneda llega a | escalera de ayer | enfocada en el x3 |
|---|---|---|
| +25% | +9,0% | +7,0% |
| +60% | +41,5% | +36,4% |
| +100% | +64,2% | +68,5% |
| **x3** | +64,2% | **+121,0%** |

**Se cobra algo menos de las que se quedan a medias, y casi el doble de las
que llegan al x3.** Ayer una que hacía x3 cobraba lo mismo que una que llegaba
al +100%, porque no había peldaño arriba.

Y en aciertos necesarios para no perder dinero:

```
si sólo tocan el +25%     81% de aciertos
si tocan el +60%          45%
si tocan el +100%         30%
si llegan al x3           20%   ← ayer también pedía 32%
```

### Por market cap: el 15% que queda

Cuando se han cobrado **los cuatro** de arriba —o sea, al llegar al x3— se
arma la segunda escalera. Ésta no mira lo que ha subido, sino la cifra de
market cap que ves en la app:

| market cap | vende | acumulado |
|---|---|---|
| 50k $ | 5% | 90% |
| 60k $ | 4% | 94% |
| 70k $ | 3% | 97% |
| 80k $ | 3% | 100% |

Si llega a 80k, **la posición se cierra entera**. No queda nada corriendo.

> **Ojo con los 80k.** La curva de pump.fun topa en 410,9 **SOL** de market
> cap, no en una cifra de dólares — así que el techo en dólares se mueve con el
> precio de SOL. Con SOL a 220 $ la moneda migra sobre los 90k y los 80k caen
> dentro de la curva. **Con SOL por debajo de 195 $ los 80k quedan fuera**, y
> ese último escalón sólo se cobra si la moneda migra y sigue subiendo después.
> No está roto: si no se llega, ese 15% se queda dentro protegido por el suelo.

### El primer peldaño es el que importa, y no por lo que cobra

En cuanto toca el **+25%**, el suelo sube de −30% a **+5%** y la operación ya
no puede acabar en pérdida.

Ahora bien, vendiendo sólo un 20% ese rescate es más pequeño que antes:

| la moneda llega a | escalera de ayer | esta escalera |
|---|---|---|
| +24% | −35,0% | −30,0% |
| +25% | −35,0% | +9,0% |
| +45% | +20,0% | +9,0% |
| +59% | +20,0% | +9,0% |
| +60% | +39,7% | +41,5% |
| +99% | +39,7% | +41,5% |
| +100% | +39,7% | +64,2% |

**Se cobra menos de las medianas y mucho más de las que corren.** El tramo que
pierde es el de +30% a +59%, que es el que más ocurre. A cambio, una moneda que
dobla pasa de dejar un +39,7% a dejar un **+64,2%**.

Dicho de otra forma — cuántas tienen que salir bien para no perder dinero,
contando que una que sale mal cuesta el 30%:

```
si sólo tocan el +25%     hacen falta 77% de aciertos
si tocan el +60%          hacen falta 42% de aciertos
si tocan el +100%         hacen falta 32% de aciertos
```

Esta escalera **no se sostiene con monedas que sólo tocan el primer peldaño**.
Necesita que una parte llegue arriba. El +25% está ahí por el suelo que arma,
no por lo que cobra.

### Y el suelo que sube

Esto es lo más importante de todo el sistema:

```
al empezar         el suelo está en -30%
tras cobrar +25%   el suelo sube a   +5%     ← ya no puede acabar en pérdida
tras cobrar +60%   el suelo sube a  +31,25%
tras cobrar +100%  el suelo sube a  +68%
```

El suelo se pone un peldaño **por detrás** del que acabas de cobrar, y un 5%
por encima de ese. Un 5% *del precio*, no cinco puntos: por eso tras el +25%
queda en +31,25% y no en +30%.

Eso deja entre 19 y 22 puntos de aire entre cada suelo y el peldaño siguiente.
Ese hueco importa: si un suelo alcanzara al peldaño que lo armó, la posición se
cerraría sola en el mismo instante en que cumple su objetivo. Pasó en agosto.

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
