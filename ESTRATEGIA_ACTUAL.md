# DEVMIG — la estrategia

Estado a **21 de agosto de 2026**. Esto es lo que corre en el servidor, no lo
que se planea.

Servicio `devmig` en `root@159.89.19.12`. Es la **única** estrategia viva; las
demás (UNICA, RECIEN, PRUEBA3 y otras 30) están paradas y deshabilitadas.

---

## La estrategia

```
ENTRADA
  creador             ha migrado el 20% o más de lo que ha lanzado (mín. 1)
  market cap          ≤ 3.496 $        (se reescala solo con el precio de SOL)
  volumen             sin exigencia    ← NO se espera confirmación
  carteras distintas  sin exigencia    ← NO se espera confirmación
  edad                ≤ 5 s            (sólo anti-rancia, ya no se espera)
  mayhem              no
  plataforma          solo pump.fun

COMPRA
  tamaño              70% del saldo libre, calculado al comprar
  suelo               0,03 SOL — por debajo NO compra
  posiciones          1 a la vez
  reserva             0,02 SOL
  comisión prioridad  0,07 $
  slippage            12% compra / 25% venta · 1 reintento

SALIDA
  al +35%             vende el 100%
  más de 60 s por debajo del precio de compra → vende entera
  stop-loss           65%
  moonbag             ninguno
```

### Qué cambió el 21-ago por la noche y por qué

**La decisión se toma al detectar la moneda, no al final.** Antes el historial
del creador se miraba después de gastar 3 segundos vigilando volumen y
carteras. Ahora se pregunta primero: lo que no cumple se cae antes de gastar
una sola llamada RPC, y lo que cumple se compra sin esperar a nadie.

**Se quitó la espera de confirmación** (8 SOL y 6 carteras). Eran entre 1 y 3
segundos de espera. La apuesta pasa a ser quién lanza, no que el mercado lo
confirme.

Esto tiene un coste conocido y hay que decirlo: **las 6 carteras eran el
filtro que más pesaba** — de 0-5 carteras el 5,8% llega a 6K, de 12-20 el
15,0%, y bajarlo de 6 a 5 hundía el acierto del 43,7% al 34,7%. Se cambia ese
filtro por el del creador. Si el ≥20% no compensa, esto es lo primero que hay
que devolver a 8,0 / 6.

**Exigir ≥20% deja pasar el 0,3% del flujo**: 13 monedas de 5.167 evaluaciones.
Cuenta con horas entre operación y operación. Que no compre no significa que
esté roto.

**El tamaño sale del saldo**, no de un número fijo: 70% de lo libre, calculado
en el instante de comprar y dentro del mismo cerrojo que lo reserva. Sube solo
cuando sube la wallet.

**Y hay un suelo de 0,03 SOL.** La renta de la cuenta de token son 0,00204 SOL
y pump.fun cobra un 2,25% por lado: en una compra de 0,02 SOL eso pasa del 15%
de ida y vuelta, así que la operación nace necesitando un +15% sólo para
empatar.

## De dónde sale cada número

Nada de esto es intuición. Sale de cruzar **1.016 monedas** (la lectura del bot
en los 5 primeros segundos) con su máximo real según pump.fun, y de comprobarlo
después sobre **las últimas 24 horas**.

### Lo que separa a las que se disparan

**Cuánta gente distinta compra, no cuánto dinero entra:**

```
0-5 carteras     5,8% llegan a 6K
5-8              8,3%
8-12            12,2%
12-20           15,0%
```

Bajar de 6 a 5 carteras hunde el acierto del 43,7% al 34,7%. Es el filtro que
más pesa y el que no hay que tocar.

### El techo de entrada

Toda moneda de pump.fun nace en **2.097 $** — 30 SOL virtuales y 1.073.000.191
tokens. Como el precio solo sube si alguien compra, si todos venden vuelve ahí.

```
compras a 2.800 $  →  si muere, pierdes el 25%
compras a 8.000 $  →  si muere, pierdes el 76%
```

