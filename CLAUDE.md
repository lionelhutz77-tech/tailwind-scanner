# CLAUDE.md — Tailwind Scanner

Erkennt unterbewertete Aktien über Makro-/Sektor-Signale („Rückenwind"). Nur kostenlose Tools, Ausführung in der Cloud via GitHub Actions.

## Ausführen
- Scan: `python scanner.py`. Schnelltests: `python test_quick.py`, `python test_scan.py`.
- Benachrichtigung: `telegram_notify.py` (Telegram). Themen/Konfig: `themes.json`.
- Secrets in `.env` lokal; in der Cloud als **GitHub Secrets** (Actions). `requirements.txt`.

## Konventionen / Gotchas
- **Nur kostenlose Datenquellen/Tools** — keine kostenpflichtigen APIs einführen.
- LLM-Anteile über das gemeinsame Free-AI-Routing; bei direktem Cloudlauf Groq
  `openai/gpt-oss-20b` für Routine und `openai/gpt-oss-120b` nur für begründete
  Tiefenanalyse, nie Anthropic-API und kein kostenpflichtiger Fallback.
- Cloud-Lauf über GitHub Actions: keine lokalen Pfade/Abhängigkeiten annehmen, die in CI fehlen.
- Verbindung zum Trading-System über dessen `agents/tailwind_connector.py`.
- Keine persönliche Anlageberatung — Signal-Ausgabe ist Werkzeug-Output.

## Arbeitsweise
Skill **bau-qualitaet**: Scanner-Änderungen mit echtem Lauf/Test verifizieren vor „fertig".
