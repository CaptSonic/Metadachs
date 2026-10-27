# Metadachs v0.1.0

Erste öffentliche Version des NFO-Dienstes für Docker auf Unraid.

## Funktionen

- Online-Metadaten von TMDB für Filme, Serien und Episoden.
- Mehrere konfigurierbare Mediatheken mit getrennten Sonarr-/Plex-Pfadzuordnungen.
- Periodische Ordnerprüfung und authentifizierte Sonarr-Webhooks.
- Persistente Auftragswarteschlange und getrennte Plex-Refresh-Wiederholungen.
- WebUI für Einstellungen, Bestandsübersicht, NFO-Feldanalyse und Auftragsstatus.
- Vorschaumodus als Standard; Schutz vorhandener NFOs und Sicherungen beim Ersetzen.
- Dockerfile, Compose-Beispiel und Unraid-Template.

## Installation

1. `metadachs-v0.1.0.zip` herunterladen und entpacken.
2. Im entpackten Projektverzeichnis `docker build -t metadachs:local .` ausführen.
3. Die Anleitung in `README.md` befolgen: Appdata/Mounts vorbereiten,
   `config.example.toml` als `config.toml` ablegen und eigene Tokens hinterlegen.
4. Unraid-Template importieren oder Compose verwenden. Zunächst Vorschau aktiv lassen.
5. WebUI über den konfigurierten Host-Port öffnen. Benutzer: `metadachs`;
   Passwort: der konfigurierte Webhook-Token.

Es wird in diesem Release noch kein fertiges Container-Image in einer Registry
bereitgestellt. Das Image muss auf dem Zielsystem gebaut werden.

## Validierung und Einschränkungen

- 37 automatisierte Tests erfolgreich unter Windows und im Linux-Docker-Container.
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