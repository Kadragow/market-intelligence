# Market Intelligence

System badawczo-raportowy: pobiera dane rynkowe i nagłówki wiadomości, liczy proste
metryki, a następnie generuje tygodniowy brief **po polsku** (Markdown + HTML) z pomocą
Claude API. Raport jest zapisywany lokalnie i opcjonalnie wysyłany e-mailem.

> Materiał ma charakter badawczy i informacyjny. Nie stanowi rekomendacji inwestycyjnej
> ani porady finansowej. System nie składa zleceń i nie ma dostępu do kont brokerskich.

Specyfikacja: [docs/SPEC.md](docs/SPEC.md) · Plan etapów: [docs/ROADMAP.md](docs/ROADMAP.md)

## Wymagania

- [uv](https://docs.astral.sh/uv/) (instaluje też Pythona 3.12)
- Do trybu `--mock`: nic więcej — bez kluczy API i bez sieci.
- Do trybu produkcyjnego: klucz Anthropic API, dostęp do internetu (Yahoo Finance, RSS).

## Instalacja

```bash
uv python install 3.12   # jeśli nie masz Pythona 3.12
uv sync
```

## Szybki start (offline)

```bash
uv run mi weekly --mock --dry-run
```

Tryb `--mock` używa **syntetycznych** danych z `src/market_intelligence/mock_data/`
(ceny startujące od 100, testowe nagłówki RSS) i deterministycznego „modelu” zamiast
Claude API. Zegar jest zamrożony na niedzielę 2026-10-18 18:00 Europe/Warsaw, więc wynik
jest powtarzalny. Raport trafia do `reports/weekly-2026-W42-mock.{md,html}` i ma wyraźny
baner „DANE TESTOWE”. Dane testowe celowo zawierają przypadki brzegowe: brak danych
(USD/PLN), dane nieaktualne (US 10Y), duplikat (GLD), brakujące zamknięcie (EWG) oraz
nagłówek z próbą prompt injection.

## Konfiguracja

1. Skopiuj `.env.example` do `.env` i uzupełnij potrzebne wartości. `.env` jest w `.gitignore`.
2. `config/instruments.yaml` — lista instrumentów (symbole w notacji Yahoo Finance).
3. `config/sources.yaml` — kanały RSS. **Domyślnie pusta**: system nie zgaduje adresów,
   uzupełnia je operator. Bez kanałów raport zawiera adnotację o braku wiadomości.

### Zmienne środowiskowe

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Klucz z Claude Console (rozliczany osobno; subskrypcja Claude Pro go nie obejmuje). Wymagany poza `--mock`. |
| `MI_LLM_MODEL` | `claude-sonnet-5-5` | Model Claude. |
| `MI_LLM_MAX_TOKENS` | `16000` | Limit tokenów odpowiedzi. |
| `MI_LLM_MAX_COST_USD` | `0.50` | Limit kosztu na uruchomienie. Przed wywołaniem liczony jest górny szacunek (`count_tokens` + pełne `max_tokens`); po przekroczeniu wywołanie nie następuje. |
| `MI_LLM_INPUT_USD_PER_MTOK` / `MI_LLM_OUTPUT_USD_PER_MTOK` | `2.0` / `10.0` | Cennik modelu w USD za 1M tokenów (stawki Sonnet 5.5 z [cennika Anthropic](https://platform.claude.com/docs/en/about-claude/pricing), sprawdzone 2026-10-22). Zaktualizuj przy zmianie modelu. |
| `MI_LLM_REFUSAL_FALLBACK` | `true` | Serwerowy fallback przy odmowie modelu (`fallbacks: "default"`, beta). |
| `MI_EMAIL_ENABLED` | `false` | Jawna zgoda na wysyłkę e-maili. |
| `MI_SMTP_HOST`, `MI_SMTP_PORT`, `MI_SMTP_USERNAME`, `MI_SMTP_PASSWORD`, `MI_SMTP_USE_STARTTLS` | — / `587` / — / — / `true` | Serwer SMTP. |
| `MI_EMAIL_FROM`, `MI_EMAIL_TO` | — | Nadawca i odbiorcy (lista po przecinku). |

Pełna lista (ścieżki, parametry danych) jest w `.env.example`.

## Uruchomienie

```bash
uv run mi weekly --mock --dry-run   # offline, syntetyczne dane, bez maila
uv run mi weekly --dry-run          # prawdziwe dane + Claude API, bez maila
uv run mi weekly --send             # jw. + wysyłka e-mail
```

E-mail wychodzi **tylko** gdy jednocześnie: podano `--send` (domyślnie jest dry-run),
`MI_EMAIL_ENABLED=true` i konfiguracja SMTP jest kompletna. Raport niekompletny
(błąd modelu, zły format, brak źródeł, liczby spoza danych) nigdy nie jest wysyłany.

Kody wyjścia: `0` — sukces, `1` — raport nie przeszedł walidacji (zapisany jako
`*-FAILED.{md,html}`), `2` — błąd konfiguracji, `3` — błąd wysyłki e-mail.

## Jak powstaje raport

1. Ceny dzienne (yfinance) → czyszczenie (duplikaty, NaN, wartości ≤ 0 → brak) →
   status `ok` / `stale` / `insufficient_data` → zmiana tygodniowa i miesięczna.
2. Nagłówki RSS (tylko tytuł, link, źródło, daty, krótki lead — nigdy pełne artykuły).
3. Dane policzone przez kod trafiają do Claude jako JSON; nagłówki w osobnym bloku
   oznaczonym jako niezaufane dane.
4. Odpowiedź ma narzucony schemat (structured outputs, `messages.parse` z modelem
   Pydantic) i jest walidowana przez kod:
   - każda teza ma co najmniej jedno istniejące źródło,
   - każda liczba i data w tekście występuje w danych wejściowych (liczby z nagłówka tylko
     wtedy, gdy teza cytuje ten nagłówek),
   - kandydaci do researchu mają świeże i kompletne dane.
5. Tabele, źródła i timestampy renderuje kod (Jinja2); model pisze tylko komentarz.

## Rozwój

```bash
uv run pytest            # testy (bez sieci)
uv run ruff check .      # lint
uv run ruff format .     # formatowanie
uv run mypy src          # typy (strict)
UPDATE_GOLDEN=1 uv run pytest tests/integration   # odświeżenie golden files
```

## Znane ograniczenia (Etap 1)

- yfinance to prototyp, nie licencjonowany feed; dane mogą być opóźnione lub niepełne.
- Brak danych makro (FRED — Etap 2), fundamentów (Etap 4) i kalendarza wydarzeń.
- Brak scoringu i sygnałów (Etap 4) — sekcja „kandydaci do researchu” to tylko
  interpretacja prostych zmian cen przez model, nie rekomendacja.
- Brak retry/backoff dla yfinance i RSS (Etap 2); Claude SDK sam ponawia błędy 429/5xx.
- Brak bazy danych i historii sygnałów (Etap 3); raporty to pliki w `reports/`.
- Brak kontroli duplikatów wysyłki: ponowne `--send` w tym samym tygodniu wyśle maila
  ponownie (Etap 5). Plik raportu jest nadpisywany.
- Walidacja liczb jest heurystyczna (regex + tolerancja zaokrąglenia); małe liczby
  całkowite mogą przypadkowo pasować do wartości z danych.
- Koszt przy fallbacku na inny model jest liczony według cennika modelu z settings.
- Przy realnym API nie sprawdzono jeszcze jakości tekstu ani odsetka odrzuceń walidacji.
