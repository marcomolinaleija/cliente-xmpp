"""User-requested local counters; no account/contact IDs or captured samples."""
from cliente_xmpp.models.voice import VoiceAudioStats


def diagnostic_text(s: VoiceAudioStats) -> str:
    return (
        "Diagnóstico local de audio\n"
        f"Captura: {s.capture_frames} bloques; "
        f"descartados en cola de captura: {s.capture_dropped}; "
        f"descartados antes de envío: {s.transport_capture_dropped}.\n"
        f"Reproducción: {s.playback_gaps} agotamientos; {s.playback_missing_ms} ms sin PCM; "
        f"{s.playback_dropped_ms} ms descartados; "
        f"{s.transport_playback_dropped} paquetes descartados antes de reproducir.\n"
        f"Reserva: {s.playback_prefill_ms} ms; audio en cola: {s.playback_buffer_ms} ms.\n"
        f"Avisos del dispositivo: entrada {s.driver_input_overflows}; "
        f"salida {s.driver_output_underflows}; callbacks omitidos {s.callback_lock_misses}.\n"
        f"Pausa máxima entre paquetes recibidos: {s.receive_gap_max_ms} ms; "
        f"espera máxima de envío: {s.send_max_ms} ms.\n"
        f"Retrasos del servicio: {s.pump_late_ticks}; máximo {s.pump_late_max_ms} ms.\n"
        "Los huecos y pausas no demuestran por sí solos pérdida de red. "
        "El silencio remoto también puede dejar de entregar PCM. "
        "Contadores del dispositivo desde su última apertura y del transporte desde la llamada."
    )
