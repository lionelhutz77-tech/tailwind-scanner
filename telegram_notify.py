"""
Telegram-Benachrichtigung fuer den Tailwind Scanner.
Sendet taeglich eine formatierte Zusammenfassung + HTML-Report als Datei.

Benoetigt zwei Umgebungsvariablen:
  TELEGRAM_BOT_TOKEN  — Token vom @BotFather
  TELEGRAM_CHAT_ID    — ID deines Kanals oder Chats
"""

import os
import json
import requests
from datetime import datetime
from pathlib import Path


def sende_telegram(ergebnisse: list[dict], trends_scores: dict, report_pfad: Path):
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("[Telegram] Keine Credentials gesetzt — Benachrichtigung uebersprungen")
        return

    heute = datetime.now().strftime("%d.%m.%Y")
    top   = sorted(ergebnisse, key=lambda x: x["gesamt_score"], reverse=True)[:10]

    ampel = {"STARK": "🟢", "MODERAT": "🟡", "SCHWACH": "⚪"}

    zeilen = []
    letztes_thema = ""
    for e in top:
        t_score = trends_scores.get(e["thema"], {}).get("score", 0)
        gesamt  = min(100, e["gesamt_score"] + t_score)
        symbol  = ampel[e["signal_stufe"]]
        ki      = e["kurs_info"]

        if e["thema"] != letztes_thema:
            zeilen.append(f"\n<b>{e['thema']}</b>")
            letztes_thema = e["thema"]

        zeilen.append(
            f"{symbol} <code>{e['ticker']:6s}</code> {gesamt:3d}/100"
            f"  ${ki['kurs']}  ({ki['abstand_ath_prozent']}% unter ATH)"
        )

    stark_count   = sum(1 for e in ergebnisse if e["signal_stufe"] == "STARK")
    moderat_count = sum(1 for e in ergebnisse if e["signal_stufe"] == "MODERAT")

    nachricht = (
        f"📡 <b>Tailwind Scanner — {heute}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Stark: {stark_count}  🟡 Moderat: {moderat_count}\n"
        f"\n<b>Top Signale:</b>\n"
        + "\n".join(zeilen)
        + "\n\n"
        + "<i>Score = News + Revisions + Options + Trends (max 100)</i>"
    )

    # 1. Text-Nachricht senden
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": nachricht, "parse_mode": "HTML"},
        timeout=10,
    )

    # 2. HTML-Report als Datei senden
    if report_pfad.exists():
        with open(report_pfad, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": f"Vollstaendiger Report {heute}"},
                files={"document": (report_pfad.name, f, "text/html")},
                timeout=30,
            )

    print(f"[Telegram] Nachricht + Report gesendet an Chat {chat_id}")
