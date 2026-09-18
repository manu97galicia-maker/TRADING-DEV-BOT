"""El droplet se trae los cambios solo. Lo que puede y lo que NO puede hacer.

POR QUE EXISTE ESTE AUTOMATISMO. Desde el movil, con un SSH que se reconecta
solo, escribir un comando es fragil: cinco intentos seguidos del mismo no
llegaron a ejecutarse -- una errata de teclado, una sesion que murio a mitad,
la carpeta equivocada, un instalador que nunca corrio y una pantalla en
blanco. Mientras tanto el bot seguia con la configuracion vieja y cada cambio
empujado se quedaba en GitHub sin llegar.

POR QUE ESTOS TESTS SON SEVEROS. Esto corre como root, cada diez minutos, sin
que nadie mire, en el mismo directorio donde viven el `.env` con la clave de
la wallet y las sesiones de Telegram. Un automatismo con permiso para borrar
es un accidente esperando a las tres de la manana. Asi que aqui no se
comprueba solo que funcione: se comprueba lo que NO puede hacer.
"""

# ruff: noqa: S101

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GUION = RAIZ / "deploy" / "autoactualiza.sh"


def _fuente() -> str:
    return GUION.read_text(encoding="utf-8")


# ------------------------------------------------- lo que NO puede hacer


def test_solo_hace_pull_y_nada_mas() -> None:
    """LA REGLA QUE MAS IMPORTA. En ese directorio estan el .env y las
    sesiones de Telegram. Un `reset --hard` o un `checkout` automatico a las
    3 de la manana se lleva por delante lo que no se pueda recuperar."""
    s = _fuente()
    for prohibido in (
        "git add", "git reset", "git checkout", "git clean",
        "rm ", "rm -", "mv ", "> .env", "chmod 777",
    ):
        assert prohibido not in s, f"el automatismo hace '{prohibido}'"


def test_no_empuja_nada_a_github() -> None:
    """La llave del droplet es de solo lectura, y aunque no lo fuera: un push
    automatico desde ahi podria subir el .env."""
    assert "git push" not in _fuente()


def test_no_abre_ningun_editor() -> None:
    """Un `git pull` que abre nano deja el servicio colgado para siempre,
    esperando a alguien que nunca va a escribir en esa terminal."""
    assert "git pull --no-edit" in _fuente()


# --------------------------------------- reiniciar solo cuando toca


def test_no_reinicia_si_no_ha_cambiado_nada() -> None:
    """Reiniciar por reiniciar corta posiciones vivas cada diez minutos sin
    razon. Se retoman --eso se arreglo en el commit 4d79769-- pero cada
    reinicio es una ventana sin vigilancia y no hay que abrirla gratis."""
    s = _fuente()
    corte = s.index('if [ "$antes" = "$despues" ]')
    rama = s[corte : s.index("fi", corte)]
    assert "exit 0" in rama
    assert "systemctl restart" not in rama


def test_no_reinicia_si_el_pull_falla() -> None:
    """Un bot corriendo con codigo viejo es mejor que un bot reiniciado a
    medias: el viejo al menos sigue vigilando las posiciones abiertas."""
    s = _fuente()
    corte = s.index("if ! salida=")
    rama = s[corte : s.index("despues=", corte)]
    assert "exit 1" in rama
    assert "systemctl restart" not in rama


def test_reinicia_cuando_si_ha_cambiado() -> None:
    """Y la otra mitad: que cuando hay cambios, de verdad los aplique."""
    s = _fuente()
    assert 'systemctl restart "$SERVICIO"' in s
    # Despues de la comparacion, no antes.
    assert s.index('if [ "$antes" = "$despues" ]') < s.index('systemctl restart')


def test_avisa_por_telegram_cuando_reinicia() -> None:
    """Es donde Manu mira. Sin aviso, un cambio llega sin que se entere."""
    s = _fuente()
    assert "notify(" in s
    assert "BOT ACTUALIZADO" in s


def test_el_aviso_no_puede_tumbar_la_actualizacion() -> None:
    """Si Telegram falla, da igual: lo importante --traer el cambio y
    reiniciar-- ya esta hecho para cuando se intenta avisar."""
    s = _fuente()
    assert s.index('systemctl restart "$SERVICIO"') < s.index("notify(")
    assert "|| true" in s


