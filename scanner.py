"""
Tailwind Scanner — erkennt fruehzeitige Signale bei unterbewerteten Aktien
die von einem Makro-Trend profitieren, bevor der Markt ihn einpreist.

Signale:
  1. Google Trends Momentum (pytrends)
  2. News-Volumen-Spike (RSS)
  3. Analyst-Revisions-Kaskade (yfinance)
  4. Options-Aktivitaet Call/Put-Ratio (yfinance)

Ausgabe: HTML-Report unter reports/report_DATUM.html
"""

import json
import time
import os
import sys
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram_notify import sende_telegram

import yfinance as yf
import feedparser
import requests

sys.stdout.reconfigure(encoding="utf-8")

# ─── Pfade ────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
THEMES     = json.loads((BASE_DIR / "themes.json").read_text(encoding="utf-8"))
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)

# ─── Konfiguration ────────────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.marketwatch.com/rss/topstories",
]

TRENDS_AVAILABLE = True
try:
    from pytrends.request import TrendReq
except ImportError:
    TRENDS_AVAILABLE = False
    print("[WARN] pytrends nicht installiert — Trends-Score wird uebersprungen")


# ─── Google Trends Score (0–30) ───────────────────────────────────────────────

def berechne_trends_score(keywords: list[str]) -> tuple[int, dict]:
    """Misst ob ein Thema gerade Fahrt aufnimmt. Vergleicht letzte 7 Tage vs. 90 Tage."""
    if not TRENDS_AVAILABLE or not keywords:
        return 0, {}

    try:
        pytrends = TrendReq(hl="en-US", tz=360)
        keyword = keywords[0][:100]
        pytrends.build_payload([keyword], timeframe="today 3-m", geo="US")
        df = pytrends.interest_over_time()

        if df.empty or keyword not in df.columns:
            return 0, {}

        werte = df[keyword].tolist()
        if len(werte) < 8:
            return 0, {}

        schnitt_gesamt = sum(werte[:-7]) / max(len(werte) - 7, 1)
        schnitt_aktuell = sum(werte[-7:]) / 7

        if schnitt_gesamt == 0:
            wachstum = 0.0
        else:
            wachstum = (schnitt_aktuell - schnitt_gesamt) / schnitt_gesamt * 100

        score = min(30, max(0, int(wachstum / 5)))
        time.sleep(2)  # Rate-Limit schutz
        return score, {"wachstum_prozent": round(wachstum, 1), "aktuell": round(schnitt_aktuell, 1)}

    except Exception as e:
        print(f"[WARN] Trends-Fehler fuer '{keywords[0]}': {e}")
        return 0, {}


# ─── News-Volumen Score (0–30) ────────────────────────────────────────────────

def zaehle_news_treffer(ticker: str, keywords: list[str]) -> tuple[int, int]:
    """Zaehlt Artikel-Treffer der letzten 7 Tage ueber Ticker-spezifischen RSS-Feed."""
    feed_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        feed = feedparser.parse(feed_url)
    except Exception:
        return 0, 0

    grenze = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    treffer = 0
    gesamt  = 0

    for entry in feed.entries:
        gesamt += 1
        titel = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
        for kw in keywords:
            if kw.lower() in titel:
                treffer += 1
                break

    score = min(30, treffer * 5)
    return score, treffer


# ─── Analyst-Revisions Score (0–20) ──────────────────────────────────────────

def berechne_revisions_score(ticker: str) -> tuple[int, dict]:
    """Wertet Analyst-Konsensus aus. yfinance liefert strongBuy/buy/hold/sell Zaehler."""
    try:
        tk = yf.Ticker(ticker)
        recs = tk.recommendations

        if recs is None or recs.empty:
            return 0, {}

        # yfinance liefert aggregierte Perioden (0m = aktuell, -1m = letzter Monat, etc.)
        neueste = recs.iloc[0] if len(recs) > 0 else None
        if neueste is None:
            return 0, {}

        stark_kauf  = int(neueste.get("strongBuy", 0))
        kauf        = int(neueste.get("buy", 0))
        gesamt_bull = stark_kauf + kauf

        # Vergleich mit vorherigem Monat falls vorhanden
        trend_hinweis = ""
        if len(recs) >= 2:
            vorher = recs.iloc[1]
            vorher_bull = int(vorher.get("strongBuy", 0)) + int(vorher.get("buy", 0))
            if gesamt_bull > vorher_bull:
                trend_hinweis = "steigend"
            elif gesamt_bull < vorher_bull:
                trend_hinweis = "fallend"
            else:
                trend_hinweis = "stabil"

        score = min(20, gesamt_bull * 2)
        return score, {"bull_analysten": gesamt_bull, "davon_strong_buy": stark_kauf, "trend": trend_hinweis}

    except Exception as e:
        print(f"[WARN] Revisions-Fehler {ticker}: {e}")
        return 0, {}


