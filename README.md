# Market Intelligence

System badawczo-raportowy: pobiera dane rynkowe i nagłówki wiadomości, liczy proste
metryki, a następnie generuje tygodniowy brief **po polsku** (Markdown + HTML) z pomocą
modelu językowego wywoływanego przez [OpenRouter](https://openrouter.ai). Raport jest zapisywany lokalnie i opcjonalnie wysyłany e-mailem.

> Materiał ma charakter badawczy i informacyjny. Nie stanowi rekomendacji inwestycyjnej
> ani porady finansowej. System nie składa zleceń i nie ma dostępu do kont brokerskich.

Specyfikacja: [docs/SPEC.md](docs/SPEC.md) · Plan etapów: [docs/ROADMAP.md](docs/ROADMAP.md)

## Wymagania

- [uv](https://docs.astral.sh/uv/) (instaluje też Pythona 3.12)
- Do trybu `--mock`: nic więcej — bez kluczy API i bez sieci.
- Do trybu produkcyjnego: klucz OpenRouter API, dostęp do internetu (Yahoo Finance, RSS).

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
prawdziwego LLM. Zegar jest zamrożony na niedzielę 2026-10-18 18:00 Europe/Warsaw, więc wynik
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
| `OPENROUTER_API_KEY` | — | Klucz z https://openrouter.ai/settings/keys. Wymagany poza `--mock`. |
| `MI_LLM_MODEL` | `anthropic/claude-sonnet-5.5` | Identyfikator modelu w OpenRouter. Model musi obsługiwać structured outputs. |
| `MI_LLM_MAX_TOKENS` | `16000` | Limit tokenów odpowiedzi (u modeli z rozumowaniem obejmuje też tokeny rozumowania). |
| `MI_LLM_MAX_COST_USD` | `0.50` | Limit kosztu na uruchomienie. Przed wywołaniem liczony jest górny szacunek (wejście ≈ znaki / 3 + pełne `max_tokens`); po przekroczeniu wywołanie nie następuje. |
| `MI_LLM_INPUT_USD_PER_MTOK` / `MI_LLM_OUTPUT_USD_PER_MTOK` | `2.0` / `10.0` | Maksymalna cena w USD za 1M tokenów. Służy do szacunku kosztu i jest wysyłana do OpenRouter jako `provider.max_price`, więc droższy dostawca nie zostanie użyty. Domyślnie stawki Sonnet 5.5 u Anthropic; sprawdź stronę modelu na openrouter.ai. Dla modeli `:free` ustaw `0` / `0`. |
| `MI_LLM_DATA_COLLECTION` | `allow` | `allow` (domyślne w OpenRouter) albo `deny` — tylko dostawcy, którzy nie przechowują promptów (mniej modeli, darmowe mogą być niedostępne). |
| `MI_EMAIL_ENABLED` | `false` | Jawna zgoda na wysyłkę e-maili. |
| `MI_SMTP_HOST`, `MI_SMTP_PORT`, `MI_SMTP_USERNAME`, `MI_SMTP_PASSWORD`, `MI_SMTP_USE_STARTTLS` | — / `587` / — / — / `true` | Serwer SMTP. |
| `MI_EMAIL_FROM`, `MI_EMAIL_TO` | — | Nadawca i odbiorcy (lista po przecinku). |

Pełna lista (ścieżki, parametry danych) jest w `.env.example`.

## Wybór modelu

Modele obsługujące structured outputs: https://openrouter.ai/models?supported_parameters=structured_outputs.
Przykład z darmowym modelem (bez kosztów, z limitami zapytań):

```bash
MI_LLM_MODEL=<autor>/<model>:free
MI_LLM_INPUT_USD_PER_MTOK=0
MI_LLM_OUTPUT_USD_PER_MTOK=0
```

## Uruchomienie

```bash
uv run mi weekly --mock --dry-run   # offline, syntetyczne dane, bez maila
uv run mi weekly --dry-run          # prawdziwe dane + LLM (OpenRouter), bez maila
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
3. Dane policzone przez kod trafiają do modelu jako JSON; nagłówki w osobnym bloku
   oznaczonym jako niezaufane dane.
4. Odpowiedź ma narzucony schemat JSON (structured outputs w OpenRouter:
   `response_format: json_schema` ze schematem z Pydantic, `provider.require_parameters`
   kieruje zapytanie tylko do dostawców, którzy go obsługują) i jest walidowana przez kod:
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
- Brak retry/backoff dla yfinance, RSS i OpenRouter (Etap 2). OpenRouter sam przełącza
  się na zapasowego dostawcę, jeśli błąd wystąpi przed pierwszym tokenem.
- Brak bazy danych i historii sygnałów (Etap 3); raporty to pliki w `reports/`.
- Brak kontroli duplikatów wysyłki: ponowne `--send` w tym samym tygodniu wyśle maila
  ponownie (Etap 5). Plik raportu jest nadpisywany.
- Walidacja liczb jest heurystyczna (regex + tolerancja zaokrąglenia); małe liczby
  całkowite mogą przypadkowo pasować do wartości z danych.
- OpenRouter nie ma endpointu do liczenia tokenów, więc szacunek wejścia to heurystyka
  (znaki / 3). Faktyczny koszt z odpowiedzi (`usage.cost`, w kredytach) jest logowany.
- Modele z rozumowaniem (np. część darmowych) zużywają `MI_LLM_MAX_TOKENS` także na
  rozumowanie; przy błędzie „max_tokens reached” zwiększ ten limit (dla `:free` nie
  zmienia to kosztu).
- Darmowe modele (`:free`) mają limity: 20 zapytań/min i 50/dzień (1000/dzień po zakupie
  co najmniej 10 kredytów). Nie każdy darmowy model obsługuje structured outputs, a jakość
  może nie wystarczyć, żeby przejść walidację liczb i źródeł.
- Przy realnym API nie sprawdzono jeszcze jakości tekstu ani odsetka odrzuceń walidacji.
