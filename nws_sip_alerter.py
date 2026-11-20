#!/usr/bin/env python3
"""Automated National Weather Service (NWS) SIP Weather Alerter.

Polls the NWS API for active weather alerts, generates a custom 8kHz mono WAV
text-to-speech announcement for each new alert, and places outbound SIP calls
via FlyNumber (using baresip in headless mode).
"""

import csv
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests
from gtts import gTTS
from pydub import AudioSegment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("nws-sip-alerter")

NWS_POINT = os.getenv("NWS_POINT", "33.7751,-89.8084").strip()
NWS_ZONE = os.getenv("NWS_ZONE", "").strip()
NWS_USER_AGENT = os.getenv(
    "NWS_USER_AGENT", "GrenadaWeatherAlerter/1.0 (contact@example.com)"
)
ALERT_SEVERITIES = {
    s.strip().lower()
    for s in os.getenv("ALERT_SEVERITIES", "Extreme,Severe").split(",")
    if s.strip()
}
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

GOOGLE_SHEET_CSV_URL = os.getenv("GOOGLE_SHEET_CSV_URL", "").strip()
LOCAL_CONTACTS_CSV = Path(os.getenv("LOCAL_CONTACTS_CSV", "contacts.csv"))

SIP_DOMAIN = os.getenv("FLYNUMBER_SIP_DOMAIN", "sip.flynumber.com").strip()
SIP_USER = os.getenv("FLYNUMBER_SIP_USER", "w6FgX8kkq").strip()
SIP_PASSWORD = os.getenv("FLYNUMBER_SIP_PASSWORD", "H@Tbps2a").strip()
CALLER_ID = os.getenv("FLYNUMBER_CALLER_ID", "6627527073").strip()

DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes"}
STATE_DIR = Path(os.getenv("STATE_DIR", "/data"))
SEEN_ALERTS_FILE = STATE_DIR / "seen_alerts.json"


def load_seen_alerts() -> set[str]:
    if SEEN_ALERTS_FILE.exists():
        try:
            data = json.loads(SEEN_ALERTS_FILE.read_text(encoding="utf-8"))
            return set(data)
        except Exception as exc:
            log.warning("Could not read %s: %s", SEEN_ALERTS_FILE, exc)
    return set()


