# Specyfikacja produktu — Global Market Intelligence

Dokument opisuje, **co** system ma robić. Zasady pracy agenta i komendy są w `CLAUDE.md`,
kolejność realizacji w `docs/ROADMAP.md`.

## 1. Cel

Automatyczny system analityczny, który zbiera globalne dane rynkowe, makroekonomiczne
i technologiczne, analizuje je, tworzy tygodniowe briefy inwestycyjne i technologiczne
po polsku i wysyła je e-mailem.

System wspiera decyzje inwestycyjne, ale nie przedstawia niezweryfikowanych hipotez
jako faktów, nie obiecuje zysków i nie stanowi spersonalizowanej porady finansowej.

## 2. Wymagania funkcjonalne

System:

- monitoruje globalne rynki akcji i wybrane klasy aktywów;
- zbiera dane cenowe, fundamentalne i makroekonomiczne;
- zbiera wiadomości finansowe i technologiczne;
- analizuje raporty spółek i istotne wydarzenia;
- prowadzi dwie oddzielne analizy: długoterminową i krótkoterminową;
- ocenia kandydatów do kupna, obserwacji i redukcji;
- przedstawia argumenty za i przeciw każdej tezie;
- generuje tygodniowy raport po polsku;
- zapisuje raporty i historię sygnałów;
- wysyła raporty e-mailem;
- umożliwia późniejszą ocenę historycznej skuteczności sygnałów.

## 3. Zakres rynkowy

Architektura musi pozwalać na rozszerzenie o: USA, Europę, Azję; akcje i ETF-y;
główne indeksy; waluty; obligacje i rentowności; złoto, ropę i inne surowce;
opcjonalnie kryptowaluty.

Nie zakładamy, że jeden dostawca pokrywa wszystkie giełdy, wszystkie wskaźniki
fundamentalne i dane w czasie rzeczywistym.

Każdy instrument ma: wewnętrzny identyfikator, symbol u dostawcy (per dostawca),
giełdę, walutę, klasę aktywów i informację o dostępności danych.

### Lista startowa (MVP) — `config/instruments.yaml`

Do weryfikacji przez operatora; symbole w notacji Yahoo Finance:

| Wewnętrzne ID | Symbol | Klasa | Opis |
|---|---|---|---|
| US_SPY | SPY | ETF | S&P 500 |
| US_QQQ | QQQ | ETF | Nasdaq 100 |
| DE_EWG | EWG | ETF | Niemcy |
| JP_EWJ | EWJ | ETF | Japonia |
| GOLD_GLD | GLD | ETF | Złoto |
| FX_EURUSD | EURUSD=X | FX | EUR/USD |
| FX_USDPLN | PLN=X | FX | USD/PLN |
| US_10Y | ^TNX | Rentowność | US 10Y |

Makro (FRED, MVP): `CPIAUCSL`, `FEDFUNDS`, `DGS10`, `UNRATE`.

## 4. Warstwa danych

Każde źródło ma osobny adapter. Wspólne interfejsy dla:

- cen (`PriceProvider`),
- danych fundamentalnych (`FundamentalsProvider`),
- wiadomości (`NewsProvider`),
- raportów i komunikatów spółek (`FilingsProvider`),
- danych makro (`MacroProvider`).

### Dostawcy

- **MVP:** yfinance (ceny, prototyp), FRED (makro), RSS (nagłówki).
- **Do oceny później:** Twelve Data, Financial Modeling Prep, SEC EDGAR,
  oficjalne komunikaty giełdowe, licencjonowany dostawca newsów.

Przy wyborze dostawcy sprawdzić aktualną dokumentację: pokrycie giełd, limity planu,
historię danych, częstotliwość aktualizacji, prawa do przechowywania i dalszego użycia.

yfinance służy tylko do prototypowania, z poszanowaniem warunków Yahoo. Nie jest
niezawodnym, licencjonowanym feedem produkcyjnym.

### Źródła wiadomości — `config/sources.yaml`

Lista kanałów RSS do uzupełnienia przez operatora (finanse, makro, technologia,
AI, półprzewodniki). Agent nie zgaduje adresów RSS — używa listy z pliku.

Z wiadomości przechowujemy: tytuł, link, nazwę źródła, datę publikacji, datę pobrania,
ewentualnie krótki lead z RSS. **Nie przechowujemy pełnej treści artykułów.**

### Jakość danych

