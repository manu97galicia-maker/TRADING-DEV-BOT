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
| **Gente de verdad** | mínimo **14 carteras distintas** la han comprado |
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

**El 50% de la cartera.** Cartera = todo lo que tienes, el SOL de la wallet
más lo que ya vale lo que hay metido en monedas.

Con 1 SOL de saldo salen así:

```
0,500 · 0,350 · 0,085 · 0,019     (suma 0,955 = el 95% del saldo)
```

**Una compra de verdad y tres restos.** La primera se lleva la mitad de todo
tu dinero; la segunda, lo que quedaba libre; las otras dos son calderilla — la
última son 4,2 $ de los que 1,12 $ se van en comisión, el 27%.

> **La primera compra se lleva medio patrimonio, y no es la mejor moneda del
> día: es la primera que pasa los filtros.** El bot no elige entre
> candidatas, compra la que llega. Con el stop en el 35%, esa sola posición
> arriesga el **17,5% de todo tu dinero**.

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

Puede tener **2 posiciones abiertas a la vez**, y con el 50% de la cartera por
compra eso es justo lo que el dinero da de sí: dos compras enteras y se para.

Antes el techo eran 5, pero era mentira: la tercera y la cuarta salían de 8 $ y
1,7 $, y la comisión de prioridad se llevaba el 12% y el 54% de ellas antes de
que la moneda se moviera. Eso no es diversificar, es pagar por tener una
posición que no puede ganar nada.

Lo que cuesta: todo el dinero está en dos monedas. Si una se va al stop, son
17,5% de la cartera; las dos a la vez, el 35%.

---

## 4. Cómo vende

Dos escaleras, una detrás de la otra.

### Por porcentaje: tres peldaños

Lo que sube la moneda **desde el precio al que la compró el bot**.

| sube | vende | acumulado | hay que llegar al |
|---|---|---|---|
| +25% | 20% | 20% | 77,8% de la curva |
| +60% | 40% | 60% | 84,5% de la curva |
| +100% | 25% | 85% | 89,8% de la curva |

**Los tres caben dentro de la curva, y esto es nuevo.** Comprando al 71% queda
un +232% hasta que la moneda migra, así que hasta el tercero entra con diez
puntos de sobra. En la versión de ayer el tercer peldaño se quedaba *fuera*:
sólo cobraba si la moneda migraba y además seguía subiendo en otro mercado.

### Por market cap: el 15% que queda

Cuando se han cobrado **los tres** de arriba, se arma la segunda escalera. Ésta
no mira lo que ha subido, sino la cifra de market cap que ves en la app:

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

En cuanto toca el **+25%**, el suelo sube de −35% a **+5%** y la operación ya
no puede acabar en pérdida.

Ahora bien, vendiendo sólo un 20% ese rescate es más pequeño que antes:

| la moneda llega a | escalera de ayer | esta escalera |
|---|---|---|
| +24% | −35,0% | −35,0% |
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
contando que una que sale mal cuesta el 35%:

```
si sólo tocan el +25%     hacen falta 80% de aciertos
si tocan el +60%          hacen falta 46% de aciertos
si tocan el +100%         hacen falta 35% de aciertos
```

Esta escalera **no se sostiene con monedas que sólo tocan el primer peldaño**.
Necesita que una parte llegue arriba. El +25% está ahí por el suelo que arma,
no por lo que cobra.

### Y el suelo que sube

Esto es lo más importante de todo el sistema:

```
al empezar         el suelo está en -35%
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