def save_seen_alerts(seen: set[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_ALERTS_FILE.write_text(
        json.dumps(sorted(seen), indent=2), encoding="utf-8"
    )


def fetch_active_alerts() -> list[dict]:
    if NWS_ZONE:
        url = f"https://api.weather.gov/alerts/active?zone={NWS_ZONE}"
    else:
        url = f"https://api.weather.gov/alerts/active?point={NWS_POINT}"

    headers = {
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    features = resp.json().get("features", [])

    matched = []
    for feat in features:
        props = feat.get("properties", {})
        severity = (props.get("severity") or "").lower()
        if ALERT_SEVERITIES and severity not in ALERT_SEVERITIES:
            continue
        matched.append(
            {
                "id": feat.get("id") or props.get("id"),
                "event": props.get("event", "Weather Alert"),
                "severity": props.get("severity", "Unknown"),
                "headline": props.get("headline", ""),
                "area": props.get("areaDesc", ""),
                "instruction": props.get("instruction", ""),
            }
        )
    return matched


def normalize_target(raw_phone: str, raw_ext: str) -> str | None:
    digits = re.sub(r"\D", "", raw_phone or "")
    if len(digits) == 10:
        return f"1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return digits
    ext = (raw_ext or "").strip()
    if ext:
        return ext
    return None


def load_contacts() -> list[dict]:
    csv_text = ""
    if GOOGLE_SHEET_CSV_URL:
        try:
            resp = requests.get(GOOGLE_SHEET_CSV_URL, timeout=15)
            resp.raise_for_status()
            csv_text = resp.text
        except Exception as exc:
            log.warning("Google Sheet fetch failed, falling back to local CSV: %s", exc)

    if not csv_text and LOCAL_CONTACTS_CSV.exists():
        csv_text = LOCAL_CONTACTS_CSV.read_text(encoding="utf-8")

    if not csv_text:
        log.warning("No contact list available.")
        return []

    reader = csv.DictReader(io.StringIO(csv_text))
    contacts = []
    for row in reader:
        norm = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        name = norm.get("name") or norm.get("contact") or "Subscriber"
        phone = norm.get("phone") or norm.get("number") or norm.get("phone number") or ""
        ext = norm.get("extension") or norm.get("ext") or ""
        target = normalize_target(phone, ext)
        if target:
            contacts.append({"name": name, "target": target})
    return contacts


def build_alert_wav(alert: dict, out_wav: Path) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    message = (
        f"Attention. This is an automated National Weather Service alert. "
        f"{alert['event']}. Severity: {alert['severity']}. "
        f"{alert['headline']}. "
        f"{alert['instruction'][:300] if alert['instruction'] else ''}"
    )

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        mp3_path = Path(tmp_mp3.name)

    try:
        tts = gTTS(text=message, lang="en")
        tts.save(str(mp3_path))
        audio = AudioSegment.from_mp3(str(mp3_path))
        audio = audio.set_frame_rate(8000).set_channels(1).set_sample_width(2)
        audio.export(str(out_wav), format="wav")
    except Exception as exc:
        log.warning("gTTS failed (%s), falling back to espeak-ng", exc)
        raw_wav = out_wav.with_suffix(".raw.wav")
        subprocess.run(
            ["espeak-ng", "-w", str(raw_wav), message],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(raw_wav),
                "-ar",
                "8000",
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                str(out_wav),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        raw_wav.unlink(missing_ok=True)
    finally:
        mp3_path.unlink(missing_ok=True)

    return out_wav


def place_sip_call(target: str, wav_path: Path) -> None:
    sip_uri = f"sip:{target}@{SIP_DOMAIN}"
    if DRY_RUN:
        log.info("[DRY RUN] Would call %s playing %s", sip_uri, wav_path.name)
        return

    if not SIP_USER or not SIP_PASSWORD:
        raise RuntimeError(
            "FLYNUMBER_SIP_USER and FLYNUMBER_SIP_PASSWORD must be set when DRY_RUN=false"
        )

    with tempfile.TemporaryDirectory(prefix="baresip_") as cfg_dir:
        cfg_path = Path(cfg_dir)
        (cfg_path / "accounts").write_text(
            f"<sip:{SIP_USER}@{SIP_DOMAIN}>;auth_pass={SIP_PASSWORD};regint=0\n",
            encoding="utf-8",
        )
        (cfg_path / "config").write_text(
            f"audio_source\t\taufile,{wav_path}\n"
            f"audio_player\t\tnone\n"
            f"audio_alert\t\tnone\n"
            f"module\t\t\taufile.so\n"
            f"module\t\t\tg711.so\n",
            encoding="utf-8",
        )
        cmd = ["baresip", "-f", str(cfg_path), "-e", f"/dial {sip_uri}"]
        log.info("Dialing %s via %s", sip_uri, SIP_DOMAIN)
        subprocess.run(cmd, timeout=45, check=False)


def main() -> None:
    log.info(
        "Starting NWS SIP Alerter (point=%s, severities=%s, dry_run=%s)",
        NWS_POINT,
        sorted(ALERT_SEVERITIES),
        DRY_RUN,
    )
    seen = load_seen_alerts()

    while True:
        try:
            alerts = fetch_active_alerts()
            new_alerts = [a for a in alerts if a["id"] and a["id"] not in seen]
            if new_alerts:
                contacts = load_contacts()
                log.info(
                    "Found %d new alert(s); notifying %d contact(s)",
                    len(new_alerts),
                    len(contacts),
                )
                for alert in new_alerts:
                    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", alert["id"])[-40:]
                    wav_path = STATE_DIR / f"alert_{safe_id}.wav"
                    build_alert_wav(alert, wav_path)
                    for contact in contacts:
                        try:
                            place_sip_call(contact["target"], wav_path)
                        except Exception as call_exc:
                            log.error(
                                "Call failed for %s (%s): %s",
                                contact["name"],
                                contact["target"],
                                call_exc,
                            )
                    seen.add(alert["id"])
                    save_seen_alerts(seen)
            else:
                log.debug("No new matching alerts.")
        except Exception as exc:
            log.error("Polling cycle error: %s", exc)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
