# Metadachs

Docker-Dienst für Unraid: Online-Metadaten von TMDB als Plex/Kodi-NFO speichern,
Sonarr-Webhooks empfangen, Medienordner regelmäßig prüfen und anschließend Plex
gezielt aktualisieren. Python >= 3.13, keine zusätzlichen Python-Abhängigkeiten.

## Sicherheitsstandard

- Vorschau ist standardmäßig aktiv: keine NFO-Änderungen, kein Plex-Refresh.
- Videos werden niemals geändert, verschoben oder gelöscht.
- Vorhandene fremde NFOs bleiben erhalten. `replace_existing = true` erlaubt
  Ersetzen mit zeitgestempelter `.bak`-Sicherung. Von Metadachs verwaltete NFOs
  dürfen aktualisiert werden; manuelle Änderungen daran heben diese Freigabe auf.
- Auch jede Änderung einer eigenen NFO erhält eine Sicherung neben der Datei.
  Diese Sicherungen werden nicht automatisch gelöscht.
- Nur ID-basierte Zuordnung, keine automatische unsichere Namenssuche.
- Die API ist für das interne Netzwerk gedacht, nicht für öffentliche Freigaben.
  HTTP Basic und Bearer übertragen Geheimnisse nicht verschlüsselt: extern ist
  ein HTTPS-Reverse-Proxy notwendig. Secrets sind in Docker-Umgebungen bzw.
  Unraid-Vorlagen für Administratoren sichtbar. Keine Tokens in URLs verwenden.

## Unraid einrichten

1. Projekt auf den Unraid-Server kopieren. Im Projektverzeichnis bauen:
   `docker build -t metadachs:local .`
2. Appdata-Ordner anlegen, `config.example.toml` als `config.toml` dort ablegen.
   Ordner und Medien müssen für die Container-UID/GID beschreibbar sein.
   Das Image läuft standardmäßig als `99:100`; keine privilegierten Rechte nötig.
3. `unraid/metadachs.xml` nach
   `/boot/config/plugins/dockerMan/templates-user/my-metadachs.xml` kopieren
   und in Unraid unter „Add Container“ auswählen. Das Image ist lokal gebaut,
   nicht in einer öffentlichen Registry veröffentlicht; Updates durch Neubau.
4. Hostpfade den Containerpfaden zuordnen. Alle Windows-Analysepfade entfallen.
5. In `config.toml` Bibliotheken und Pfade eintragen. Die Datei unterstützt
   `${VARIABLE}` für beliebige Umgebungsvariablen. Diese Variablen zusätzlich
   in Unraid anlegen, **oder** die Platzhalter durch deine konkreten Container-
   bzw. Sonarr-/Plex-Pfade ersetzen. Das Template setzt nicht alle Platzhalter.
6. Eigenen TMDB Read Access Token, Plex-Adresse/-Token und zufälligen
   Webhook-Token (mindestens 16 Zeichen) als Container-Variablen hinterlegen.
7. Zuerst Vorschau prüfen. Dann `METADACHS_DRY_RUN=false` setzen und Container
   neu erstellen/starten. Der Moduswechsel führt zu neuer Bestandsverarbeitung.

Alternativ: `.env.example` nach `.env` kopieren, Werte ausfüllen und
`docker compose up -d --build` verwenden. Compose benötigt auf dem Host eine
Compose-Installation; der Unraid-Template-Weg nicht. Das Compose-Beispiel hat
zwei Serienordner; weitere Mounts/Variablen nach Bedarf ergänzen oder entfernen.

### Pfade und mehrere Mediatheken

Jeder `[[libraries]]`-Eintrag beschreibt **einen nicht überlappenden Quellordner**.
Beliebig viele Einträge sind möglich. Mehrere Quellordner derselben Plex-
Mediathek erhalten unterschiedliche Namen, aber dieselbe `plex_section`.

