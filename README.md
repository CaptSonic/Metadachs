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

1. Fertiges Image verwenden: `ghcr.io/captsonic/metadachs:latest`.
   Ein eigener Build ist nicht notwendig. Für eine feste Version statt `latest`
   den Tag `0.1.2` verwenden.
2. Appdata-Ordner anlegen, `config.example.toml` als `config.toml` dort ablegen.
   Ordner und Medien müssen für die Container-UID/GID beschreibbar sein.
   Das Image läuft standardmäßig als `99:100`; keine privilegierten Rechte nötig.
3. `unraid/metadachs.xml` nach
   `/boot/config/plugins/dockerMan/templates-user/my-metadachs.xml` kopieren
   und in Unraid unter „Add Container“ auswählen. Das Template lädt das Image
   aus GitHub Container Registry. Updates über Unraids Container-Aktualisierung.
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
`docker compose pull` und `docker compose up -d` verwenden. Compose benötigt auf dem Host eine
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

Die regelmäßige Suche lässt sich unter WebUI → Einstellungen → Allgemein →
**Automatische Ordnerprüfung** ausschalten. Speichern und Container neu starten.
Alternativ unter `[settings]` in `config.toml`: `polling_enabled = false`,
oder als Container-Variable `METADACHS_POLLING_ENABLED=false` setzen.
Ohne Angabe bleibt die Prüfung wie bisher aktiviert. Eine gesetzte
Umgebungsvariable hat Vorrang vor der WebUI/TOML-Einstellung.

Bei deaktivierter Prüfung erfolgen **keine automatische Startprüfung und keine
regelmäßigen Medien-/WebUI-Bestandsabgleiche**. Sonarr-Webhooks und bereits
gespeicherte Aufträge einschließlich Wiederholungsversuchen werden weiterhin
verarbeitet. Neue Filme ohne Webhook werden nicht automatisch entdeckt.
„Bestand neu prüfen“ in der WebUI bleibt verfügbar, liest aber nur die Übersicht
ein und erzeugt keine NFO-Aufträge. Der explizite CLI-Befehl `scan` funktioniert
weiterhin unabhängig vom Automatikschalter. Die WebUI-Aktualisierung des
angezeigten Status alle 15 Sekunden durchsucht keine Medienordner.

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
Die Eintragssuche liest paginiert die Bibliothek und hält den Pfadindex für
60 Sekunden im Arbeitsspeicher. Aufeinanderfolgende Aufträge verwenden diesen
Index wieder, statt die gesamte Mediathek für jedes Video erneut abzurufen.
Neue Plex-Einträge können dadurch bis zum Ablauf des Index oder nächsten
Wiederholungsversuch warten. Die gezielten Refreshs selbst erfolgen weiterhin
für jeden Auftrag. Bereits unverändert vorhandene fremde NFOs lösen keinen Refresh aus.
Häufig verwendete TMDB-Antworten werden zusätzlich zum SQLite-Cache für maximal
60 Sekunden im Arbeitsspeicher gehalten (höchstens 256 Antworten).

## Mehrfachepisoden

Dateien wie `Serie - S08E01-E02.mkv`, `S08E01-E03` (Bereich inklusive
Endfolge) und `S08E01E03` (explizite Liste) werden unterstützt. Die Episoden
müssen aufsteigend innerhalb derselben Staffel liegen; pro Datei maximal
100 Episoden. Einzelne Specials wie `S00E25` funktionieren weiterhin.

Bei Quelle TheTVDB ermittelt Sonarr alle zur konkreten Datei gehörenden Episoden.
Ihre eindeutigen TVDB-IDs werden in Sonarr-Episodenreihenfolge verarbeitet; die
Anzahl muss zur Dateibenennung passen. Sonarr-Nummern dürfen von der lokalen
Nummerierung abweichen. Titel und Handlung werden für jede Episode separat aus
den konfigurierten TVDB-Sprachen abgerufen. Fehlende IDs, widersprüchliche NFOs
oder fehlende erforderliche Übersetzungen verhindern eine unvollständige Ausgabe.

