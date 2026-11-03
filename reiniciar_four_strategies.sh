#!/usr/bin/env bash
# Manual execution only. This script is never invoked by deployment or tests.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "${1:-}" != "--real" ]]; then
  printf '%s\n' 'Uso manual: bash reiniciar_four_strategies.sh --real'
  exit 2
fi
mkdir -p logs
exec 9>logs/four-strategies-restart.lock
flock -n 9 || { printf '%s\n' 'Ya hay un reinicio en curso'; exit 2; }
# Resolve every manual closure before stopping existing monitors.
.venv/bin/python reconcile_four_manual_sales.py > logs/manual-sale-plan.json
PIDS=$(.venv/bin/python - <<'PY'
from pathlib import Path
root = Path.cwd().resolve()
found = []
for p in Path('/proc').iterdir():
    if not p.name.isdigit():
        continue
    try:
        args = (p/'cmdline').read_bytes().split(b'\0')
        if any(b'run_bot_process' in a and b'bot-paper-bloque4-segundo-bloque.yaml' in a for a in args):
            raise SystemExit('El otro bot de bloque 4 está activo; revisar antes de iniciar otra estrategia')
        if any(a == b'run_four_strategies.py' or a.endswith(b'/run_four_strategies.py') for a in args):
            if (p/'cwd').resolve() != root:
                raise SystemExit('Existe otra instancia fuera de esta carpeta; revisar manualmente')
            found.append(p.name)
    except OSError:
        continue
if len(found) > 1:
    raise SystemExit('Hay varias instancias; revisar manualmente')
print(' '.join(found))
PY
)
if [[ -n "$PIDS" ]]; then
  kill -TERM "$PIDS"
  for _ in $(seq 1 30); do
    kill -0 "$PIDS" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$PIDS" 2>/dev/null; then
    printf '%s\n' 'La instancia anterior no terminó; no se inicia otra'
    exit 2
  fi
fi
.venv/bin/python reconcile_four_manual_sales.py --apply > logs/manual-sale-applied.json
nohup .venv/bin/python -u run_four_strategies.py --run >> logs/four-strategies.log 2>&1 9>&- &
printf 'Arranque solicitado, PID %s. Comprueba logs/four-strategies.log\n' "$!"
