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

**NUEVO (19-ago) — `max_tx_por_compra: 1.2` en las tres.** Es el rasgo que más
separa de todo lo medido y llevaba desde el 18-ago sin implementar: por debajo
de 1,2 tx por compra el **41%** hace x2 contra el 17% de base (+24 puntos); el
tramo siguiente (1,2-1,6) se hunde al 7%. Mide si **ya hay alguien saliendo**,
que no es lo mismo que la presión compradora (esa compara SOL y puede salir
bien mientras alguien se va por detrás con calderilla).

Ojo al embudo: se suma al techo de market cap y al de volumen, que ya tiran el
uno contra el otro. El aviso de descarte del log ahora dice cuántas "tx por
compra" se llegaron a ver, para saber si es este filtro el que seca el flujo.

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

2. ~~**La venta por muerta se comprueba UNA sola vez** a los 120 s.~~
   **ARREGLADO (19-ago)**: `venta_muerta_repetir_seg: 120` en los tres. Ahora
   se vuelve a preguntar cada 2 minutos en vez de una sola vez. Era lo que
   dejó CATECOIN 20 minutos abierta, y en RAPIDA —que no lleva stop— una
   posición así no tenía ninguna salida salvo el x2.

3. **`Custom 6024` al vender.** Cuando la curva se vacía no hay contrapartida a
   ningún precio y la bolsa queda atrapada. Van seis casos: Parmesan, FARM,
   CATECOIN, MORE, CVP y BABYANSEM.

4. ~~**Las tres no se coordinan.**~~ **ARREGLADO (19-ago)**:
   `coordinar_mints: true` en los tres. La primera que reclama una moneda es la
   única que entra; el reclamo se crea con `O_EXCL`, que es atómico, así que de
   tres procesos que lo intenten a la vez gana exactamente uno. Un TTL de 900 s
   libera la moneda si el bot que la pidió se muere con ella.

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

---

## Pendiente de decidir (19-ago)

Tres cosas que no se han tocado porque son decisiones de dinero, no de código.

**1. El 17% y el 46% no pueden ser los dos ciertos.** Este documento dice que
"el 46% de las monedas llega a x2 desde donde entramos" y tres párrafos antes
dice que la base es el 17%, citando las mismas 92 monedas del mismo día.
Importa porque decide si la estrategia gana o pierde:

```
ganadora   0,74 x 2,0  +  0,26 x 1,0 (el trinquete pone el suelo en la compra)
           = +74% minimo

necesitas acertar el 25% si la perdedora es -25%
necesitas acertar el 17% si la perdedora es -15%
```

Con el 17% estás justo en el filo **antes de costes**; con el 46% ganas de
sobra. La sospecha es que el 46% mide "llegó a TOCAR x2 en algún momento",
que es la métrica de máximo que este mismo documento dice no creerse. Hay que
mirar el script de la medición, que está en el droplet.

**2. Se opera a 0,06 SOL, por debajo de la fila más pequeña de la tabla de
costes.** Los 0,0023 SOL de ida y vuelta son el **3,8%** de una posición de
0,06, no el 1,5% de la fila de 0,15 que se analizó. Sumado al ~3,7% de
prioridad, los costes fijos se comen más del doble de lo previsto.

**3. `min_migrations: 1` con un comentario que pide 2.** El comentario de los
tres YAML argumenta "DOS MIGRACIONES MINIMO" y cita el caso 变狗 (0,29 SOL
perdidos en un rug a los 4 segundos, wallet recién estrenada con 1 migración de
1 lanzamiento = 100%). El valor puesto es 1, que es justo el caso que el
comentario dice que hay que cortar.

Y una contradicción de fondo sin resolver: `ESTRATEGIAS.md` (12-ago) dejó el
filtro del creador **apagado**, con el motivo escrito —"los creadores cuyas
monedas llegan al nacimiento con arranque limpio tienen 0 migraciones"— y el
caso `App`, que cumplía el perfil entero y solo esa puerta impidió comprar.
DEFINITIVA y ELITE lo llevan encendido al 10% y al 25%.