Bei TMDB gelten dieselben Sicherheitsregeln wie für Einzelepisoden: externe IDs
pro Episode aus vorhandener NFO oder bewusst aktiviertes `season_numbering`.
Es wird keine Übereinstimmung zwischen Sonarr-/TVDB- und TMDB-Nummerierung geraten.

Die gleichnamige `.nfo` enthält mehrere aufeinanderfolgende
`<episodedetails>`-Blöcke mit jeweils eigener Nummer, ID, Titel und Handlung.
Das entspricht dem Plex-NFO-Mehrfachepisodenformat; keine zusammengefasste
Beschreibung und kein Umbenennen oder Aufteilen des Videos. Die WebUI zeigt die
Felder aller enthaltenen Episoden. Vorhandene fremde NFOs bleiben gemäß den
bisherigen Schreib-/Backup-Regeln geschützt.

Nach einem Update können zuvor wegen Mehrfachepisoden fehlgeschlagene Aufträge
über die vorhandene WebUI-Aktion zum Wiederholen fehlgeschlagener Aufträge erneut
angestoßen werden. Ein automatischer Scan allein setzt fehlgeschlagene Aufträge
mit unveränderter Signatur nicht zurück.

Formatreferenz: https://support.plex.tv/articles/using-nfo-metadata-files-with-plex/

## Weboberfläche

### Deutsche Serienmetadaten über Sonarr + TheTVDB

In Einstellungen → Allgemein die Sonarr-Serveradresse (inklusive eventuellem
URL-Präfix) eintragen. Unter Zugangsdaten den Sonarr API-Key, einen eigenen
TheTVDB-v4-API-Key und gegebenenfalls Subscriber-PIN hinterlegen. Für die betreffende
Serienmediathek die Quelle **TheTVDB + Sonarr-Dateizuordnung** auswählen und den
Sonarr-Pfadpräfix passend konfigurieren. Speichern und Container neu starten.
Filme verwenden weiterhin TMDB; TMDB-Serien bleiben unverändert unterstützt.
Aktuell wird eine Sonarr-Instanz unterstützt.

Sonarr wird anhand des tatsächlichen Dateipfads abgefragt, nicht anhand gleicher
Staffelnummern in verschiedenen Datenbanken. Eine bestehende Episoden-NFO ist
nicht nötig. Die TVDB-Episoden-ID wird gegen die Serie geprüft. Titel und Beschreibung
kommen ausschließlich aus TVDB-Übersetzungen, nicht aus englischen Sonarr-Texten.
`de-DE` entspricht TVDB `deu`. Die konfigurierte Ersatzsprache greift nur bei
fehlenden Übersetzungsfeldern; für ausschließlich deutsche Texte Ersatzsprache
leeren. Fehlt dann ein deutscher Titel, wird der Auftrag zur Prüfung angehalten.
Genres und technische Basisangaben stammen aus dem TVDB-Basisdatensatz und sind
nicht zwingend übersetzt. In dieser ersten TVDB-Anbindung werden noch keine
Besetzungs-/Bilddaten aus TVDB übernommen. Der TVDB-Popularitätsscore wird nicht
als Bewertung ausgegeben. Lokale Staffel-/Folgenummern und Videodateien bleiben
unverändert. Vorhandene fremde NFOs bleiben gemäß Schreibregeln geschützt.

Eigener TVDB-API-Zugang ist erforderlich, je nach Zugangsmodell mit Subscriber-PIN.
Zugangsbedingungen: https://github.com/thetvdb/v4-api
Sonarr-Datei-/Episodenlisten werden 60 Sekunden, TVDB-Antworten gemäß
`cache_seconds` zwischengespeichert. Änderungen der Quellenzuordnung stoßen beim
nächsten automatischen/CLI-Scan neue Aufträge an. Bei abgeschaltetem Polling einen
expliziten Scan ausführen oder fehlgeschlagene Aufträge nach der Korrektur wiederholen.

### Anmeldung optional abschalten (nur vertrauenswürdiges LAN)

Unter Einstellungen → Allgemein **WebUI-Anmeldung erforderlich** deaktivieren,
Warnung bestätigen, speichern und Container neu starten. Alternativ
`ui_auth_enabled = false` unter `[settings]` oder
`METADACHS_UI_AUTH_ENABLED=false` setzen. Standard ist `true`.