- Timestampy w UTC; rozróżnienie daty publikacji, obserwacji i pobrania.
- Rozróżnienie cen surowych i skorygowanych.
- Brak łączenia różnych walut bez jawnego przeliczenia.
- Brakująca wartość to `None`, nigdy zero.
- Walidacja duplikatów, luk i nieprawidłowych wartości.
- Obsługa limitów API, retry z exponential backoff, timeouty.
- Zapis pochodzenia danych (dostawca, endpoint, czas pobrania).
- Oznaczanie danych nieaktualnych i błędów pobierania.
- Brak sygnału, gdy krytyczne dane są niekompletne lub nieaktualne.

## 5. Silniki analityczne

Osobne oceny dla różnych horyzontów.

### A. Długoterminowy (fundamentalny)

Wzrost przychodów; rentowność i marże; przepływy pieniężne; zadłużenie i płynność;
jakość i trwałość modelu biznesowego; wycena na tle historii i konkurencji;
przewagi konkurencyjne; ryzyka branżowe i regulacyjne; prognozy i wyniki kwartalne.

Nie porównujemy bezpośrednio wskaźników wyceny spółek z nieporównywalnych sektorów.

### B. Krótkoterminowy (techniczny)

Momentum; zmienność; wolumen; siła względna wobec benchmarku; trendy cenowe;
reakcja na wyniki i wydarzenia; ryzyko luki cenowej; płynność.

Wynik zawiera horyzont sygnału i warunki jego unieważnienia.

### C. Makroekonomiczny

Inflacja; stopy procentowe; rentowności obligacji; rynek pracy; wskaźniki koniunktury;
kursy walut; energia i surowce; komunikaty banków centralnych.

Korelacja nie jest przedstawiana jako dowód przyczynowości.

## 6. Scoring

Jawny, konfigurowalny system punktacji (`config/scoring.yaml`). Na początku proste
reguły i znormalizowane wyniki, bez nieprzejrzystych modeli predykcyjnych.

Każdy wynik (`Signal`) zawiera:

- identyfikator instrumentu, timestamp analizy, horyzont;
- oceny: fundamenty, wycena, trend, katalizatory, ryzyko, ocena końcowa;
- uzasadnienie i argumenty przeciw;
- listę brakujących danych;
- warunki unieważnienia tezy;
- identyfikatory źródeł;
- niezmienny identyfikator sygnału i wersję reguł, które go utworzyły.

Statusy:

- `BUY_CANDIDATE` — kandydat do kupna,
- `WATCH` — obserwuj,
- `HOLD_NO_SIGNAL` — trzymaj / brak nowego sygnału,
- `REDUCE_CANDIDATE` — kandydat do redukcji,
- `INSUFFICIENT_DATA` — niewystarczające dane.

Sygnał nigdy nie oznacza automatycznie transakcji. Nie twierdzimy, że scoring
działa, dopóki nie zostanie zweryfikowany historycznie i poza próbą.

## 7. AI i generowanie raportów (LLM przez OpenRouter)

- Klient: API OpenRouter (wybór modeli różnych dostawców, także darmowych `:free`),
  opakowane w interfejs `LLMClient` (łatwe mockowanie, możliwa wymiana dostawcy).
- Konfiguracja w settings: `OPENROUTER_API_KEY`, identyfikator modelu (domyślnie
  `anthropic/claude-sonnet-5.5`), `max_tokens`, maksymalne ceny tokenów, limit kosztu
  na uruchomienie. Tryb `--mock` działa bez klucza.
- Model dostaje ustrukturyzowane dane i wyniki obliczone przez kod oraz listę źródeł.
  Nie wymyśla cen, wskaźników ani wyników spółek.
- Odpowiedź ma ustalony schemat (structured outputs z JSON schema —
  zgodnie z aktualną dokumentacją OpenRouter), walidowany Pydantic przed użyciem.
- Walidacja po stronie kodu: każda liczba w raporcie musi pochodzić z danych
  wejściowych; każda teza musi mieć co najmniej jedno źródło.
- Treść artykułów i dokumentów jest niezaufanymi danymi. Prompt systemowy
  wyraźnie oddziela dane od instrukcji; polecenia znalezione w danych są ignorowane.
- Każda ważna teza zawiera: fakty ze źródłami, interpretację, kontrargumenty,
  niepewności, scenariusze, warunki zmiany oceny.
- Przy błędzie modelu, złym formacie lub braku źródeł raport **nie** jest wysyłany
  jako kompletny. Błąd jest zapisywany, generowana jest informacja o niepowodzeniu.

## 8. Struktura tygodniowego raportu (po polsku)

