# Estrategia actual

Estado a **20 de agosto de 2026**. Esto es lo que corre en el servidor, no lo
que se planea.

**Una sola estrategia viva: `unica`.** Las tres anteriores (DEFINITIVA, ELITE,
RAPIDA) están paradas y deshabilitadas.

---

## La estrategia

```
ENTRADA
  market cap          ≤ 2.800 $
  volumen             >= 8 SOL  (sin techo)
  carteras distintas  ≥ 6
  creador             sin filtro
  edad máxima         60 s

COMPRA
  tamaño              0,20 SOL   (+35% por cada SOL ganado)
  posiciones a la vez 2   ·  tope por tanda 6
  slippage            12% compra / 25% venta  ·  1 reintento
  reserva             0,05 SOL

SALIDA
  al +75%             vende el 100%
  más de 60 s por debajo del precio de compra → vende entera
  stop-loss 80%  ·  sin moonbag
```

**Los tres filtros salen de la misma medida**: 1.016 monedas (la lectura del
bot en los 5 primeros segundos) cruzadas con su máximo real según pump.fun. No
queda ningún filtro puesto a ojo.

---

## Por qué cada número

### La entrada

```
base de la muestra ................  8,1% llegan a 6K   3,1% a 12K
6+ carteras, >=8 SOL, MC <=2.800 .. 20,0% llegan a 6K   7,5% a 12K
```

**Lo que separa es cuánta gente distinta compra, no cuánto dinero entra:**

```
0-5 carteras     5,8% llegan a 6K
5-8              8,3%
8-12            12,2%
12-20           15,0%
```

**El techo de volumen se quitó el 20-ago, y esto corrige una medida anterior.**
Sobre *todas* las monedas, más de 15 SOL rinde peor (9,1% frente a 10,8%): SOL
de golpe suele ser bundle. Pero esa medida no estaba condicionada.

Exigiendo ya 6 carteras distintas **y** market cap bajo 2.800, el volumen alto
deja de ser bundle — 20 SOL repartidos entre seis compradores con el precio aún
por debajo de 2.800 es demanda real que todavía no ha movido el market cap:

```
con techo de 15 ... 40 casos, 45,0% aciertan, +0,2280, 1,94 ops/h
sin techo ......... 71 casos, 43,7% aciertan, +0,2168, 3,20 ops/h
```

El tramo que se tiraba (>15 SOL) son 31 monedas de las que el **41,9%** llegaba
al +75%. Se pierden 1,3 puntos de acierto y el SOL por hora sube un **69%**.

**El suelo de carteras sí importa**: bajar de 6 a 5 hunde el acierto al 34,7%
(+0,1411). Sobraba el techo de volumen, no el suelo de carteras.

**El 18% de la curva no era la señal**: pasarlo en 5 segundos da 9,3% frente al
6,7% de no pasarlo. Existe, pero es ruido al lado de lo anterior.

### La salida

```
vende todo a x1,50 ... +0,145 por cada 1 SOL
vende todo a x1,75 ... +0,228   <- el mejor en tres muestras distintas
vende todo a x2,00 ... +0,177
la escalera anterior . +0,159
```

No es un pico de suerte: los vecinos (1,70 / 1,80 / 1,85) dan entre +0,196 y
+0,228, o sea una meseta. Remuestreando 1.000 veces sale positivo el **100%**
de las veces, con el 90% de los casos entre +0,102 y +0,333.

Se descartaron x3 y x4, que en la media salían mejor pero **pierden dinero en
la primera mitad de la muestra**: todo su resultado venía de una sola moneda
que hizo x14,4.

**El moonbag restaba**: vender el 100% da +0,228, el 80% da +0,161 y el 50% da
+0,059. Lo que queda dentro casi nunca sigue subiendo — vuelve al precio de
compra y sale por el suelo del trinquete.

### El minuto por debajo de la compra

El suelo estructural son 2.097 $ y se entra en 2.800. Una moneda que al minuto
sigue por debajo de lo pagado no ha encontrado comprador, y lo único que le
queda por delante es la caída a ese suelo. El stop del 80% está demasiado abajo
para sacarla.

### El filtro del creador, apagado

Era el único sin una medida detrás, y los datos dicen lo contrario de la
intuición:

- el **85,2%** de las monedas con arranque limpio las lanza alguien con **cero
  migraciones**, así que exigir historial deja fuera casi todo el flujo
- los creadores que **repiten rinden peor**: 13,3% llegan a x1,75 frente al
  16,4% de los que solo aparecen una vez

Con ELITE al 25% llegó a tumbar el 98% de lo que le llegaba: **3 compras en
toda su vida**. El 20-ago tumbó a TradeFi, que cumplía todo lo demás, solo por
ser la primera moneda de su creador.

---

## Cuánto y a qué ritmo

```
3,20 operaciones por hora   (~77 al dia; rachas de hasta 12 y horas enteras sin nada)
```

**El tamaño óptimo es 0,15 SOL, con meseta de 0,10 a 0,25.** Por debajo se lo
comen las comisiones fijas; por encima, tu propia compra mueve la curva:

