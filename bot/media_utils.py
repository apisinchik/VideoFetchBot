def estimate_voice_size_bytes(duration_s: int, bitrate_kbps: int = 60) -> int:
    try:
        duration_i = int(duration_s or 0)
    except Exception:
        duration_i = 0

    if duration_i <= 0 or bitrate_kbps <= 0:
        return 0

    return int((duration_i * bitrate_kbps * 1000) / 8)
