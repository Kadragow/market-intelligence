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
- Do trybu produkcyjnego: klucz OpenRouter API, dostęp do internetu (Yahoo Finance, RSS,
  FRED). Klucz FRED jest opcjonalny: bez niego sekcja makro ma status „brak danych”.

## Instalacja

```bash
uv python install 3.12   # jeśli nie masz Pythona 3.12
uv sync
uv run mi db upgrade     # tworzy/aktualizuje bazę (domyślnie data/market_intelligence.db)
```

## Szybki start (offline)

```bash
uv run mi weekly --mock --dry-run
```

Tryb `--mock` używa **syntetycznych** danych z `src/market_intelligence/mock_data/`
(ceny startujące od 100, testowe nagłówki RSS, serie makro w formacie FRED) i deterministycznego „modelu” zamiast
prawdziwego LLM. Zegar jest zamrożony na niedzielę 2026-10-18 18:00 Europe/Warsaw, więc wynik
jest powtarzalny. Raport trafia do `reports/weekly-2026-W42-mock.{md,html}` i ma wyraźny
baner „DANE TESTOWE”. Dane testowe celowo zawierają przypadki brzegowe: brak danych
(USD/PLN), dane nieaktualne (US 10Y, seria UNRATE), duplikat (GLD), brakujące zamknięcie
(EWG), brakująca wartość makro (DGS10) oraz nagłówek z próbą prompt injection.

## Konfiguracja

1. Skopiuj `.env.example` do `.env` i uzupełnij potrzebne wartości. `.env` jest w `.gitignore`.
2. `config/instruments.yaml` — lista instrumentów (symbole w notacji Yahoo Finance).
3. `config/sources.yaml` — kanały RSS (lista operatora; system nie zgaduje adresów).
   Bez kanałów raport zawiera adnotację o braku wiadomości.
4. `config/macro.yaml` — serie makro z FRED: polska nazwa, częstotliwość, próg
   świeżości w dniach i to, czy liczyć zmianę rok do roku. Tytuł i jednostki serii
   pobierane są z API FRED.

### Zmienne środowiskowe

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Klucz z https://openrouter.ai/settings/keys. Wymagany poza `--mock`. |
| `MI_LLM_MODEL` | `anthropic/claude-sonnet-5.5` | Identyfikator modelu w OpenRouter. Model musi obsługiwać structured outputs. |
| `MI_LLM_MAX_TOKENS` | `16000` | Limit tokenów odpowiedzi (u modeli z rozumowaniem obejmuje też tokeny rozumowania). |
| `MI_LLM_MAX_COST_USD` | `0.50` | Limit kosztu na uruchomienie. Przed wywołaniem liczony jest górny szacunek (wejście ≈ znaki / 3 + pełne `max_tokens`); po przekroczeniu wywołanie nie następuje. |
| `MI_LLM_INPUT_USD_PER_MTOK` / `MI_LLM_OUTPUT_USD_PER_MTOK` | `2.0` / `10.0` | Maksymalna cena w USD za 1M tokenów. Służy do szacunku kosztu i jest wysyłana do OpenRouter jako `provider.max_price`, więc droższy dostawca nie zostanie użyty. Domyślnie stawki Sonnet 5.5 u Anthropic; sprawdź stronę modelu na openrouter.ai. Dla modeli `:free` ustaw `0` / `0`. |
| `MI_LLM_DATA_COLLECTION` | `allow` | `allow` (domyślne w OpenRouter) albo `deny` — tylko dostawcy, którzy nie przechowują promptów (mniej modeli, darmowe mogą być niedostępne). |
| `MI_DATABASE_URL` | `sqlite:///data/market_intelligence.db` | Baza danych (SQLAlchemy URL). PostgreSQL wymaga doinstalowania sterownika. |
| `FRED_API_KEY` | — | Darmowy klucz z https://fred.stlouisfed.org/docs/api/api_key.html. Opcjonalny. |
| `MI_HTTP_MAX_ATTEMPTS` | `3` | Liczba prób dla RSS, yfinance, FRED i OpenRouter (wykładniczy backoff z jitterem, `Retry-After` respektowany do `MI_HTTP_BACKOFF_MAX_SECONDS`). OpenRouter ponawiany tylko przy 429, 503 i błędach połączenia, żeby nie zapłacić dwa razy. |
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
`*-FAILED.{md,html}`), `2` — błąd konfiguracji (także nieaktualny schemat bazy),
`3` — błąd wysyłki e-mail, `4` — błąd zapisu do bazy (raport i tak powstał i został wysłany).

## Baza danych

