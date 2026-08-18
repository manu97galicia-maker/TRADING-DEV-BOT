# Qué caracteriza a las que se disparan

Notas de lo aprendido midiendo, no de lo que parece. Se actualizan cuando haya
más muestra.

Método: se cruza el perfil que el bot anota al ver la moneda (volumen, compras,
reparto de compradores, market cap, segundo en que la vio) con lo que hizo
después, sacado de velas de 30 s. **"Hacer x2" significa x2 desde el precio al
que el bot la vio**, no desde el nacimiento.

Última medición: 18-ago-2026 · **92 monedas** con las dos cosas · base: el
**17%** hace x2.

---

## Lo que más separa

### 1. Que nadie haya vendido todavía — el rasgo más fuerte

```
transacciones por compra < 1,2   ->  41% hacen x2   (+24 sobre la base)
transacciones por compra 1,2-1,6 ->   7%            (-10)
```

`tx / compras` mide cuántas operaciones hay por cada compra. Si vale 1, **todas
las transacciones son compras y nadie ha vendido aún**; si vale 1,5, hay una
venta por cada dos compras.

Es la versión limpia de "presión compradora": no mide cuánto dinero entra sino
si **alguien ya está saliendo**. Y es lo que más separa de todo lo medido.

### 2. Llegar pronto

```
vista en menos de 2 s   ->  29%   (+12)
vista entre 2 y 5 s     ->  19%   (+2)
vista entre 5 y 15 s    ->   6%   (-11)
```

Entrar tarde es lo peor que se mide. A partir del quinto segundo el porcentaje
se hunde por debajo de la base.

### 3. Compradores que no repiten

```
compras por cartera < 1,3    ->  23%   (+6)
compras por cartera 1,3-1,8  ->   4%   (-14)
```

Cada cartera compra una vez y ya. Cuando el mismo puñado de carteras repite
compras, va mucho peor.

---

## Lo que apenas separa

```
primer bloque < 10%   ->  19%  (+1)      el anti-bundle apenas aporta aqui
carteras 20 o mas     ->  19%  (+2)
SOL por cartera 1-3   ->  17%  (+0)
```

El **anti-bundle**, que en su día parecía el filtro más fuerte, aquí aporta un
punto. Tiene explicación: se midió contra el máximo histórico de la moneda a lo
largo de toda su vida, y aquí se mide un x2 desde la entrada. Cuando el
objetivo es cercano, el bundle deja de importar.

---

## Lo que NO se replica

Una medición anterior (sobre 279 monedas, contra `ath_market_cap`) decía que
**1,3-1,8 compras por cartera** era la mejor banda, con un 90% llegando al
+30%. Aquí esa misma banda es **la peor** (4%).

No es que una esté mal y la otra bien: **miden cosas distintas**. La primera
usaba el máximo histórico —que incluye subidas que no se pueden cobrar porque
el pool está seco— y la segunda usa el recorrido real desde la entrada. Cuando
las dos discrepan, vale la segunda.

Conclusión práctica: **no fiarse de nada medido contra `ath_market_cap`.**

---

## Avisos sobre estos números

- **92 monedas es poco.** Las bandas tienen entre 16 y 58 casos, así que una
  diferencia de ±5 puntos es ruido. Solo me creo las dos primeras (+24 y +12).
- La muestra son monedas que el bot **llegó a evaluar**, no todas las que
  salen. Está sesgada hacia las que ya pasaban algún filtro.
- Todo esto mide **desde donde el bot la vio**, que depende de la configuración
  de ese momento. Si cambia el techo de market cap, cambia el punto de partida
  y estos porcentajes se mueven.

---

## Qué haría con esto

El rasgo de `tx/compras` **no está implementado como filtro**. Es el que más
separa y sale gratis: los dos números ya se cuentan en memoria desde el
websocket. Sería un campo tipo `max_tx_por_compra: 1.2`.

Y confirma que la ventana corta importa: por debajo de 2 segundos casi duplica
la base, por encima de 5 la hunde.
