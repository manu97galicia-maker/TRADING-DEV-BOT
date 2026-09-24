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
nace ──────────────────────────────▶ 87,5% ────────▶ migra
                                       ▲
                                  aquí compra
                             (le faltan ~30 SOL)
```

Son pocas al día: la mayoría de las monedas mueren mucho antes de llegar. Que
compre poco **no significa que esté roto**.

---

## 2. Qué tiene que cumplir una moneda para que la compre

Todo esto, sin saltarse nada:

| | |
|---|---|
| **Punto de la curva** | entre el 87,5% y el 99,5% (pasado eso ya está migrando) |
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

**El 15% del SOL libre.** Libre = lo que hay en la cartera menos una reserva
intocable de 0,03 SOL (hace falta para poder *vender* después) y menos lo ya
comprometido.

Con 1 SOL de saldo salen cinco compras así:

```
0,146 · 0,123 · 0,104 · 0,088 · 0,075
```

**Menguan a propósito.** Cada una es el 15% de lo que *sobra*, no del total.
Eso es un freno automático: por muchas posiciones que se abran, nunca se
compromete la cartera entera. Las cinco juntas se quedan en torno a la mitad.

Puede tener **5 posiciones abiertas a la vez**.

---

## 4. Cómo vende

Dos escaleras, una detrás de la otra.

### Por porcentaje de subida

| sube | vende | acumulado |
|---|---|---|
| +25% | 15% | 15% |
| +55% | 35% | 50% |
| +90% | 30% | 80% |
| +125% | 5% | 85% |

### Por market cap (solo después de cobrar los cuatro de arriba)

| llega a | vende | acumulado |
|---|---|---|
| 100k $ | 5% | 90% |
| 140k $ | 4% | 94% |
| 200k $ | 3% | 97% |

Queda un **3% (el "moonbag")** que el bot **no vende nunca**. Ese lo vende una
persona a mano, si quiere.

> Los porcentajes son sobre la **compra original**, no sobre lo que queda
> dentro. Es la confusión que más caro puede salir aquí: "vender el 30%"
> significa el 30% de todo lo que se compró.

### Y el suelo que sube

Esto es lo más importante de todo el sistema:

```
al empezar        el suelo está en -26%   ← si baja de ahí, se vende TODO
tras cobrar +25%  el suelo sube a +5%     ← ya no puede acabar en pérdida
tras cobrar +55%  el suelo sube a +25%
tras cobrar +90%  el suelo sube a +55%
```

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
