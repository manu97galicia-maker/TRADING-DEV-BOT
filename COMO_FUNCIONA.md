# Bot Definitivo — cómo funciona

Nota para quien llega nuevo. No hace falta saber programar para entenderla.

Esto es un bot que compra y vende monedas de **pump.fun** en la red Solana,
solo. Corre en un servidor encendido las 24 horas (un droplet de DigitalOcean)
y opera con **dinero real**.

---

## 1. La idea en una frase

**Comprar cuando mucha gente DISTINTA está comprando a la vez.**

No cuando una moneda sube — eso lo puede fabricar una sola cartera comprándose
a sí misma. Lo que no se puede fabricar es que entren **carteras distintas**.

Esto no es una corazonada. Se midieron **27.241 monedas** de pump.fun: se
apuntaron sus señales a los 20 segundos de vida y se siguieron cinco minutos
para ver qué hacían.

| | llegan al +25% |
|---|---|
| comprando a ciegas | 14,4% |
| con **≥3** compradores en la ventana | **27,4%** |
| con **≥12** compradores | **25,8%** |

Casi el doble que comprar a ciegas. Y un hallazgo que va contra la intuición:
**la presión como ratio** (más compras que ventas) **no predijo nada**. Lo que
predice es el *número de carteras*.

> **Es la única parte de este bot que está medida.** Todo lo demás —el punto de
> la curva, la escalera, los porcentajes— son números razonados, no probados.

---

## 2. Qué tiene que cumplir una moneda para que la compre

Todo esto, sin saltarse nada:

| | |
|---|---|
| **Gente a la vez** | mínimo **8 carteras distintas** comprando en 5 segundos |
| **Gente de verdad** | mínimo **9 carteras distintas** en toda su vida |
| **No casi migrada** | como mucho el **95%** de la curva recorrido |
| **Sin bundle** | máximo 3 carteras en su bloque de nacimiento, y máximo 2 SOL |
| **Sin concentración** | los mayores tenedores no pueden tener más del 50% |
| **Sin vela verde** | si ha subido más de un **80% en 30 segundos**, no entra |

Los dos últimos están ahí por algo que pasó:

> El 24 de septiembre el bot compró una moneda con **cinco tenedores** justo en
> la punta de una vela vertical. Cuarenta segundos después valía un 92% menos y
> se llevó 45 $. Era un *pump and dump* de manual.

La protección de la vela verde **ya existía**, pero estaba guardada dentro de
una estrategia que se había apagado, así que no se aplicaba. Ahora vale para
todas las entradas, venga de donde venga la moneda.

### La estrategia de la curva sigue escrita, pero apagada

Hasta el 9 de octubre el bot compraba monedas que ya habían recorrido el 59%
de su curva. Esa configuración entera —entrada, compradores, la escalera del
x3— sigue en el fichero y vuelve cambiando un `enabled`. Se apagó porque sólo
puede haber **una** fuente encendida: las dos se reparten las mismas 2 plazas,
y con las dos a la vez no se puede saber cuál produjo el resultado del día.

---

## 3. Cuánto pone en cada compra

**El 30% de la cartera.** Cartera = todo lo que tienes, el SOL de la wallet
más lo que ya vale lo que hay metido en monedas.

Con 1 SOL de saldo salen así:

```
0,300 · 0,299 · 0,298 · 0,048
```

**Cuatro compras, y la cuarta es una miga.** Son ~4,4 $, de los que la comisión
de ida y vuelta (unos 0,93 $) se lleva el **21%** antes de que la moneda se
mueva.

Eso pasa porque **no hay techo de posiciones**. En el fichero pone
`0 posiciones abiertas` como máximo, y ese **0 no significa cero: significa
40**, porque el código lo traduce a su tope duro. Con 40 plazas lo que para al
bot es quedarse sin saldo, y para entonces ya ha comprado restos.

> El 25 de septiembre esto se arregló bajando el techo a 2, justo para no pagar
> comisiones por compras de 4 $. El cambio del 5 de octubre lo deshizo. Si
> molesta, las dos palancas son volver a poner techo o usar
> `compra_minima_sol`, que está a 0.

### Lo que se arriesga

Con el stop en el 30%, cada posición que se hunde cuesta **el 9% de la
cartera**. Y como no hay freno de rachas (`parar_tras_perdidas: 0`), tres
seguidas son −24,6% y diez son −61,3%. Nada para al bot por el camino.

**Tampoco hay tope por compra.** El `máximo 2 SOL por operación` que se pidió
el 25 de septiembre se quitó el 5 de octubre (`tope_compra_sol: 0`). Hoy no
duele porque la cartera es pequeña, pero la red no está.

---

## 4. Cómo vende

### Seis peldaños

| sube | vende | acumulado | suelo después |
|---|---|---|---|
| +35% | 25% | 25% | compra +5,00% |
| +65% | 40% | 65% | compra +41,75% |
| +100% | 15% | 80% | compra +73,25% |
| +150% | 10% | 90% | compra +110,00% |
| +200% | 5% | 95% | compra +162,50% |
| +400% | 3% | 98% | compra +215,00% |

**El peso está abajo:** entre los dos primeros peldaños se cobra el **65%** de
la posición. Al llegar al x2 ya se ha vendido el 80%, y los tres de arriba
reparten 18 puntos entre todos.

Es una escalera que **cobra pronto y deja una cola fina corriendo**. Gana en
las monedas que se quedan a medias; pierde en las que se disparan.

El **2% que queda** no se vende nunca: es el moonbag. Mientras exista, la
cuenta de token sigue abierta con su renta (~0,00204 SOL) dentro.

### Los peldaños de arriba sólo pagan si compras pronto

Con la estrategia de presión el bot compra la moneda **donde esté**, hasta el
95% de su curva. Y eso decide cuántos peldaños puede cobrar:

| entra al... | +35% | +65% | +100% | +150% | +200% | +400% |
|---|---|---|---|---|---|---|
| 20% | 36,1% | 45,5% | 53,8% | 62,4% | 68,7% | 83,7% |
| 40% | 53,3% | 61,1% | 67,9% | 75,0% | 80,3% | 92,7% |
| 60% | 70,5% | 76,7% | 82,1% | 87,7% | 91,8% | **migra** |
| 80% | 87,7% | 92,2% | 96,2% | **migra** | **migra** | **migra** |
| 95% | **migra** | **migra** | **migra** | **migra** | **migra** | **migra** |

(los porcentajes son el punto de curva al que tiene que llegar la moneda)

**En una moneda comprada arriba, la escalera efectiva son uno o dos peldaños y
luego el suelo.** No es un fallo — es lo que permite la curva.

Hay una franja estrecha, del **94,3% al 95%**, donde ni el primer peldaño cabe:
esas posiciones sólo pueden cerrarse por el suelo o después de migrar.

### Y el suelo que sube

Esto es lo más importante de todo el sistema:

```
al empezar         el suelo está en -30%
tras cobrar +35%   sube a   +5,00%     ← ya no puede acabar en pérdida
tras cobrar +65%   sube a  +41,75%
tras cobrar +100%  sube a  +73,25%
tras cobrar +150%  sube a +110,00%
```

El suelo se pone un peldaño **por detrás** del que acabas de cobrar, y un
**+5%** por encima de ese. Un 5% *del precio*, no cinco puntos: por eso tras
cobrar el +65% el suelo queda en +41,75% y no en +40%.

**En cuanto toca el primer peldaño, la operación ya no puede perder dinero.**

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