El techo no es una preferencia: es lo que acota la pérdida.

### La salida

```
vende todo a x1,50 ... +0,145 por cada 1 SOL
vende todo a x1,75 ... +0,228   ← el mejor en tres muestras distintas
vende todo a x2,00 ... +0,177
```

No es un pico de suerte: 1,70 / 1,80 / 1,85 dan entre +0,196 y +0,228, o sea
una meseta. Remuestreando 1.000 veces sale positivo el **100%** de las veces.

Se descartaron x3 y x4, que en la media salían mejor pero **pierden dinero en la
primera mitad de la muestra**: todo su resultado venía de una moneda que hizo
x14,4.

**El moonbag restaba**: vender el 100% da +0,228; dejar el 50% corriendo, +0,059.
Lo que queda dentro casi nunca sigue subiendo — vuelve al precio de compra y sale
por el suelo del trinquete.

### El minuto por debajo de la compra

El suelo estructural son 2.097 $ y se entra en 2.800. Una moneda que al minuto
sigue por debajo de lo pagado no ha encontrado comprador, y solo le queda caer.
El stop del 65% está demasiado abajo para sacarla.

### El tamaño

```
posición   tu compra mueve   coste fijo   coste total
  0,03          0,36%          8,33%        8,69%
  0,15          1,80%          1,67%        3,47%   ← el mínimo
  1,00         12,15%          0,25%       12,40%
```

Por debajo mandan las comisiones fijas; por encima, tu propia compra mueve la
curva. **La meseta buena va de 0,10 a 0,30.**

Con 1,64 SOL de saldo, 0,20 por posición deja 0,66 de colchón sobre el mínimo
necesario para aguantar la peor racha de 2.000 simulaciones.

### Por qué el filtro de creador está apagado

- el **85,2%** de las monedas con arranque limpio las lanza alguien con **cero
  migraciones**
- los creadores que **repiten rinden peor**: 13,3% frente a 16,4%

Exigir un 25% llegó a tumbar el 98% del flujo: aquella estrategia hizo **3
compras en toda su vida**.

Lo que sí queda es el castigo: **al primer rug, bloqueado**. Se cuenta por
pérdida real leída de la cadena, no por "tocó un stop", así que el que clava un
TP1 y luego tira también cuenta.

---

## Lo que rinde

Medido sobre las **últimas 24 horas reales** — 31 operaciones que el filtro
habría hecho, cruzadas con el máximo real de cada moneda:

```
operaciones      31   (1,29 por hora)
acertaron        32,3%
```

```
posición   capital mínimo   por día      en dólares
  0,10       0,44 SOL       +0,4455        +33 $
  0,20       0,98 SOL       +0,8317        +62 $      ← configurado
  0,30       1,49 SOL       +1,1431        +86 $
  0,50       2,54 SOL       +1,5397       +115 $
  0,66       3,20 SOL       +1,6390       +120 $      ← techo absoluto
```

**Por encima de 0,66 SOL por posición el rendimiento baja**, y a partir de 1,5
es negativo: tu compra mueve tanto la curva que te comes tu propio pump. No es
cuestión de meter más capital.

Para **5 SOL diarios harían falta 95 operaciones al día** en vez de 31, y ese
flujo no existe con este filtro. Aflojarlo baja el acierto más de lo que sube el
número de operaciones.

### Cómo leer estos números

**Son un techo, no una previsión.** La simulación usa el máximo alcanzado, o sea
da por cobrada toda venta que el precio llegó a rozar. En la realidad hay
retraso, órdenes que fallan y precios que suben y bajan antes de que entre la
venta.

Y el acierto real de las 24 h fue **32,3%**, once puntos por debajo del 43,7%
del backtest largo. Esa diferencia es la medida de cuánto optimismo lleva
dentro.

---

## Operación

```bash
bash desplegar.sh                  # desde tu PC: sube y reinicia sin abandonar posiciones
bash /root/ai-bot/solo-devmig.sh   # en el servidor: deja limpio y arranca
```

