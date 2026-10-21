# Pump Bot Development Guide

## Instrucciones BSC obligatorias

Antes de revisar, configurar, desplegar o arrancar el bot de BNB Smart Chain
(Four.meme o Flap), lee completo `INSTRUCCIONES.md` y respeta sus puertas de
seguridad, rutas, validación paper y procedimiento de activación real. No
actives operaciones reales ni muestres secretos sin autorización explícita.

This is a trading bot for pump.fun and letsbonk.fun platforms that snipes new tokens and implements various trading strategies.

## REGLA 0 — LAS ORDENES DE MANU SON PERMANENTES, Y NO SE TOCA NADA MAS

Pedida el 25-sep: *"pon una orden de que las ordenes sean permanentes, que no
hagas cambios"*. Va la primera porque manda sobre todo lo demas de este
fichero.

**Este bot opera con dinero real de una persona que no programa.** Cada
numero que hay en `bots/bot-paper-ai-entry.yaml` esta ahi porque Manu lo
pidio, y sigue ahi hasta que Manu pida otra cosa. Las dos mitades de la regla:

### 1. No se cambia nada que no se haya pedido

Se hace lo pedido. Ni mas, ni de paso, ni "ya que estaba".

- **Un numero de configuracion NUNCA se mueve por iniciativa propia.** Ni
  aunque la cuenta diga que esta mal, ni aunque un test lo sugiera, ni aunque
  haya un comentario en el YAML recomendandolo. Si un numero parece
  equivocado: **se dice, con la cuenta hecha, y se espera.**
- **Tampoco se arreglan cosas "de paso"**: ni un log que miente, ni un panel
  incompleto, ni una herramienta de diagnostico. Se avisa y se ofrece. Lo
  decide el.
- Lo que SI se puede hacer sin preguntar: **leer, calcular, diagnosticar y
  contar lo que se ve.** Mirar el codigo no cambia nada.
- Si al hacer lo pedido hace falta tocar otra cosa para que no quede roto
  (un test que fija el numero viejo, una tabla del YAML o de
  `COMO_FUNCIONA.md` que se queda mintiendo), eso SI va incluido: forma parte
  de dejar lo pedido bien hecho, no es un cambio aparte. Y se dice en la
  respuesta.

### 2. Lo que ya se ordeno, sigue ordenado

Un cambio nuevo no puede deshacer uno viejo en silencio. Este repo tiene
historial de justo eso, y cada caso costo dinero o dias:

- los frenos contra rugs vivian dentro de la fuente `holders`; al apagar esa
  fuente se apagaron con ella sin que nada lo dijera. **FUCKETHAN, −91,9%.**
- `min_compradores` se calibro para una entrada al 96% de curva y se quedo en
  30 mientras la entrada bajaba al 71%. **El bot dejo de comprar durante
  dias** y nadie sabia por que.
- `min_presion_ratio` se pidio y se dejo escrito, pero encenderlo solo no
  hace nada sin `max_buy_age`: habria quedado puesto y mudo.

Asi que **antes de cerrar un cambio hay que mirar que no desactive otra cosa
que Manu pidio antes**, y si la desactiva, decirlo en voz alta en la
respuesta en vez de dejarlo pasar.

### Como se aplica esto en la practica

- Cuando algo va mal, lo primero es **diagnosticar y contarlo**, no tocar.
  Bajar filtros persiguiendo un problema que estaba en otro sitio es lo que
  costo el 21 y el 22-sep.
- Si hay dos lecturas posibles de lo pedido y llevan a sitios distintos con
  dinero de por medio, **se pregunta** antes de escribir.
- Cada cambio deja escrito en el YAML **que se pidio, con las palabras de
  Manu y la fecha**, y que cuesta. Es lo que permite deshacerlo despues.

## REGLA 1 — HELIUS NO SE ENCIENDE SOLO, Y SIEMPRE CON PLAZO

Pedida el 16-oct: *"activa helius cuando yo te digo y siempre preguntame
durante cuanto tiempo para no gastar a lo tonto"*.

