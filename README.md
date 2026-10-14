# Meld

Meld verbindet Direktnachrichten und den bisherigen Notizraum in einer Website mit gemeinsamen Benutzerkonten. In der Seitenleiste kannst du zwischen **Nachrichten** und **Notizen** wechseln. Beide Bereiche verwenden dieselbe Anmeldung und dieselbe gespeicherte Hell-/Dunkelmodus-Auswahl.

Nach der Anmeldung kannst du über **Neuen Chat starten** andere registrierte Konten finden und direkt anschreiben. Nachrichten werden alle vier Sekunden aktualisiert und dauerhaft in der SQLite-Datenbank gespeichert. Die Chatoberfläche passt sich an Hoch- und Querformat an. Die Chats sind nicht Ende-zu-Ende-verschlüsselt: Betreibende des Servers können auf gespeicherte Nachrichten zugreifen. Verwende den Messenger daher nicht für vertrauliche Inhalte.

## Lokal starten

Voraussetzung: Python 3.10 oder neuer.

```powershell
cd "$env:USERPROFILE\Documents\Notizen-Website"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Lass dieses Terminalfenster geöffnet, während du die Website benutzt. Öffne danach <http://127.0.0.1:5000>. Falls der Browser „NetworkError“ anzeigt, starte den Server erneut mit `python app.py` und lade die Seite neu. Öffne die frühere Notizen-App unter <http://127.0.0.1:5000/notes>.

Mit **Konto erstellen** kannst du ein Mitgliedskonto anlegen. Registrierte Konten erhalten zunächst die Rolle **Mitglied**. In der Notizen-App kannst du Notizen als **öffentlich** (für alle angemeldeten Mitglieder sichtbar) oder **privat** (nur für dich sichtbar) markieren. Öffentliche Notizen können gemeinsam bearbeitet und gelöscht werden; private Notizen kann ausschließlich ihr Ersteller ansehen, bearbeiten und löschen. Nur der Ersteller kann die Sichtbarkeit einer öffentlichen Notiz ändern. Wenn ein Admin ein Konto entfernt, bleiben dessen öffentliche Notizen erhalten; private Notizen dieses Kontos werden gelöscht. Bestehende Notizen bleiben bei der Datenbankaktualisierung öffentlich.

Im Editor kannst du Text formatieren, Tabellen mit bis zu 12 Zeilen und 8 Spalten einfügen sowie JPEG-, PNG-, GIF- und WebP-Bilder (höchstens 15 MB) und MP4-/WebM-Videos (höchstens 50 MB) anhängen. Pro Notiz sind bis zu 20 Anhänge und zusammen höchstens 100 MB erlaubt. Anhänge werden zusammen mit der Notiz-Sichtbarkeit geschützt und auf demselben Datenträger wie die Datenbank gespeichert. Beim Löschen einer Notiz werden ihre Anhänge ebenfalls entfernt.

## Erstes Admin-Konto

Beende den Server mit `Ctrl+C`, aktiviere bei Bedarf die virtuelle Umgebung und führe aus:

```powershell
flask --app app create-admin
```

Der Befehl erstellt ein Admin-Konto oder stuft ein vorhandenes Konto hoch. Admins können Mitglieder verwalten, Rollen ändern und weitere Konten entfernen. Das letzte Admin-Konto kann nicht herabgestuft oder gelöscht werden.

## Für späteres Hosting

Die App kann mit Flask und SQLite auf einem einzelnen Server betrieben werden. Für einen Internetbetrieb:

- Setze `SECRET_KEY` auf einen langen, zufälligen Geheimwert und `SESSION_COOKIE_SECURE=true`.
- Verwende HTTPS, einen produktiven WSGI-Server und einen Reverse Proxy; setze `DATABASE_PATH` auf einen dauerhaften, gesicherten Speicherort.
- Veröffentliche den Entwicklungsserver (`python app.py`) nicht direkt im Internet. Der Server startet absichtlich nur auf `127.0.0.1`.
- Lege die SQLite-Datei und `data/.secret-key` nicht in ein öffentliches oder eingechecktes Verzeichnis.

Für einen einzelnen Produktionsserver kann Waitress beispielsweise mit `waitress-serve --listen=127.0.0.1:5000 app:app` gestartet werden; der Reverse Proxy terminiert HTTPS.

Die Anwendung ist noch nicht im Internet veröffentlicht; dafür wird ein Hosting-Anbieter benötigt.

## Weltweiter Zugriff mit Render

Die Datei `render.yaml` richtet einen Render-Webdienst mit Waitress, HTTPS-sicheren Sitzungs-Cookies und einem dauerhaften Datenträger für die SQLite-Datenbank ein. Ein dauerhafter Datenträger benötigt bei Render einen kostenpflichtigen Webdienst; dieser Dienst ist auf eine Instanz ausgelegt, weil SQLite auf einem einzelnen persistenten Datenträger liegt.

1. Lege ein privates GitHub-Repository an und lade den Projektordner **ohne** `data/notes.sqlite3`, `data/.secret-key`, Passwörter oder andere Geheimnisse hoch. Die lokalen Datenbankdateien sind durch `.gitignore` ausgeschlossen.
2. Melde dich bei Render an, wähle **New → Blueprint** und verbinde das Repository mit der Datei `render.yaml`.
3. Prüfe die angezeigten Kosten und bestätige die Erstellung. Render erzeugt automatisch einen geheimen `SECRET_KEY`; die Website wird anschließend über eine HTTPS-Adresse unter `onrender.com` erreichbar.
4. Öffne in Render die Shell des Dienstes und erstelle das erste Admin-Konto mit `flask --app app create-admin`. Danach können sich weitere Nutzer über „Konto erstellen“ registrieren.

Der geheime Sitzungsschlüssel wird von Render verwaltet und die Datenbank liegt auf dem persistenten Datenträger. Veröffentliche weder diese Werte noch ein Backup mit privaten Notizen. Render-Blueprints und persistente Datenträger: [Blueprints](https://render.com/docs/blueprint-spec), [Datenträger](https://render.com/docs/disks).
