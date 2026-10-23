# Pomysły na przyszłość

Pomysły poza aktualnym planem z `docs/ROADMAP.md`. Wpis tutaj nie oznacza decyzji o realizacji.
Przed wejściem do roadmapy każdy pomysł wymaga planu i akceptacji operatora.

## 1. Portfele obserwowane (watchlisty) i podsumowania per portfel

Zgłoszone: 2026-10-23. Status: pomysł, nie zaplanowany.

### Cel

Użytkownik tworzy portfele lub listy instrumentów do obserwacji. Raport tygodniowy dostaje
dodatkową sekcję z podsumowaniem dla każdego portfela. W dalszych krokach system może
proponować instrumenty pokrewne do dalszego researchu, a ewentualnie także sygnały
kup/sprzedaj.

### Proponowane etapy (od najprostszego)

1. **Watchlisty w pliku**, bez strony WWW: np. `config/watchlists.yaml` (nazwa, lista
   `instrument_id`, opcjonalnie wagi). Raport ma sekcję „Twoje portfele”, którą kod liczy
   z tych samych danych: zmiany instrumentów, zmiana ważona portfela, status danych.
   LLM tylko komentuje, a walidacja liczb i źródeł obowiązuje tak jak dziś.
   - Zależności: dowolne symbole spoza `instruments.yaml` (lista instrumentów musi się
     stać dynamiczna), sensowne przeliczanie walut przy wagach (SPEC §4: bez łączenia
     walut bez jawnego przeliczenia).
2. **Prosta strona dla jednego użytkownika (operatora)** do edycji watchlist i przeglądania
   historii raportów.
   - Zależności: baza danych z Etapu 3 (watchlisty, raporty, historia) oraz lokalne
     uruchamianie lub hosting z uwierzytelnieniem.
   - Proponowany stos do oceny: FastAPI + szablony Jinja2 (już używamy Jinja2) albo
     Streamlit jako najszybszy prototyp.
3. **Wielu użytkowników**: konta, logowanie, osobne portfele i osobne maile.
   - Zależności: uwierzytelnianie, izolacja danych użytkowników, RODO (dane osobowe,
     adresy e-mail, zgody), koszty LLM liczone per użytkownik (limit kosztu per osoba
     i per uruchomienie), kolejka generowania raportów.
4. **Sugestie „poszerz portfel o pokrewne”** jako kandydaci do researchu, a nie zalecenia
   zakupu. Pokrewieństwo wyznacza kod (sektor, ETF-y tematyczne, korelacja), a LLM opisuje
   argumenty za i przeciw.
   - Zależności: dane fundamentalne i sektorowe (Etap 4).
5. **Sygnały kup/sprzedaj dla portfela**: tylko na bazie zweryfikowanego scoringu
   (Etap 4) i backtestu poza próbą (Etap 6). LLM nie generuje sygnałów, tylko je opisuje.

### Ryzyka i zastrzeżenia

- **Regulacje (najważniejsze).** Rekomendacja kup/sprzedaj dopasowana do portfela
  konkretnej osoby może zostać uznana za doradztwo inwestycyjne, czyli usługę
  regulowaną. Sam disclaimer może tego nie zmieniać. Przed udostępnieniem sugestii
  innym osobom niż operator potrzebna jest konsultacja prawna. Dla jednego użytkownika
  na własny użytek ryzyko jest znacznie mniejsze.
- SPEC §1 i §6: system ma nie być spersonalizowaną poradą finansową, a sygnał nigdy nie
  oznacza transakcji. Etapy 4–5 wymagają zmiany specyfikacji za zgodą operatora.
- Jakość: obecny walidator wyłapuje wymyślone liczby i brak źródeł, ale nie spekulacyjne
  tezy. Sugestie dla portfeli zwiększają wagę tego problemu.
- Bezpieczeństwo: strona WWW to nowa powierzchnia ataku (logowanie, sekrety, CSRF/XSS).

### Proponowana kolejność

Etap 1 (watchlisty w YAML) można zrobić po Etapie 3 lub w trakcie Etapu 4, kiedy lista
instrumentów i baza będą gotowe. Strona i wielu użytkowników to osobny etap po Etapie 5,
a sygnały dopiero po Etapie 6.