**Helius se paga por uso.** El bot consume cuota cada segundo que corre:
`listener_type: logs` es una suscripcion WSS abierta a Helius, y `node.max_rps`
(20) son consultas por segundo mientras haya posiciones vivas.

### Las dos mitades de la regla

1. **No se enciende por iniciativa propia.** Ni "para probar", ni "para ver si
   compra", ni porque acabe de subirse un cambio. Se arranca cuando Manu lo
   dice, con esas palabras o equivalentes.

2. **Siempre se pregunta DURANTE CUANTO TIEMPO**, antes de dar el comando.
   No se asume "hasta que lo pares": eso es justo lo que gasta de mas. Si
   Manu dice "arranca" sin plazo, se pregunta el plazo primero.

### Que significa "activar" en la practica

El bot corre como servicio systemd en el droplet (`pump-bot`). Nadie desde
una sesion de Claude puede arrancarlo ni pararlo: no hay red hacia el droplet
ni `ssh`. Asi que "activar" es **darle a Manu el comando**, y ese comando
tiene que llevar el apagado automatico dentro -- no vale decirle "y luego lo
paras tu", porque eso es exactamente lo que falla.

    arrancar:  bash /root/ai-bot/deploy/arranca.sh
    parar:     systemctl stop pump-bot

### Lo que NO cubre esta regla

Mirar logs, leer configuracion, calcular o diagnosticar no gasta Helius: eso
se hace sin preguntar. Lo que gasta es el bot corriendo.

## Project Structure

- `src/` - Main source code
- `learning-examples/` - Educational scripts and examples
- `bots/` - Bot configuration files (YAML)
- `logs/` - Log files from bot executions
- `idl/` - Interface definition files for Solana programs

## Bash Commands & Development

### Setup Commands
```bash
# Install dependencies
uv sync

# Activate virtual environment (Unix/macOS)
source .venv/bin/activate

# Install as editable package
uv pip install -e .
```

### Running the Bot
```bash
# Run as installed package
pump_bot

# Run directly
uv run src/bot_runner.py
```

### Learning Examples
```bash
# Bonding curve status
uv run learning-examples/bonding-curve-progress/get_bonding_curve_status.py TOKEN_ADDRESS

# Listen to migrations
uv run learning-examples/listen-migrations/listen_logsubscribe.py
uv run learning-examples/listen-migrations/listen_blocksubscribe_old_raydium.py

# Compute associated bonding curve
uv run learning-examples/compute_associated_bonding_curve.py

# Listen to new tokens
uv run learning-examples/listen-new-tokens/listen_logsubscribe_abc.py
uv run learning-examples/listen-new-tokens/compare_listeners.py
```

### Code Quality
```bash
# Format code
ruff format

# Lint code
ruff check

# Fix linting issues
ruff check --fix
```

## Pump.fun protocol notes (gotchas)

- The vendored IDL at `idl/pump_fun_idl.json` is **incomplete**: it does not list `bonding-curve-v2` (BC trades) or `pool-v2` (PumpSwap trades). Both are required on-chain. `bonding-curve-v2` PDA seed is `["bonding-curve-v2", mint]` under the pump program; `pool-v2` is `["pool-v2", base_mint]` under the pump-amm program. Always cross-check account lists against a recent successful on-chain tx — IDL alone will produce broken code.
- BC `buy` ix is **18 accounts** (post 2026-04-28 upgrade). The trailing account is one of 8 `BREAKING_FEE_RECIPIENTS` (mutable), AFTER `bonding-curve-v2`.
- BC `sell` ix is **16 accounts non-cashback / 17 cashback**. Cashback path inserts `user_volume_accumulator` (PDA seed `["user_volume_accumulator", user]`) BEFORE `bonding-curve-v2`. Detect cashback from byte 82 of the bonding-curve account data, or via the `is_cashback_coin` field returned by the curve manager.
- The BondingCurve account is **83 bytes** (was 81): trailing fields are `is_mayhem_mode: bool` then `is_cashback_coin: bool`. PumpSwap Pool is **245 bytes** with the same `is_cashback_coin` byte at offset 244.
- The IDL instruction is `create_v2` (snake_case). `create_v2` args: `name (str), symbol (str), uri (str), creator (pubkey), is_mayhem_mode (bool), is_cashback_enabled (OptionBool 1B)`. `OptionBool` is a struct wrapping a single bool — serialized as 1 byte, not 2.
- `extreme_fast_mode` skips the curve-state RPC fetch — make sure event parsers populate `is_mayhem_mode` and `is_cashback_coin` on `TokenInfo` from the `CreateEvent` payload (otherwise mayhem coins use the wrong fee_recipient and cashback coins use the wrong sell account count).

