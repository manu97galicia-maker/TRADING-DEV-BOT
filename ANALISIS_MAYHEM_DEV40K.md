# Mayhem DEV40K: corte derivado de los datos

Fuente principal: `mayhem_v2_raw.csv` (250 filas). El cruce se hizo por wallet
contra los demás `top_devs*.json`, sin sumar métricas de ventanas temporales
distintas como si fueran equivalentes.

- 21 filas cumplen mediana de pico >= $40K y 3+ lanzamientos Mayhem.
- Una wallet estaba duplicada; quedan 20 creadores únicos.
- Solo 4 filas llegan a 5 lanzamientos Mayhem.
- Ninguno de esos creadores tiene además 5+ lanzamientos en siete días
  confirmados en los otros rankings disponibles.
- Mediana del grupo seleccionado: $52.722; mediana de frecuencia: 4
  lanzamientos Mayhem; mediana de hit-rate sobre $30K: 50%.

Por eso `top_devs_mayhem40k.json` usa 3 lanzamientos Mayhem como mínimo y la
estrategia permanece en paper. La escalera de salida no puede validarse con el
CSV agregado, porque contiene picos pero no trayectorias tick a tick. Se mide
fuera de muestra con operaciones nuevas antes de cualquier decisión sobre real.
