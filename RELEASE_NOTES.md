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