from datetime import datetime, timezone, timedelta

# Timezone Helper
TZ_TAIPEI = timezone(timedelta(hours=8))

def format_time_taipei(ts_str: str | None) -> str:
    if not ts_str:
        return ""
    try:
        if hasattr(ts_str, "astimezone"):
            dt = ts_str
        else:
            dt = datetime.fromtimestamp(int(ts_str) / 1000, tz=timezone.utc)
        return dt.astimezone(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts_str)
