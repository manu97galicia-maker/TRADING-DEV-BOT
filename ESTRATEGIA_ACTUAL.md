# Las tres estrategias que están corriendo

Droplet `root@159.89.19.12`, carpeta `/root/ai-bot`. Las tres comparten wallet y
las tres arrancan solas si se reinicia el servidor (`enabled`).

```
A · DEFINITIVA   bots/bot-sniper-definitiva.yaml   servicio definitiva
B · ELITE        bots/bot-sniper-premium.yaml      servicio premium
C · RAPIDA       bots/bot-sniper-dev10.yaml        servicio dev10
```

Actualizado: 18 de agosto de 2026. SOL a 75,12 $.

---

## Qué compra cada una

```
              market cap    volumen      creador   ultimas   edad
A DEFINITIVA    <= 2.900     849 $ +      >= 10%      2       90 s
B ELITE         <= 2.900     849 $ +      >= 25%      3       90 s
C RAPIDA        <= 2.500    203-503 $     >=  1%      3        3 s
```

`ultimas` = cuántas de las últimas operaciones tienen que ser **compras**. No es
lo mismo que la presión compradora: esa compara SOL comprado contra vendido y
puede salir bien mientras alguien va saliendo por detrás. Esto exige que
**nadie haya vendido** en las últimas operaciones.

Las tres exigen además: **≥4 carteras distintas**, **≥4 transacciones previas**,
que entre más SOL del que sale en los últimos 3 s, y **sin mayhem**.

## Cómo vende — igual en las tres

```
x2 (+100%)   vende 74%   ->  el suelo pasa al PRECIO DE COMPRA
             26% de moonbag que no se vende nunca

stop         80% en DEFINITIVA y ELITE · SIN stop en RAPIDA
muerta       a los 120 s si esta por debajo del precio de compra
anti-rug     sale si en 4 s se vende el doble de lo que entra
```

**Trinquete escalón a escalón** (activo en las tres): al cobrar el primer
take-profit el suelo pasa al precio de compra; al cobrar el segundo, al primero;
y así. Con una sola escalera en x2 lo que hace es proteger el 26% de moonbag:
en cuanto se cobra el x2, ese resto ya no puede acabar en pérdida.

## Dinero

```
0,06 SOL por compra · 1 posicion a la vez · tanda de 3 compras y para
reserva 0,08 SOL · deslizamiento 12% compra / 12% venta
comision de prioridad ~3,7% de la posicion
veta al creador al PRIMER rug
```

---

## Por qué están así estos números

**El stop del 80% casi nunca saltará.** Toda moneda nace a 2.100 $ y ese es el
suelo de la curva: comprando a 2.900 la caída máxima estructural es del **28%**,
así que un stop del 80% queda muy por debajo. Lo que cierra las malas es la
venta por muerta a los 120 s. No es un fallo — es la consecuencia de tener el
techo de entrada bajo.

**El deslizamiento al 12%** viene de haber medido que con el 30% una ida y
vuelta podía dejarse el 46% de la posición sin que el precio se moviera. Con el
12% el peor caso es el 21%. El precio: alguna compra falla por no confirmar.

**RAPIDA entra a los 3 segundos** y es la única en la ventana buena. Medido
sobre 92 monedas: entrar antes de 2 s casi duplica la probabilidad de x2 frente
a la base (29% contra 17%); entre 5 y 15 s se hunde al 6%.

**El x2 es alcanzable**: el 46% de las monedas llega a x2 desde donde entramos,
y luego acaban a 0,90x. Objetivos más altos (x3, x5) los alcanzan muchas menos.

---

## Historial

```
tanda 1   volumen 1,9K · sin techo · desliz 30%     -0,0678 SOL   0/3
tanda 2   volumen 1,7K · techo 3,2K · desliz 30%    -0,1074 SOL   0/3
```

**Ninguna murió por el stop.** Casi todas entraron y salieron casi al mismo
precio; se las comió el deslizamiento. Por eso se bajó al 12%: las tandas de
ahora son las primeras que miden la estrategia y no las comisiones.

---

## Lo que NO hay que creerse

**El P&L del log miente.** Ha llegado a decir `+11,86 SOL` y `+8,25 SOL` en
operaciones que la cadena cifró en `-0,0097` y `-0,0160`. Mirar **solo** la
línea `P&L REAL`, que se lee de los deltas de la wallet en cada firma.

**Nada medido contra `ath_market_cap`.** Ese campo sale del precio de la curva y
con el pool seco se infla con calderilla: un creador enseñaba 89.950.686 $ de
máximo con 1.387 $ de liquidez real y 5 $ de volumen diario.

---

## Fallos conocidos sin arreglar

1. **Token-2022.** El bot deriva la cuenta de tokens con el programa clásico,
   pero muchas monedas son Token-2022. Cuando pasa lee 0 tokens, da la posición
   por vendida **sin venderla** y abandona la bolsa. Llegaron a acumularse 24.
   Es el arreglo pendiente más caro.

2. **La venta por muerta se comprueba UNA sola vez** a los 120 s. Una moneda que
   en ese instante esté a +0% se deja correr y ya solo la cierran el stop o el
   objetivo. Pasó con CATECOIN (20 min), MORE, CVP y PUMPFUN.

3. **`Custom 6024` al vender.** Cuando la curva se vacía no hay contrapartida a
   ningún precio y la bolsa queda atrapada. Van seis casos: Parmesan, FARM,
   CATECOIN, MORE, CVP y BABYANSEM.

4. **Las tres no se coordinan.** Si ven la misma moneda entran las tres y
   acabas con 0,18 SOL en una sola posición creyendo que son tres apuestas
   distintas. Ya pasó con GAMBLE (dos bots, 0,27 SOL en la misma moneda).

---

## Operar

```bash
# comprobar SIEMPRE antes de reiniciar
ls /root/ai-bot/open_position_*.json

systemctl restart definitiva premium dev10     # solo si no hay ninguna
bash desplegar.sh                              # sube y espera a que no haya
```

**Nunca reiniciar con una posición abierta**: el bot no las reanuda, avisa de
"bolsa huérfana" y la deja sin vigilancia. Pasó con MOLE, Parmesan y wCAT.

Para sacar a mano una posición atascada:

```bash
cd /root/ai-bot && .venv/bin/python rescatar_posicion.py <mint> <cantidad>
```

## Telegram

```
wagbo     configurado       @Wagbocooks     sesion tg_wagbo OK
naomi     NO puede arrancar sesion tg_naomi sin autenticar
legends   NO puede arrancar sesion tg_calls sin autenticar (ojo: NO tg_legends)
```

Autenticar hace falta una vez, a mano, en el droplet:

```bash
cd /root/ai-bot && .venv/bin/python -c "
from telethon import TelegramClient
import os; from dotenv import load_dotenv; load_dotenv('.env')
TelegramClient('tg_naomi', int(os.getenv('TELEGRAM_API_ID')), os.getenv('TELEGRAM_API_HASH')).start()"
```

Se sabe que funcionó porque el `.session` pasa de 28 KB a ~150 KB.
