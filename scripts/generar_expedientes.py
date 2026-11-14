# Corpus sintético de expedientes (texto en español) del sistema biométrico, semilla fija.
# Se refiere a cada persona por su código P00042; no usa nombres ni hechos reales.
#   python scripts/generar_expedientes.py [--n 25000] [--salida data/expedientes.csv]

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MAX_BYTES = 1000
PERSONAS_LFW = 5749

SEDES = ["San Isidro", "Miraflores", "Barranco", "Surco", "La Molina", "Jesús María",
         "Lince", "San Borja", "Callao", "Chorrillos", "Magdalena", "Pueblo Libre"]
AREAS = ["recepción", "laboratorio de cómputo", "biblioteca", "auditorio", "sala de servidores",
         "estacionamiento", "comedor", "archivo central", "almacén", "oficina de tesorería",
         "centro de datos", "sala de reuniones", "pabellón de aulas", "gimnasio", "azotea"]
ACCESOS = ["torniquete", "puerta principal", "puerta lateral", "ascensor", "garita vehicular",
           "esclusa de seguridad", "control peatonal"]
CAMARAS = ["CAM-01", "CAM-07", "CAM-12", "CAM-18", "CAM-23", "CAM-31", "CAM-44", "CAM-52"]
MODELOS = ["domo infrarrojo", "cámara bala 4K", "cámara térmica", "cámara PTZ",
           "cámara de reconocimiento frontal", "lector facial de pared"]
TURNOS = ["mañana", "tarde", "noche", "madrugada"]
CARGOS = ["estudiante", "docente", "investigador", "contratista", "proveedor", "visitante",
          "personal administrativo", "personal de limpieza", "agente de seguridad", "practicante"]
CONDICIONES = ["iluminación deficiente", "contraluz intenso", "uso de mascarilla", "uso de gorra",
               "lentes oscuros", "rostro parcialmente cubierto", "ángulo lateral pronunciado",
               "imagen desenfocada", "lluvia sobre el lente", "reflejos en el vidrio"]
FALLAS = ["pérdida de conexión de red", "corte de energía", "desenfoque del lente",
          "sobrecalentamiento del equipo", "error de sincronización horaria",
          "almacenamiento lleno", "firmware desactualizado", "cable de red dañado",
          "falla del disco del grabador", "latencia alta en el servidor de inferencia"]
MOTIVOS = ["reunión de trabajo", "entrega de documentos", "mantenimiento eléctrico",
           "entrevista laboral", "auditoría externa", "capacitación", "evento académico",
           "inspección municipal", "entrega de equipos", "soporte técnico"]

