import os
import sys
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

CAIYUN_TOKEN = os.environ["CAIYUN_TOKEN"]
CAIYUN_LNG = os.environ["CAIYUN_LNG"]  # 经度
CAIYUN_LAT = os.environ["CAIYUN_LAT"]  # 纬度

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# 空值安全：Secrets 为空字符串也不会炸
TIMEZONE = (os.getenv("TIMEZONE") or "Asia/Shanghai").strip()
PLACE_NAME = (os.getenv("PLACE_NAME") or "").strip()

SKYCON_ZH = {
    "CLEAR_DAY": "晴",
    "CLEAR_NIGHT": "晴",
    "PARTLY_CLOUDY_DAY": "多云",
    "PARTLY_CLOUDY_NIGHT": "多云",
    "CLOUDY": "阴",
    "LIGHT_HAZE": "轻度雾霾",
    "MODERATE_HAZE": "中度雾霾",
    "HEAVY_HAZE": "重度雾霾",
    "FOG": "雾",
    "WIND": "大风",
    "LIGHT_RAIN": "小雨",
    "MODERATE_RAIN": "中雨",
    "HEAVY_RAIN": "大雨",
    "STORM_RAIN": "暴雨",
    "LIGHT_SNOW": "小雪",
    "MODERATE_SNOW": "中雪",
    "HEAVY_SNOW": "大雪",
    "STORM_SNOW": "暴雪",
    "DUST": "浮尘",
    "SAND": "沙尘",
}

def sky_emoji(sky: str) -> str:
    m = {
        "CLEAR_DAY": "☀", "CLEAR_NIGHT": "🌙",
        "PARTLY_CLOUDY_DAY": "⛅", "PARTLY_CLOUDY_NIGHT": "☁",
        "CLOUDY": "☁",
        "LIGHT_HAZE": "🌫", "MODERATE_HAZE": "🌫", "HEAVY_HAZE": "🌫",
        "FOG": "🌫",
        "WIND": "💨",
        "LIGHT_RAIN": "🌧", "MODERATE_RAIN": "🌧", "HEAVY_RAIN": "🌧",
        "STORM_RAIN": "⛈",
        "LIGHT_SNOW": "🌨", "MODERATE_SNOW": "🌨", "HEAVY_SNOW": "🌨",
        "STORM_SNOW": "❄",
        "DUST": "🌪", "SAND": "🌪",
    }
    return m.get(sky or "", "☁")