# ─── Options-Aktivitaet Score (0–20) ─────────────────────────────────────────

def berechne_options_score(ticker: str) -> tuple[int, dict]:
    """Misst Call/Put-Ratio. Hohe Call-Aktivitaet = institutionelles Interesse."""
    try:
        tk = yf.Ticker(ticker)
        verfuegbar = tk.options

        if not verfuegbar:
            return 0, {}

        # Naechsten Verfallstermin nehmen
        chain = tk.option_chain(verfuegbar[0])
        calls = chain.calls
        puts  = chain.puts

        call_vol = calls["volume"].fillna(0).sum()
        put_vol  = puts["volume"].fillna(0).sum()

        if put_vol == 0:
            ratio = 5.0
        else:
            ratio = call_vol / put_vol

        if ratio >= 3.0:
            score = 20
        elif ratio >= 2.0:
            score = 15
        elif ratio >= 1.5:
            score = 10
        elif ratio >= 1.0:
            score = 5
        else:
            score = 0

        return score, {"call_put_ratio": round(ratio, 2), "call_vol": int(call_vol), "put_vol": int(put_vol)}

    except Exception as e:
        print(f"[WARN] Options-Fehler {ticker}: {e}")
        return 0, {}


# ─── Kurs-Check ───────────────────────────────────────────────────────────────

def hole_kurs_info(ticker: str) -> dict:
    """Aktueller Kurs + 52-Wochen-Abstand + Analysten-Kursziel (Upside %)."""
    try:
        tk   = yf.Ticker(ticker)
        info = tk.fast_info
        try:
            hist = tk.history(period="2d")
            kurs = round(float(hist["Close"].iloc[-1]), 2) if not hist.empty else round(float(info.last_price or 0), 2)
        except Exception:
            kurs = round(float(info.last_price or 0), 2)

        hoch_52w = round(float(info.year_high or 0), 2)
        tief_52w = round(float(info.year_low  or 0), 2)
        abstand_ath = round((hoch_52w - kurs) / hoch_52w * 100, 1) if hoch_52w > 0 else 0.0

        # Analysten-Kursziel (Konsensus)
        try:
            full_info   = tk.info
            ziel_kurs   = round(float(full_info.get("targetMeanPrice") or 0), 2)
            upside_pct  = round((ziel_kurs - kurs) / kurs * 100, 1) if kurs > 0 and ziel_kurs > 0 else None
        except Exception:
            ziel_kurs  = 0
            upside_pct = None

        return {
            "kurs": kurs,
            "hoch_52w": hoch_52w,
            "tief_52w": tief_52w,
            "abstand_ath_prozent": abstand_ath,
            "ziel_kurs": ziel_kurs,
            "upside_pct": upside_pct,
        }
    except Exception:
        return {"kurs": 0, "hoch_52w": 0, "tief_52w": 0, "abstand_ath_prozent": 0, "ziel_kurs": 0, "upside_pct": None}


# ─── Gesamt-Score ─────────────────────────────────────────────────────────────

def analysiere_ticker(ticker: str, thema: str, thema_daten: dict) -> dict:
    """Berechnet Gesamt-Score (0–100) fuer einen Ticker in einem Thema."""
    print(f"  → {ticker} analysieren...")

    kurs_info                     = hole_kurs_info(ticker)
    news_score, news_treffer      = zaehle_news_treffer(ticker, thema_daten["rss_keywords"])
    revisions_score, rev_details  = berechne_revisions_score(ticker)
    options_score, opt_details    = berechne_options_score(ticker)

    gesamt = news_score + revisions_score + options_score  # Trends kommen auf Thema-Ebene

    signal_stufe = (
        "STARK"   if gesamt >= 55 else
        "MODERAT" if gesamt >= 30 else
        "SCHWACH"
    )

    return {
        "ticker":          ticker,
        "thema":           thema,
        "gesamt_score":    gesamt,
        "signal_stufe":    signal_stufe,
        "kurs_info":       kurs_info,
        "scores": {
            "news":      news_score,
            "revisions": revisions_score,
            "options":   options_score,
        },
        "details": {
            "news_treffer":  news_treffer,
            "revisions":     rev_details,
            "options":       opt_details,
        },
    }


# ─── HTML-Report ──────────────────────────────────────────────────────────────

AMPEL = {"STARK": "#22c55e", "MODERAT": "#f59e0b", "SCHWACH": "#6b7280"}