# ------------------------------------------------------- el temporizador


def test_el_temporizador_lo_dispara_cada_diez_minutos() -> None:
    t = (RAIZ / "deploy" / "pump-bot-autoactualiza.timer").read_text(
        encoding="utf-8"
    )
    assert "OnUnitActiveSec=10min" in t
    assert "OnBootSec=" in t, "sin esto no arranca tras reiniciar la maquina"


def test_el_servicio_llama_al_guion_y_no_a_otra_cosa() -> None:
    u = (RAIZ / "deploy" / "pump-bot-autoactualiza.service").read_text(
        encoding="utf-8"
    )
    assert "deploy/autoactualiza.sh" in u
    assert "Type=oneshot" in u


def test_el_arranque_lo_deja_instalado() -> None:
    """El objetivo entero: que el ultimo comando manual cierre el sistema."""
    s = (RAIZ / "deploy" / "arranca.sh").read_text(encoding="utf-8")
    assert "pump-bot-autoactualiza.timer" in s
    assert "systemctl enable --now pump-bot-autoactualiza.timer" in s


# ------------------------------------------- que se vea que esta puesto


def test_el_revisor_avisa_si_el_temporizador_esta_caido() -> None:
    """Un automatismo que nadie comprueba es un automatismo caido. En agosto
    el bot estuvo OCHO DIAS parado sin que nadie se enterara."""
    s = (RAIZ / "deploy" / "revisar_droplet.py").read_text(encoding="utf-8")
    assert "_autoactualiza()" in s
    assert "pump-bot-autoactualiza.timer" in s


def test_la_sintaxis_del_guion_es_valida() -> None:
    """Un fallo de sintaxis aqui no se veria hasta que el timer fallara en
    silencio diez minutos despues."""
    r = subprocess.run(  # noqa: S603
        ["/usr/bin/bash", "-n", str(GUION)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert r.returncode == 0, r.stderr


# ------------------------------------------ el diagnostico de "no compra"


def _porque() -> str:
    return (RAIZ / "deploy" / "porque.sh").read_text(encoding="utf-8")


def test_el_diagnostico_agrupa_los_motivos_en_vez_de_volcar_el_log() -> None:
    """Desde el movil no se puede leer un log de miles de lineas. Lo que hace
    falta es el EMBUDO: cuantas se caen en cada paso y por que."""
    s = _porque()
    assert "uniq -c" in s, "no agrupa: volcaria el log entero"
    assert "sort -rn" in s, "no ordena por frecuencia: el motivo gordo se pierde"


def test_los_motivos_se_agrupan_quitando_lo_que_cambia() -> None:
    """Si no se quita el simbolo y los numeros, cada descarte es unico y el
    recuento sale todo a uno: inservible."""
    s = _porque()
    assert "s/Evitado [^:]+: //" in s, "no quita el simbolo de la moneda"
    assert "s/[0-9]+" in s, "no quita los numeros"


def test_distingue_no_hay_candidatas_de_no_se_compran() -> None:
    """Son problemas OPUESTOS con arreglos opuestos: o el corte de la fuente
    esta muy alto, o lo que sobra es un filtro de mas abajo."""
    s = _porque()
    assert "La fuente no ha mandado NI UNA candidata" in s
    assert "NINGUNA llega a comprarse" in s


def test_mira_tambien_los_frenos_de_cartera() -> None:
    """Un bot con las 5 plazas llenas o sin SOL no compra, y no es culpa de
    ningun filtro."""
    s = _porque()
    assert "max posiciones" in s.lower()
    assert "SOL insuficiente" in s


def test_el_diagnostico_solo_lee() -> None:
    """Se va a correr cuando algo va mal: no puede empeorarlo."""
    s = _porque()
    for peligroso in ("systemctl restart", "systemctl stop", "git ", "rm ", ">"):
        if peligroso == ">":
            continue  # las redirecciones de `2>/dev/null` son inevitables
        assert peligroso not in s, f"el diagnostico hace '{peligroso}'"


def test_esta_entre_los_comandos_cortos() -> None:
    """Se escribe cuando uno esta harto: tiene que ser una palabra."""
    s = (RAIZ / "deploy" / "instalar_comandos.sh").read_text(encoding="utf-8")
    assert "_poner porque porque.sh" in s
