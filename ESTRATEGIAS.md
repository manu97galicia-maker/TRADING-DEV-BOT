# ESTRATEGIAS

Qué hay montado, por qué, y qué está medido de verdad.
Última revisión: 12 de agosto de 2026.

---

## 1. La aritmética que sostiene todo

Antes de las estrategias, el número del que salen las dos.

**Toda moneda de pump.fun nace exactamente igual.** La curva arranca con 30 SOL
virtuales y 1.073.000.191 tokens, lo que da un precio de salida de
2,796·10⁻⁸ SOL y un market cap de **2.097 $** para todas, sin excepción.

Como el precio de la curva **solo sube si alguien compra**, si todos venden
vuelve exactamente ahí. **2.097 $ es el suelo, y es estructural.**

    compras a 2.100 $  →  si la moneda muere, pierdes comisiones:  10-15%
    compras a 8.580 $  →  si la moneda muere, pierdes:             76%

El 11 de agosto se hicieron cuatro compras seguidas a 4-11K y las cuatro
perdieron ~70% (verificado firma por firma en la cadena). El stop estaba puesto
al 50% y cobró el 70%. **No falló el stop: no había nada entre la entrada y el
suelo.**

Para subir un 30% desde el nacimiento hacen falta **4,2 SOL de compra neta**
(`precio ∝ ((30+x)/30)²`). Por eso un objetivo bajo es alcanzable: no necesitas
que la moneda triunfe, necesitas que alguien meta cuatro SOL.

### Cuánto acierto hace falta

Ganando +24% cuando acierta (vender 80% en el +30%):

| pérdida cuando falla | acierto necesario para no perder |
|---|---|
| −70% (comprando a 8K) | **74%** |
| −50% | 68% |
| −15% (comprando al nacimiento) | **38%** |

**Dónde compras decide más que cualquier filtro de entrada.**

### Los costes fijos

Medido en la cadena sobre 14 transacciones reales el 12 de agosto: la comisión
de red es de **0,00033 SOL de media**. Sumando el alquiler de la cuenta de
token (0,00204, recuperable al cerrarla), una ida y vuelta cuesta unos
**0,0023 SOL**.

| posición | se lo llevan las comisiones |
|---|---|
| 0,15 SOL | **1,5%** |
| 0,30 SOL | 0,8% |
| 0,50 SOL | 0,5% |

**Corrección**: una versión anterior de este documento decía 0,02 SOL por ida y
vuelta y un 13% sobre 0,15. Estaba mal: venía de suponer que `fixed_amount:
100_000_000` se cobraba entero, cuando son microlamports por unidad de cómputo
y el consumo real ronda las 110.000 unidades. **Operar a 0,15 SOL es viable**;
la conclusión de que "no funciona por debajo de 0,5 SOL" no se sostiene.

---

## 2. Estrategia A — "Nacimiento" (EN MARCHA)

Fichero: `bots/bot-sniper-premium.yaml` · servicio `premium.service`

### Entrada

```
market cap    ≤ 2.750 $        (el suelo está a 2.097: caída máxima 24%)
ventana       6 s              (plazo para rendirse, no espera)
volumen       sin mínimo
bundle        < 30% de las compras en el bloque de creación
gente         ≥ 3 compras de ≥ 3 carteras distintas
mayhem        no
```

**Por qué el volumen mínimo es cero.** A 2.750 $ la moneda acaba de nacer y no
hay volumen que medir. Exigirlo obliga a esperar, y esperar es entrar a 8K —
que es exactamente lo que perdió cuatro veces seguidas.

**Por qué 3 compras de 3 carteras.** Medido sobre 300 monedas: la mediana de lo
que hay en esos segundos es **1 compra** — el dev comprándose a sí mismo y nadie
más. Ese uno es el rug. Con tres carteras distintas se corta la mediana entera.

**Por qué el bundle al 30%.** Por debajo del 30% de compras en el bloque de
creación, el 56% de las monedas llega al +30%; con bundle puro (100%), el 29%.
La escala es limpia: cuanto más concentrado el arranque, peor acaba.

Los tres se leen de los logs del websocket, **en memoria, sin una sola llamada
de red**. Por eso caben en la ventana. Todo lo que necesite RPC (reparto del
supply, holders) no cabe y está desactivado a propósito.

### Salida

```
+30%    vende 50%     → el stop sube al PRECIO DE ENTRADA
+70%    vende 12,5%
+110%   vende 12,5%
+150%   vende 12,5%
stop inicial 35%
```

La primera línea es la clave: al cobrar el primer escalón el stop sube a la
entrada y **lo que queda dentro ya no puede acabar en pérdida**.

### Riesgo

```
0,15 SOL por operación · máximo 3 posiciones · reserva 0,02 SOL
corta sola a 2 pérdidas seguidas
Telegram APAGADO
```

### Estado del filtro del creador