def wind_dir_zh(deg: float | None) -> str:
    """度数转 8 方位中文：0/360 北，90 东，180 南，270 西"""
    if deg is None:
        return "-"
    try:
        d = float(deg) % 360
    except Exception:
        return "-"
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    idx = int((d + 22.5) // 45) % 8
    return dirs[idx]

def beaufort_level_ms(ms: float | None) -> int | None:
    """m/s -> 蒲福风级（近似）"""
    if ms is None:
        return None
    try:
        v = float(ms)
    except Exception:
        return None
    thresholds = [0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7]
    for i, t in enumerate(thresholds):
        if v < t:
            return i
    return 12

def caiyun_weather():
    url = f"https://api.caiyunapp.com/v2.6/{CAIYUN_TOKEN}/{CAIYUN_LNG},{CAIYUN_LAT}/weather"
    params = {
        "alert": "true",
        "dailysteps": "1",
        "hourlysteps": "24",
        "lang": "zh_CN",
        "unit": "metric",
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"Caiyun API status != ok: {data}")
    return data

def _parse_hour(dt_raw: str, tz: ZoneInfo) -> str:
    try:
        dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00")).astimezone(tz)
        return dt.strftime("%H")
    except Exception:
        return "??"

def _fmt_cell(hh: str, t: float, sky: str) -> str:
    # 固定宽度单元：保证两列对齐（不显示降水）
    emoji = sky_emoji(sky)
    temp = f"{t:>4.1f}℃"
    return f"{hh:>2} {temp} {emoji}"

def build_message(payload: dict) -> str:
    # TIMEZONE 容错：即使传了非法值也不炸
    global TIMEZONE
    try:
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
        TIMEZONE = "Asia/Shanghai"

    now = datetime.now(tz)
    # GitHub Actions 是 Linux：%-m / %-d 会去掉前导 0，得到 2-19
    date_str = now.strftime("%-m-%-d")

    result = payload["result"]
    rt = result.get("realtime", {}) or {}
    daily = result.get("daily", {}) or {}
    hourly = result.get("hourly", {}) or {}

    # 当前
    temp_now = rt.get("temperature")
    feels = rt.get("apparent_temperature")
    if temp_now is None or feels is None:
        raise RuntimeError(f"Missing realtime temperature fields: {rt}")
    temp_now = float(temp_now)
    feels = float(feels)

    # 风况（realtime.wind: speed(m/s), direction(度)）
    wind = (rt.get("wind") or {})
    wind_speed = wind.get("speed")
    wind_dir = wind.get("direction")
    wdir_zh = wind_dir_zh(wind_dir)
    wlevel = beaufort_level_ms(wind_speed)

    wind_speed_str = "-"
    if wind_speed is not None:
        try:
            wind_speed_str = f"{float(wind_speed):.1f}"
        except Exception:
            wind_speed_str = str(wind_speed)
    wind_level_str = f"{wlevel}级" if wlevel is not None else "-"

    # 今日低高
    today_temp = (daily.get("temperature", []) or [{}])[0] or {}
    tmin = today_temp.get("min")
    tmax = today_temp.get("max")
    if tmin is None or tmax is None:
        raise RuntimeError(f"Missing daily temperature min/max: {daily.get('temperature')}")
    tmin = float(tmin)
    tmax = float(tmax)

    # 概况：优先 daily.skycon[0].value，否则 realtime.skycon
    daily_sky = (daily.get("skycon", []) or [])
    today_sky = daily_sky[0].get("value") if (daily_sky and isinstance(daily_sky[0], dict)) else None
    sky_key = today_sky or rt.get("skycon")
    sky_text = SKYCON_ZH.get(sky_key, sky_key or "-")
    sky_icon = sky_emoji(sky_key)

    # 24小时（温度 + 天气现象）
    h_temp = hourly.get("temperature", []) or []
    h_sky = hourly.get("skycon", []) or []
    n = min(24, len(h_temp), len(h_sky))

    cells = []
    for i in range(n):
        dt_raw = (h_temp[i].get("datetime") or h_sky[i].get("datetime") or "")
        hh = _parse_hour(dt_raw, tz)
        t = h_temp[i].get("value")
        sky = h_sky[i].get("value")
        if t is None:
            continue
        cells.append(_fmt_cell(hh, float(t), sky))

    # 两列：12 + 12
    left = cells[:12]
    right = cells[12:24]
    colw = 12  # 单元列宽（短一点更干净）
    rows = []
    for i in range(12):
        l = left[i] if i < len(left) else ""
        r = right[i] if i < len(right) else ""
        rows.append(f"{l:<{colw}}  {r}".rstrip())

    # 标题：富平县｜2-19｜晴
    place = PLACE_NAME or ""
    title = f"{place}｜{date_str}｜{sky_text} {sky_icon}".strip("｜ ").strip()

    body = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"现在  {temp_now:>5.1f}℃  体感 {feels:>4.1f}℃\n"
        f"区间  ⬇ {tmin:>2.0f}℃   ⬆ {tmax:>3.0f}℃\n"
        f"风况  {wdir_zh}风  {wind_speed_str}m/s  {wind_level_str}\n"
        f"\n"
        f"🕒 未来24小时\n"
        + "\n".join(rows)
    )

    # 等宽渲染保证对齐（HTML pre）
    return f"<pre>{body}</pre>"

def telegram_send(html_text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=20)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"Telegram non-JSON: http={r.status_code} text={r.text[:500]}")
    if not r.ok or not data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: http={r.status_code} resp={data}")

def main():
    try:
        payload = caiyun_weather()
        msg = build_message(payload)
        telegram_send(msg)
        print("Sent ok")
    except Exception as e:
        print("ERROR:", repr(e), file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
