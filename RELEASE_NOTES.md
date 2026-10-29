# Metadachs v0.1.3 – Sonarr + TheTVDB und optionale WebUI-Anmeldung

## Neu seit v0.1.2

- Einrichtung von Sonarr-Adresse/API-Key sowie TheTVDB-API-Key und optionalem
  Subscriber-PIN über die WebUI. Zugangsdaten werden nicht an den Browser zurückgegeben.
- Pro Serienmediathek wählbare Quelle: bisheriges TMDB oder TheTVDB mit exakter
  Sonarr-Dateizuordnung. Filme verwenden weiterhin TMDB.
- Sonarr liefert die Datei-/Episodenidentität, nicht die Texte. Deutsche Titel und
  Beschreibungen stammen direkt aus TVDB-Übersetzungen. Für ausschließlich deutsche
  Texte die Ersatzsprache leeren. Fehlende Titel und mehrdeutige Zuordnungen werden
  zur Prüfung angehalten; keine geratenen Episoden oder stillen englischen Sonarr-Texte.
- Vorhandene Episoden-NFOs sind für die Sonarr-Zuordnung nicht erforderlich.
  Lokale Nummerierung und Videodateien bleiben unverändert.
- Optional abschaltbare WebUI-Anmeldung mit Bestätigung, Status- und Log-Warnung.
  Standard bleibt Anmeldung aktiv. Ohne Anmeldung kann jeder mit Netzwerkzugriff
  Einstellungen und Zugangsdaten ersetzen: nur im vertrauenswürdigen LAN verwenden.
  Der Sonarr-Webhook bleibt unabhängig davon token-geschützt.
- Automatische Start-/Intervallscans optional abschaltbar; Webhooks, manuelle
  Bestandsabfrage und expliziter CLI-Scan bleiben verfügbar.
- Plex-Pfadindex mit 60-Sekunden-Cache und begrenzter TMDB-RAM-Cache.
- Aussagekräftige TMDB-404-Diagnosen ohne automatische Wiederholungen bei
  unpassender Identität/Nummerierung; Netzwerkfehler behalten ihre Wiederholungen.

## Aktualisieren und einrichten

1. Container auf `ghcr.io/captsonic/metadachs:0.1.3` oder `:latest` aktualisieren.
2. In der WebUI Sonarr-/TVDB-Zugangsdaten, Quelle und Sonarr-Pfadpräfix der
   Serienmediathek konfigurieren. Bestehende Konfigurationen bleiben bei TMDB.
3. Speichern und Container neu starten; Umgebungsvariablen haben weiterhin Vorrang.
4. Zunächst im Vorschaumodus an einem kleinen Bestand prüfen.

Release-Assets: `metadachs-v0.1.3.zip`, `config.example.toml`, Unraid-Template
`metadachs.xml` und `SHA256SUMS.txt`. Der Tag-Workflow veröffentlicht Images für
`linux/amd64` und `linux/arm64` unter `:0.1.3` und `:latest`.

## Validierung und Grenzen

- 64 automatisierte Tests unter Windows und Linux-Docker erfolgreich;
  JavaScript-Syntaxprüfung und Docker-Build erfolgreich.
- Eigener TVDB-Zugang erforderlich; Subscriber-PIN abhängig vom Zugangsmodell.
- Aktuell eine Sonarr-Instanz; Mehrfachepisoden-Dateien werden abgelehnt.
- TVDB-Besetzung und -Bilder sind noch nicht angebunden. Genres/Basisangaben
  sind nicht zwingend übersetzt; ein fehlender Beschreibungstext bleibt leer.
- Noch keine Prüfung mit persönlichen Sonarr-/TVDB-/Plex-Zugängen oder auf Unraid.
  Insbesondere die echte Wilsberg-Zuordnung ist noch nicht bestätigt.
- Keine Open-Source-Lizenz festgelegt. Medien werden nicht umbenannt oder verändert.

---

# Metadachs v0.1.2 – Sichtbare Konsolenaktivität

Erweiterte Statusmeldungen auf stdout, direkt im Unraid-Container-Log sichtbar.

## Funktionen

- Online-Metadaten von TMDB für Filme, Serien und Episoden.
- Mehrere konfigurierbare Mediatheken mit getrennten Sonarr-/Plex-Pfadzuordnungen.
- Periodische Ordnerprüfung und authentifizierte Sonarr-Webhooks.
- Persistente Auftragswarteschlange und getrennte Plex-Refresh-Wiederholungen.
- WebUI für Einstellungen, Bestandsübersicht, NFO-Feldanalyse und Auftragsstatus.
- Vorschaumodus als Standard; Schutz vorhandener NFOs und Sicherungen beim Ersetzen.
- Dockerfile, Compose-Beispiel und Unraid-Template.
- Fertige Images unter `ghcr.io/captsonic/metadachs:0.1.2` und `:latest`.
- Multi-Arch-Workflow für `linux/amd64` und `linux/arm64`; `:edge` für main.
- Startmeldung mit Betriebsmodus, WebUI-Port und Prüfintervall.
- Jeder Prüfzyklus mit Anzahl Videos, neuen/geänderten Aufträgen, wartenden Dateien und Fehlern.
- Zwischenstände alle 500 Videos; WebUI-Bestandsabgleich mit Zusammenfassung.
- Fortschritt für Metadaten, NFO-Schreiben/Vorschau, Plex-Refresh und Wiederholungsfristen.
- Ohne zusätzliche Konfiguration: `docker logs -f metadachs` oder Unraid „Logs“.

## Installation

1. `metadachs-v0.1.2.zip` oder das Unraid-Template herunterladen.
2. Image `ghcr.io/captsonic/metadachs:latest` verwenden; kein eigener Build nötig.
3. Die Anleitung in `README.md` befolgen: Appdata/Mounts vorbereiten,
   `config.example.toml` als `config.toml` ablegen und eigene Tokens hinterlegen.
4. Unraid-Template importieren oder Compose verwenden. Zunächst Vorschau aktiv lassen.
5. WebUI über den konfigurierten Host-Port öffnen. Benutzer: `metadachs`;
   Passwort: der konfigurierte Webhook-Token.

Image und Build-Status stehen beim Repository unter Packages bzw. Actions bereit.
Ein neues GHCR-Paket muss für anonymen Download zunächst öffentlich gestellt sein.

## Validierung und Einschränkungen

- 39 automatisierte Tests einschließlich Log-Ausgaben und Schutz vor Token-Ausgabe.
- Docker-Build sowie HTTP-Integrationstests erfolgreich.
- Noch kein echter Integrationstest mit den persönlichen TMDB-/Plex-Zugängen
  und noch kein Test auf einem Unraid-Zielsystem; zunächst kleinen Testbestand nutzen.
- Polling statt nativer Dateisystemereignisse; Bilder als URLs statt lokaler Downloads.
- Einstellungsänderungen benötigen einen Container-Neustart. Umgebungsvariablen
  haben Vorrang. Eine initial gültige Konfiguration ist erforderlich.
- Multi-Episoden-Dateien und mehrere Filmdateien im selben Filmordner werden
  nicht automatisch verarbeitet. Details und weitere Grenzen stehen in `README.md`.
- Keine Open-Source-Lizenz festgelegt; öffentliche Sichtbarkeit allein erteilt
  keine allgemeine Erlaubnis zur Weiterverwendung.

## Datenquelle

Dieses Produkt verwendet die TMDB-API, ist aber nicht von TMDB unterstützt oder
zertifiziert. Eigene API-Zugangsdaten und Beachtung der Nutzungsbedingungen erforderlich.