**Apagado.** Se probó con ≥30% migrado (≈3 compras/día) y con ≥10% (ninguna
compra en 7 minutos): los creadores cuyas monedas llegan al nacimiento con
arranque limpio tienen **0 migraciones**. `App` cumplía el perfil entero — MC
2.097 $, 1 de 27 compras en el bloque de creación, 27 carteras distintas, a los
3,5 segundos — y lo único que impidió comprarla fue esa puerta.

| exigir migrado | devs | lanzamientos/día | % del flujo |
|---|---|---|---|
| ≥5% | 412 | 438 | 8,8% |
| ≥10% | 253 | 250 | 5,0% |
| ≥30% | 46 | 33 | 0,7% |

Lo que protege ahora es la **estructura** (el techo de entrada), no la
reputación.

---

## 3. Estrategia B — "Retroceso" (SIN ARRANCAR, en medición)

La idea: las monedas nacen, pegan un tirón y retroceden un 30-40%. Se entra en
ese retroceso.

```
entrada    MC 4.000-50.000 $ · volumen 3.000-100.000 $
           al tocar un retroceso del 30-40% desde el máximo
stop       35%
salida     +40%   vende 60%   → stop al precio de entrada
           +90%   vende 10%   → stop al escalón anterior (+40%)
           +150%  vende 10%   → stop a +90%
           +250%  vende 10%   → stop a +150%
           lo que queda: la mitad a 80.000 $ de MC, el resto no se vende
```

El trinquete por escalones es lo que la hace viable: desde el primer objetivo
la operación no puede acabar en pérdida.

**Falta código**: la entrada por retroceso no existe en el bot todavía (hay que
seguir el máximo desde el nacimiento y disparar en la caída).

**Falta la medición**: hay un backtest corriendo sobre 1.600 monedas con velas
de 30 s de GMGN — que dan el **recorrido** del precio, no solo el máximo, y por
eso permiten saber si el stop del 35% habría saltado antes de llegar al
objetivo.

---

## 4. Lo que está medido y lo que no

### Medido y fiable

- **P&L real**: se lee de los deltas de la wallet en las firmas de la
  transacción. El teórico ha llegado a equivocarse **de signo**.
- **El pico llega pronto**: el 50% de las monedas hace su máximo en los
  primeros 10 segundos; el 68% en 30. En el 25% ya había pasado cuando el bot
  la vio.
- **El suelo de 2.097 $** y toda la aritmética de la sección 1.
- **Frecuencias**: 27.422 monedas de 4 días de log.

### Medido pero con trampa conocida

- **`ath_market_cap` de pump.fun se puede inflar.** Se calcula del precio de la
  curva, y con el pool seco ese precio se mueve con calderilla. Un creador
  enseñaba un máximo de **89.950.686 $** con **1.387 $** de liquidez real y 5 $
  de volumen diario. Cinco de sus seis monedas no tienen par en ningún DEX.
  **Cualquier estudio que ordene por ese campo premia a las granjas.**
  Verificar siempre contra DexScreener.
- Un estudio que compare ese máximo con el precio de entrada **sin mirar la
  fecha del máximo** cuenta como aciertos las compras del techo. Con ese fallo
  salía 61-88% de acierto; corregido, **35%**.

### Sin medir

- Si el acierto al nacimiento es mejor que ese 35% (medido sobre entradas a
  4-11K). Debería serlo — llegar a 2.726 $ desde 2.097 es mucho más fácil que
  subir un 30% desde 8.580 — **pero no está comprobado**.
- La estrategia B entera.

---

## 5. Operación

```bash
bash desplegar.sh              # sube y reinicia, esperando a que no haya posiciones
bash desplegar.sh --solo-subir # sube sin reiniciar
bash desplegar.sh --ya         # reinicia abandonando posiciones (avisa de cuáles)
```

**`desplegar.sh` existe por una razón.** El bot **no reanuda posiciones al
reiniciar**: detecta la marca, avisa de "bolsa huérfana" y la deja para vender a
mano. Quien reinicia con una posición abierta la abandona — deja de vigilarse el
take-profit, el stop-loss y el anti-rug. El 11 de agosto se reinició para
aplicar un stop nuevo con dos posiciones abiertas y las dos se quedaron sin
vigilancia.

El acceso al servidor va con la clave en `~/.ssh/id_droplet_bot`
(`root@159.89.19.12`). **La clave privada no se copia en este documento** —
quien la tenga controla el servidor y la wallet, y un fichero en el Escritorio
con contraseña "HOLA" no es sitio para eso. Si quieres una copia de seguridad,
lo correcto es un gestor de contraseñas o un archivo cifrado con una
contraseña larga, no este documento.

### Trampas del entorno

- El repo es **privado**: `git pull` falla en el droplet. Se sube con
  `tar | ssh`, que es lo que hace `desplegar.sh`.
- `pkill -f "algo"` en un comando ssh **mata su propia sesión** si el patrón
  aparece en la línea de comandos. Usar `pgrep -f "alg[o]"` y comprobar que el
  resto del comando no contiene la cadena.
