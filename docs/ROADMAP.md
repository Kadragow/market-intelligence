# Roadmap

Każdy etap kończy się działającą, przetestowaną wersją. Nie realizujemy kilku etapów
naraz. Przed startem etapu agent przedstawia plan i czeka na akceptację.
Na końcu etapu agent aktualizuje status i sekcję „Notatki”.

Status: `[ ]` do zrobienia, `[~]` w toku, `[x]` gotowe.

## Etap 1 — działający prototyp (MVP)

- [ ] Struktura projektu, `pyproject.toml` (uv), Ruff, mypy, pytest, `.gitignore`, `.env.example`
- [ ] Settings (Pydantic) z walidacją; tryby `--mock` i `--dry-run`
- [ ] `config/instruments.yaml`, `config/sources.yaml`
- [ ] Pobieranie cen dla listy startowej (yfinance) + fake provider z fixture'ami
- [ ] Pobieranie nagłówków z RSS + fixture'y
- [ ] Minimalne modele domenowe (Instrument, PriceBar, NewsItem)
- [ ] Generowanie raportu przez Claude API za interfejsem `LLMClient` + fake client
- [ ] Walidacja odpowiedzi modelu (schemat, źródła, liczby z wejścia)
- [ ] Zapis raportu (Markdown + HTML) do `reports/`
- [ ] Wysyłka SMTP tylko przy kompletnej konfiguracji i wyłączonym `--dry-run`
- [ ] Testy podstawowych funkcji; `uv run mi weekly --mock --dry-run` działa offline
- [ ] README: instalacja, konfiguracja, uruchomienie, ograniczenia

**Gotowe, gdy:** spełniona definicja MVP z `docs/SPEC.md` §14.

## Etap 2 — jakość i normalizacja danych

- [ ] Wspólne interfejsy providerów (ceny, fundamenty, newsy, filings, makro)
- [ ] Adapter FRED
- [ ] Retry, exponential backoff, timeouty, obsługa limitów
- [ ] Timestampy (published/observed/fetched) i pochodzenie danych
- [ ] Walidacja świeżości, duplikatów i luk; status danych nieaktualnych

## Etap 3 — baza danych

- [ ] Interfejs repozytorium (implementacja SQLite → PostgreSQL)
- [ ] Modele SQLAlchemy i migracje Alembic
- [ ] Zapis cen, newsów, sygnałów, raportów, błędów
- [ ] Niezmienne ID sygnałów i wersjonowanie reguł analitycznych

## Etap 4 — analityka inwestycyjna

- [ ] Silnik techniczny (krótkoterminowy)
- [ ] Silnik fundamentalny (długoterminowy) — wymaga wyboru dostawcy fundamentów
- [ ] Silnik makro
- [ ] Scoring z konfigurowalnymi wagami (`config/scoring.yaml`) i analiza ryzyka
- [ ] Argumenty za/przeciw, warunki unieważnienia, brakujące dane
- [ ] Brak automatycznego tradingu

## Etap 5 — automatyzacja produkcyjna

- [ ] Harmonogram (GitHub Actions), strefa `Europe/Warsaw`, obsługa DST
- [ ] Kontrola duplikatów raportów i bezpieczne ponawianie
- [ ] Logi i monitoring błędów
- [ ] Sekrety w GitHub Secrets; środowisko uruchomieniowe

## Etap 6 — backtesting

- [ ] Silnik historyczny bez look-ahead bias
- [ ] Koszty, poślizg, dywidendy, splity, waluty
- [ ] Benchmarki i podział train/validation/test
- [ ] Raport metryk i ograniczeń

## Otwarte decyzje operatora

- [ ] Potwierdzić listę instrumentów startowych (`docs/SPEC.md` §3)
- [ ] Podać kanały RSS (`config/sources.yaml`)
- [ ] Potwierdzić harmonogram (domyślnie niedziela 18:00 Europe/Warsaw)
- [ ] Dane SMTP i adres odbiorcy
- [ ] Klucz Anthropic API (Claude Console) i miesięczny limit kosztu
- [ ] Dostawca fundamentów przed Etapem 4

## Notatki

_(agent dopisuje tu decyzje oznaczone `# DECISION:` i znane ograniczenia po każdym etapie)_
