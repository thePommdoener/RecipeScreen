# Küchendisplay

Projektübersicht für das Küchen-Rezeptdisplay: ein Bildschirm in der Küche, der Rezepte einheitlich anzeigt, im Leerlauf Wetter und Kalender zeigt und Alexa-Timer einbindet.

> Übernommen aus dem Claude-Dokument „Küchendisplay“: https://claude.ai/artifact/PJWCJ1FzcqoiNtXrQTx3V5 (Stand 2026-11-04)

## Idee und Ziel

- Kleiner Bildschirm in der Küche, auf dem ein QR-Code angezeigt wird.
- Per Handy scannen und Rezepte hochladen, zum Beispiel von TikTok, YouTube, Pinterest oder Webseiten wie chefkoch.de.
- Rezepte werden per LLM analysiert und alle im gleichen Format dargestellt (Zutaten, Schritte).
- Im Leerlauf, also ohne laufendes Rezept, zeigt die Web-App Wetter und Kalender an.
- Die Restzeit von Alexa-Timern soll als zusätzliche Kachel angezeigt werden.
- Eventuell Steuerung der Web-App per Sprache über die Alexa in der Küche.

## Hardware

### Server

- Raspberry Pi als lokaler Server im Heimnetz war die ursprüngliche Entscheidung. Er muss kein Pi 5 sein, Pi 3, Zero oder Pico wurden erwogen, weil Raspberries aktuell teuer sind.
- Pi-Angebote auf Kleinanzeigen waren zu teuer oder unpassend, zum Beispiel ein Paket mit Monitor für 220 Euro.
- Alternative: gebrauchte Mini-PCs oder Thin Clients.
- Konkretes Angebot: Lenovo ThinkCentre M700 Tiny für 60 Euro (i3 6100T, 8 GB RAM, Original-Netzteil, keine SSD dabei).
- Überlegt wird auch ein refurbished Shop mit Garantie.

### Anzeige

- Wunsch: ca. 10 Zoll, soll an der Wand hängen, in Griffweite.
- Kandidat 1: gebrauchtes iPad Air 2 (16 GB, WLAN) für 49 Euro auf Kleinanzeigen.
- Kandidat 2: Fire HD 8 Kids (Modellnummer K72LL4) für 35 Euro, aktuell favorisiert.
- Ein vorhandenes iPad liegt zu Hause. Das alte 7-Zoll Fire Tab ist nicht mehr vorhanden.
- Kenntnisse in Tablet-Programmierung und Flashen sind gering.

### Budget

- Ca. 100 bis 150 Euro.

## Software

- Web-App läuft lokal auf dem Server und wird im Browser des Tablets aufgerufen.
- Die App spricht nur mit der LLM-API in der Cloud, um Rezepte aufzubereiten. Kein Abo gewünscht.
- Idee für Alexa-Timer: Home Assistant mit der Alexa-Media-Player-Integration (HACS) läuft auf demselben Server, zum Beispiel in Docker. Die Web-App holt den Timer-Zustand über die Home-Assistant-API, per WebSocket oder regelmäßiger Abfrage, mit einem Langzeit-Zugriffstoken.
- Hinweis: Die Alexa-Integration ist inoffiziell und kann gelegentlich brechen.
- Alternative für Timer: Alexa-Routine oder eigener Skill, der beim Timerstart einen Webhook an den Server schickt. Dann kennt der Server aber keine Änderungen oder Abbrüche.

## Struktur der App

- Startansicht im Leerlauf: Wetter, Kalender, Timer-Kachel.
- QR-Code-Seite zum Hochladen neuer Rezepte per Handy.
- Rezeptansicht mit einheitlichem Format: Zutaten und Schritte.
- Rezeptliste zum Auswählen gespeicherter Rezepte.

## Offene Punkte

- Entscheidung Server: Raspberry Pi oder Lenovo M700 Tiny.
- Entscheidung Anzeige: Fire HD 8 Kids oder iPad Air 2.
- Umsetzung der Alexa-Timer-Anzeige über Home Assistant.
- Optional: Sprachsteuerung über Alexa.
