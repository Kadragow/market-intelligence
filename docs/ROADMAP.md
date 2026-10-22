# Roadmap

Każdy etap kończy się działającą, przetestowaną wersją. Nie realizujemy kilku etapów
naraz. Przed startem etapu agent przedstawia plan i czeka na akceptację.
Na końcu etapu agent aktualizuje status i sekcję „Notatki”.

Status: `[ ]` do zrobienia, `[~]` w toku, `[x]` gotowe.

## Etap 1 — działający prototyp (MVP)

- [x] Struktura projektu, `pyproject.toml` (uv), Ruff, mypy, pytest, `.gitignore`, `.env.example`
- [x] Settings (Pydantic) z walidacją; tryby `--mock` i `--dry-run`
- [x] `config/instruments.yaml`, `config/sources.yaml`
- [x] Pobieranie cen dla listy startowej (yfinance) + fake provider z fixture'ami
- [x] Pobieranie nagłówków z RSS + fixture'y
- [x] Minimalne modele domenowe (Instrument, PriceBar, NewsItem)
- [x] Generowanie raportu przez Claude API za interfejsem `LLMClient` + fake client
- [x] Walidacja odpowiedzi modelu (schemat, źródła, liczby z wejścia)
- [x] Zapis raportu (Markdown + HTML) do `reports/`
- [x] Wysyłka SMTP tylko przy kompletnej konfiguracji i wyłączonym `--dry-run`
- [x] Testy podstawowych funkcji; `uv run mi weekly --mock --dry-run` działa offline
- [x] README: instalacja, konfiguracja, uruchomienie, ograniczenia

Status: ukończony w trybie offline (2026-10-22). Nie przetestowano jeszcze z prawdziwym
kluczem Anthropic API, prawdziwym SMTP ani na żywych danych yfinance/RSS.

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

### Etap 1 — decyzje (`# DECISION:` w kodzie)

- CLI na `typer` (`cli.py`).
- HTML z osobnego szablonu Jinja2, nie z konwersji Markdown; tabele, źródła i timestampy
  renderuje kod, model pisze tylko komentarz (`reports/render.py`).
- Walidacja liczb: każda liczba w tekście modelu musi być poprawnym zaokrągleniem wartości
  z wejścia (znak ignorowany); daty (ISO i dd.mm.rrrr) muszą występować w wejściu; liczby
  z nagłówka dozwolone tylko w tezie cytującej ten nagłówek (`ai/validation.py`).
- Dane `stale`: ostatnia świeca starsza niż 5 dni roboczych (`MI_STALE_AFTER_BUSINESS_DAYS`).
- Zmiany liczone na `adj_close`, gdy oba punkty go mają, inaczej na `close`; podstawa jest
  zapisywana (`analysis/price_metrics.py`).
- Świece yfinance: zachowana data sesji, zapis jako 00:00 UTC (konwersja północy giełdowej
  do UTC przesuwałaby datę).
- Wolumen 0 dla FX i rentowności traktowany jako brak danych.
- Ceny tokenów w settings (domyślnie Sonnet 5.5: 2 / 10 USD za 1M, z cennika Anthropic
  sprawdzonego 2026-10-22); limit kosztu sprawdzany przed wywołaniem jako górny szacunek
  (`count_tokens` + pełne `max_tokens`).
- Serwerowy refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`)
  domyślnie włączony; można wyłączyć `MI_LLM_REFUSAL_FALLBACK=false`.
- Bez `ANTHROPIC_API_KEY` poza `--mock` program kończy się błędem konfiguracji (bez cichego
  przejścia na fake), sprawdzane przed pobieraniem danych.
- Brak jakiegokolwiek instrumentu ze statusem `ok` → raport o niepowodzeniu bez wywołania modelu.
- Wiadomości bez daty publikacji są zachowywane; z datą spoza okna `MI_NEWS_LOOKBACK_DAYS`
  lub z przyszłości — odrzucane.
- `--mock`: zamrożony zegar (2026-10-18 18:00 Europe/Warsaw), syntetyczne dane w pakiecie
  (`mock_data/`, ceny od 100), baner „DANE TESTOWE”, plik `weekly-<tydzień>-mock.*`.
- Raport nieudany zapisywany jako `*-FAILED.{md,html}`; nie jest wysyłany e-mailem.
- Tydzień ISO liczony w strefie Europe/Warsaw.

### Etap 1 — znane ograniczenia

- Nie uruchomiono z prawdziwym Claude API, SMTP ani żywymi danymi — tylko testy z fake'ami.
- Brak retry/backoff dla yfinance i RSS (Etap 2); SDK Anthropic ponawia 429/5xx domyślnie 2×.
- Brak kontroli duplikatów wysyłki (Etap 5) — ponowne `--send` wyśle maila ponownie.
- Walidacja liczb jest heurystyczna; małe liczby całkowite mogą przypadkowo pasować.
- Koszt przy fallbacku na inny model liczony cennikiem modelu z settings (logowane ostrzeżenie).
- `config/sources.yaml` pusty — potrzebna lista kanałów RSS od operatora.
- SDK `anthropic` 2.1.0 (API `2026-10-21`); dokumentacja z której korzystałem opisywała 1.x —
  sygnatury `messages.parse`, `count_tokens` i `fallbacks` zweryfikowano w zainstalowanym SDK.