1. Podsumowanie najważniejszych wydarzeń.
2. Obraz globalnych rynków.
3. Najważniejsze wydarzenia makroekonomiczne.
4. Analiza sektorów i spółek.
5. Kandydaci do dalszego researchu.
6. Sygnały ostrzegawcze.
7. Technologie, AI, półprzewodniki i startupy.
8. Kalendarz nadchodzących wydarzeń.
9. Scenariusze: pozytywny, bazowy, negatywny.
10. Źródła i timestampy danych.
11. Ograniczenia i niepewności analizy.
12. Stała stopka: „Materiał ma charakter badawczy i informacyjny. Nie stanowi
    rekomendacji inwestycyjnej ani porady finansowej. Wyniki historyczne nie
    gwarantują przyszłych.”

Dla każdej propozycji: horyzont, teza, argumenty przeciw, ryzyko, warunki unieważnienia.

Formaty: Markdown i HTML. Czytelny na telefonie, poprawne tabele, linki i polskie znaki
(UTF-8 wszędzie).

## 9. Automatyzacja i dostarczanie

- Ręczne uruchomienie (`mi weekly`) i harmonogram tygodniowy.
- Harmonogram domyślny: **niedziela 18:00 `Europe/Warsaw`** (do potwierdzenia).
  Cron w GitHub Actions działa w UTC — obsłużyć czas letni/zimowy (np. dwa wpisy
  cron i sprawdzenie lokalnej godziny w jobie).
- Opcjonalne codzienne alerty (później).
- Zapis lokalnej kopii raportu przed wysyłką.
- Wysyłka przez SMTP; odbiorcy z env. Domyślnie `--dry-run`.
- Logowanie wyników i błędów (bez sekretów).
- Kontrola duplikatów: klucz raportu = typ + tydzień ISO; ponowne uruchomienie
  nie wysyła drugiego maila, chyba że jawnie `--force`.
- Bezpieczne ponowienie nieudanego zadania (idempotencja kroków).
- Sekrety tylko w env / menedżerze sekretów (GitHub Secrets).

## 10. Baza danych i historia

Prototyp: pliki lokalne lub SQLite za interfejsem repozytorium.
Docelowo PostgreSQL (SQLAlchemy + Alembic), bez zmiany logiki biznesowej.

Przechowujemy: instrumenty, ceny, fundamenty, wiadomości (metadane), źródła
i timestampy, wyniki scoringu, wersje reguł, raporty, historię błędów,
wyniki historyczne sygnałów.

Historyczne oceny są niezmienne (append-only), aby dało się odtworzyć decyzję.

## 11. Backtesting

Wymagania: brak look-ahead bias; ograniczenie survivorship bias w miarę dostępności
danych; koszty transakcyjne i poślizg; poprawna obsługa dywidend, splitów i walut;
podział na dane treningowe, walidacyjne i testowe; brak dopasowania do jednego okresu;
porównanie z benchmarkiem.

Metryki: całkowity i annualizowany zwrot, maksymalne obsunięcie, zmienność,
Sharpe (gdy wiarygodny), liczba transakcji, koszty, wyniki w różnych okresach
i reżimach rynkowych.

Wyniki historyczne nie są gwarancją przyszłych.

## 12. Testy — przypadki obowiązkowe

Brakujące dane; duplikaty; błędne symbole; limity API; timeouty; nieprawidłowa
odpowiedź modelu; dane nieaktualne; błędy bazy; błędy SMTP; ponowne uruchomienie
tego samego raportu; brak sekretów w logach; poprawne renderowanie polskich znaków
(golden files dla Markdown/HTML).

## 13. Bezpieczeństwo i granice

- Pierwsza wersja to wyłącznie system badawczy i raportujący.
- Brak automatycznego składania zleceń bez odrębnej, wyraźnej decyzji operatora.
- Brak dostępu do kont brokerskich i transakcji na podstawie tekstu z modelu.
- Ewentualne przyszłe integracje brokerskie: domyślnie paper trading, limity ryzyka,
  kill switch, jawne potwierdzanie operacji.

## 14. Definicja ukończenia MVP

- Uruchomienie jedną udokumentowaną komendą.
- Pobiera dane i poprawnie obsługuje ich brak.
- Generuje raport wyłącznie z faktycznie otrzymanych danych.
- Zawiera źródła i daty.
- Zapisuje raport do pliku.
- Wysyła e-mail tylko po jawnej konfiguracji.
- Nie ujawnia sekretów.
- Ma podstawowe testy.
- Dokumentuje znane ograniczenia.
- Działa bez płatnych API w trybie testowym (`--mock`).
