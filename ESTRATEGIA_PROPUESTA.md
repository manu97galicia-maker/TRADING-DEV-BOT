# La estrategia que propongo

Una sola, no tres. Sale de cruzar lo que está medido y fiable con la aritmética
de la curva. Escrito el 19-ago-2026.

**No está probada.** Al final está lo que tendría que ser cierto para que gane y
cómo saber pronto que no lo es.

---

## 0. Lo que encontré al hacer las cuentas

Antes de la estrategia, el motivo por el que no es un retoque de la actual.

**El filtro de volumen de DEFINITIVA y ELITE exige matemáticamente que alguien
ya haya vendido.**

En la curva de pump.fun el market cap es función del SOL **neto** dentro
(`MC = 2097 · ((30+x)/30)²`), y el volumen es el **bruto** (comprado + vendido).
Son la misma magnitud medida de dos formas, y eso los ata:

```
techo MC 2.900 $   ->  como mucho   5,28 SOL netos dentro
volumen >= 11,3    ->  comprado + vendido >= 11,3
                       => vendido >= (11,3 - 5,28) / 2 = 3,01 SOL
```

**Para cumplir los dos filtros a la vez tienen que haberse vendido ya 3 SOL.**
Y el filtro nuevo `max_tx_por_compra: 1.2` permite como mucho una venta por cada
cinco compras. Las dos cosas juntas describen un perfil muy concreto:

> 3 SOL saliendo en dos a seis ventas grandes (0,5-1,5 SOL cada una), mientras
> entra gente pequeña.

Eso es **una ballena saliendo contra compradores minoristas** — exactamente lo
que `tx/compras` se inventó para detectar. El filtro de volumen no es que pelee
con el techo de market cap: es que **selecciona justo el perfil que quieres
evitar**.

RAPIDA no tiene ese problema en la parte baja de su banda (2,7 SOL de volumen
contra 2,76 netos: se cumple con cero ventas), pero sí en la alta: con volumen
6,7 vuelve a forzar 1,97 SOL vendidos.

**La conclusión de diseño**: el suelo de volumen sobra. El techo de market cap
ya demuestra que entró dinero. Lo que hay que poner es un **techo de volumen**,
no un suelo.

---

## 1. La idea en una frase

Comprar tan pegado al suelo estructural que equivocarse sea barato, dentro de
la ventana de segundos que está medida como buena, y sólo mientras **nadie haya
empezado a salir**. Que el límite de la pérdida lo ponga la aritmética de la
curva y no un stop que llegue a tiempo.

---

## 2. Entrada

```
edad                 <= 3 s
market cap           <= 2.600 $
volumen              <= 4,5 SOL        (TECHO, no suelo)
tx / compras         <  1,2
compras / cartera    <  1,3
carteras distintas   >= 4
operaciones previas  >= 4
presion compradora   > 1 en los ultimos 3 s
creador              SIN FILTRO
mayhem               no
```

### Por qué cada número

**MC ≤ 2.600 $.** Es lo único que de verdad decide, y está en las notas del
12-ago: *"dónde compras decide más que cualquier filtro de entrada"*.

| MC de entrada | SOL netos dentro | caída máxima al suelo | acierto mínimo |
|---|---|---|---|
| 2.500 | 2,76 | −16,1% | 17,9% |
| **2.600** | **3,40** | **−19,3%** | **20,7%** |
| 2.900 | 5,28 | −27,7% | 27,2% |
| 3.500 | 8,76 | −40,1% | 35,1% |
| 8.580 | 30,68 | −75,6% | — |

Bajar el techo de 2.900 a 2.600 baja el acierto necesario del 27,2% al 20,7%.
Son seis puntos y medio regalados por no pagar 300 $ más de market cap.

**Volumen ≤ 4,5 SOL, sin suelo.** Con el techo de MC en 2.600 (3,40 netos), un
volumen de 4,5 significa que como mucho se han vendido 0,55 SOL. Es la versión
en SOL del mismo criterio que `tx/compras` mide en número de operaciones:

```
volumen <= 3,4 SOL  ->  como mucho 0,00 SOL vendidos
volumen <= 4,5 SOL  ->  como mucho 0,55 SOL vendidos
volumen <= 6,0 SOL  ->  como mucho 1,30 SOL vendidos
volumen <= 11,3 SOL ->  como mucho 3,95 SOL vendidos   <- la config de hoy
```

Lo que dispara la compra son las 4 carteras y las 4 operaciones, no el dinero.

**Edad ≤ 3 s.** Uno de los dos únicos efectos que las propias notas se creen:
antes de 2 s el 29% hace x2 contra el 17% de base; entre 5 y 15 s se hunde al
6%. Y el pico llega pronto — el 50% de las monedas hace su máximo en los
primeros 10 segundos.

**tx/compras < 1,2.** El efecto más grande medido: 41% contra 17%, +24 puntos.

**Sin filtro de creador.** `ESTRATEGIAS.md` ya lo probó y lo apagó, con el
motivo escrito: *"los creadores cuyas monedas llegan al nacimiento con arranque
limpio tienen 0 migraciones"*, y el caso `App` —perfil perfecto, 27 carteras,
3,5 segundos— que sólo esa puerta impidió comprar. A este techo de entrada lo
que protege es la estructura, no la reputación.

---

## 3. Salida — sin cambios

La de ahora está bien y la aritmética la respalda:

```
x2 (+100%)   vende 74%  ->  el suelo pasa al PRECIO DE COMPRA
             26% de moonbag que no se vende nunca

muerta       a los 120 s por debajo de la compra, REPITIENDO cada 120 s
sin volumen  120 s seguidos sin negociar -> fuera
anti-rug     sale si en 4 s se vende el doble de lo que entra
max_hold     3600 s
stop         80% (nunca saltará: la caída máxima es 19,3%. Se deja de red)
```

Una ganadora deja **+74% como mínimo**: `0,74 × 2,0 + 0,26 × 1,0`. El 26% no
puede acabar en pérdida porque al cobrar el x2 el trinquete pone su suelo en el
precio de compra.

---

## 4. Dinero

```
0,15 SOL por posicion
1 posicion a la vez · tanda de 3 y para
reserva 0,08 SOL · deslizamiento 12% / 12%
veta al creador al PRIMER rug
```

**0,15 y no 0,06.** Es la fila más pequeña de la tabla de costes del propio
repo. Los 0,0023 SOL de ida y vuelta son el 1,5% de una posición de 0,15 y el
**3,8%** de una de 0,06. A 0,06 los costes fijos se comen dos veces y media más.

**Y el tamaño no crea problema de salida.** Es la parte que no es obvia:

| MC de entrada | SOL en la curva | impacto de salir con 0,15 |
|---|---|---|
| 2.600 | 3,40 | −0,90% |
| 8.580 | 30,68 | −0,49% |

Comprar cerca del suelo parece que compra menos liquidez, y en absoluto sí: la
curva sólo tiene 3,4 SOL. Pero **tu posición también es diminuta comparada con
ella**, y la curva garantiza contrapartida a cualquier precio hasta el suelo.

El problema del *"sólo el 4-6% tiene pool para pagarte la salida"* no es de las
monedas baratas: es de las que **subieron mucho con poco SOL real detrás**. Un
máximo de 89.950.686 $ con 1.387 $ de liquidez. Comprando a 2.600 nunca estás en
esa situación, porque el market cap bajo es literalmente la prueba de que no ha
entrado dinero falso.

**Comprar cerca del suelo no es sólo el riesgo más barato: es también la
liquidez más honesta.** Es la razón de fondo por la que esta estrategia y no
otra.

---

## 5. La apuesta, dicha claramente

Necesita **20,7% de acierto** para no perder, antes de costes. Con 0,15 SOL los
costes fijos son ~1,5% más el deslizamiento real.

```
base medida (92 monedas)              17%     <- NO llega
banda tx/compras < 1,2                41%     <- llega de sobra
banda vista antes de 2 s              29%     <- llega
```

**La apuesta es que la banda del 41% se sostenga.** Si `tx/compras < 1,2` sólo
vale lo que la base, esto pierde despacio. Y las propias notas avisan de que 92
monedas es poco y que las bandas tienen entre 16 y 58 casos.

Los dos efectos que se apilan aquí (tx/compras y llegar pronto) son justo los
dos únicos que las notas dicen creerse. Pero **no está medido que sean
independientes**: puede que las monedas con tx/compras bajo sean en su mayoría
las mismas que se vieron pronto, y entonces apilarlos no suma.

---

## 6. Lo que hay que resolver antes de creerse nada

**El 17% contra el 46%.** `ESTRATEGIA_ACTUAL.md` dice las dos cosas, citando las
mismas 92 monedas del mismo día. Con el 46% cualquier versión de esto gana; con
el 17% hace falta que la banda buena aguante. El script está en el droplet.
Sospecho que el 46% mide "llegó a TOCAR x2", que es la métrica de máximo que
este repo dice no creerse — pero hay que mirarlo, no suponerlo.

**Nada de esto se decide con seis operaciones.** El historial son dos tandas,
0/6. Para distinguir un 20% de un 40% con algo de confianza hacen falta del
orden de 50-100 operaciones. A 3 por tanda, eso es semanas. **Esa es la razón
de correr una sola estrategia y no tres**: tres reparten la muestra en tercios y
ninguna llega nunca a significar nada.

---

## 7. Cómo saber pronto que va mal

En este orden, sin tocar nada hasta tener la respuesta:

1. **¿Compra algo?** Si en unas horas no hay ni una compra, el embudo está
   cerrado. El aviso de descarte del log dice cuántas "tx por compra" se
   llegaron a ver: si todas se caen ahí, sube a 1,4 antes que tocar nada más.
2. **¿Cuántas llegan a x2?** Es el número que importa, no el P&L de las
   primeras. Con 20 operaciones ya se distingue un 40% de un 10%.
3. **¿El P&L REAL cuadra con el teórico?** Si no cuadra, hay bolsas
   abandonándose (Token-2022, `Custom 6024`) y todo lo demás que midas está
   contaminado.

Y lo de siempre: **sólo la línea `P&L REAL`**, leída de los deltas de la wallet.
El teórico ha llegado a equivocarse de signo.

---

## 8. Qué cambia respecto a lo que corre hoy

| | hoy (3 bots) | propuesta |
|---|---|---|
| estrategias | 3 en paralelo | 1 |
| market cap | ≤ 2.900 / ≤ 2.500 | ≤ 2.600 |
| volumen | **suelo** 849 $ / 203-503 $ | **techo** 4,5 SOL |
| edad | 90 s / 90 s / 3 s | 3 s |
| creador | ≥10% / ≥25% / ≥1% | sin filtro |
| posición | 0,06 SOL | 0,15 SOL |
| acierto necesario | 27,2% | 20,7% |
| salida | x2, 74%, moonbag 26% | igual |

Lo más importante de la tabla no es ninguna fila: es la primera. **Una sola
estrategia, para que la muestra crezca tres veces más rápido y se pueda saber
si esto funciona.**