```
posición   tu compra mueve   coste fijo   total
  0,03          0,36%          8,33%      8,69%
  0,15          1,80%          1,67%      3,47%   <- el mínimo
  1,00         12,15%          0,25%     12,40%
```

**Capital necesario.** Con un 45% de aciertos ganando +75% y un 55% de fallos
perdiendo 15%, Kelly da el 34% del saldo; con 40 datos de muestra lo prudente
es Kelly/4 ≈ 8,5%:

```
saldo    por operación
  1,0        0,085
  2,0        0,170
  5,0        0,425
```

Para operar a 0,20 hacen falta ~2,5 SOL. Con menos, dos fallos seguidos —que
con un 55% de fallos pasa constantemente— dejan la cuenta fuera de juego.

---

## Primeras operaciones reales (20-ago, 12:00-12:25)

```
DAP        +75% cobrado          +0,1484 SOL
Snezhana   bajo_entrada          -0,0469
Dolly      bajo_entrada          -0,0496
```

Una ganadora paga tres perdedoras y sobra. Con esa asimetría basta con acertar
una de cada cuatro, y lo medido es 45%. **Son 3 operaciones: no significan
nada todavía**, pero las tres piezas nuevas se ven funcionando — el objetivo
único cobró, la regla del minuto acotó las pérdidas y el bote subió la compra
de 0,2000 a 0,2048.

---

## Operación

```bash
bash desplegar.sh              # sube y reinicia esperando a que no haya posiciones
bash desplegar.sh --solo-subir # sube sin reiniciar
bash desplegar.sh --ya         # reinicia abandonando posiciones (avisa de cuáles)
```

**Nunca reiniciar con una posición abierta**: el bot no las reanuda, la deja
como bolsa huérfana y deja de vigilarse el take-profit, el stop y el anti-rug.

Para dejar el droplet con una sola estrategia viva:

```bash
bash /root/ai-bot/solo-unica.sh
```

Para y **deshabilita** todo lo demás. Lo de deshabilitar importa tanto como lo
de parar: un servicio `inactive` pero `enabled` vuelve solo en el siguiente
reinicio del servidor, y entonces hay dos bots comprando con la misma wallet
sin que nadie se entere.

Servidor: `root@159.89.19.12` (`ubuntu-s-1vcpu-1gb-fra1`), clave en
`~/.ssh/id_droplet_bot`. El segundo droplet (`sniper-bot`, 142.93.124.57) está
vacío. La clave privada no se copia en ningún documento.

El panel AGALAZ BANK vive en `https://159-89-19-12.sslip.io` (usuarios Manu y
Ricardo). Solo lee: `PANEL_HOT_WALLET=false`.

---

## Bugs arreglados en esta sesión

- **La tanda no se cerraba nunca.** `_compras_de_la_tanda` solo subía: con
  `max_real_buys: 3` cada bot compraba tres veces y se callaba para siempre. El
  19-ago estuvo **23 horas sin comprar** con 4.435 monedas cumpliendo delante, y
  el servicio en `active` todo el rato.
- **El programa de token equivocado.** El 10% de las ventas (37 de 373) cerraba
  en falso sin vender nada: la dirección de la cuenta sale de wallet + mint +
  **programa**, y con el programa mal la cuenta no existe y el saldo se lee como
  cero. Ahora se miran los dos y se corrige el campo, porque la venta construye
  sus cuentas con ese mismo campo.
- **El moonbag del stop-loss.** Se calculaba solo con los escalones de
  porcentaje: dejaba media posición tirada en vez del 3,75%.
- **La curva migrada.** Al migrar, `calculate_price` lanza y el monitor daba
  vueltas para siempre: 16.623 errores en hora y media con un bot bloqueado.
  Ahora se pregunta el precio al DEX, y si tampoco hay, a las 40 lecturas se
  suelta el hueco.
- **El bote no se alimentaba.** Colgaba de `max_perdidas_seguidas`, una clave
  que ningún YAML define.
- **Dos bots compraban la misma moneda.** BECKY salió con tres `CUMPLE` en el
  mismo segundo. Ahora se reserva con `O_EXCL` antes de comprar.

---

## Lo que sigue sin resolver

- **El P&L teórico miente, y de signo.** Reportó +229 SOL en una operación que
  dio −0,02, y +22,5 en una que perdió 0,05. Solo es cosmético (lo que decide
  sale de los deltas de la wallet), pero no fiarse de lo que llegue por
  Telegram.
- **`venta_muerta` se mira una sola vez a los 120 s.**
- **`Custom 6024`**: curva vacía, no hay contrapartida a ningún precio.
- **Operaciones a mano en la misma wallet.** El 20-ago hubo compras y ventas de
  0,009 a 0,094 SOL que no salen en ningún log del bot. Se descartó un segundo
  bot (droplet limpio), el panel (no firma) y el resto de servicios (parados).
  **Mientras la wallet se comparta, su saldo no sirve para medir la
  estrategia.**
- **Nada de esto está validado en real.** Los +0,228 salen de simular sobre el
  máximo alcanzado, o sea suponiendo que se vende en cuanto el precio toca el
  objetivo. Es un techo. Hacen falta ~3 días de operación real para saber
  cuánto se lo come la ejecución.