| Feld | Bedeutung |
|---|---|
| `name` | Eindeutiger interner Name |
| `kind` | `tv` oder `movie` |
| `path` | Absoluter Pfad im Metadachs-Container |
| `sonarr_path` | Derselbe Quellordner aus Sicht von Sonarr |
| `plex_path` | Derselbe Quellordner aus Sicht von Plex |
| `plex_section` | Numerische Plex-Bibliotheks-ID; leer deaktiviert Plex für diesen Eintrag |
| `season_numbering` | Bei Serien ohne externe Episoden-ID TMDB-SxxEyy-Zuordnung ausdrücklich erlauben |

Beispiel: Sonarr meldet `/tv/Serie/Season 01/Folge.mkv`; Metadachs sieht
`/media/series/Serie/Season 01/Folge.mkv`; Plex sieht
`/data/serien/Serie/Season 01/Folge.mkv`. Entsprechend lauten die drei Präfixe
`/tv`, `/media/series`, `/data/serien`. Kein Pfad wird aus einem Titel geraten.

Mehrfachzuordnung desselben physischen Bestands zu mehreren Plex-Mediatheken
ist in v0.1 nicht unterstützt. Überlappende Pfade werden abgewiesen.

## Sonarr verbinden

Settings → Connect → Webhook:

- URL: `http://UNRAID-IP:8080/webhooks/sonarr`
- Method: POST
- Username: `metadachs`
- Password: derselbe Wert wie `METADACHS_WEBHOOK_TOKEN`
- Import/Upgrade (Webhook-Ereignis `Download`), Import Complete soweit verfügbar,
  und Rename aktivieren. Kein Grab-Ereignis erforderlich.
- Test senden: Testmeldungen werden ohne Medienzugriff bestätigt.

Alternativ `Authorization: Bearer <Token>` für eigene Aufrufer.
Das Webhook bestätigt erst nach persistenter Aufnahme der gefundenen Dateien.
Zur sicheren Mehrfachbenachrichtigung wird der Serienordner abgeglichen;
unveränderte Dateien erzeugen keine doppelten Aufträge. Pfade außerhalb der
konfigurierten Medienwurzeln werden zurückgewiesen. Sonarr muss die konfigurierte
Wurzel eindeutig melden; bei mehreren Sonarr-Instanzen identische Pfadpräfixe
vermeiden. Für Filme genügt das Polling; ein Radarr-Webhook ist noch nicht vorhanden.

## Überwachung und Aufträge

Polling erfasst neue/geänderte Videodateien alle `poll_seconds` (Standard 60).
Eine unveränderte Größe/mtime muss über `stable_seconds` beobachtet werden.
Das ist kein garantierter Kopierabschluss; bei langsamen Kopien höher einstellen.
Sonarr-Importmeldungen haben eine entsprechende Verzögerung. Fehlende Mounts
werden gemeldet; es werden keine Videos oder Metadaten wegen eines Scans gelöscht.
NFO-eigene Schreibvorgänge lösen keine Aufträge aus. Externe NFO-Änderungen allein
lösen ebenfalls keinen Auftrag aus. Es gibt keinen periodischen Online-Refresh
unveränderter Medien und keine native inotify-Überwachung in dieser Version.

Jobs und API-Cache liegen unter `/config/state/metadachs.sqlite3`, also lokal im
Appdata-Share. Ein Container/Prozess pro Zustandverzeichnis; keine parallelen
`scan`- oder `retry`-Kommandos während `serve` laufen lassen. Abgebrochene Aufträge
werden beim Start erneut aufgenommen. Temporäre Fehler: maximal zehn Versuche mit
Backoff. Dauerhafte Zuordnungsfehler benötigen Prüfung und `retry`.