```bash
uv run mi db upgrade   # migracje Alembic do najnowszej wersji
uv run mi db status    # wersja schematu i ostatnie uruchomienia
```

Każde `mi weekly` zapisuje uruchomienie (tydzień, tryb, model, koszt LLM, wersja reguł
i hash konfiguracji), surowe dane (ceny, obserwacje makro, metadane wiadomości),
wyliczone snapshoty instrumentów i makro, raport (Markdown, HTML, zwalidowany szkic
modelu), próby wysyłki i błędy.

- Surowe dane są nadpisywane przy ponownym pobraniu (dostawcy korygują historię);
  wiadomości zapisywane są raz, z datą pierwszego pobrania.
- Snapshoty, raporty, wysyłki i błędy są **tylko dopisywane** — każde uruchomienie
  dodaje nowe rekordy z niezmiennym UUID; nie ma operacji ich zmiany ani usuwania.
- `RULES_VERSION` (`analysis/rules.py`) i hash konfiguracji pozwalają ustalić, jakimi
  regułami powstał historyczny rekord. Wersję reguł podbija się ręcznie przy zmianie
  logiki liczenia metryk.
- `--mock` używa bazy w pamięci, więc dane testowe nie trafiają do prawdziwej bazy.
- Wszystkie czasy w bazie są w UTC (SQLite nie przechowuje strefy czasowej, więc kod
  zapisuje UTC i przy odczycie ją przywraca).

## Jak powstaje raport

1. Ceny dzienne (yfinance) → czyszczenie (duplikaty, NaN, wartości ≤ 0 → brak) →
   wykrywanie luk (więcej niż `MI_PRICE_MAX_GAP_BUSINESS_DAYS` brakujących dni roboczych) →
   status `ok` / `stale` / `insufficient_data` → zmiana tygodniowa i miesięczna.
2. Serie makro (FRED) → brak wartości (`.`) to `None` → status świeżości według
   `config/macro.yaml` → ostatnia i poprzednia wartość, zmiana, zmiana r/r.
3. Nagłówki RSS (tylko tytuł, link, źródło, daty, krótki lead — nigdy pełne artykuły):
   najnowsze wpisy z każdego kanału, deduplikacja po linku i znormalizowanym tytule
   (także między źródłami), wybór równo po kategoriach.
4. Każda dana ma zapisane pochodzenie: dostawca, endpoint, symbol/seria, czas pobrania,
   liczba prób (sekcja 10 raportu).
5. Dane policzone przez kod trafiają do modelu jako JSON; nagłówki w osobnym bloku
   oznaczonym jako niezaufane dane.
6. Odpowiedź ma narzucony schemat JSON (structured outputs w OpenRouter:
   `response_format: json_schema` ze schematem z Pydantic, `provider.require_parameters`
   kieruje zapytanie tylko do dostawców, którzy go obsługują) i jest walidowana przez kod:
   - każda teza ma co najmniej jedno istniejące źródło,
   - każda liczba i data w tekście występuje w danych wejściowych (liczby z nagłówka tylko
     wtedy, gdy teza cytuje ten nagłówek),
   - kandydaci do researchu mają świeże i kompletne dane.
7. Tabele, źródła i timestampy renderuje kod (Jinja2); model pisze tylko komentarz.

Dane makro: *This product uses the FRED® API but is not endorsed or certified by the
Federal Reserve Bank of St. Louis.* (nota wymagana przez warunki FRED API, dodawana
też do raportu).

## Rozwój

```bash
uv run pytest            # testy (bez sieci)
uv run ruff check .      # lint
uv run ruff format .     # formatowanie
uv run mypy src          # typy (strict)
UPDATE_GOLDEN=1 uv run pytest tests/integration   # odświeżenie golden files
```

## Znane ograniczenia

- yfinance to prototyp, nie licencjonowany feed; dane mogą być opóźnione lub niepełne.
- Brak danych fundamentalnych (Etap 4) i kalendarza wydarzeń. Makro to tylko 4 serie
  z USA; dane FRED mogą należeć do podmiotów trzecich (warunki FRED API).
- Brak scoringu i sygnałów (Etap 4) — sekcja „kandydaci do researchu” to tylko
  interpretacja prostych zmian cen przez model, nie rekomendacja.
- yfinance rzuca różne wyjątki, więc ponawiany jest każdy; nieznany symbol też zużywa
  wszystkie próby.
- Testowane tylko na SQLite. Kod jest przenośny, ale PostgreSQL wymaga sterownika
  (`psycopg`) i nie był uruchamiany.
- Niezmienność rekordów wymusza warstwa repozytorium, a nie triggery w bazie.
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