PLANTILLAS = {
    "registro de acceso": [
        "La persona {p} ingresó por el {acceso} de la sede {sede} durante el turno de {turno}.",
        "El sistema registró la entrada de {p} al {area} a las {hora}.",
        "Se validó la credencial de {p} como {cargo} y se autorizó el acceso al {area}.",
        "El acceso de {p} fue denegado en el {acceso} porque la credencial no estaba vigente.",
        "Se registró la salida de {p} por el {acceso} a las {hora} sin observaciones.",
        "El ingreso de {p} se realizó fuera del horario permitido y quedó marcado para revisión.",
        "La {camara} capturó el rostro de {p} al momento del ingreso al {area}.",
    ],
    "verificación biométrica": [
        "La verificación facial de {p} obtuvo una coincidencia de {conf}% con la fotografía registrada.",
        "El reconocimiento facial no superó el umbral de confianza por {condicion}.",
        "Se comparó el vector biométrico de {p} contra la galería y la identidad fue confirmada.",
        "La {camara} ({modelo}) detectó el rostro de {p} con {condicion}.",
        "El algoritmo de reconocimiento reportó un posible falso positivo y se solicitó verificación manual.",
        "Se repitió la captura del rostro de {p} porque la primera imagen presentaba {condicion}.",
        "La búsqueda k-NN devolvió a {p} como la identidad más cercana con distancia {dist}.",
        "El operador confirmó manualmente la identidad de {p} con un documento oficial.",
    ],
    "renovación de credencial": [
        "Se renovó la credencial de {p} con vigencia hasta {vence}.",
        "La fotografía de {p} fue actualizada porque la anterior tenía más de dos años.",
        "{p} solicitó un duplicado de su credencial por pérdida.",
        "Se registró una nueva plantilla biométrica de {p} con cinco capturas frontales.",
        "La credencial de {p} cambió de categoría a {cargo}.",
        "Se revocó la credencial anterior de {p} y se emitió una nueva.",
    ],
    "incidencia técnica": [
        "La {camara} del {area} presentó {falla} durante el turno de {turno}.",
        "El equipo {modelo} de la sede {sede} quedó fuera de servicio por {falla}.",
        "Se programó mantenimiento preventivo de la {camara} por {falla}.",
        "El técnico reemplazó el equipo dañado y la {camara} volvió a operar con normalidad.",
        "Mientras duró la falla, los accesos de {p} y otras personas se validaron manualmente.",
        "Se recalibró el enfoque de la {camara} para mejorar la detección de rostros.",
        "El servidor de reconocimiento facial reportó {falla} y se reinició el servicio.",
    ],
    "reporte de visitante": [
        "{p} visitó la sede {sede} por motivo de {motivo}.",
        "El visitante {p} fue acompañado por personal de seguridad hasta el {area}.",
        "Se registró la fotografía del visitante {p} en recepción para el control de acceso.",
        "La visita de {p} se extendió más allá del horario autorizado.",
        "El anfitrión confirmó la visita de {p} por correo antes de su ingreso.",
        "Se entregó a {p} una credencial temporal válida solo para el {area}.",
    ],
    "auditoría de seguridad": [
        "La auditoría revisó la bitácora de accesos de la sede {sede} del último mes.",
        "Se verificó que los permisos de {p} correspondan a su cargo de {cargo}.",
        "La auditoría encontró accesos al {area} sin registro biométrico asociado.",
        "Se revisaron las grabaciones de la {camara} para contrastarlas con la bitácora.",
        "Se recomendó actualizar el protocolo de verificación facial en la sede {sede}.",
        "Los registros de {p} fueron consistentes con las capturas de las cámaras.",
    ],
    "protección de datos": [
        "{p} solicitó acceso a los datos biométricos almacenados sobre su persona.",
        "{p} pidió la rectificación de su información personal registrada.",
        "Se registró el consentimiento informado de {p} para el tratamiento de su imagen facial.",
        "{p} solicitó la eliminación de sus fotografías antiguas de la galería biométrica.",
        "Se respondió la solicitud de {p} dentro del plazo establecido por la ley de protección de datos.",
        "Se anonimizaron las capturas de {p} usadas en el reporte estadístico.",
    ],
    "alerta de seguridad": [
        "Se activó una alarma en el {area} y la {camara} registró movimiento a las {hora}.",
        "El sistema detectó un rostro no registrado cerca del {acceso} de la sede {sede}.",
        "Se reportó un intento de ingreso con una credencial ajena en el {acceso}.",
        "El agente de seguridad verificó la zona y no encontró riesgo para las personas.",
        "La alerta se cerró después de confirmar que se trataba de {p}, quien estaba autorizado.",
        "Se solicitó revisar las grabaciones de la {camara} entre las {hora} y la medianoche.",
    ],
}
SUSTANTIVOS = """mochila maleta paraguas casco chaqueta abrigo bufanda guantes audífonos laptop
tableta celular cargador llavero billetera carpeta cuaderno libro caja paquete sobre bolsa
botella termo bicicleta patineta scooter silla mesa escritorio estante pizarra proyector
parlante micrófono impresora escáner monitor teclado ratón router switch servidor rack
ventilador extintor botiquín camilla señalética cinta candado cerradura bisagra ventana
persiana cortina alfombra lámpara foco reflector sensor detector sirena intercomunicador
timbre buzón casillero vitrina mostrador módulo cubículo pasillo escalera rampa barandal
jardín patio fuente banca macetero tacho contenedor vehículo camioneta motocicleta furgón
grúa montacargas carretilla andamio herramienta taladro escalera manguera tablero medidor
transformador generador batería panel antena cableado tubería válvula grifo lavadero
uniforme chaleco credencial gafete sello firma documento formulario expediente solicitud
oficio memorando contrato factura recibo boleta comprobante inventario bitácora acta
cronograma presupuesto informe reporte constancia certificado licencia permiso pase""".split()
ADJETIVOS = """abandonado olvidado extraviado dañado roto mojado sucio nuevo antiguo pesado liviano
grande pequeño oscuro brillante rojo azul verde negro blanco gris amarillo naranja morado
metálico plástico sospechoso extraño inusual frecuente urgente pendiente resuelto
incompleto duplicado vencido vigente provisional definitivo confidencial público
restringido autorizado desconocido identificado anónimo reciente temporal permanente""".split()
VERBOS = """encontrado reportado retirado devuelto custodiado inspeccionado reparado reemplazado
instalado desinstalado trasladado registrado fotografiado etiquetado sellado entregado
recibido almacenado descartado revisado verificado aprobado observado""".split()
GENERALES = [
    "El caso quedó cerrado sin observaciones adicionales.",
    "Se notificó al supervisor de turno de la sede {sede}.",
    "La información se archivó en el expediente digital de {p}.",
    "Se adjuntó la captura de la {camara} como evidencia.",
    "Se recomienda dar seguimiento en la próxima revisión mensual.",
    "El registro fue validado por el área de seguridad.",
    "No se requieren acciones adicionales por el momento.",
]
PESOS = [30, 22, 10, 12, 10, 6, 4, 6]