Plex-Refresh läuft nach dem Schreiben in einer getrennten Jobphase. Neue Dateien
lösen zunächst einen gezielten Scan aus; erst wenn der Eintrag sichtbar ist, wird
er aktualisiert. Bei Serien wird auch die Serien-Metadatenebene aktualisiert.
Die Eintragssuche liest paginiert die Bibliothek; für sehr große Bestände kann dies
langsam sein. Bereits unverändert vorhandene fremde NFOs lösen keinen Refresh aus.

## Weboberfläche

Nach dem Neubau des Images ist die Oberfläche unter `http://UNRAID-IP:8080/`
erreichbar (bei abweichendem Host-Port entsprechend anpassen). Anmeldung im
Browser: Benutzer **metadachs**, Passwort = `METADACHS_WEBHOOK_TOKEN`.
Es werden keine Zugangsdaten im Browser-LocalStorage gespeichert. HTTP Basic
hat keinen zuverlässigen Logout-Knopf; zum Abmelden das private Browserfenster
schließen. Für nicht vertrauenswürdige Netze HTTPS und Zugriffsschutz vorschalten.

- **Bestand:** lokale Film-/Serienerkennung mit Suche, Mediatheksfilter und
  Seitenansicht. Serien lassen sich bis zu einzelnen Episoden aufklappen.
  NFO-Status: fehlend, lesbar oder fehlerhaft. Die Kategorien zeigen tatsächlich
  befüllte XML-Felder, darunter Beschreibungen, IDs, Bilder, Besetzung, Genres,
  Bewertungen, Veröffentlichung, Altersfreigaben und technische Angaben.
  Unter „Felder und Inhalte“ sind die gelesenen Werte sichtbar. Eine lesbare NFO
  ist noch keine Garantie für eine korrekte Online-Zuordnung oder Plex-Übernahme.
- **Bestand neu prüfen:** ausschließlich lesender, asynchroner Abgleich.
  Der Bestand wird zusätzlich nach den regelmäßigen Medienprüfungen aktualisiert;
  die Anzeige fragt alle 15 Sekunden den aktuellen Stand ab. Große Bibliotheken
  können den ersten Abgleich verzögern. Der Bestandsindex ist im Arbeitsspeicher
  und wird nach Neustarts neu erstellt. Die Prüfung verändert keine Medien/NFOs.
- **Aufträge:** Status, Verarbeitungsphase und Fehlermeldungen. Fehlgeschlagene
  Aufträge lassen sich erneut anstoßen; dabei gelten die aktiven Schreibregeln.
- **Einstellungen:** allgemeine Optionen, Zugangsdaten und beliebig viele
  nicht überlappende Medienwurzeln bearbeiten. Container-/Sonarr-/Plex-Pfade und
  Bibliotheks-ID werden je Mediathek hinterlegt. Docker-Mounts kann die UI nicht
  anlegen: zunächst in Unraid einbinden, anschließend den Containerpfad eintragen.

Speichern validiert die Konfiguration, legt eine `.bak`-Sicherung an und ersetzt
die TOML-Datei atomar. **Container danach manuell neu starten**, erst dann gelten
die neuen Einstellungen. Laufende Aufträge verwenden bis dahin die alte
Konfiguration. Auch nach mehreren Speichervorgängen zeigt das Formular die
gespeicherte, noch nicht aktivierte Fassung. Kommentare der ursprünglichen TOML
gehen beim Speichern verloren; `${VARIABLE}`-Platzhalter werden beibehalten.

Token-Werte werden nicht an den Browser zurückgegeben; leere Token-Felder behalten
gespeicherte Werte. Entfernen von Tokens ist bei Bedarf direkt in der TOML möglich.
Umgebungsvariablen haben weiterhin Vorrang, insbesondere `METADACHS_DRY_RUN` und
die Token-Variablen aus der Unraid-/Compose-Konfiguration. Die UI weist darauf hin.
Konfigurationsdateien und Sicherungen können Geheimnisse enthalten: Appdata schützen.
Eine initial gültige `config.toml` und ein Webhook-Token sind für den ersten Start
weiterhin erforderlich; es gibt noch keinen Ersteinrichtungsassistenten.