Para cambiar el tamaño de la posición:

```bash
sed -i -E 's/^  buy_amount: [0-9.]+/  buy_amount: 0.20/' /root/ai-bot/bots/bot-sniper-devmig.yaml
bash /root/ai-bot/solo-devmig.sh
```

**Nunca reiniciar con una posición abierta.** El bot no las reanuda: detecta la
marca, avisa de bolsa huérfana y deja de vigilarse el take-profit, el stop y el
anti-rug. `solo-devmig.sh` comprueba esto y se niega a tocar nada si las hay.

Servidor `root@159.89.19.12` (`ubuntu-s-1vcpu-1gb-fra1`), clave en
`~/.ssh/id_droplet_bot`. El segundo droplet (`sniper-bot`, 142.93.124.57) está
vacío. La clave privada no se copia en ningún documento.

Panel AGALAZ BANK: `https://159-89-19-12.sslip.io`, usuarios Manu y Ricardo.
Solo lee, no firma (`PANEL_HOT_WALLET=false`).

---

## Bugs arreglados, con su síntoma

Ninguno daba error. Todos costaban dinero o tiempo en silencio.

**`yolo_mode` faltaba en el yaml.** Sin esa clave el bot procesa **una moneda y
se para para siempre**. El 21-ago estuvo de 07:44 a 10:05 viendo 2.349 monedas y
evaluando **cero**, con `systemctl` diciendo `active` todo el rato. Y se lee de
`filters`, no de `trade`: puesta en `trade` no hace nada.

**`_SOL_USD` clavado en 75 $** desde el 9-ago. Con SOL a 91,24 el bot creía
comprar a 2.800 $ y compraba a 3.406. Ahora se refresca cada hora y **el techo
se escala con él**, porque actualizar el precio a secas mueve el filtro sin que
nadie lo decida.

**`ignorar_listas_de_devs: true`** apagaba todas las listas de creadores,
incluido el auto-baneo por rugs. `rugs_para_bloquear` no se aplicaba nunca.

**La tanda no se cerraba nunca.** `_compras_de_la_tanda` solo subía: con
`max_real_buys: 3` el bot compraba tres veces y se callaba. Estuvo **23 horas
sin comprar** con 4.435 monedas cumpliendo delante.

**El programa de token equivocado.** El 10% de las ventas (37 de 373) cerraba en
falso sin vender nada. La dirección de la cuenta sale de wallet + mint +
**programa**; con el programa mal la cuenta no existe y el saldo se lee cero.

**El moonbag del stop-loss** se calculaba solo con los escalones de porcentaje:
dejaba media posición tirada en vez del 3,75%.

**La curva migrada.** Al migrar, `calculate_price` lanza y el monitor daba
vueltas para siempre: 16.623 errores en hora y media con el bot bloqueado.

**El bote no se alimentaba.** Colgaba de `max_perdidas_seguidas`, una clave que
ningún yaml define.

**Dos bots compraban la misma moneda.** Ahora se reserva con `O_EXCL` antes de
comprar.

---

## Lo que sigue sin resolver

- **El P&L teórico miente, y de signo.** Reportó +229 SOL en una operación que
  dio −0,02. Lo que decide sale de los deltas de la wallet, pero no te fíes de
  lo que llegue por Telegram.
- **`venta_muerta` se mira una sola vez** a los 120 s.
- **`Custom 6024`**: curva vacía, no hay contrapartida a ningún precio.
- **Operaciones a mano en la misma wallet.** El 20-ago hubo compras y ventas de
  0,009 a 0,094 SOL que no salen en ningún log. Se descartó un segundo bot
  (droplet limpio), el panel (no firma) y el resto de servicios (parados).
  **Mientras la wallet se comparta, su saldo no sirve para medir la estrategia.**
- **Nada está validado en real todavía.** Hacen falta 2-3 días de operación para
  saber cuánto se come la ejecución del +0,228 medido.