**Ohne Anmeldung kann jeder mit Netzwerkzugriff den Bestand lesen, Einstellungen
ändern und gespeicherte Zugangsdaten ersetzen.** Keine Portweiterleitung oder
öffentliche Reverse-Proxy-Freigabe verwenden. Das LAN ist nicht automatisch
vertrauenswürdig, insbesondere bei Gast-/IoT-Geräten. Tokens werden weiterhin
nicht an den Browser zurückgegeben; JSON-/Origin-Schutz für Änderungen bleibt aktiv.
Der Sonarr-Webhook benötigt unabhängig vom UI-Schalter weiterhin seinen Token;
auch bei deaktivierter UI-Anmeldung muss ein mindestens 16 Zeichen langer
Webhook-Token konfiguriert sein. Die Anmeldung lässt sich über dieselbe Option
wieder aktivieren. Ein deutlicher Warnhinweis erscheint in WebUI und Konsolen-Log.

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

### Konsolen-Log

Statusmeldungen werden automatisch mit Zeitstempel und Schweregrad auf stdout
ausgegeben; es ist keine zusätzliche Aktivierung nötig. In Unraid beim Container
„Logs“ öffnen oder `docker logs -f metadachs` ausführen. Mit Compose:
`docker compose logs -f metadachs`.

Das Log zeigt Start und Betriebsmodus, Beginn/Ende jeder Ordnerprüfung,
Videodateizahlen je Mediathek, neue/geänderte Aufträge, wartende Dateien und Fehler.
Auch ohne neue Medien wird jeder Prüfzyklus bestätigt. Bei großen Beständen
erscheint zusätzlich alle 500 Videos ein Zwischenstand. Aufträge melden den
Metadatenabruf, NFO-Ergebnisse, Plex-Refresh und Wiederholungen mit Wartezeit.
Der lesende WebUI-Bestandsabgleich sowie gespeicherte Einstellungen werden ebenfalls
bestätigt. Wiederholte Healthcheck-/Browser-Abfragen werden nicht einzeln geloggt.
Tokens und vollständige Webhook-Payloads werden nicht ausgegeben; Medienpfade
und Dateinamen sind jedoch sichtbar. Logs vor öffentlichem Teilen prüfen.

TMDB-404-Fehler enthalten den API-Pfad ohne Zugangsdaten. Beispielsweise bedeutet
`/tv/123/season/2/episode/9`, dass der konkrete Episodenabruf fehlgeschlagen ist;
`/tv/123` betrifft die Serien-ID. Solche Aufträge werden ohne automatische
Wiederholung als fehlgeschlagen markiert. Erst Zuordnung/NFO-IDs bzw. die bewusst
aktivierte Staffelnummerierung prüfen, dann über die WebUI erneut anstoßen.
TVDB- und TMDB-Staffelreihenfolgen dürfen nicht ungeprüft gleichgesetzt werden.
Es erfolgt keine automatische Titel-Zuordnung oder Umnummerierung bei 404.
Netzwerkfehler und TMDB-Serverfehler behalten ihre Wiederholungsversuche.

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
- Ein Film pro Ordner. Mehrteilige Filme,
  Anime-Absolutnummerierung und mehrere Filmversionen im
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

### Container-Veröffentlichung

GitHub Actions testet den Code und veröffentlicht Multi-Arch-Images für
`linux/amd64` (Unraid x86-64) und `linux/arm64` in GHCR:

- `ghcr.io/captsonic/metadachs:latest`: zuletzt veröffentlichter Versions-Tag.
- `ghcr.io/captsonic/metadachs:0.1.2`: feste Release-Version.
- `ghcr.io/captsonic/metadachs:edge`: Entwicklungsstand von `main`.

Ein Push eines Versions-Tags wie `v0.1.1` veröffentlicht Version und `latest`.
Der Workflow verwendet den Repository-Token mit `packages: write`; kein eigener
Registry-Token in den Repository-Dateien. Ein neu angelegtes GHCR-Paket kann
zunächst privat sein: in den Package Settings die Sichtbarkeit auf Public setzen,
damit Unraid es ohne Registry-Anmeldung herunterladen kann.

Optionaler lokaler Entwickler-Build: `docker build -t metadachs:local .`.

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