## Befehle und Diagnose

Im Container (Standardkonfiguration `/config/config.toml`):

```sh
python -m metadachs check
python -m metadachs jobs
```

Bei gestopptem Hauptdienst, z.B. mit `docker compose run --rm --no-deps metadachs`:

```sh
python -m metadachs scan
python -m metadachs retry
```

`scan` führt einen Bestandsdurchlauf aus und verarbeitet aktuell fällige Jobs.
Zu neue Dateien werden ausgelassen; noch nicht fällige Wiederholungen bleiben
gespeichert. Für die wiederholte Verarbeitung `serve` starten.
`retry` setzt fehlgeschlagene Jobs zurück; danach `serve`/`scan` starten.

- `GET /health`: Prozessstatus, letzter Scan, Anzahl Scanfehler; kein Token nötig.
- `GET /jobs`: Auftragsstatus mit Bearer- oder Basic-Authentifizierung.
- Logs: stdout / Unraid Container-Log. Keine Metadaten- oder Refresh-Garantie
  allein durch einen grünen Healthcheck: zusätzlich Aufträge/Scanfehler prüfen.
- `python -m metadachs --config /anderer/pfad.toml check` für alternative Datei.

## Grenzen und Voraussetzungen

- Plex Media Server >= 1.43.1 und NFO-Agenten „Plex NFO Movie“ bzw. „Plex NFO
  Series“ aktivieren. Bestehende gesperrte Felder können ein Refresh-Ergebnis
  beeinflussen. Vor Umstellung an einer kleinen Testmediathek prüfen.
- Ein Film pro Ordner, eine Datei pro Episode. Mehrteilige Filme,
  Multi-Episoden-Dateien, Anime-Absolutnummerierung und mehrere Filmversionen im
  selben Ordner werden nicht automatisch verarbeitet.
- Serien-Episoden werden vorzugsweise über TVDB-/IMDb-Episoden-IDs aus vorhandenen
  NFOs zugeordnet. Ohne solche IDs ist `season_numbering = true` erforderlich.
  Das nur aktivieren, wenn die Dateien TMDBs Episodenreihenfolge entsprechen.
  Bestehende reine TMDB-Episoden-IDs werden beim Abruf auf Übereinstimmung geprüft.
- Fehlerhafte NFOs werden gemeldet, nicht automatisch repariert. Ein bekannter
  angehängter Scraper-Link wird beim Lesen toleriert; das Original bleibt erhalten.
- Poster/Fanart/Personenbilder werden als Online-URLs referenziert, nicht lokal
  heruntergeladen. Noch keine FSK-Abfrage oder IMDb-Bewertungsquelle.
- Ohne eigene API-Zugangsdaten und Zielserver konnte kein echter TMDB-/Plex-
  Integrationstest erfolgen. Automatisierte Tests verwenden simulierte Antworten.

## Entwicklung und Tests

```sh
python -m unittest discover -s tests -v
python -m compileall -q metadachs tests
```

## Datenquelle und Dokumentation

Dieses Produkt verwendet die TMDB-API, ist aber nicht von TMDB unterstützt oder
zertifiziert. Eigene TMDB-Zugangsdaten und Einhaltung der TMDB-Nutzungsbedingungen
sind erforderlich. Vor öffentlicher Verteilung die dann geltenden Branding- und
Attributionsvorgaben einschließlich TMDB-Logo ergänzen.

- Plex: https://support.plex.tv/articles/using-nfo-metadata-files-with-plex/
- TMDB: https://developer.themoviedb.org/reference/find-by-id
- TMDB-Nutzung: https://developer.themoviedb.org/docs/faq
- Sonarr: https://github.com/Sonarr/Sonarr/tree/develop/src/NzbDrone.Core/Notifications/Webhook
- Unraid: https://docs.unraid.net/unraid-os/using-unraid-to/run-docker-containers/managing-and-customizing-containers/