## Code Style & Conventions

### Python Style (Ruff Configuration)
- **Line length**: 88 characters
- **Indentation**: 4 spaces
- **Target Python**: 3.11+
- **Quote style**: Double quotes
- **Import sorting**: Enabled

### Linting Rules
- Security best practices (S)
- Type annotations (ANN)
- Exception handling (BLE, TRY)
- Code complexity (C90)
- Pylint conventions (PL)
- No commented-out code (ERA)

### Code Organization
- **Imports**: Standard library, third-party, local imports
- **Docstrings**: Google-style for functions and classes
- **Type hints**: Required for all public functions
- **Logging**: Use `get_logger(__name__)` pattern
- **Error handling**: Comprehensive try-catch with proper logging

### File Structure Patterns
- `__init__.py` files for all packages
- Separate concerns: client, trading, monitoring, platforms
- Abstract base classes in `interfaces/`
- Platform-specific implementations in `platforms/`

## Workflow & Development Practices

### Configuration Management
- Environment variables in `.env` file
- Bot configurations in YAML files under `bots/`
- Platform detection from config files
- Validation of platform-listener combinations

### Logging
- Timestamped log files in `logs/` directory
- Format: `{bot_name}_{timestamp}.log`
- Different log levels for development vs production
- Centralized logger utility in `utils/logger.py`

### Trading Architecture
- Universal trader pattern for platform abstraction
- Platform-specific implementations (pumpfun, letsbonk)
- Position tracking and management
- Priority fee management (dynamic/fixed)

### Monitoring Systems
- Multiple listener types: logs, blocks, geyser, pumpportal
- Universal listeners with platform abstraction
- Event parsing and processing
- Real-time data stream handling

### Antes de decirle a alguien que ejecute un script de `deploy/`

Comprobar que ese fichero EXISTE en el commit que tiene la maquina de destino:

```bash
git log --oneline --diff-filter=A -- deploy/<fichero>
```

El 18-sep esto costo tres viajes seguidos. Se mandaron `deploy/devs.sh`,
`deploy/instalar_comandos.sh` y `deploy/arranca.sh` a un droplet parado en un
commit anterior a los tres, y los "No such file or directory" se
diagnosticaron como carpeta equivocada. El unico comando que siempre funciona
en una maquina desactualizada es `cd <repo> && git pull`, porque solo usa git.

### Development Workflow
1. Make changes to source code
2. Run `ruff check --fix` for linting
3. Run `ruff format` for formatting
4. Test with learning examples (standalone scripts) before deploying bots
5. Use separate processes for production bot instances

### Bot Configuration
- YAML-based configuration files
- Environment variable interpolation
- Platform-specific settings
- Trading parameters (slippage, amounts, timeouts)
- Filter configurations for token selection
- Cleanup and account management settings

### Testing Strategy
- Learning examples serve as integration tests
- Manual testing with learning scripts
- Configuration validation before bot startup
- Logging verification for debugging

## Key Features

- **Multi-platform support**: pump.fun and letsbonk.fun
- **Multiple listening methods**: WebSocket logs, block subscription, Geyser
- **Trading strategies**: Time-based, take profit/stop loss, manual
- **Priority fee management**: Dynamic and fixed fee strategies
- **Account cleanup**: Automated token account management
- **Extreme fast mode**: Skip validation for faster execution

## Security Considerations

- Private keys stored in environment variables
- No sensitive data in configuration files
- Comprehensive input validation
- Error handling to prevent crashes
- Rate limiting and retry mechanisms