def erstelle_html(ergebnisse: list[dict], trends_scores: dict) -> str:
    heute = datetime.now().strftime("%d.%m.%Y %H:%M")

    zeilen = ""
    for e in sorted(ergebnisse, key=lambda x: x["gesamt_score"], reverse=True):
        farbe = AMPEL[e["signal_stufe"]]
        ki = e["kurs_info"]
        s  = e["scores"]
        d  = e["details"]
        trend_info = trends_scores.get(e["thema"], {})
        trend_score = trend_info.get("score", 0)
        gesamt_mit_trend = min(100, e["gesamt_score"] + trend_score)

        upside = ki.get("upside_pct")
        upside_str = f"+{upside}%" if upside and upside > 0 else (f"{upside}%" if upside is not None else "–")
        upside_farbe = "#22c55e" if upside and upside > 0 else "#ef4444"
        ziel_str = f"${ki['ziel_kurs']}" if ki.get("ziel_kurs") else "–"

        zeilen += f"""
        <tr>
          <td><strong>{e['ticker']}</strong></td>
          <td>{e['thema']}</td>
          <td style="color:{farbe};font-weight:bold;font-size:1.2em">{gesamt_mit_trend}/100</td>
          <td><span style="background:{farbe};color:#fff;padding:2px 8px;border-radius:4px">{e['signal_stufe']}</span></td>
          <td>${ki['kurs']}</td>
          <td>{ziel_str}</td>
          <td style="color:{upside_farbe};font-weight:bold">{upside_str}</td>
          <td style="color:#f59e0b">{ki['abstand_ath_prozent']}% unter ATH</td>
          <td>{s['news']} | {s['revisions']} | {s['options']} | {trend_score}</td>
          <td>{d['news_treffer']} Artikel</td>
          <td>{d['revisions'].get('bull_analysten', '–')} Bullen</td>
          <td>{d['options'].get('call_put_ratio', '–')}</td>
        </tr>"""

    trends_tabelle = ""
    for thema, info in trends_scores.items():
        trends_tabelle += f"<tr><td>{thema}</td><td>{info.get('score', 0)}/30</td><td>{info.get('details', {}).get('wachstum_prozent', '–')}%</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Tailwind Scanner — {heute}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }}
  h1 {{ color: #38bdf8; }} h2 {{ color: #94a3b8; border-bottom: 1px solid #334155; padding-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 30px; }}
  th {{ background: #1e293b; color: #94a3b8; padding: 10px; text-align: left; font-size: 0.85em; }}
  td {{ padding: 10px; border-bottom: 1px solid #1e293b; font-size: 0.9em; }}
  tr:hover td {{ background: #1e293b; }}
  .hinweis {{ background: #1e293b; padding: 15px; border-radius: 8px; border-left: 4px solid #38bdf8; margin: 20px 0; }}
</style>
</head>
<body>
<h1>Tailwind Scanner Report</h1>
<p style="color:#64748b">Erstellt: {heute} — Signale fuer unterbewertete Aktien mit Makro-Rueckenwind</p>

<div class="hinweis">
  <strong>Score-Erklaerung:</strong> News (0–30) + Analyst-Revisions (0–20) + Options Call/Put (0–20) + Google Trends (0–30) = max. 100<br>
  <strong>STARK ≥ 55 | MODERAT ≥ 30 | SCHWACH &lt; 30</strong>
</div>

<h2>Ticker-Signale</h2>
<table>
  <tr>
    <th>Ticker</th><th>Thema</th><th>Score</th><th>Signal</th>
    <th>Einstieg</th><th>Ziel (Analysten)</th><th>Upside %</th><th>ATH-Abstand</th>
    <th>News|Rev|Opt|Trend</th>
    <th>News-Treffer</th><th>Bull-Analysten</th><th>Call/Put</th>
  </tr>
  {zeilen}
</table>

<h2>Google Trends — Themen-Momentum</h2>
<table>
  <tr><th>Thema</th><th>Trends-Score</th><th>Wachstum vs. 90-Tage-Schnitt</th></tr>
  {trends_tabelle}
</table>

<p style="color:#475569;font-size:0.8em">Kein Finanzberatung. Nur zur Analyse.</p>
</body>
</html>"""


# ─── JSON-Export fuer Trading-System ─────────────────────────────────────────

DATA_DIR = BASE_DIR / "data"

def speichere_json(alle_ergebnisse: list[dict], trends_scores: dict):
    """
    Speichert die Scan-Ergebnisse als maschinenlesbare JSON-Datei.
    Wird vom Trading-System als tailwind_connector gelesen.
    Pfad: data/latest_signals.json
    """
    DATA_DIR.mkdir(exist_ok=True)

    heute = datetime.now().strftime("%Y-%m-%d")
    uhrzeit = datetime.now().strftime("%H:%M:%S")

    signale_liste = []
    for e in alle_ergebnisse:
        # Trends-Score auf Ticker-Ebene anwenden
        t_score = trends_scores.get(e["thema"], {}).get("score", 0)
        gesamt_mit_trend = min(100, e["gesamt_score"] + t_score)

        signal_stufe = (
            "STARK"   if gesamt_mit_trend >= 55 else
            "MODERAT" if gesamt_mit_trend >= 30 else
            "SCHWACH"
        )

        signale_liste.append({
            "ticker":       e["ticker"],
            "thema":        e["thema"],
            "gesamt_score": gesamt_mit_trend,
            "signal_stufe": signal_stufe,
            "kurs_info":    e["kurs_info"],
            "scores": {
                "news":      e["scores"]["news"],
                "revisions": e["scores"]["revisions"],
                "options":   e["scores"]["options"],
                "trends":    t_score,
            },
            "details": e["details"],
        })

    # Nach Score sortieren
    signale_liste.sort(key=lambda x: x["gesamt_score"], reverse=True)

    stark   = [s for s in signale_liste if s["signal_stufe"] == "STARK"]
    moderat = [s for s in signale_liste if s["signal_stufe"] == "MODERAT"]

    ausgabe = {
        "meta": {
            "scan_datum":     heute,
            "scan_uhrzeit":   uhrzeit,
            "total_signale":  len(signale_liste),
            "stark_count":    len(stark),
            "moderat_count":  len(moderat),
            "top_ticker":     signale_liste[0]["ticker"] if signale_liste else "",
        },
        "signals": signale_liste,
        "themes": {
            thema: {
                "trends_score":      info.get("score", 0),
                "wachstum_prozent":  info.get("details", {}).get("wachstum_prozent", 0),
            }
            for thema, info in trends_scores.items()
        },
    }

    json_pfad = DATA_DIR / "latest_signals.json"
    json_pfad.write_text(json.dumps(ausgabe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ JSON gespeichert: {json_pfad}")
    return json_pfad


# ─── Hauptprogramm ────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  TAILWIND SCANNER")
    print(f"  {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("=" * 60)

    # Doppelter Run verhindern: wenn heute schon ein Report existiert, abbrechen
    heute = datetime.now().strftime("%Y-%m-%d")
    report_pfad = REPORT_DIR / f"report_{heute}.html"
    if report_pfad.exists():
        print(f"✓ Report fuer {heute} existiert bereits — kein zweiter Run noetig.")
        return

    alle_ergebnisse  = []
    trends_scores    = {}

    for thema, daten in THEMES.items():
        print(f"\n[THEMA] {thema}")

        # Google Trends einmal pro Thema abfragen (nicht pro Ticker)
        print("  → Google Trends abfragen...")
        t_score, t_details = berechne_trends_score(daten["search_keywords"])
        trends_scores[thema] = {"score": t_score, "details": t_details}
        print(f"     Trends-Score: {t_score}/30 ({t_details})")

        for ticker in daten["beneficiary_tickers"]:
            ergebnis = analysiere_ticker(ticker, thema, daten)
            alle_ergebnisse.append(ergebnis)
            time.sleep(0.5)  # yfinance Rate-Limit

    # HTML-Report speichern
    heute = datetime.now().strftime("%Y-%m-%d")
    report_pfad = REPORT_DIR / f"report_{heute}.html"
    html = erstelle_html(alle_ergebnisse, trends_scores)
    report_pfad.write_text(html, encoding="utf-8")
    print(f"\n✓ HTML gespeichert: {report_pfad}")

    # JSON fuer Trading-System speichern
    speichere_json(alle_ergebnisse, trends_scores)

    # Telegram-Benachrichtigung
    sende_telegram(alle_ergebnisse, trends_scores, report_pfad)

    # Zusammenfassung in Terminal
    print("\n" + "=" * 60)
    print("  TOP SIGNALE")
    print("=" * 60)
    top = sorted(alle_ergebnisse, key=lambda x: x["gesamt_score"], reverse=True)[:5]
    for e in top:
        t = trends_scores.get(e["thema"], {}).get("score", 0)
        gesamt = min(100, e["gesamt_score"] + t)
        print(f"  {e['ticker']:6s} {gesamt:3d}/100  [{e['signal_stufe']:7s}]  {e['thema']}")

    print()


if __name__ == "__main__":
    main()