def generar(n: int, personas: list[int], semilla: int):
    rng = random.Random(semilla)
    tipos = list(PLANTILLAS)
    inicio = dt.date(2024, 1, 1)
    # unas pocas personas concentran muchos expedientes, como en datos reales
    pesos_personas = [1.0 / (rank ** 0.6) for rank in range(1, len(personas) + 1)]
    for numero in range(1, n + 1):
        tipo = rng.choices(tipos, PESOS)[0]
        persona = rng.choices(personas, pesos_personas)[0]
        sede = rng.choice(SEDES)
        slots = {
            "p": f"P{persona:05d}",
            "sede": sede,
            "area": rng.choice(AREAS),
            "acceso": rng.choice(ACCESOS),
            "camara": rng.choice(CAMARAS),
            "modelo": rng.choice(MODELOS),
            "turno": rng.choice(TURNOS),
            "cargo": rng.choice(CARGOS),
            "condicion": rng.choice(CONDICIONES),
            "falla": rng.choice(FALLAS),
            "motivo": rng.choice(MOTIVOS),
            "hora": f"{rng.randint(6, 23):02d}:{rng.randint(0, 59):02d}",
            "conf": rng.randint(40, 99),
            "dist": f"{rng.uniform(0.2, 0.9):.2f}",
            "vence": (inicio + dt.timedelta(days=rng.randint(400, 1500))).isoformat(),
        }
        propias = rng.sample(PLANTILLAS[tipo], k=min(len(PLANTILLAS[tipo]), rng.randint(2, 5)))
        extra = [rng.choice(PLANTILLAS[rng.choice(tipos)])] if rng.random() < 0.35 else []
        cierre = [rng.choice(GENERALES)] if rng.random() < 0.6 else []
        oraciones = [plantilla.format(**slots) for plantilla in propias + extra]
        oraciones += observaciones(rng, slots)
        oraciones += [plantilla.format(**slots) for plantilla in cierre]
        cuerpo = ""
        for oracion in oraciones:
            candidato = f"{cuerpo} {oracion}".strip()
            if len(candidato.encode("utf-8")) > MAX_BYTES:
                break
            cuerpo = candidato
        fecha = inicio + dt.timedelta(days=rng.randint(0, 900))
        yield numero, persona, tipo, sede, fecha.isoformat(), cuerpo


def _zipf(rng: random.Random, opciones: list[str]) -> str:
    # Elige con probabilidad ~ 1/rango: pocos términos muy comunes, muchos raros.
    return opciones[min(len(opciones) - 1, int(rng.paretovariate(1.1)) - 1)]


def observaciones(rng: random.Random, slots: dict) -> list[str]:
    # Notas libres del operador, con vocabulario amplio y códigos únicos.
    notas = []
    for _ in range(rng.choice((0, 0, 1, 1, 2, 3))):
        forma = rng.random()
        objeto = f"{_zipf(rng, SUSTANTIVOS)} {_zipf(rng, ADJETIVOS)}"
        if forma < 0.45:
            notas.append(f"Se observó un {objeto} en el {slots['area']}, que fue "
                         f"{_zipf(rng, VERBOS)} por el personal de turno.")
        elif forma < 0.75:
            notas.append(f"El operador dejó constancia de un {objeto} "
                         f"{_zipf(rng, VERBOS)} con el ticket TK{rng.randint(10000, 99999)}.")
        else:
            notas.append(f"Equipo con número de serie SN{rng.randint(100000, 999999)} "
                         f"{_zipf(rng, VERBOS)} junto a un {objeto}.")
    return notas


def leer_personas(ruta: str) -> list[int]:
    if not os.path.exists(ruta):
        return list(range(1, PERSONAS_LFW + 1))
    with open(ruta, newline="", encoding="utf-8") as fh:
        return [int(fila["persona_id"]) for fila in csv.DictReader(fh)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Corpus sintético de expedientes")
    parser.add_argument("--n", type=int, default=25_000)
    parser.add_argument("--salida", default=os.path.join(DATA, "expedientes.csv"))
    parser.add_argument("--personas", default=os.path.join(DATA, "lfw", "personas.csv"))
    parser.add_argument("--semilla", type=int, default=2026)
    args = parser.parse_args()

    personas = leer_personas(args.personas)
    os.makedirs(os.path.dirname(os.path.abspath(args.salida)), exist_ok=True)
    with open(args.salida, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "persona_id", "tipo", "sede", "fecha", "cuerpo"])
        writer.writerows(generar(args.n, personas, args.semilla))
    print(f"{args.n} expedientes en {args.salida} ({len(personas)} identidades)")


if __name__ == "__main__":
    main()
