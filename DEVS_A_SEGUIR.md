# Devs a seguir

Creadores que Manu quiere vigilar. **El bot filtra por dirección de wallet de
Solana**, así que un perfil de X no se puede meter tal cual: hace falta la
wallet con la que lanza.

## Pendientes de resolver la wallet

| perfil | wallet | estado |
|---|---|---|
| https://x.com/xWugi_wara | — | **falta la dirección** (anotado 13-ago-2026) |

Para resolverlo hace falta una de estas dos cosas:

- que el perfil enlace a una moneda suya en pump.fun — de ahí sale el `creator`
  en una llamada, y con la wallet se puede sacar su historial entero
- o la dirección directamente

Con la wallet, comprobar antes de meterla en la lista:

```bash
# historial del creador: cuántas lanzó y cuántas migraron
curl -s "https://frontend-api-v3.pump.fun/coins?creator=<WALLET>&limit=200"
```

Y **verificar la liquidez real en DexScreener antes de fiarse del market cap**.
El campo `ath_market_cap` de pump.fun se infla con el pool seco: un creador
enseñaba 89.950.686 $ de máximo con 1.387 $ de liquidez y 5 $ de volumen
diario. Migrar sí es señal fiable — exige que entren 85 SOL de verdad en la
curva y eso no se puede fingir.

## Listas ya construidas (en el droplet, `/root/ai-bot`)

- `top_devs_25_solidos.json` — 25 creadores con 10+ migraciones cada uno,
  54 lanzamientos/día entre todos. **La recomendada.**
- `top_devs_25.json` — máxima calidad: 614 migraciones sumadas, pero solo 27
  lanzamientos/día.
- `top_devs_25_cien.json` — llega a 120/día, pero 4 granjas aportan 93 de esos
  lanzamientos y suman **una** migración entre las cuatro. No usar.
- `top_devs_liquidez_real.json` — los 2 únicos (de 22 candidatos con mediana
  >14.000 $) que tenían una moneda con 10.000 $ de liquidez real.

## Cómo se meten en el bot

En `bots/bot-sniper-premium.yaml`:

```yaml
  devs_marcados: [ "wallet1", "wallet2", ... ]   # compran buy_amount_marcados
  # o la puerta general por reputación:
  pumpfun_dev:
    enabled: true
    min_rate: 0.20        # % migrado mínimo
    min_migrations: 1
```