- Arrancar con `nohup ... &` en el mismo comando que sube el fichero por `cat`
  **corta la subida**: el fichero llega vacío. Subir primero, arrancar después.
- GMGN **banea la IP** por ratio. Una petición cada 1,2 s y no cachear nunca las
  respuestas vacías, o se graba el fallo como si fuera dato.
- `StartLimitIntervalSec` va en `[Unit]`, no en `[Service]`. En `[Service]` se
  ignora en silencio.

---

## 6. Historial de errores caros

Están aquí para no repetirlos.

- **El P&L teórico mentía, y de signo.** Reportaba +0,16 SOL mientras la wallet
  perdía 0,75.
- **El bucle de salida hacía `continue`** después del primer take-profit, así
  que se saltaba anti-rug, trinquete y stop-loss en cada vuelta. La protección
  principal no se disparó nunca hasta que se encontró.
- **Claves duplicadas en el YAML**: `pumpfun_dev` tenía `min_migrations` dos
  veces y ganaba en silencio la de abajo.
- **Umbral de volumen invertido**: exigir 40 SOL rechazó a Leelavati (16 SOL →
  x58) mientras las dos que sí lo superaron perdieron. Volumen alto en pocos
  segundos es bundle, no demanda.
- **Parmesan y FARM quedaron invendibles** (`Custom 6024`): la curva se vacía y
  no hay contrapartida a ningún precio. Es el riesgo que el suelo estructural
  no cubre.

---

## 7. Configuración viva (18 de agosto de 2026)

Las tres estrategias corren en paralelo sobre la misma wallet, con el mismo
tamaño y la misma salida. Lo único que las separa es **qué compran**.

```
                MC entrada   volumen    creador   últimas   edad    stop
DEFINITIVA      ≤ 2.800 $    ≥ 900 $     ≥10%        2      90 s    80%
ELITE           ≤ 2.800 $    ≥ 900 $     ≥25%        3      90 s    80%
RAPIDA          ≤ 2.800 $    ≥ 900 $     ≥ 1%        3       3 s    sin
```

Común a las tres: **0,12 SOL** por posición, ≥4 carteras distintas, ≥4
operaciones previas, sin mayhem, slippage 12%.

### Salida

```
+35%  vende el 80%   →  el suelo sube al PRECIO DE COMPRA
      moonbag 20% que no se vende nunca
```

**Por qué +35% y no x2.** Sobre lo medido, al +40% llega el 79% de las monedas
y a x2 solo el 57%. Las configuraciones anteriores pedían x2, x3 o x5 y por eso
casi ninguna llegaba a cobrar nada. Con el objetivo al +35% se cobra el 80% en
la mayoría de las que se mueven algo, y en ese momento se ha recuperado 1,08
veces lo invertido: **el 20% que queda corre gratis**.

---

## 8. El bug del programa de token (arreglado el 18-ago-2026)

**37 de 373 ventas acabaron en "no quedan tokens que vender"** — el 10%. El bot
daba la posición por cerrada sin haber vendido nada.

La causa no era el RPC. La dirección de la cuenta donde viven los tokens sale
de mezclar **wallet + mint + programa de token**. Si el programa que trae
`token_info` no es el bueno, la dirección que se calcula **no existe**, el saldo
se lee como cero, y la posición se cierra en falso.

Y pump.fun usa **Token-2022**, no el clásico. En el listener de logs no se puede
distinguir `create` de `create_v2`, así que el campo se rellena a ojo
(`event_parser.py:361`) y a veces se rellena mal.

Comprobado en la cadena el 18-ago con tres bolsas atrapadas:

```
3wDQXhTxD7C6...    163.232 tokens    calculado con el clásico: no existe
GKsWvnezWr4C...  1.647.104 tokens    calculado con el clásico: no existe
7kzFcxXPCYJ4...  1.452.754 tokens    calculado con el clásico: no existe
```

**El arreglo** (`_saldo_real_tokens`): se miran **los dos programas**, y cuando
la bolsa aparece bajo el otro se **corrige `token_info.token_program_id`**, no
solo se lee. Esto último es lo importante: la venta construye su lista de
cuentas con ese mismo campo (`address_provider.py:345-369`), así que arreglar
la lectura sin arreglar el campo dejaría la venta apuntando igualmente a una
cuenta vacía.

Probado contra las bolsas reales: donde el código viejo leía 0, el nuevo
encuentra los 163.232 y los 1.647.104 tokens.

### Lo que sigue sin arreglar

- **El P&L teórico miente a lo bestia.** Ha reportado +64 y +98 SOL en
  operaciones que en la cadena fueron +0,07 y −0,01. Solo es cosmético — lo que
  se apunta de verdad sale de los deltas de la wallet — pero no fiarse de él.
- **`venta_muerta` se mira una sola vez a los 120 s.**
- **Los tres bots no se coordinan** y pueden comprar la misma moneda (GAMBLE
  costó 0,27 SOL entre dos).
- **`Custom 6024`**: curva vacía, no hay contrapartida a ningún precio.
