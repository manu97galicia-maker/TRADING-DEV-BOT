# La estrategia que está corriendo

`bots/bot-sniper-definitiva.yaml` · servicio `definitiva.service` · droplet
`root@159.89.19.12`. Es la **única** que opera: el resto de bots están parados y
deshabilitados.

Última revisión: 18 de agosto de 2026.

---

## Qué compra

```
volumen        >= 22,6 SOL   (1.698 $ a 75,12 $/SOL)   <- la puerta principal
market cap     sin techo ni suelo
edad           hasta 90 s de vida · hasta 60 s esperando a que cumpla
transacciones  al menos 4 previas
presión        entra más SOL del que sale en los últimos 3 s
creador        SIN filtro
mayhem         fuera
nombre         no repite uno visto en los últimos 2 min
```

**La única condición que decide es el volumen.** Todo lo demás son guardas
baratas: las 4 transacciones se cumplen solas cuando hay 1.700 $ negociados, y
la presión compradora es la que de verdad añade algo — el volumen dice que hay
movimiento, la presión dice **hacia dónde va**, y evita entrar justo cuando el
dinero está saliendo.

**Sin techo de market cap.** Para juntar 1.700 $ de volumen una moneda ya ha
subido bastante desde el nacimiento (2.100 $), así que se entra alto. Eso aleja
el suelo de la curva y hace que el stop tenga trabajo real.

## Cómo vende

```
+50%   vende 30%   ->  el suelo pasa al PRECIO DE COMPRA
x3     vende 30%   ->  el suelo pasa al primer take-profit
resto  40% de moonbag
stop inicial 25%
```

**El trinquete escalón a escalón** es la pieza que hace esto sobrevivible: al
cobrar el primer objetivo lo que queda dentro ya no puede acabar en pérdida, y
al cobrar el segundo el suelo sube al primero. Deja sitio para que la moneda
respire entre objetivos y aun así asegura lo ganado.

### Otras salidas que pueden dispararse

```
anti-rug        si en 4 s se venden el doble de SOL de los que entran, sale
                (solo si hay 3 SOL o más en juego)
venta muerta    a los 120 s, si está por debajo del precio de compra, fuera
tiempo máximo   3.600 s
```

**Cuidado con la venta por muerta**: se comprueba **una sola vez** a los 120
segundos. Una moneda que en ese instante esté justo a +0% se deja correr, y a
partir de ahí solo la cierran el stop o los objetivos. Pasó con CATECOIN, que
se quedó 20 minutos abierta.

## Dinero

```
0,07 SOL por operación
1 posición a la vez
tanda de 3 compras y para
reserva 0,08 SOL intocables
deslizamiento 30% en compra y 30% en venta
veta al creador al PRIMER rug
comisión de prioridad 8.480.000 microlamports/CU
   -> 0,0012 en la compra + 0,0010 en la venta = 3,1% de una posición de 0,07
```

El deslizamiento del 30% es para que no fallen transacciones, que se pidió
expresamente. No es gratis: una ida y vuelta puede dejarse hasta el 45% sin que
el precio se mueva. Si las operaciones salen justas, es el primer número que
hay que revisar.

---

## Resultado de la primera tanda

```
Ass         -0,0235   (con moonbag)
Ozzy        -0,0096
SKIBIDIFY   -0,0347   (con moonbag)
            ────────
            -0,0678 SOL sobre 0,21 arriesgados  =  -32%
```

Tres de tres en pérdida. **La regla era "si funciona, otras 3"; no ha
funcionado, así que la tanda no se rearma** hasta decidir qué cambiar.

---

## Lo que NO hay que creerse

**El P&L del log miente.** En esta misma tanda dijo `+3,6929 SOL` en una
operación que la cadena cifró en `-0,0096`. Mirar **solo** la línea `P&L REAL`,
que se lee de los deltas de la wallet en cada firma.

**Y hay un fallo sin arreglar**: el bot deriva la cuenta de tokens con el
programa clásico, pero muchas monedas son **Token-2022**. Cuando eso pasa lee 0
tokens, da la posición por cerrada sin vender y abandona la bolsa. Llegaron a
acumularse 24 bolsas huérfanas por esto. Es el arreglo pendiente más caro.

---

## Operar

```bash
bash desplegar.sh                 # sube y reinicia esperando a que no haya posiciones
systemctl status definitiva       # en el droplet
```

**Nunca `systemctl restart` a secas**: el bot no reanuda posiciones al
arrancar, así que reiniciar con una abierta la abandona. Pasó con MOLE.

Servicios de Telegram (`naomi`, `legends`) **no pueden arrancar**: sus sesiones
no están autenticadas y Telethon pide el teléfono por consola. `wagbo` sí
funciona.
