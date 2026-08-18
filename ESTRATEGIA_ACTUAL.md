# Las dos estrategias que están corriendo

Droplet `root@159.89.19.12`, carpeta `/root/ai-bot`. Las dos comparten wallet.
Actualizado: 18 de agosto de 2026.

```
A · DEFINITIVA   bots/bot-sniper-definitiva.yaml   servicio definitiva
B · ELITE        bots/bot-sniper-premium.yaml      servicio premium
```

Las dos `enabled`: arrancan solas si se reinicia el servidor.

---

## Lo que comparten

```
volumen        >= 11,3 SOL   (849 $ a 75,12 $/SOL)
market cap     <= 2.700 $     (el suelo de la curva esta en 2.100)
carteras       >= 4 distintas
transacciones  >= 4 previas
presion        entra mas SOL del que sale en los ultimos 3 s
edad           hasta 60 s para entrar · descarta a los 90 s
mayhem         fuera
deslizamiento  12% compra / 12% venta
tanda          3 compras y para
trinquete      al cobrar el 1er objetivo el suelo pasa al PRECIO DE COMPRA;
               al cobrar el 2o pasa al 1er objetivo, y asi
anti-rug       sale si en 4 s se vende el doble de lo que entra
veta al dev    al PRIMER rug
```

---

## A · DEFINITIVA — dispara mucho, apuesta poco

```
creador        >= 1% migrado, minimo 1 moneda      (bloquea al 80% de los devs)
ultimas ops    las 2 ultimas tienen que ser COMPRAS
tamaño         0,07 SOL

SALIDA
   +50%         vende 30%   -> suelo al precio de compra
   MC  7.000 $  vende 20%
   MC 12.000 $  vende 20%
   MC 38.000 $  vende 20%
   10% de moonbag permanente
   stop 25%
```

## B · ELITE — dispara poco, apuesta el triple

```
creador        >= 25% migrado                       (deja pasar poquisimo)
ultimas ops    las 3 ultimas tienen que ser COMPRAS
tamaño         0,20 SOL

SALIDA   cada escalon un 30% por encima del anterior
   x1,40  (+40%)    vende 50%     -> suelo al precio de compra
   x1,82  (+82%)    vende 12,5%   -> suelo al escalon anterior
   x2,37  (+137%)   vende 12,5%
   x3,08  (+208%)   vende 12,5%
   x4,00  (+300%)   vende 12,5%
   sin moonbag
   stop 32%
```

La separacion tiene sentido asi: el filtro del creador al 25% deja pasar
poquisimas monedas, asi que cuando aparece una se apuesta el triple.

---

## El dinero está justo

```
saldo                       0,4548 SOL
ELITE reserva por compra    0,229
DEFINITIVA reserva          0,083
las dos dentro a la vez     0,312
```

Cabe, pero si ELITE esta en una posicion quedan 0,22 libres. Es probable ver
`SOL insuficiente` en el log: no es un fallo, es que ELITE se lleva la mitad de
la wallet en una sola posicion. **Con 0,7-0,8 SOL las dos operarian sin
estorbarse.**

---

## Historial de las tandas

```
tanda 1   volumen 1,9K, sin techo, desliz 30%     -0,0678 SOL   0/3 ganadoras
tanda 2   volumen 1,7K, techo 3,2K, desliz 30%    -0,1074 SOL   0/3
```

**Ninguna murio por el stop.** Casi todas entraron y salieron casi al mismo
precio; lo que se las comio fue el 30% de deslizamiento por lado. Por eso se
bajo al 12%: el coste maximo de una ida y vuelta pasa del 46% al 21%.

Estas tandas son las primeras con el coste de ejecucion bajo control. Si siguen
perdiendo, ya no sera por comisiones.

---

## Lo que NO hay que creerse

**El P&L del log miente.** Ha llegado a decir `+8,2492` y `+11,86 SOL` en
operaciones que la cadena cifro en `-0,0160` y `-0,0097`. Mirar **solo** la
linea `P&L REAL`, que se lee de los deltas de la wallet en cada firma.

**Nada medido contra `ath_market_cap`.** Ese campo se calcula del precio de la
curva y con el pool seco se infla con calderilla: un creador enseñaba
89.950.686 $ de maximo con 1.387 $ de liquidez real.

---

## Fallos conocidos sin arreglar

1. **Token-2022.** El bot deriva la cuenta de tokens con el programa clasico,
   pero muchas monedas son Token-2022. Cuando pasa lee 0 tokens, da la posicion
   por vendida **sin venderla** y abandona la bolsa. Llegaron a acumularse 24.
   Es el arreglo pendiente mas caro.

2. **La venta por muerta se comprueba UNA sola vez** a los 120 s. Una moneda
   que en ese instante este justo a +0% se deja correr y solo la cierran el stop
   o los objetivos. Paso con CATECOIN (20 min abierta) y MORE.

3. **`Custom 6024` al vender.** Cuando la curva se vacia no hay contrapartida a
   ningun precio y la bolsa queda atrapada. Paso con Parmesan, FARM, CATECOIN y
   MORE.

---

## Operar

```bash
bash desplegar.sh                       # sube y reinicia esperando a que no haya posiciones
systemctl restart definitiva premium    # solo si NO hay posiciones abiertas
ls /root/ai-bot/open_position_*.json    # comprobarlo antes
```

**Nunca reiniciar con una posicion abierta**: el bot no las reanuda, avisa de
"bolsa huerfana" y la deja sin vigilancia. Paso con MOLE y con Parmesan/wCAT.

## Telegram

```
wagbo     funcionando       @Wagbocooks
naomi     NO puede arrancar  sesion tg_naomi sin autenticar
legends   NO puede arrancar  sesion tg_calls sin autenticar (ojo: no tg_legends)
```

Para autenticarlas hace falta meter el telefono una vez a mano, en el droplet:

```bash
cd /root/ai-bot && .venv/bin/python -c "
from telethon import TelegramClient
import os; from dotenv import load_dotenv; load_dotenv('.env')
TelegramClient('tg_naomi', int(os.getenv('TELEGRAM_API_ID')), os.getenv('TELEGRAM_API_HASH')).start()"
```

Se sabe que funciono porque el .session pasa de 28 KB a ~150 KB.
