# Estilos de rug, medidos con dinero real

Catálogo de las formas en que nos han quitado dinero, con los números de cada
caso y qué filtro lo tapa. Todo lo de aquí está **medido**, no leído: sale de
las monedas que compró el bot y de las que Manu pasó para analizar el 10-ago
de 2026.

La regla al leer esto: **un filtro solo vale si se puede comprobar ANTES de
comprar**. Varias de las señales más claras (curva seca, caída desde el ATH)
solo se ven después, y por eso no protegen — sirven para entender, no para
filtrar.

---

## 1. El market cap de humo

**Qué es.** El precio sube mucho y rápido, pero casi nadie ha comprado. Cuando
el creador tiene el supply, empujar la valoración cuesta cuatro duros: unas
pocas compras pequeñas mueven la curva entera. El market cap que ves en la
pantalla es real como número y falso como dinero, porque no hay nadie al otro
lado para pagártelo.

**Cómo se detecta antes de comprar.** Comparando lo que la moneda dice valer
con el SOL que hay **dentro de la curva**.

| Moneda | Market cap | SOL en curva | $ por SOL |
|---|---|---|---|
| `pynHmjtj…` (subió) | $3.607 | 9,015 | **$400** |
| `6r5pLyiG…` (dump) | $2.132 | 0,000 | **$82.000.000.000** |
| `4LBA49XU…` (dump) | $2.132 | 0,000 | **$101.000.000.000** |
| TNOS (jul-2026) | $27.000.000 | 0,198 | absurdo |

**Filtro.** `max_mc_por_sol_curva: 6000` y `min_curve_sol: 1.5`. Una moneda sana
está en $400-2.700 por SOL, así que el corte en 6.000 deja margen de sobra.

---

## 2. La subida sin compradores

**Qué es.** El caso que engañó a todos los demás filtros. La moneda sube hasta
$8.542 de ATH, tiene liquidez aparente ($2.852 por SOL, **pasa** el filtro
anterior) y encima **el creador es bueno** (2 migradas de 7, un 28,6%). Todo
correcto salvo una cosa: **el 97,3% del supply seguía dentro de la curva**.

Nadie la había comprado. La subida era un empujón, no demanda.

| Moneda | Supply en la curva | En manos de compradores |
|---|---|---|
| `pynHmjtj…` (subió) | 74,5% | **25,5%** |
| `EomtYTUQ…` (mala) | 97,3% | **2,7%** |
| `6r5pLyiG…` (dump) | 100% | **0%** |
| `4LBA49XU…` (dump) | 100% | **0%** |

**Filtro.** `min_supply_en_manos: 0.10`. La buena tenía dos veces y media eso;
las malas, ni la cuarta parte.

**Por qué importa tanto:** es el único de los once filtros que cazaba a
`EomtYTUQ`. Los demás la dejaban pasar.

---

## 3. El bundle repartido

**Qué es.** El supply sí está fuera de la curva, pero en manos del mismo dueño
usando muchas carteras. Parece distribución y es una sola persona esperando a
vender encima de ti.

**Cómo se detecta.** Mirar al mayor titular no basta: reparte en diez carteras
y ninguna destaca. Hay que mirar **la suma de todos los grandes** y **cuántos
titulares distintos** hay.

**Filtros.** `max_holder_pct: 0.15` (ninguno solo), `max_bundle_pct: 0.30` (la
suma de los veinte mayores) y `min_holders: 8` (titulares distintos, sin contar
la curva). Los tres juntos, porque cada uno tapa el agujero del otro.

---

## 4. El farmero de una sola moneda

**Qué es.** Wallet nueva, un lanzamiento, una migración: **100% de tasa de
éxito** en las estadísticas. Un número perfecto construido sobre una sola
moneda no es historial, es casualidad.

**Caso real.** `变狗`, comprada el 9-ago con el filtro en "mínimo 1 migración".
Dev de 1 de 1 (100%). Rug a los cuatro segundos: el anti-rug vio **28,4 SOL de
ventas contra 2,0 de compras** y salió, pero ya sin precio. Costó 0,29 SOL.

**Filtro.** `pumpfun_dev.min_migrations` en 2 o más, para que el porcentaje
tenga sobre qué calcularse.

---

## 5. El pico al nacer y sangrado

**Qué es.** Vela verde grande al lanzar y después una bajada lenta y constante.
No hay un momento de rug: la moneda simplemente se desangra mientras el stop
está demasiado lejos para hacer nada.

**Por qué nos costó dinero.** `venta_muerta_seg` estaba en **0** — desactivada —
en las estrategias del 10-ago. La moneda que nacía plana o en rojo se quedaba
dentro hasta el stop del −50%.

**Filtro.** `venta_muerta_seg: 120`. A los dos minutos se mira **una sola vez**:
si no está por encima de la entrada, se vende y se libera la posición. Si va en
verde se la deja correr y ya no se vuelve a preguntar — a partir de ahí mandan
los take-profit y el trailing.

---

## 6. El vuelco instantáneo

**Qué es.** La moneda va bien y de pronto se vuelca en segundos. El stop-loss
reacciona al precio, que es la consecuencia; para cuando salta, ya no hay pool.

**Filtro.** `anti_rug_ratio: 2.0` en ventana de 4 s: si se vende el doble de lo
que se compra, sale entera **sin esperar al precio**. Mide la causa, no el
efecto, y por eso llega antes.

---

## 7. El clon en serie

**Qué es.** El mismo creador relanza la misma moneda una y otra vez con el
mismo nombre. Comprar tres es pagar tres veces por la misma apuesta.

**Filtro.** `no_repeat_name_min: 10` — si el nombre ya salió en los últimos diez
minutos, no se compra.

---

## 8. El reincidente

**Qué es.** El dev que ya te la jugó una vez y vuelve con otra moneda.

**Filtro.** Auto-baneo: dos rugs acumulados y queda bloqueado para siempre. Se
apunta a **las dos identidades** (`user` y `creator`), porque un dev que firma
con carteras nuevas pero deja el mismo `creator` acumularía un strike suelto en
cada una y nunca llegaría a dos.

Requiere `ignorar_listas_de_devs: false`.

---

## Lo que todavía no sabemos tapar

- **El ATH miente sobre lo cobrable.** Comprobado el 10-ago: una moneda que
  declara **$13,87M de ATH** tiene hoy un pool de **$392**. "Marcó $200K" no es
  "se pudo cobrar $200K". Cualquier lista construida sobre ATH hereda este
  problema.
- **En el segundo 7 todas se parecen.** Los números de la curva y del reparto
  del supply son mucho más claros *después*. En el instante de decidir, la
  información es escasa y ruidosa.
- **No hay medida de "no dumpea" propia.** La que usamos viene de un análisis de
  Dune de julio (conserva ≥60% del precio a los 2-4 min). Es buena, pero es
  ajena y caduca.

## Lo que se está construyendo

Un rastreador de **las primeras wallets que compran en las monedas que
triunfan** (`descubrir_compradores_listos.py`). Si las mismas carteras aparecen
temprano en un lanzamiento nuevo, es la señal más difícil de falsificar de
todas: no depende de lo que el creador diga ni de cómo se vea el gráfico, sino
de quién está poniendo dinero.
