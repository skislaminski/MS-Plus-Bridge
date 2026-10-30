# Notizen

## Phase 1, 2026-10-06 (MS-60B+ Firmware 1.20, macOS)

**Funktioniert**
- Identity: `F0 7E 00 06 02 52 6E 00 27 00 31 2E 32 30 F7`, Device-ID `6E`, Model `27 00`.
- Edit enable `50` und edit disable `51` werden mit `F0 52 00 6E 00 00 F7` bestätigt.
- Nach edit enable meldet das Pedal Reglerbewegungen als `64 20 00 <slot> <param> <LSB> 00 00 00 00`.
  Gemessen: Slot 0, Param 2, 0–100, lückenlos monoton, Wert direkt im LSB.
  Sonst kam in den 30 s nichts (kein Patch-Dump `64 12`, keine CC/PC).
- Chocolate Plus (Port `SINCO`): Expression = CC 25, Kanal 1, 0–127, nicht invertiert, im Stillstand ruhig.

**Parameter setzen (Gegenprobe)**
- Funktioniert: `F0 52 00 6E 64 20 00 00 02 <LSB> 00 00 00 00 F7` wird übernommen und nach 8–11 ms mit
  `64 20 01 …` bestätigt. Ab 02:22 Uhr fünf von fünf geänderten Werten bestätigt (50, 60, 40, 100),
  mit 0,5 s Pause nach edit enable und ganz ohne Pause.
- Das Ack kommt nur, wenn sich der Wert ändert. Denselben Wert noch einmal zu senden bleibt
  unbeantwortet (zweimal gezielt geprüft: 60 → 60, 40 → 40).
- Ungeklärt: Die allererste Sendung (02:09 Uhr, 100 → 50, ohne Pause) blieb ohne Ack, ebenso die
  zweite (02:12 Uhr, 50, mit Pause). Beim nächsten `learn` meldete der erste Reglerklick 49, das Pedal
  stand also auf 50. Passt zu „erste Sendung übernommen, aber nicht bestätigt; zweite war dann
  unverändert“. Der Nutzer hat den Regler dazwischen nicht von Hand bewegt, die erste Sendung wurde
  also übernommen, aber nicht bestätigt. Warum, ist offen. In Phase 3 beobachten, ob das erste Setzen
  nach dem Anstecken ohne Ack bleibt.

**Abgleich mit dem zoom-explorer-Quelltext** (`src/ZoomDevice.ts`, `src/index.ts`, Stand 2026-10-06)
- Format identisch: `64 20 00` + Slot, Param, dann 5 Wertbytes zu je 7 Bit (LSB zuerst).
- Param 0 = Effekt an/aus, Param 1 = Effekttyp, Parameter ab 2. Slot und Param werden beim Senden
  und Empfangen gleich gezählt.
- Reihenfolge dort: Probe-Sequenz (`05`, `29`, `64 13`, `07`, `44`, `09`, `46`, `33`, ggf. Program-Change-Test
  mit Zurückschreiben des aktuellen Patches, `64 02`), dann `51`, dann `50`; danach Parameter per `64 20 00`.
  PC mode (`52`) wird nirgends aufgerufen, keine Wartezeiten zwischen Nachrichten.

**Offen**
- Aktualisiert das Pedal sein Display, wenn der Wert per SysEx gesetzt wird?
- Ziel: Patch 095, Galactic Reverb (Custom Effect) an erster Position, erster Parameter „Hold“.
  Damit zählen Slots ab 0 und Parameter ab 2 (wie in der Referenz).
- 7-Bit-Aufteilung für Werte über 127 (an diesem Parameter nicht prüfbar).
- Meldet das Pedal Patchwechsel von sich aus? (`33` antwortet: Patch 095 = CC0 `0`, CC32 `9`, PC `4`, Kanal 1,
  auch ohne edit enable. Also 10 Patches pro Bank, Anzeige = Bank · 10 + Program + 1.)

## Phase 2/3, erster Lauf von bridge.py am echten Gerät, 2026-10-06 03:42 Uhr (ca. 17 s aktiv)

- Patch 095 = Bank 9, Program 4; Mapping `"9/4"` wurde beim Start über `33` erkannt.
- 190 Parameter gesendet, 190 bestätigt, Acks 1:1 in Sendereihenfolge, Werte 0–100 ausgefahren.
- Latenz TX→Ack Median 11,0 ms, Max 14,0 ms; CC→Ack Median 11,5 ms, Max 14,1 ms.
- Das Chocolate liefert höchstens ca. 30 CCs pro Sekunde (kleinster Abstand 33 ms). Die Drosselung auf
  10 ms greift also nie; bei schnellem Durchtreten springt der Wert um bis zu 10 Schritte (Median 2).
- Hotplug: Beim Start fehlte `SINCO`, 43 s später war es da und die Bridge hat von selbst verbunden.
  Die Port-Liste wird unter macOS also im laufenden Prozess aktualisiert.
- Auch das erste Setzen nach dem Start wurde diesmal bestätigt.
- Sauber beendet: edit disable gesendet und bestätigt.

## Phase 3, zweiter Lauf, 2026-10-06 03:45–03:48 Uhr

- Patchwechsel am Pedal: Das MS-60B+ meldet ihn von sich aus, bei laufendem edit enable in dieser Reihenfolge:
  `64 26 00 00 <bank LSB MSB> <prog LSB MSB>`, dann `64 20 00 64 02 78 00 …` (Tempo 120), dann CC0, CC32,
  Program Change. Die Bridge hat fünf Wechsel (9/3, 9/4, 9/5) erkannt und das Mapping jeweils umgeschaltet.
  Eine regelmäßige Abfrage mit `33` ist nicht nötig.
- 92 Parameter gesendet, 92 bestätigt. Latenz CC→Ack Median 3,9–5,2 ms, Max 5,2 ms (im ersten Lauf 11,5 ms).
- Chocolate im Betrieb abgezogen: nach höchstens 1 s erkannt, edit disable gesendet und bestätigt, danach
  „Warte auf Geräte“. Bis zum Ende des Laufs (101 s) wurde es nicht wieder angesteckt; das Wiederverbinden
  nach einem Verlust ist also noch ungetestet (beim Start hatte es im ersten Lauf funktioniert).

- Dritter Lauf (03:51–03:58 Uhr): Chocolate abgezogen und wieder angesteckt, die Bridge war 4 s nach dem
  Wiedererscheinen des Ports wieder bereit. Sauber beendet (edit disable bestätigt).

**Noch nicht getestet (Phase 3):** Abziehen des MS-60B+ im Betrieb, Patchwechsel während das Pedal bewegt
wird, Zipper-Geräusche und Display-Verhalten nach Eindruck des Nutzers, gespeicherter Patch nach der
Session unverändert.

## Bedienoberfläche, 2026-10-06

- `bridge.py` startet jetzt zusätzlich eine Oberfläche im Browser (`ui.py`, `ui.html`): Status, „Regler lernen“
  für den am Pedal gewählten Preset, pro Preset Regler, Name, Wert an Ferse und Spitze, Verlauf.
- Messbereiche gelten pro Preset (`messungen.json`: Preset → Slot/Param → Bereich). `probe.py learn` und
  `verify` fragen dafür jetzt den aktuellen Preset per `33` ab.
- Am echten Gerät geprüft: Start, Anzeige von Preset 095, Speichern (Bereich und Umkehrung landen in
  `config.yaml` und gelten sofort), Lernen starten und abbrechen.
- Nur in der Simulation geprüft: ein vollständiger Lernvorgang (Regler drehen, übernehmen), Werte über 127.

## Erweiterung der Oberfläche, 2026-10-06 (ab 04:50 Uhr)

- Bis zu zwei Regler pro Preset; die Sendungen wechseln sich ab, je Mindestabstand geht eine raus.
- Ferse/Spitze je Regler (Bereich begrenzen, Richtung umkehren), Knopf „Umkehren“.
- Export (alle Presets oder einer) und Import als JSON, samt gelernten Bereichen.
- Der Nutzer hat um 04:35 Uhr selbst gelernt: Preset 006, Effekt 3, Parameter 1, 0–100. Der Lernvorgang
  funktioniert also am echten Gerät.
- Am echten Gerät geprüft: Anzeige beider Presets, Umkehren und Speichern, Export (alle, einzeln) und Import
  über die Schnittstelle. Nur in der Simulation: zwei Regler gleichzeitig senden, Import in eine leere
  Installation, der Download über die Knöpfe.
- Stoppen über den Browser-Bereich der App beendet den Prozess hart: Es wird kein edit disable gesendet.
  Bei Strg+C, SIGTERM oder Schließen des Terminalfensters wird sauber beendet.

## Englische Fassung und vier Parameter, 2026-10-06 (ab 05:00 Uhr)

- Bis zu vier Parameter pro Preset (`MAX_TARGETS = 4`). Das Chocolate liefert rund 30 CCs/s; vier Parameter
  wären 120 Sendungen/s, der Mindestabstand von 10 ms lässt 100 zu. In der Simulation kommen deshalb pro
  Parameter rund 25 von 33 Zwischenwerten an, die Endwerte stimmen immer. Am echten Gerät noch nicht geprüft.
- Der Mindestabstand zählt jetzt ab dem tatsächlichen Sendezeitpunkt (vorher ab Beginn des Schleifendurchlaufs;
  ein langsamer Durchlauf konnte den Abstand auf unter 6 ms verkürzen).
- Alles am Programm ist englisch: `bridge.py`, `ui.py`, `ui.html`, `zoom_sysex.py`, `probe.py`, Kommentare in
  `config.yaml`. Umbenannt: `freigaben.json` → `approvals.json` (Schlüssel `backup_confirmed`, `messages`),
  `messungen.json` → `measurements.json`, `probe.py freigabe` → `probe.py approve`,
  `Bridge starten.command` → `Start Bridge.command`, Exportdatei `…-alle-presets.json` → `…-all-presets.json`.
- Der Nutzer hat zwei Parameter in Preset 095 im Einsatz (Effekt 1/Parameter 1 und Effekt 2/Parameter 3,
  100→80) und meldet: klappt.

## Zuordnen ohne Lernen, 2026-10-06 (ab 05:20 Uhr)

- Das Auswahlfeld „Parameter“ bietet Effect 1–6 × Parameter 1–9 direkt an; gelernte Bereiche stehen in Klammern.
  „Add preset“ legt einen Preset per Nummer an, ohne dass er am Pedal gewählt sein muss.
- Nicht gelernte Parameter: Bereich vom Nutzer (Vorschlag 0–100, Grenze 16383). Solange das Pedal einen solchen
  Parameter noch nie bestätigt hat, geht immer nur eine Nachricht raus und es wird auf Ack oder Timeout (250 ms)
  gewartet; nach 5 unbestätigten Sendungen stoppt die Bridge ihn bis zum nächsten Presetwechsel, Speichern oder
  Drehen am Regler. Die Oberfläche zeigt „not confirmed“ bzw. „stopped“.
- Ein teilweise zu großer Bereich (z. B. 0–100 bei echtem Maximum 50) wird nicht gestoppt, nur als „not confirmed“
  markiert; die globale Drosselung kann dabei anspringen.
- Nur in der Simulation geprüft (gültig, teilweise gültig, ungültig). Am echten Pedal ist offen, was bei einer
  nicht vorhandenen Effekt- oder Parameternummer passiert. Hinweis aus der Referenz: Beim Systembefehl
  `64 20 00 64 09` frieren Werte über 0A das Pedal ein; die Firmware prüft also nicht alles.
- Lernt man einen schon zugeordneten Parameter, wird sein Bereich auf das Gelernte begrenzt.

## Umzug und Windows, 2026-10-06 (ab 05:45 Uhr)

- Das Projekt liegt jetzt in `~/Desktop/zoom-expression-bridge`. Der Nutzer hat dort `Start Bridge.command`
  per Doppelklick gestartet: Die Python-Umgebung wurde automatisch neu angelegt, die Bridge lief sofort.
- Veröffentlichung: Quellcode auf GitHub per Web-Upload aus `github-upload/`, Lizenz MIT.
- Windows (alles ungetestet, kein Windows-Rechner vorhanden): `Start Bridge.bat` ergänzt. Behoben, was dort
  sicher gescheitert wäre: `signal.SIGHUP` gibt es unter Windows nicht (Absturz beim Start). Vorsorglich:
  Konsolenausgabe bricht bei nicht darstellbaren Zeichen nicht mehr ab. Bekannt: Vor Python 3.11 schläft
  `time.sleep` unter Windows in Schritten von typisch 16 ms, die Bridge reagiert dann träger.

## Parameter 1–12, 2026-10-07

- Das Auswahlfeld bietet jetzt Parameter 1–12 je Effekt (`MAX_PARAMS = 12`, param 2–13), vorher 1–9.
  Unverändert: Effekt 1–6, höchstens vier Parameter pro Preset, Schutz für nicht gelernte Parameter.
- Ob das MS-60B+ Parameter 10–12 annimmt, ist am Gerät nicht geprüft.
- Die Simulationsskripte aus der ersten Sitzung lagen in einem temporären Ordner und sind nicht mehr da.
  Geprüft wurde diesmal nur die Validierung (Parameter 12 zulässig, 13 abgelehnt) und der gemeldete Grenzwert.

## Tests fest im Projekt, 2026-10-07

- `tests/` enthält 31 Tests gegen simulierte Geräte: Allowlist und Freigaben (`test_sysex.py`), Bridge und
  Oberfläche (`test_bridge.py`), Einrichtungsskript (`test_probe.py`). Aufruf: `.venv/bin/python -m unittest`.
- Das simulierte Pedal verhält sich wie am echten Gerät gemessen (Ack nur bei geändertem Wert, Presetmeldung
  als `64 26 …` plus CC 0, CC 32, Program Change).
- Klarstellung zur Zählweise: In der Oberfläche und am Pedal Parameter 1–12, intern `param` 2–13.

## Keyboard-Synth, 2026-10-07

- Neu: Ein MIDI-Keyboard spielt den DIY-Effekt KeySynth (Projekt `~/Desktop/ms-plus-synth`) über dessen
  Key-Regler. Abschnitt `synth:` in `config.yaml`; beim Nutzer eingetragen: Keyboard `KOMPLETE KONTROL A61`,
  alle Kanäle, Effekt-ID `07000f61`.
- `zoom_sysex.py`: `build_set_key` (Parameter fest 2, Werte 0–128, Slot 0–5), `key_target`, `parse_patch_dump`.
  Der Parser wurde gegen fünf echte Dumps des MS-60B+ vom 2026-10-07 geprüft (IDs, an/aus und Wert des ersten
  Reglers stimmten mit dem Pedal überein); einer davon liegt als Testdaten in `tests/test_synth.py`.
- `bridge.py`: Keyboard-Port, Spiellogik (`Keys`), Suche des Effekts per Patch-Abfrage, Noten ohne
  Mindestabstand, Key = 0 beim Beenden. Chocolate und Keyboard sind mit `synth`-Abschnitt beide optional.
- Tests: 29 neue in `tests/test_synth.py`, zusammen 60, alle bestanden. `tests/sim.py` kennt jetzt ein
  Keyboard und beantwortet die Patch-Abfrage.
- Am echten Gerät gemessen (im Projekt ms-plus-synth, nicht mit der Bridge): Key-Schreibvorgänge im
  Sechzehntelraster bei 140 BPM (53,6 ms) 128 von 128 bestätigt, Ack im Median nach 9,4 ms, höchstens
  11,5 ms; bei 26,8 ms Abstand ebenfalls 128 von 128. Slot-Zählung ab 0, 6 Slots.
- Freigabe: `query_patch` vom Nutzer am 2026-10-07 um 02:22 Uhr erteilt.
- Sicherung des Stands vor der Änderung: `~/Desktop/ms-plus-synth/backup/bridge-vor-phase4-2026-10-07/`.
- `github-upload/` ist nicht aktualisiert; dafür `Prepare GitHub upload.command` ausführen.

## Keyboard-Synth am echten Gerät, 2026-10-07 02:23–02:27 Uhr

- Start nur mit Keyboard (`KOMPLETE KONTROL A61`), Chocolate nicht angesteckt: bereit nach unter 1 s,
  Patch-Abfrage in 6 ms beantwortet.
- Der Nutzer hat den KeySynth erst danach am Pedal eingefügt: sofort gefunden, ohne zweite Abfrage.
  **Das Pedal meldet Kettenänderungen von sich aus:** Bei laufendem edit enable schickt es den Patch als
  `64 12 00 …` (19-mal in diesem Lauf, auch beim Blättern im Effektmenü). Die Antwort auf `64 13`
  beginnt mit `64 12 01 …`. Der Parser nimmt beide.
- Effekt in der Kette verschoben (Effekt 1 → 2): über denselben Dump erkannt, danach an Slot 1 gesendet.
- 188 Key-Sendungen, 186 bestätigt. Taste → Ack: Median 9,9 ms, 90 % unter 10,5 ms, 99 % unter 20,5 ms.
- Zwei blieben unbestätigt:
  1. Ein Gate-off ging verloren, während der Nutzer im Effektmenü des Pedals war. Die Wiederholung
     250 ms später wurde bestätigt (Ack nach 47,9 ms). Daraufhin geändert: `KEY_ACK_TIMEOUT` 60 ms und bis
     zu drei Wiederholungen des letzten Werts (`KEY_RETRIES`). Diese Änderung ist nur in der Simulation
     geprüft.
  2. Gate-off und neue Note im Abstand von 1 ms: Das Pedal bestätigte nur die Note. Kein Fehler im
     Klang gemeldet.
- Meldung des Nutzers: „Alles funktioniert.“ Kommt eine neue Note, während er im Menü des Pedals ist,
  wirft ihn das Pedal aus dem Menü; bei gehaltenem Ton kann er sich im Menü bewegen. Das ist die Reaktion
  des Pedals auf eine Parameter-Nachricht, die Bridge kann daran nichts ändern.
- Unbekannte Meldung vom Pedal, einmal: `F0 52 00 6E 64 20 00 64 01 00 00 00 00 00 F7`.
- Sauber beendet (SIGTERM): edit disable gesendet und bestätigt; Key stand schon auf 0.
- 62 Tests bestanden (zwei neue für den verlorenen Gate-off).
- **Noch nicht am echten Gerät geprüft:** Key 128 (7-Bit-Aufteilung), Abziehen des Keyboards im Betrieb,
  Chocolate und Keyboard gleichzeitig, die schnellere Wiederholung.

## Dauertest mit Keyboard-Synth, 2026-10-07 02:57–03:21 Uhr

Aufbau: REAPER spielt Sechzehntel (8 Noten pro Sekunde) über den IAC-Treiber, die Bridge liest von dort.
Zwei Durchgänge, zusammen rund 13 Minuten; der Nutzer hat nach 13 Minuten abgebrochen, die geplante Stunde
ist nicht gelaufen.

- 12233 Key-Sendungen, 11021 bestätigt. Kein Absturz, kein Verbindungsabbruch, sauber beendet.
- **Unbestätigte Noten gibt es nur, während der Nutzer das Pedal bedient.** In den Minuten ohne Bedienung
  (03:08, 03:09): 1421 von 1421 bestätigt, und in der Aufnahme kein gehaltener oder fehlender Ton. Mit
  Bedienung (Regler eines anderen Effekts drehen, in der Kette oder der Effektauswahl blättern) antwortet
  das Pedal 0,1 bis 0,7 s lang verspätet oder gar nicht; hörbar als kurz gehaltener Ton.
- Presetwechsel weg und zurück (zweimal): Synth als fehlend gemeldet, Noten ignoriert, nach dem
  Wiedereinfügen 10 bis 14 s später von selbst gefunden.
- **USB-Kabel des Pedals rund 2 s gezogen (03:20:41):** Die Bridge hat das nicht bemerkt (keine Meldung
  „Connection lost“; die Portprüfung läuft einmal pro Sekunde). Danach beantwortete das Pedal die
  Patch-Abfrage, bestätigte aber 27 s lang keinen einzigen Parameter. Deutung: Nach dem Wiedereinstecken ist
  der Edit-Modus aus. Laut Nutzer lief der Ton weiter; eine Aufnahme davon gibt es nicht.
- Daraufhin geändert:
  - Bleiben Parameter unbestätigt, sendet die Bridge erneut edit enable, höchstens alle 2 s
    (`_reenable_edit`). Das gilt für Noten und für Expression.
  - Die Warnung und die erneute Patch-Abfrage bei unbestätigten Noten kommen höchstens alle 5 s
    (vorher bis zu einmal pro Sekunde, 59 Warnungen in neun Minuten).
  - Das simulierte Pedal kennt jetzt den Edit-Modus und ein kurzes Umstecken (`World.replug`).
- 65 Tests bestanden (drei neue). Am echten Gerät sind diese Änderungen noch nicht geprüft.
- Am Rande: Die Parameter 9 und 10 (intern) hat das Pedal per SysEx angenommen und bestätigt.

## Umbau für KeySynth 0.20, 2026-10-07 (entwickelt in `ms-plus-synth/bridge-staging/`, übernommen um 17:57 Uhr zusammen mit der Installation von 0.20 auf dem Pedal)

Anlass: Der Nutzer wünscht Glide, Pitch Bend, Mod-Wheel und Keyboard-Regler für den Synth, und die Zuweisung
soll mit jedem Keyboard gehen. Der Effekt bekommt dafür eine neue Reglerbelegung (Projekt ms-plus-synth,
`NOTES.md`, „KeySynth 0.20“). Bridge und Effekt müssen zusammen gewechselt werden: Die alte Bridge spielt nur
0.10, die neue nur 0.20.

- **Key 0–1000:** Note und Bend in einer Zahl (`zs.key_for`), 10 Cent pro Schritt ab C0. Noten außerhalb
  12–111 werden nicht gespielt.
- **Pitch-Wheel:** `bend_range` in der Config (Standard 2). Bei klingender Note höchstens 9 Schritte pro
  Nachricht, im Mindestabstand, vor allen anderen Reglern. Der letzte Schritt wird wiederholt, wenn das
  Pedal ihn nicht bestätigt.
- **Controller auf Regler:** anlernbar in der Oberfläche (Karte „Keyboard synth“), abgelegt in
  `controls.json`. Ohne Datei: Mod-Wheel auf Rate.
- **Anzeige:** nur noch Noten im Terminal, Bend-Schritte nicht. Die Zeile nennt die klingende Note.
- **Überholte Werte** zählen nicht mehr als unbestätigt (neuer Zähler `overtaken`).
- **Expression auf einen Synth-Regler** wird nicht gesendet, wenn der gelernte Bereich größer ist als der
  Regler. Betrifft die Zuordnung des Nutzers für Preset 050 (Parameter 11, mit 0.10 als „Rate“ gelernt):
  Mit 0.20 liegt dort „LFO“ (0–20), sie muss neu gelernt werden.
- 84 Tests bestanden (19 neu). Die Karte wurde im Browser gegen einen nachgebauten Zustand geprüft
  (Anlernen starten und abbrechen, keine Skriptfehler), nicht gegen echte Geräte.
- **Am Gerät noch nicht geprüft:** alles hiervon. Key-Werte über 128, die Regler 3–13 von 0.20 per SysEx,
  das Verhalten bei schnellen Bends (bis zu 100 Nachrichten pro Sekunde).

## Erster Lauf mit KeySynth 0.20 am Gerät, 2026-10-07 18:09–18:13 Uhr

- **Key bis 1000 funktioniert am Pedal:** 623 Key-Nachrichten, 588 bestätigt, die 35 übrigen von der nächsten
  Nachricht überholt. Werte über 128 (bis 271) wurden 540-mal gesendet und bestätigt; ACK im Median nach 11 ms.
- **Bend:** 520 Schritte bei klingender Note, Abstand mindestens 10 ms, bis zu 71 Key-Nachrichten in einer
  Sekunde, ohne Ausfall und ohne Warnung.
- **Controller auf Regler:** Mod-Wheel auf Rate 861 gesendet, 844 bestätigt. Die Parameter 3–12 des Effekts
  (intern 3–11 und 13) wurden per SysEx gesetzt und bestätigt. Unbestätigt blieben nur Werte ohne Änderung
  und überholte Werte.
- **Anlernen in der Oberfläche:** elfmal benutzt, funktioniert. Das KOMPLETE KONTROL A61 sendet auf seinen
  acht Drehreglern CC 14–21.
- **Nachgebessert nach diesem Lauf:** Die Meldung über eine gesperrte Expression-Zuordnung kommt nur noch
  einmal pro Preset (vorher nach jedem Anlernen erneut), und ein Regler, dessen Zuweisung sich nicht ändert,
  behält seinen zuletzt gesendeten Wert (vorher ging er nach jedem Anlernen noch einmal hinaus). 85 Tests.
- **Ziele „Vib“ und „Trm“ (Wunsch des Nutzers, 18:3x Uhr):** LFO-Tiefe per Wheel oder Regler ab Off, ohne
  dass der Effekt einen weiteren Regler braucht. Zehn Stufen je Richtung (so fein wie der LFO-Regler des
  Effekts). 86 Tests. Am Gerät noch nicht geprüft.
- **Zweiter Lauf, 18:13–18:39 Uhr:** Der Nutzer hat einen Effekt vor den Synth gesetzt (Synth jetzt Effekt 2).
  Die alte Expression-Zuordnung für Preset 050 (Effekt 1, Parameter 11, 0–100, gelernt mit KeySynth 0.10 an
  dieser Stelle) zeigte damit auf den neuen Effekt 1 und war nicht mehr gesperrt. Um 18:37:50 gingen sieben
  Werte an Slot 0, Parameter 12; das Pedal hat keinen bestätigt. Folge: „acks are missing“, Mindestabstand
  20 ms, dann 40 ms, und damit auch ein langsamerer Bend. Das ist der bekannte Fall „Effekt getauscht, neu
  lernen“; die Zuordnung sollte gelöscht oder neu gelernt werden.
- **Daraufhin geändert:** Bend-Schritte halten immer den eingestellten Mindestabstand ein, unabhängig von der
  Drosselung der übrigen Parameter. 87 Tests.
- **Die Bridge endete um 18:39:53 ohne sauberes Beenden** (kein „Stopped.“, kein edit disable), als die
  Arbeitssitzung neu startete. Letzte Nachricht war ein bestätigtes Key 0. Neu gestartet um 18:52 Uhr.

## Umstellung auf KeySynth 0.21, 2026-10-07 19:49 Uhr

- Der LFO-Regler des Effekts hat jetzt 101 Stellungen (Vib50 … Vib1, Off, Trm1 … Trm50), das Vibrato reicht
  bis ±100 Cent. In der Bridge: `SYNTH_KNOBS` LFO 0–100, `LFO_OFF` 50. „Vib“ und „Trm“ haben damit 50 Stufen.
- Zusammen mit der Installation von 0.21 auf dem Pedal übernommen; die Bridge spielt 0.20 nicht mehr richtig
  (dort war Off = 10). 87 Tests.

## Oberfläche und Name, 2026-10-07 21:15 Uhr (Wünsche des Nutzers)

- **Name:** „Controller Bridge“ statt „Expression Bridge“ (Titel, README, Meldungen, Dateiname des Exports).
  Exportdateien tragen jetzt `controller-bridge/1`; das alte `expression-bridge/1` wird beim Import weiter
  angenommen. Der Ordner und die geplanten Pi-Dateinamen bleiben, wie sie sind.
- **Keine Vorbelegung:** Ohne `controls.json` ist kein Keyboard-Regler zugewiesen (vorher Mod-Wheel auf Rate).
  Die Zuweisungen des Nutzers wurden geleert: `controls.json` leer, `mappings: {}` in `config.yaml` (Preset 050
  und 095). Die gelernten Bereiche in `measurements.json` sind geblieben. Der Stand davor liegt in
  `ms-plus-synth/backup/bridge-vor-oberflaeche-2026-10-07/`.
- **Reihenfolge:** „Keyboard synth“ steht oben, darunter „Expression pedal“ und die Expression-Zuordnungen.
- **Aussehen:** schwarzes Bedienfeld im Holzrahmen. Nur CSS, kein Bild, keine fremde
  Schrift. Im Browser gegen einen nachgebauten Zustand geprüft, auch mit 375 px Breite.
- 88 Tests.

## Durchsicht und Stand für GitHub, 2026-10-07 21:40–22:00 Uhr

- **Learn-Schalter** einheitlich blau (auch „Learn parameter“); Orange bleibt für „Apply“ und „Save“.
- **Zwei Fehler bei der Durchsicht gefunden und behoben:**
  - `POST /api/synth` mit einem Regler, der kein Text ist (Liste, Objekt), hätte die Hauptschleife mit einem
    `TypeError` beendet. Jetzt „Unknown knob.“; Test dazu.
  - Wer nur das Keyboard nutzt, musste trotzdem einen Expression-CC in die Config eintragen, sonst startete
    die Bridge nicht. Jetzt nur noch nötig, wenn unter `ports:` ein Expression-Controller steht; Test dazu.
- pyflakes ohne Befund (ein ungenutzter Import in den Tests entfernt). Keine persönlichen Pfade im Code.
- **App mit Icon:** `Start Bridge.command` legt `Controller Bridge.app` an. Am Mac des
  Nutzers angelegt und darüber gestartet: Terminal-Fenster öffnet sich, Bridge „Ready“.
- **`keysynth/`:** Effekt 0.21 (bytegleich mit der auf dem Pedal installierten Datei), Icon, Quellen, README,
  Lizenz.
- README überarbeitet (Keyboard gleichrangig, App-Icon, neue Messwerte, Dateiliste, Danksagung).
- 89 Tests. Oberfläche mit den echten Geräten im Browser angesehen.

## Name wieder „Expression Bridge“, 2026-10-07 22:10 Uhr (Wunsch des Nutzers)

- Die Umbenennung in „Controller Bridge“ ist vollständig zurückgenommen, damit Name, Ordner und Repository
  übereinstimmen: Titel, README, Meldungen, Export (`expression-bridge/1`, Dateiname `expression-bridge-…`),
  Config-Kopfzeilen. Die App heißt jetzt `Expression Bridge.app`; die alte `Controller Bridge.app` ist entfernt.
- Die kurzlebige Kennung `controller-bridge/1` wird beim Import nicht angenommen; in der Zwischenzeit gab es
  keine Zuordnungen, also auch keine Exportdateien damit.
- **SYNTHESIS SYNx2** wird jetzt deutlicher genannt: im README des Synths oben und unter „Credits“, außerdem im
  README der Bridge. Übernommen wurde nur der Funktionsumfang als Idee, kein Code.

## Phase 4 begonnen: Vorbereitung für den Raspberry Pi, 2026-10-07 22:30–23:00 Uhr (ohne Pi)

- Neu: `config_schema.py`, `export.py`, `status_led.py`, `pi/expression-bridge.service`, `bridge.py --settings ORDNER`.
- Der echte Stand dieser Installation besteht `export.py --check` (0 Presets mit Zuordnung, 5 gelernte Bereiche,
  1 Keyboard-Controller). Geschrieben wurde nichts.
- Beim Bauen gefunden und behoben: Zeitstempel wurden zuerst als „rohe Bytes“ erkannt; abgelehnte Änderungen im
  Betrieb aus der Datei veränderten den Zustand, bevor sie abgelehnt wurden; die LED wechselte ihr Muster erst nach
  bis zu einer Sekunde.
- 110 Tests (21 neu), pyflakes ohne Befund.
- **Nichts davon lief auf einem Pi.** Die Recherche zur Hardware steht im Projekt ms-plus-synth, `NOTES.md`.
- `github-upload/` enthält noch den Stand von 22:15 Uhr, ohne diese Dateien.


## KeySynth für MS-50G+ und MS-70CDR+, 2026-10-07 (Wunsch des Nutzers, ungetestet)

- `keysynth/` hat jetzt zwei Ordner: `ms-60b-plus/` (die bisherige, am Pedal geprüfte Datei, nur verschoben) und
  `ms-50g-plus_ms-70cdr-plus/` (der unveränderte stomphacks-Build 0.21, Target `0x0090`, „SFX“). Beide Dateien
  heißen `KEYSYNTH.ZD2`; Code und Tabellen sind bytegleich, sie unterscheiden sich in 18 Kopf-Bytes.
- **Die Datei für MS-50G+ und MS-70CDR+ war auf keinem Pedal.** Der Nutzer hat keines der beiden Geräte.
  Begründung und offene Punkte stehen im README des Synths und in `ms-plus-synth/NOTES.md`.
- **Am Code der Bridge ist nichts geändert.** Sie prüft das Modell-Byte nicht, die Effekt-ID ist dieselbe,
  Geräte-ID `6E` und Portname „ZOOM MS Plus Series“ gelten laut stomphacks und nam-to-zoom für alle drei Modelle,
  alle drei haben 6 Slots. Nur am MS-60B+ belegt ist der Leser für den Patch-Dump (`parse_patch_dump`); findet er
  den Synth nicht, meldet die Bridge das und ignoriert das Keyboard. Die Meldungen nennen weiter das MS-60B+.
- README und Dateiliste nachgezogen.

## KeySynth 1.00 im Veröffentlichungsordner, 2026-10-08 03:1x Uhr (Nutzer: „Passt mache ready“)

- `keysynth/ms-60b-plus/` und `keysynth/ms-50g-plus_ms-70cdr-plus/` enthalten jetzt **Version 1.00**: der Code von
  0.21, bytegleich, mit neuem Bild (heller Bodentreter, LED und vier Regler an den Stellen der Stock-Effekte,
  „KEY SYNTH“, Tastatur mit Fußschalter, unten „SLAMINSKI“, oben rechts „V1.0“ aus dem Manifest).
  `ms-60b-plus/KEYSYNTH.ZD2` ist bytegleich mit der am 08.10. um 03:07 installierten und zurückgelesenen Datei
  (sha256 `77642922…`). 0.22 und 0.23 waren Bildversuche am Pedal und sind nicht veröffentlicht.
- Quellen ergänzt: `source/effects/keysynth/icon/` (beide Bilder), `source/tools-local/draw_icon.py` und
  `set_icon.py`, Versionsnummer im Manifest.
- **Gegenprobe:** aus `keysynth/source/` in einer Kopie neu gebaut (zeichnen, bauen, Bild einsetzen, Kopf
  angleichen): beide Effektdateien und die Icon-Datei bytegleich mit den veröffentlichten. Desktop-Tests:
  6 486 010 Prüfungen bestanden.
- README des Synths nachgezogen (Version, Prüfsummen, Bild, Bauanleitung, Versionsliste).
- **Am Code der Bridge ist nichts geändert**, die Tests liefen deshalb nicht neu. Mit 1.00 auf dem Pedal ist die
  Bridge noch nicht gelaufen (der Code des Effekts ist derselbe wie bei 0.21).
- `github-upload/` neu gefüllt.
- **Nutzer am Pedal (08.10., ca. 03:10):** „Passt.“ Das Bild von 1.00 stimmt, „V1.0“ ist lesbar. Sobald etwas
  editiert wurde, schreibt das Pedal „Edited“ über die Ecke oben rechts und verdeckt die Versionsnummer; laut
  Nutzer „halb so wild“, bleibt so. README entsprechend gekürzt und ergänzt, `github-upload/` erneut gefüllt.

## KeySynth 1.10 statt 1.00, 2026-10-08 03:3x Uhr (Entscheidung des Nutzers)

- Der Synth heißt jetzt **1.10** (im Bild „V1.1“), passend zum Commit „Update V1.1 Beta“, mit dem die Dateien für
  MS-50G+ und MS-70CDR+ auf GitHub kamen. 1.00 war nur auf dem Pedal des Nutzers und wurde nicht committet.
- Beide Pedal-Ordner, Manifest und Bilder unter `keysynth/` sind 1.10; `ms-60b-plus/KEYSYNTH.ZD2` ist bytegleich
  mit der am 08.10. um 03:35 installierten und zurückgelesenen Datei (sha256 `ab92affa…`). Code weiter bytegleich
  mit 0.21. Gegenprobe aus `keysynth/source/`: alle drei Dateien bytegleich.
- README des Synths nachgezogen, `github-upload/` neu gefüllt. Am Code der Bridge nichts geändert.

## Stimmenverteilung für KeyPoly, 2026-10-08 (ohne Pedal, ohne den Effekt)

Anlass: KeyPoly, ein vierstimmiger Synth-Effekt, entsteht an anderer Stelle. Laut seiner Beschreibung
liegen Key1–Key4 auf Param 2–5 im Format von KeySynth, dahinter acht geteilte Regler (Level, Wave, Cutoff,
Reso, Atk, Rel, LFO, Rate); pro Stimme hält der Effekt Phase, Hüllkurve, Tonhöhe und die zuletzt verlangte
Tonhöhe für die Bend-Glättung. Offen war die Bridge: Sie konnte die vier Stimmen nicht verteilen.

- **Ein Codepfad für beide Effekte.** KeySynth ist jetzt der Sonderfall „eine Stimme“: `Session.voices`
  hält pro Key-Regler eine `Voice` (zugewiesene Note, gesendeter Key-Wert, offene Acks). Alle 66 bisherigen
  Synth- und SysEx-Tests laufen unverändert durch.
- **Zuteilung (`Session._assign`):** Gespielt werden die letzten vier Noten, gehaltene Tasten vor solchen,
  die nur das Sustain-Pedal hält (`Keys.notes`). Eine klingende Note behält ihre Stimme; beim Loslassen
  wird nur ihre Stimme geschlossen, die anderen werden gar nicht beschrieben. Eine neue Note nimmt die
  freie Stimme, die zuletzt dieselbe Note gespielt hat (so liegt ein Ton nie auf zwei Stimmen, siehe
  „phasengleich, 1,97 ×“ in der Beschreibung), sonst die am längsten freie. Die fünfte Note nimmt die
  Stimme der ältesten und wechselt ohne Gate-off dorthin; wird die neuere losgelassen, kommt die ältere
  zurück, wie bei KeySynth.
- **Akkorde gehen sofort raus**, eine Nachricht pro Note, kein Abstand. Jede Stimme hat ihre eigene
  Wiederholung bei fehlendem Ack (wie bisher 60 ms, bis zu drei Mal).
- **Pitch-Wheel:** jede klingende Stimme in Schritten von höchstens 9 Klicks, die Stimmen abwechselnd im
  Mindestabstand. Ein voller Bend eines Vierklangs dauert damit etwa viermal so lang wie auf KeySynth.
- **Key2–Key4 unbekannt:** Der Patch-Dump zeigt nur den ersten Regler eines Effekts. Ein Versuch, die
  übrigen Regler als je 12 Bit zu lesen, passte beim echten Dump vom 07.10. nicht (KeySynth: 0, 38, 1, 2,
  36, 1024, …). Die Bridge fasst Key2–Key4 deshalb erst an, wenn eine Note sie braucht; da freie Stimmen
  der Reihe nach drankommen, ist nach vier Noten jede einmal beschrieben. Beim Beenden schließt sie jede
  Stimme, die sie gespielt hat.
- **Config:** `synth.poly_effect_id` (Standard `null`). `effect_id` darf jetzt leer sein, wenn
  `poly_effect_id` gesetzt ist; beide verschieden. Gespielt wird der erste der beiden Effekte in der Kette.
  Gleiche Regeln in `config_schema.py` für den Pi.
- **Controller:** `controls.json` speichert weiter Name → CC. Namen, die beide Effekte haben (Level, Atk,
  Rel, LFO, Rate, Vib, Trm), wirken auf den Effekt im Preset; Cutoff, Reso, Wave nur auf KeyPoly, Wave1,
  Wave2, Pitch, Dtune, Mix, Glide nur auf KeySynth. Die Oberfläche zeigt die Regler des Effekts im Preset,
  ohne Effekt alle; die Statuszeile nennt den Effekt und bei KeyPoly die Note jeder Stimme.
- **Annahmen, am Manifest von KeyPoly zu prüfen** (`zoom_sysex.POLY_KNOBS`): Key1–Key4 0–1000, Wave 0–3
  (Saw, Sqr, Tri, Sine wie Wave1), LFO 0–100 mit Off = 50 (wie KeySynth 0.21, sonst stimmen „Vib“/„Trm“
  nicht), Level, Cutoff, Reso, Atk, Rel, Rate 0–100. Ein zu hoher Höchstwert würde Werte außerhalb des
  Reglers senden; KeySynth schaltet dann stumm (Guard im Kernel), was KeyPoly tut, ist offen.
- 22 neue Tests in `tests/test_poly.py`, 4 neue Fälle in `tests/test_export.py`. Der LED-Test
  `test_an_led_that_may_not_be_written_is_left_alone` schlägt in der Cloud-Umgebung schon vor der Änderung
  fehl (läuft als root, `chmod 0o444` sperrt dort nicht).
- **Am Pedal noch zu prüfen:**
  - Vier Key-Writes pro Akkord, fast gleichzeitig: Bestätigt das Pedal alle? Bisher bekannt ist nur, dass
    es von zwei Nachrichten an *denselben* Parameter im Abstand von 1 ms nur die zweite bestätigt. Gilt das
    auch für verschiedene Parameter, meldet die Bridge bei Akkorden „does not confirm notes“ und
    wiederholt die Noten; dann brauchen die Writes eines Akkords einen kleinen Abstand.
  - Zeit Taste → Ack pro Stimme (die Konsole nennt jetzt `Key1` … `Key4`).
  - Last mit vier Dreiecken, Bend eines Vierklangs, Stimmenklau bei gehaltenem Sustain-Pedal.

## Arpeggiator, 2026-10-08 (Wunsch des Nutzers: „Erst Arp, dann Step“; ohne Pedal)

- **Aufbau:** `arpeggiator.py` enthält nur die Notenlogik und die Einstellungen; die Uhr läuft in
  `bridge.py` (`Session._arp_step`, `_arp_tick`, `_on_clock`). Ist der Arpeggiator an, bestimmt er statt der
  Tasten, was klingt (`Session._sounding`); Stimmenverteilung, Bend und Wiederholungen laufen über
  denselben Weg wie beim Spielen von Hand. Auf KeyPoly nimmt jede Note die nächste freie Stimme, die
  Releases klingen also ineinander.
- **Modi:** Up, Down, Up/Down (Wendetöne nicht doppelt), As played (Reihenfolge des Anschlags), Random (nie
  zweimal derselbe Ton). Up und Down machen beim nächsten Ton über bzw. unter dem zuletzt gespielten weiter;
  ein Akkord, dessen Tasten ein paar Millisekunden nacheinander ankommen, fängt so nicht von vorn an.
- **Eigenes Tempo:** 40–300 BPM, die erste Taste startet sofort (Key-Sync), danach driftfrei im Raster.
  Ohne Tasten (und ohne Latch) ruht er bis zur nächsten Taste.
- **MIDI-Clock (Wunsch: beides, wenn nicht zu umständlich):** vom Keyboard-Port, 24 pro Viertel. Schritte
  zählen ab Start, bleiben also auf dem Takt; Stop schaltet stumm und setzt das Muster zurück, Continue
  macht weiter; Clock während Stop wird nur zur Tempomessung genutzt. Bleibt die Clock 0,5 s aus, verstummt
  er. Ein eigener Port nur für die Clock wäre mehr Aufwand (dritter optionaler Eingang mit Hotplug); eine
  DAW schickt die Clock deshalb über denselben virtuellen Port wie die Noten des Keyboards.
- **Gate:** Gate-off und nächste Note auf demselben Key-Regler liegen immer mindestens `min_interval_ms`
  (10 ms) auseinander, ebenso Note und ihr Gate-off (bekannt: von zwei Nachrichten im Abstand von 1 ms an
  denselben Parameter bestätigt das Pedal nur die zweite). Gate 100 % bindet die Töne.
- **Ein/Aus** ist kein gespeicherter Wert: Er ist nach jedem Start aus. Schaltbar in der Oberfläche und über
  einen anlernbaren Controller „Arp“ (ab 64 an). Die übrigen Einstellungen stehen in `controls.json` unter
  `arp` (nur was vom Standard abweicht; alte Dateien ohne `arp` gelten weiter) und gehen mit `export.py` auf
  den Pi.
- **Mehrere Writes kurz nacheinander (Beobachtung des Nutzers):** Das Log im Verlauf des Repositorys
  (06.10., nur Expression) enthält keine zwei Writes an verschiedene Parameter unter 9 ms Abstand und taugt
  dafür nicht. Dass Keyboard, Expression, Pitch-Wheel, Mod-Wheel und Regler gleichzeitig ohne Auffälligkeiten
  liefen, ist der bessere Beleg: Noten gehen sofort raus, landen also regelmäßig wenige Millisekunden neben
  einem Expression-Write.
- Tests: `tests/test_arp.py`. Am Pedal noch zu prüfen: Timing nach Gehör bei 1/16 und 1/32, Last bei hohem
  Tempo (1/32 bei 300 BPM wären 80 Nachrichten pro Sekunde, mehr als bisher getestet), MIDI-Clock aus einer
  DAW über IAC.

## Arpeggiator: Nacharbeit, 2026-10-08 abends (lokal am Mac des Nutzers, ohne Pedal)

Fortsetzung in einer neuen Sitzung. Die Ergebnisse des Reviews aus der Cloud-Sitzung sind mit ihr verloren
gegangen; der Arpeggiator-Code wurde deshalb noch einmal von Hand durchgesehen.

- **Latch-Fehler behoben.** Ursache: Jede Änderung einer Einstellung lief über `Session.switch_arp`, also
  über denselben Weg wie Ein/Aus. Der setzte das Muster zurück und nahm als gelatchten Akkord „was jetzt
  gedrückt ist“, bei losgelassenen Tasten also nichts. Nebenwirkung desselben Fehlers: Auch bei gehaltenen
  Tasten fing das Arpeggio bei jeder Änderung (jeder Klick am Tempo) von vorn an. Jetzt (`Session.arp_changed`)
  spielt er weiter, die Einstellungen werden ohnehin bei jedem Schritt gelesen. Neu gestartet wird nur noch
  beim Wechsel der Clock (sonst käme nach „MIDI-Clock → eigenes Tempo“ kein Schritt mehr), und das mit dem
  gelatchten Akkord. Wird Latch eingeschaltet, gilt, was in dem Moment gedrückt ist.
- **Anzeige behoben.** `arp_state()` meldete die gerade klingende Note; in der Pause nach dem Gate-off war
  das „keine“, also „Waiting for keys“. Jetzt meldet es die Note, bei der das Arpeggio steht, solange es
  läuft; bei MIDI-Clock nur, solange die Clock ankommt.
- **Zwei weitere Funde der Durchsicht, behoben:**
  - `rate` mit einem Wert, der kein Text ist (Liste, Objekt), endete in einem `TypeError`: über
    `POST /api/arp` hätte das die Hauptschleife beendet, in `controls.json` den Start, in der
    Einstellungsdatei des Pi die Prüfung statt des sicheren Modus. Dieselbe Art Fehler wie am 07.10. bei
    `/api/synth`.
  - Nach einem Presetwechsel bei laufendem Arpeggio wurde die erste Note doch im Terminal gedruckt.
- Geprüft und so belassen: Der erste Ton nach Start der MIDI-Clock bekommt seine Gate-Länge aus dem
  eingestellten Tempo, weil die Clock noch nicht gemessen ist. Ein Start ohne vorheriges Stop setzt das
  Muster nicht zurück.
- 7 neue Tests in `tests/test_arp.py` (jetzt 34), dazu neue Fälle in zwei vorhandenen. Die Tests laufen
  hier mit Python 3.9.6; der LED-Test, der in der Cloud-Umgebung fehlschlug, besteht.

## Step-Sequencer, 2026-10-08 abends (zweiter Teil von „Erst Arp, dann Step“; ohne Pedal)

**Die Auslegung ist nicht mit dem Nutzer abgestimmt.** Er hatte nur „dann Step“ gesagt; gebaut ist die bei
Monosynths übliche Form. Was davon nicht passt, lässt sich ändern.

- **Aufbau:** `sequencer.py` enthält das Muster, seine Schreibweise als Text und die Schrittlogik, wie
  `arpeggiator.py` ohne Zeit. Der Sequencer läuft auf der Uhr des Arpeggiators (`Session._arp_step` fragt
  jetzt `_next_note`, das je nach Schalter den Arpeggiator oder den Sequencer fragt) und benutzt dessen
  Einstellungen Rate, Gate, Clock, Tempo und Latch. Es spielt immer nur einer von beiden; den einen
  einzuschalten schaltet den anderen aus.
- **Muster:** bis zu 64 Schritte, jeder eine Note, eine Pause (`rest`) oder ein Haltebogen (`tie`: der Ton
  davor klingt einen Schritt länger, unabhängig vom Gate). Ein Haltebogen am Anfang bindet den letzten Ton
  des Durchlaufs davor.
- **Spielen:** Die gehaltene Taste spielt das Muster und bestimmt die Tonhöhe: Die erste Note des Musters
  klingt auf der Taste, die übrigen im selben Abstand. Die Taste, mit der das Muster beginnt, spielt es also
  wie aufgenommen. Eine andere Taste verschiebt es im Lauf, ohne Neustart. Loslassen hält an, die nächste
  Taste beginnt beim ersten Schritt. Mit Latch läuft es nach dem Loslassen von der zuletzt gedrückten Taste
  weiter. Töne, die durch das Verschieben außerhalb von `low_note`–`high_note` lägen, werden zu Pausen.
- **Aufnehmen:** „Record“ in der Oberfläche, dann die Töne nacheinander spielen (sie klingen dabei wie
  gewohnt, Arpeggiator und Sequencer sind so lange aus). „Rest“, „Tie“ und „Back“ in der Oberfläche, „Stop“
  übernimmt, „Cancel“ verwirft. Einschalten während der Aufnahme übernimmt sie ebenfalls. Ohne gespielte
  Note bleibt das alte Muster. Der 64. Schritt beendet die Aufnahme.
- **Oder als Text** im Feld „Pattern“: Notennamen (C4 = 60), `-` Pause, `_` Haltebogen, etwa
  `C3 E3 - G3 _ C4`. Gespeichert wird aber eine Liste aus Notennummern, `rest` und `tie`: Die Prüfung der
  Einstellungsdatei hält vier zweistellige Hex-Zahlen hintereinander für rohe Bytes, und „C3 E3 D3 A3“ sind
  genau das.
- **Speicher:** `controls.json` unter `seq` (`{"steps": […]}`), nur wenn es ein Muster gibt. `export.py`
  nimmt es mit, `config_schema.py` prüft es. Auf dem Pi lässt es sich schalten und spielen, aber nicht
  ändern oder aufnehmen. Ein/Aus ist wie beim Arpeggiator kein gespeicherter Wert; anlernbarer Controller
  „Seq“.
- **Nebenbei:** `note_name` liegt jetzt in `sequencer.py` (dort wird es auch für den Text gebraucht);
  `bridge.note_name` gibt es weiter. `read_arp` und `read_seq` teilen sich das Lesen der Datei.
- 28 Tests in `tests/test_seq.py`. Die Oberfläche wurde im Browser gegen die simulierten Geräte bedient
  (Abspielen, Aufnehmen, Rest/Tie/Back, Text mit und ohne Fehler, Esc, Wechsel zum Arpeggiator), keine
  Skriptfehler.
- **Am Pedal noch zu prüfen:** alles hiervon, zusammen mit dem Arpeggiator. Besonders Haltebögen (bleibt
  der Ton über die Schrittgrenze stehen?) und das Verschieben im Lauf mit KeySynths Glide.
- **Nicht gebaut:** Pausen und Haltebögen vom Keyboard aus (nur in der Oberfläche), mehrere Muster,
  Schrittlängen oder Swing, eigene Rate für den Sequencer.

## Erster Lauf von Arpeggiator und Sequencer am Pedal, 2026-10-08 21:13–21:21 Uhr

Ausgewertet aus `logs/sysex.log` bis 21:21:33; die Bridge lief beim Auswerten noch. **Ein Höreindruck des
Nutzers liegt noch nicht vor**, die Zeiten sind Sendezeiten der Bridge, nicht der Klang.

- **Start aus dem GitHub-Klon** (`~/Desktop/GITHUB/MS Express/MS Express`, einem
  Arbeitszweig). Zuerst ging er nicht: Im Klon fehlten `.venv` (`No module named 'mido'`) und
  die Dateien der Installation (`config.yaml is missing`). Jetzt dort: eigene `.venv`, und `config.yaml`,
  `approvals.json`, `measurements.json`, `controls.json` als Kopie aus `~/Desktop/zoom-expression-bridge`.
  Das Repository hat keine `.gitignore`; die Dateien stehen im Klon in `.git/info/exclude`. Die alte
  Installation ist unverändert und hat weiter den Stand vom 07.10.
- KeySynth wurde nach dem Start in Preset 051 eingefügt und sofort gefunden.
- **Arpeggiator, eigenes Tempo, 1/16 bei 120 BPM, Gate 50:** In den ersten drei Strecken (38,8 s, 4,2 s,
  2,3 s) 368 Key-Nachrichten, 368 bestätigt. Note zu Note im Median 125,0 ms, alle regulären Schritte
  zwischen 123 und 127 ms (Streuung 0,7–0,9 ms); Gate-off 62–67 ms nach der Note; Ack nach 9–10 ms.
- **Sequencer:** 13 Schritte vom Keyboard aufgenommen (nur Noten, keine Pause, kein Haltebogen), 13,7 s
  gespielt: 70 Noten und 70 Gate-offs, alle 140 bestätigt, Schritte 124–126 ms. Im Lauf mit anderen Tasten
  um 2 und um 3 Halbtöne nach unten verschoben; das Muster lief dabei an seiner Stelle weiter.
- **Einstellungen im Lauf geändert:** In der vierten Arpeggiator-Strecke (114,7 s) wechselte die Rate
  zwischen 1/16, 1/8T, 1/16T und 1/32; das Arpeggio lief dabei weiter. Das ist die Korrektur von heute
  Abend, jetzt am Gerät.
- **Ein/Aus per Controller:** Regler mit CC 14 des A61 angelernt und benutzt.
- **Mehrere Parameter zugleich** (offener Punkt vom Nachmittag): Das Chocolate wurde im Lauf angesteckt
  (21:16:51, von selbst verbunden), Preset 051, Effekt 2, Parameter 1 gelernt. Während das Arpeggio lief,
  gingen 752 Expression-Werte und 139 Mod-Wheel-Werte (Vib) hinaus, alle bestätigt. 18-mal lagen eine Note
  und ein Wert für einen anderen Parameter höchstens 3 ms auseinander: beide jedes Mal bestätigt. Meiste
  Nachrichten in einer Sekunde: 57 (21:20:07: 10 Key, 18 Expression, 29 Mod-Wheel), ohne Ausfall. Für
  KeyPoly heißt das: Writes an verschiedene Parameter kurz nacheinander nimmt das Pedal an; vier fast
  gleichzeitige sind damit noch nicht belegt.
- **Eine Warnung** („does not confirm notes“, 21:19:30): Zweimal bestätigte das Pedal rund 0,25 s lang
  nichts (21:19:30,32–30,57 und 21:19:31,13–31,40; 14 Zeilen „no ack for Key“). Beide Male meldete es
  dabei selbst `64 20 00 64 01 01` bzw. `… 01 00`. Deutung, nicht belegt: am Pedal zum zweiten Effekt
  geblättert und zurück; dieselbe Meldung stand am 07.10. als „unbekannt“ in diesen Notizen. Die Bridge hat
  die Noten wiederholt, einmal edit enable und die Patch-Abfrage geschickt und lief danach normal weiter.
- **Am Pedal noch nicht geprüft:** Pausen und Haltebögen im Muster, Latch, MIDI-Clock, 1/32 über längere
  Zeit, KeyPoly, und alles am Pi.

## Zweiter Lauf am Pedal: Pausen, Haltebögen, Gate 100, 1/32, 2026-10-08 21:49–21:54 Uhr

Der Nutzer hat nach einer Liste getestet, ausgewertet aus `logs/sysex.log`. KeySynth war dabei Effekt 3.
**Sein Höreindruck steht noch aus**, ebenso, ob er Latch probiert hat (Tasten stehen nicht im Log).

- **Haltebögen und Pausen im geschriebenen Muster** (Sequencer 21:50:23–21:51:09, 1/8T bei 120 BPM, Gate
  50): Ein Ton mit einem Haltebogen klang 250–253 ms (ein Schritt von 166,7 ms plus Gate), mit zwei
  Haltebögen 416–418 ms, jeweils ohne neue Nachricht dazwischen; nach Ton und Pause kam der nächste Ton
  nach 333 ms. Loslassen hielt an, die nächste Taste begann beim ersten Schritt, auch eine Oktave tiefer.
  177 Nachrichten, alle bestätigt.
- **Aufgenommenes Muster mit „Rest“ und „Tie“** (21:54:04, 11 Schritte: `G2 - C3 C3 _ D#3 - G2 G2 D#3 _`):
  von C2 und C3 aus gespielt, bei 1/32 und 200 BPM und bei 1/8T und 120 BPM. Die Schrittfolge im Log
  stimmt mit dem Muster überein. 71 Nachrichten, alle bestätigt.
- **Gate 100** (Arpeggiator 21:52:21 und 21:52:37): Töne ohne Gate-off dazwischen, wie vorgesehen.
  **Folge für den Sequencer:** Bei Gate 100 verschmelzen zwei gleiche Töne hintereinander (`C3 C3`) zu
  einem langen, wie ein Haltebogen. Dem Nutzer genannt; ob das so bleiben soll, ist offen.
- **Last** (Arpeggiator 21:53:05–21:53:47): 1/32 bei 200 BPM, Gate 100, 1114 Noten in 42,5 s, alle
  bestätigt. Abstand im Median 37,5 ms; fünfmal kam ein Schritt rund 5 ms zu spät und der nächste
  entsprechend früher (31–44 ms). Mit Gate 50 (doppelt so viele Nachrichten) ist 1/32 bei 200 BPM noch
  nicht gelaufen.
- **Einstellungen im Lauf** hat er diesmal nicht geändert (im ersten Lauf schon), Latch ist im Log nicht
  zu erkennen. **Nutzer danach: „Latch funktioniert.“**
- **Vorfall 21:49:** Um 21:27–21:28 beantwortete das Pedal viele Dateizugriffe (`60 03`, `60 04`, `60 05`),
  die nicht von der Bridge kamen; ein anderes Programm hat also mit dem Pedal gesprochen. Danach meldete
  das Pedal 21 Minuten lang nichts mehr, auch nicht, dass KeySynth nicht mehr Effekt 1 war. Deutung: Der
  Edit-Modus war seitdem aus. Die ersten zwei Noten um 21:49:28 blieben unbestätigt; nach 0,2 s hat die
  Bridge edit enable und die Patch-Abfrage geschickt und „No KeySynth effect“ gemeldet. Danach kamen die
  Meldungen des Pedals wieder von selbst (KeySynth als Effekt 2, nach Presetwechsel weg, dann Effekt 3).
  Mögliche Verbesserung, nicht gebaut: bei fremden `60 …`-Antworten von selbst edit enable und die
  Patch-Abfrage nachschicken, statt auf die ersten unbestätigten Noten zu warten.
- **Schutz am Gerät gesehen:** Als KeySynth kurz Effekt 2 war, zeigte die Expression-Zuordnung von Preset
  051 (Effekt 2, Parameter 1) auf seinen Key-Regler; die Bridge hat sie gesperrt und nichts gesendet.

## Oberfläche und offene Wünsche, 2026-10-08 abends

- **Lampe:** grün bei „Ready“, sonst rot (vorher rot bei „Ready“, sonst dunkel). Wunsch des Nutzers.
- **Beschreibungen eingeklappt:** Jeder Abschnitt hat statt des langen Textes eine Zeile „Help“, die eine
  kurze Liste aufklappt (`<details class="help">`). Wunsch des Nutzers: einklappen und kürzen.
- **Wunsch, noch nicht gebaut:** Pedal, Keyboard und Expression-Controller in der Oberfläche auswählen.
  Dazu die Frage nach zwei MS-Pedalen. Stand heute: Auswahl nur über Namensteile in `config.yaml`; zwei
  Pedale mit gleichem Portnamen kann `find_port` nicht trennen (gleiche Namen: immer das erste; „… 2“:
  kein eindeutiger Treffer). Vorschlag an den Nutzer: zwei Bridges mit je eigenem Profil (eigene
  Einstellungsdateien, eigener Port der Oberfläche) und Auswahl über den exakten Portnamen; eine Bridge
  für zwei Pedale wäre ein größerer Umbau.
- **Antwort des Nutzers (22:05 Uhr):** Das zweite Pedal wäre ein MS-70CDR+, er hat es noch nicht. Jedes
  Pedal soll seine eigenen Controller für Keyboard und Expression bekommen, gegebenenfalls ein Controller
  für beide Pedale. Das passt zu zwei Bridges mit je eigenem Profil; ein gemeinsamer Controller heißt dann,
  dass beide Bridges denselben Eingang lesen.

## Dritter Lauf am Pedal: Last mit Gate 50, Einstellungen im Lauf, 2026-10-08 22:02–22:11 Uhr

Ausgewertet aus `logs/sysex.log` (rotiert bei 5 MB; die Auswertung liest deshalb nach Zeit, nicht nach
Zeilennummern). KeySynth Effekt 3. Nutzer: „Latch funktioniert“, „Test fertig“; ein Höreindruck zum
Lasttest liegt nicht vor.

- **Last:** Arpeggiator 1/32 bei 200 BPM, Gate 50, also alle 37,5 ms eine Note und ein Gate-off: in drei
  Strecken (65 s, 107 s, 68 s) 10 935 Key-Nachrichten, bis zu 55 Nachrichten pro Sekunde. 10 881 einzeln
  bestätigt, Ack im Median nach 3 ms, 99 % innerhalb von 9–19 ms. Die übrigen 54 (47 überholt, 7 „no ack“)
  fallen in die beiden Strecken, in denen der Nutzer am Pedal Regler von KeySynth drehte; in der dritten
  Strecke ohne Reglerdrehen wurden alle 2393 bestätigt.
- **Raster:** 37,5 ms; von rund 5400 Schritten lagen 70 einzeln um etwa 5 ms daneben (einer zu spät, der
  nächste entsprechend früher).
- **Einstellungen im Lauf:** In der dritten Strecke wechselte die Rate zweimal zwischen 1/32 und 1/16, in
  einem durchgehenden Lauf von 1189 Noten ohne Lücke. Am Ende standen Mode Down, Rate 1/4, Tempo 40 und
  Latch an.
- **Eine Warnung** (22:07:22): Während das Pedal Reglerbewegungen meldete (Effekt 3, Parameter 5),
  blieben rund 115 ms lang die Acks aus. Bei 53 Nachrichten pro Sekunde sind drei unbestätigte Noten nach
  rund 60 ms erreicht; die Bridge schickte edit enable und die Patch-Abfrage (985 Bytes Antwort), obwohl
  das Pedal von selbst weitergemacht hätte. Daraufhin geändert, siehe unten.

## Warnschwelle, Geräteauswahl, Profile, 2026-10-08 nachts (nach dem dritten Lauf; ohne Pedal)

- **Warnschwelle:** „does not confirm notes“ samt edit enable und Patch-Abfrage kommt jetzt erst, wenn drei
  Noten unbestätigt sind **und** das Pedal seit 0,2 s keine bestätigt hat (`KEY_SILENCE_SECONDS`, gezählt ab
  dem Senden der ersten unbestätigten). Beim Spielen von Hand ändert das nichts, dort liegen drei Noten
  ohnehin weiter auseinander. Die 115 ms vom Lasttest lösen nichts mehr aus, die 0,25 s vom ersten Lauf
  und der echte Verlust des Edit-Modus um 21:49 weiterhin. Test mit 0,12 s und 0,45 s Schweigen bei 1/32
  und 200 BPM.
- **Geräteauswahl in der Oberfläche** (Wunsch des Nutzers): Abschnitt „Devices“ mit je einer Liste für
  Pedal, Keyboard und Expression-Controller. `POST /api/devices` schreibt die Namen nach `config.yaml`
  (`set_config_values` ändert nur diese Zeilen, Kommentare und der Abschnitt `mappings` bleiben) und baut
  die Verbindung neu auf. Keyboard und Expression-Controller dürfen „None“ sein, nicht beide. Wird zum
  ersten Mal ein Keyboard gewählt, lädt die Bridge dessen Controller, Arpeggiator-Einstellungen und Muster.
  - Als Pedal bietet die Liste nur Ports an, deren Name „ZOOM MS“ enthält. Grund: An diesen Port sendet die
    Bridge, und auf dem Mac des Nutzers hängen auch ein UAC-232 und der IAC-Bus; ein Zoom-Gerät mit
    anderer Geräte-ID würde die Bridge beim Start beenden („Device ID … does not match“). Andere Namen
    gehen weiter über `config.yaml`.
  - Der Expression-Controller behält CC und Kanal des bisherigen; ist noch keiner gemessen, wird die Wahl
    mit Hinweis auf `probe.py cc` abgelehnt.
  - `find_port` nimmt jetzt den Port mit genau dem Namen, sonst wie bisher den einen, der den Text enthält.
- **Profile für eine zweite Bridge** (Antwort auf „zwei MS-Pedale?“): `--profile NAME` bei `bridge.py`,
  `probe.py` und `export.py`; die Dateien liegen dann in `profiles/NAME/` (Einstellungen, Freigaben,
  gelernte Bereiche, Controller, Log). Die Oberfläche nennt das Profil im Titel. Belegt eine andere Bridge
  den Port der Oberfläche, endet der Start mit einem Hinweis auf `ui: port` statt mit einem Traceback.
- **Offen bis das MS-70CDR+ da ist:** wie macOS zwei Pedale benennt. Tragen beide exakt denselben
  Portnamen, öffnet mido immer den ersten; dann braucht es eine Auswahl über die Reihenfolge der Ports
  oder über die Modellkennung aus der Identity-Antwort. Nicht gebaut, weil ohne zweites Pedal nicht
  prüfbar. Ebenfalls ungeprüft: zwei Bridges am selben Keyboard-Eingang.
- **Absturz am Gerät, 22:27:38, behoben.** Der Nutzer hat das Pedal im Betrieb abgezogen: beim ersten Mal
  (22:27:01) „Connection lost: ZOOM MS Plus Series is gone“, 9 s später wieder „Ready“; damit ist das
  Abziehen des MS-60B+ erstmals am Gerät gelaufen. Beim zweiten Mal fiel das Abziehen in die Abfrage der
  Portliste: rtmidi hatte die Ports gezählt, einer verschwand, und die Frage nach seinem Namen warf
  `InvalidPortError` („the 'portNumber' argument (6) is invalid“). Das ist ein `ValueError`, kein `OSError`,
  die Hauptschleife fing ihn nicht, und die Bridge endete mit einem Traceback (edit disable ging vorher
  noch hinaus). Der Fehler steckt seit Phase 3 im Code. Jetzt gelten alle rtmidi-Fehler als verlorene
  Verbindung (`MIDI_ERRORS`), auch beim Schließen der Ports; Test dazu.
- 16 neue Tests (15 in `tests/test_devices.py`, einer in `tests/test_arp.py`), zusammen 210, alle
  bestanden; pyflakes ohne Befund. Abschnitt „Devices“ im Browser gegen die simulierten Geräte bedient
  (wechseln, abwählen, Ablehnung), keine Skriptfehler.
- **Für den Nutzer:** Die Bridge läuft seit dem Absturz nicht mehr; der nächste Start bringt
  Warnschwelle, Geräteauswahl, Profile und die Korrektur.

## Ganzer Export und Fragen zum Pi, 2026-10-09 nachts (Wunsch des Nutzers; ohne Pi)

Stand vorher committet (zwei Commits, `0f89b88` und `ff1c475`), nicht gepusht: Der Nutzer pusht selbst.

- **„Export all“ und „Import…“ mit Keyboard-Teil:** Die JSON-Datei enthält jetzt zusätzlich `keyboard`
  (Controller, Arpeggiator-Einstellungen, Muster des Sequencers), wenn ein Keyboard eingerichtet ist. Der
  Import ersetzt diese drei komplett, schaltet Arpeggiator und Sequencer aus und prüft vorher alles; eine
  Datei nur mit Keyboard-Teil (ohne Zuordnungen) geht. Alte Dateien ohne `keyboard` gelten weiter, der
  Export eines einzelnen Presets bleibt ohne. Format weiter `expression-bridge/1`.
- **Einstellungsdatei für den Pi aus der Oberfläche:** Knopf „Settings file for the Pi“ unter „Devices“
  (`GET /api/settings-file`) liefert dieselbe `expression-bridge.yaml`, die `export.py` schreibt, also alles:
  Geräte, Zuordnungen, gelernte Bereiche, Freigaben, Keyboard-Teil. Unvollständige Einstellungen werden mit
  den Gründen abgelehnt. Das Sammeln liegt jetzt in `bridge.gather_settings` (export.py ruft es auf), damit
  es auch mit einem Profil die richtigen Dateien liest.
- **Reicht ein Raspberry Pi Model B mit 1 GB?** Am Speicher scheitert es nicht: Die laufende Bridge belegt
  auf dem Mac mini rund 20 MB (ps) bis 41 MB (top) und 2–3 % eines Kerns; für Raspberry Pi OS Lite werden
  im Leerlauf 120–250 MB genannt (DietPi-Vergleich 177 MiB, Pi 5: ca. 250 MB). Nicht gemessen: die
  Rechenlast auf einem Pi (die Schleife schläft 1 ms; ein Pi 3 ist pro Kern um ein Vielfaches langsamer)
  und der Jitter dort. Wichtiger als RAM: vier USB-Buchsen, zusammen laut The Pi Hut 1,2 A beim Pi 4, also
  Pedal am eigenen Netzteil; 64-Bit-System; python-rtmidi für ARM. Welches „Model B“ gemeint ist, ist
  offen (2B, 3B, 3B+ und 4B gab es mit 1 GB).
- **Pi an den Rechner stecken und als Laufwerk sehen** (Frage des Nutzers): Das geht nur, wo die
  Strombuchse Datenleitungen hat. Laut den gefundenen Quellen: Pi 4 Model B (USB-C) und Zero ja, Pi 2B/3B/
  3B+ nein, Pi 5 unklar. Selbst beim Pi 4 mit Haken (Kabel, Versorgung über den Rechner, Windows laut
  Adafruit wacklig). Nicht gebaut. Einfacher und auf jedem Modell möglich: USB-Stick mit der
  Einstellungsdatei, den die Bridge beim Start einliest; oder wie geplant die SD-Karte, deren
  Boot-Partition Windows und Mac lesen. Vorschlag an den Nutzer, Entscheidung offen.
- **Zwei Pedale mit gleichem Namen** (Nutzer: das MS-70CDR+ heißt auch „ZOOM MS Plus Series“): Aus Foren
  (JUCE): Unter macOS lassen sich gleichnamige Geräte in „Audio-MIDI-Setup“ umbenennen, danach sind die
  Namen verschieden. Mit mido nicht geprüft. Sauberer wäre, dass jede Bridge ihr Pedal an der
  Modellkennung der Identity-Antwort erkennt (MS-60B+: `27 00`); dafür muss sie gleichnamige Ports einzeln
  öffnen können. Erst mit beiden Pedalen prüfbar.

## Einstellungen per USB-Stick auf den Pi, 2026-10-09 nachts (Wunsch des Nutzers: SD-Karte und Stick; ohne Pi)

- **Beides geht jetzt:** wie bisher die Datei auf der Boot-Partition der SD-Karte (`--settings ORDNER`),
  dazu `--stick ORT`. Liegt auf einem dort eingehängten Stick eine gültige `expression-bridge.yaml`, die
  sich von der im Ordner unterscheidet, übernimmt die Bridge sie in den Ordner (die bisherige wird zur
  `.prev`) und läuft damit; der Stick kann danach ab. Geprüft wird beim Start und alle 2 s im Betrieb
  (`Bridge.news`, `NewSettings`): Ein später eingesteckter Stick startet die Bridge neu, auch aus dem
  sicheren Modus heraus. Eine ungültige Datei wird einmal gemeldet und nicht benutzt. Lässt sich der
  Ordner nicht beschreiben, läuft die Bridge mit der Datei vom Stick, solange er steckt.
- Die Datei kommt aus der Oberfläche („Settings file for the Pi“) oder von `export.py --to <Stick>`.
- **Nicht gebaut und nur am Pi zu klären:** das Einhängen des Sticks (Raspberry Pi OS Lite hängt nichts
  von selbst ein; Vorschlag als Kommentar in `pi/expression-bridge.service`), Schreibrechte auf den
  Einstellungsordner, das Zusammenspiel mit dem schreibgeschützten Dateisystem, ein eigenes LED-Zeichen
  für „übernommen“ (bisher: Blinken „waiting“, dann Dauerlicht).
- 6 neue Tests in `tests/test_headless.py`.
- **Antworten an den Nutzer, nicht gebaut:** Oberfläche auf dem Pi (Monitor, oder vom Mac/Handy über
  Netzwerk bzw. ein eigenes WLAN des Pi; die Oberfläche bleibt eine Webseite, als Vollbild-Fenster ohne
  Browserleiste möglich); zwei gleiche MS-60B+ nur über die USB-Buchse unterscheidbar; AKAI MPC One+ sicher
  über MIDI OUT und ein USB-MIDI-Interface, direkt per USB-B unbestätigt.

## MPC als Taktgeber und Notenquelle, Effekte ein/aus per Controller, 2026-10-09 (Wunsch des Nutzers; ohne Pedal, ohne MPC)

Entscheidung des Nutzers vorab: Raspberry Pi 4B mit 1 GB, WLAN-Steuerung später, wenn der Pi da ist.
Gewünscht: „MPC als Taktgeber“, „MPC-Sequenzen auf den KeySynth“, „zuweisbare MIDI CC für die einzelnen
Effekte in der Kette, zum Ein- und Ausschalten“.

- **Externer Sequencer (`synth.external`, `synth.external_channel`):** ein zweiter Eingang neben dem
  Keyboard, wählbar unter „Devices“. Seine Noten spielen den Synth-Effekt wie Tasten (gemeinsame
  Tastenliste: letzte Note gewinnt, egal von wem), auf eigenem Kanal (0 = alle), damit die MPC andere
  Spuren über dasselbe Kabel an andere Geräte schicken kann. Seine MIDI-Clock ist die maßgebliche: Ist ein
  externer Sequencer eingerichtet, wird die Clock am Keyboard-Port ignoriert, sonst gilt sie wie bisher.
  Er reicht allein (ohne Keyboard), darf im Lauf kommen und gehen; geht er oder das Keyboard, werden alle
  Noten losgelassen. Eine `config.yaml` von vor dieser Änderung bekommt die Zeile `external:` beim ersten
  Auswählen ans Ende von `synth:` gesetzt (`set_config_values(..., add=…)`), der Nutzer muss nichts von
  Hand eintragen.
- **Effekte ein/aus (`effect_switch.py`, Abschnitt „Effects on / off“):** pro Platz 1–6 der Kette ein
  anlernbarer Controller, von jedem Controller-Port (Expression-Controller, Keyboard, externer Sequencer).
  Zwei Arten: „Value switches“ (ab 64 ein, darunter aus; für Schalter, die selbst umschalten) und „Every
  press switches“ (jeder Druck schaltet um; für Taster, die nur beim Drücken senden). Die Oberfläche zeigt
  den Zustand je Effekt und schaltet per Klick. Gespeichert in `controls.json` unter `effects`, im
  JSON-Export („Export all“) und in der Einstellungsdatei für den Pi.
- **Neue Nachrichtenart `switch_effect`:** `F0 52 00 <ID> 64 20 00 <slot> 00 <0|1> 00 00 00 00 F7`, also
  „set parameter“ mit Parameter 0. Bisher war Parameter 0 ausnahmslos gesperrt; jetzt gibt es dafür einen
  eigenen Builder (`build_switch_effect`), eine eigene Freigabe und eine eigene Regel in der Allowlist: nur
  Wert 0 oder 1, nur Slots 0–5, und nur Slots, die das Pedal im Patch-Dump des aktuellen Presets selbst
  gemeldet hat (`ZoomSender.switchable`). Parameter 1 (Effekttyp) bleibt gesperrt. Deshalb braucht das
  Schalten auch `query_patch`; ohne beide Freigaben wird nichts gesendet und die Bridge sagt es.
- **Zustand:** aus dem Patch-Dump (Bit „an/aus“, an fünf echten Dumps geprüft, siehe 2026-10-07), nach
  jedem Presetwechsel neu gelesen; bis der Dump da ist, wird nicht geschaltet. Meldet das Pedal selbst
  `64 20 00 <slot> 00 <wert>`, folgt die Bridge („was switched … on the pedal“). Bleibt die Bestätigung
  aus: einmal wiederholen, dann Warnung, Edit-Modus neu einschalten und Preset neu lesen.
- **Nach dem Anlernen** wird der gelernte Controller eine Sekunde lang nicht befolgt, sonst würde das
  Loslassen des Tasters (Wert 0) den Effekt sofort ausschalten.
- **NICHT AM PEDAL GEMESSEN, alles Annahme aus dem zoom-explorer-Quelltext:** dass das MS-60B+ Parameter 0
  mit 0/1 annimmt, dass es wie bei anderen Parametern nur Änderungen bestätigt, dass es den eigenen
  Fußschalter als `64 20 00 <slot> 00 …` meldet (oder stattdessen einen Dump `64 12 00` schickt, den die
  Bridge ebenfalls liest), und wie es auf einen Slot reagiert, den das Preset nicht hat (wird deshalb nie
  gesendet). Das simulierte Pedal verhält sich nach der Annahme. Erster Versuch am Gerät: mit gesichertem
  Preset, Knopf in der Oberfläche, Log mitlesen.
- **Nicht mit einer MPC gelaufen.** Offen bleibt auch, wie die MPC One+ an den Rechner kommt (MIDI OUT über
  ein USB-MIDI-Interface sicher, USB-B im Standalone-Betrieb unbestätigt) und ob sie beim Stop „All Notes
  Off“ schickt (wird befolgt, falls ja).
- Der Chocolate Plus: Ob seine Fußschalter als CC mit Umschalten oder nur beim Drücken senden, hängt von
  der Einstellung in CubeSuite ab; nicht geprüft. Dafür gibt es die zwei Arten.
- 28 neue Tests (`tests/test_effects.py` 18, `tests/test_external.py` 9, einer in `tests/test_sysex.py`),
  zusammen 250, alle bestanden (302 s); pyflakes ohne Befund. Oberfläche im Browser gegen die simulierten
  Geräte bedient (schalten, anlernen, Umschaltart, externer Sequencer in „Devices“), keine Skriptfehler;
  auf Handy-Breite liefen die Zeilen der Effekte über den Rand, sie brechen jetzt um.
- **Freigabe:** Der Nutzer hat am 2026-10-09 um 03:49 Uhr `switch_effect` freigegeben
  (`probe.py approve switch_effect query_patch`). Der erste Lauf am Pedal steht aus.

## Erster Lauf der Effekt-Schalter am Pedal, 2026-10-09 06:42–06:44 Uhr

Bridge vom Nutzer gestartet (`Ready: KOMPLETE KONTROL A61 → ZOOM MS Plus Series`, Chocolate nicht
angesteckt), ich habe im Log mitgelesen. Erst Preset 051, dann nach Durchblättern Preset 098
(„STILLBIRTH“, sechs Effekte, kein KeySynth).

- **Die Nachricht wird angenommen und bestätigt.** 44 `switch_effect` gesendet, 44 bestätigt
  (`64 20 01 <slot> 00 <0|1>`), Latenz Senden→Ack im Median 23 ms (21–45 ms), also langsamer als bei
  Reglern (4–11 ms). Alle sechs Slots dabei (Effekt 1 und 2 je 14-mal, 3–6 je 4-mal). Davon 36 über den
  Knopf der Oberfläche, darunter acht Umschaltungen von Effekt 1 im Abstand von 160–255 ms ohne Ausfall,
  und 8 über den angelernten Regler.
- **Das Pedal meldet seinen eigenen Fußschalter** als `64 20 00 <slot> 00 <0|1>` (sechsmal, Effekt 1 und
  2); die Bridge ist jedes Mal gefolgt. Davor kam `64 20 00 64 01 01`, als der Nutzer am Pedal zu Effekt 2
  wechselte: passt zur Vermutung „64 01 = gewählter Effekt“. Unaufgeforderte Dumps (`64 12 00`) kamen in
  diesem Lauf keine.
- **Anlernen:** „Learn“ bei Effekt 2, Regler CC 14 am A61 → gelernt um 06:43:11, 1,2 s später (nach der
  Ruhesekunde) schaltete derselbe Regler aus, danach mehrfach ein/aus („Value switches“). CC 14 war bis
  dahin „Arp On/Off“; diese Zuordnung ist dadurch entfallen (ein Controller, eine Aufgabe).
- **Dump und Zustand stimmen überein:** Der letzte Patch-Dump (06:44:29, vermutlich ausgelöst durch eine
  Taste am Keyboard ohne KeySynth im Preset) zeigt alle sechs Effekte an, wie die Bridge sie führte. Ein
  Dump mit einem ausgeschalteten Effekt wurde in diesem Lauf nicht gelesen.
- Durchblättern von 48 Presets in gut fünf Sekunden ohne Störung; im ganzen Lauf 12 Patch-Abfragen.
- **Nicht gelaufen:** Fußschalter des Chocolate Plus, „Every press switches“, ein Preset mit weniger als
  sechs Effekten (Meldung „has no effect N“), der Weg ohne Bestätigung (Wiederholung, Warnung), und ob das
  Pedal schweigt, wenn man ihm den Zustand schickt, den es schon hat.
- **Am Pedal gesehen:** Der Nutzer bestätigt, dass die Effekte am Pedal sichtbar mitgeschaltet haben.
  Angekündigt von ihm: Test mit dem Chocolate Plus, mit weniger Effekten und mit der MPC.
- Danach: Meldung umformuliert („Effect 1 off, switched on the pedal“ statt „was switched off on the
  pedal“), README um die Messwerte ergänzt.

## Szenen, 2026-10-09 vormittags (Wunsch des Nutzers: „Parameter per Knopfdruck von 100 auf 30, so ähnlich wie Scenes“; ohne Pedal)

Vorschlag vorab abgestimmt („Ja versuchen wirs mal“): mehrere Szenen pro Preset, sofortiger Sprung.

- **`scenes.py`, Abschnitt „Scenes for preset …“:** Eine Szene gehört zum Preset, das am Pedal gewählt
  ist, und hält bis zu acht Parameterwerte plus Effekte ein/aus; bis zu acht Szenen pro Preset. Abruf über
  den Knopf mit ihrem Namen oder einen angelernten Controller (jeder Druck ab Wert 64; Loslassen tut
  nichts). Derselbe CC darf in einem anderen Preset eine andere Szene abrufen.
- **Werte kommen vom Pedal („Capture“):** Regler am Pedal drehen, Effekte schalten, „Apply“. Übernommen
  wird der letzte gemeldete Wert jedes bewegten Reglers; alle gemeldeten Werte gelten wie bei „Learn
  parameter“ als gelernt. Keine neue Nachrichtenart: gesendet wird `set_param` (und `switch_effect`) unter
  den bisherigen Regeln. Ein Szenenwert außerhalb des Gelernten wird nicht gesendet (einmalige Meldung),
  Regler des Synth-Effekts nie.
- **Senden:** über dieselbe Warteschlange wie das Expression-Pedal, ein Wert pro Mindestabstand. Liegt ein
  Parameter auch auf dem Expression-Pedal, gilt in der Allowlist der weitere der beiden Bereiche; die
  nächste Pedalbewegung überschreibt den Szenenwert.
- **Fehlende Acks zählen bei Szenen nicht als Störung:** Das Pedal bestätigt nur Änderungen; eine Szene,
  deren Werte schon stehen, bliebe sonst unbestätigt und würde die Drosselung auslösen.
- In „Export all“, Import und Einstellungsdatei für den Pi (`scenes`); mit Effekt-Schaltern in einer Szene
  verlangt die Einstellungsdatei die Freigabe `switch_effect`.
- **Nicht am Pedal gelaufen.** Offen dort: ob mehrere Werte im 10-ms-Abstand an verschiedene Parameter alle
  ankommen (gemessen sind bisher zwei Nachrichten an verschiedene Parameter im Abstand von 3 ms).

## Erster Lauf der Szenen am Pedal, 2026-10-09 22:20–22:31 Uhr

Nutzer hat getestet (Preset 091, Chocolate und A61 angesteckt), ich habe im Log mitgelesen.

- **Szenen funktionieren am Pedal.** Drei Szenen angelegt („lo“, „lp“, „ji“), per „Capture“ gefüllt (bis zu
  acht Werte über drei Effekte, dazu bis zu fünf Effekt-Schalter), 28-mal abgerufen: per Knopf in der
  Oberfläche und über angelernte Controller (CC 20, 21, 22).
- 116 Nachrichten (`set_param` und `switch_effect`), 108 bestätigt. Die 8 unbestätigten sind zwei Abrufe
  direkt nach dem Capture, bei denen das Pedal die vier Werte schon hielt (es bestätigt nur Änderungen);
  als „no ack for value … (scene)“ protokolliert, ohne Warnung und ohne Drosselung, wie vorgesehen.
- **Vier Werte im 11-ms-Abstand an verschiedene Parameter desselben Effekts kamen alle an** (Ack je nach
  9–35 ms); eine Szene mit vier Werten steht nach etwa 70 ms.
- Effekte während „Capture“ am Pedal geschaltet → in der Szene gespeichert und beim Abruf geschaltet.
- Keine Warnung, kein Abbruch. Der Nutzer hat nicht gesagt, was er am Pedal gesehen hat.
- **MPC One+ per USB am Mac:** wird nicht erkannt (kein MIDI-Port, kein USB-Gerät in der Systemübersicht).
  Vorschlag: MIDI OUT der MPC per 5-Pol-Kabel in den MIDI-Eingang des vorhandenen ZOOM UAC-232.

## Oberfläche kompakter, 2026-10-10 (Wünsche des Nutzers)

Wünsche: überall die Regler-Symbole weg; Keyboard-Synth nur Name, „assigned/not assigned“ und ein
Learn/Clear-Knopf; „Expression pedal“ und „Expression assignments per preset“ zusammenlegen, gern mit den
Szenen; bei „Effects on / off“ die bunten Lichter weg; „Devices“ ganz nach oben und kleiner; insgesamt
kompakter, nicht breiter, Menüpunkte ggf. einklappbar („Nicht jeder braucht alles“).

- **Jeder Bereich klappt ein** (`details.panel`): Titel und eine Statuszeile bleiben sichtbar, der
  Zustand wird im Browser gemerkt (`localStorage`). Beim ersten Öffnen sind nur „Expression pedal &
  scenes“ und „Activity“ offen. Reihenfolge: Devices, Keyboard synth, Arpeggiator, Step sequencer,
  Expression pedal & scenes, Effects on / off, Activity. Arpeggiator und Step-Sequencer sind eigene Bereiche.
- **Zusammengelegt:** Preset-Anzeige, Pedalwert, „Learn parameter“, Szenen des Presets und die Zuordnungen
  aller Presets in einem Bereich, mit einer gemeinsamen Hilfe.
- **Zeilen:** keine Regler-Symbole und keine Lampen mehr (die Lampe neben „Ready“ bleibt). Keyboard-Synth:
  Name, „assigned“/„not assigned“ (die CC-Nummer steht im Tooltip), ein Knopf, der „Learn“ oder – sobald
  ein Controller zugewiesen ist – „Clear“ heißt. Derselbe eine Knopf bei Effekten und Szenen; neu anlernen
  heißt dort jetzt: erst „Clear“, dann „Learn“. Effekte in zwei Spalten, die Umschaltart heißt kurz
  „Value“/„Press“. Keyboard-Synth-Zeilen in vier Spalten, Geräte in einer Zeile.
- Schrift, Abstände und Knöpfe kleiner; Breite unverändert 900 px. Auf Handy-Breite kein Überlauf.
- Im Browser gegen die simulierten Geräte bedient, keine Skriptfehler. Nur `ui.html` geändert.
- **MPC One+ am Mac:** Nach Installation des Akai-Treibers erscheinen die Ports „Akai Network - MIDI“ und
  „Akai Network - DAW Control“; in 12 s Mithören kam auf beiden nichts an. Laut Akai-Support hat von den
  bisherigen MPCs (Live, Live II, One, One+, X, Key 61) nur das Key 37 „Direct USB-MIDI“ im
  Standalone-Betrieb; die One+ schickt also standalone kein MIDI über USB an den Rechner. Weg: MIDI OUT der
  MPC per 5-Pol-Kabel in den MIDI-Eingang des ZOOM UAC-232.

## Alle vier Key-Effekte, 2026-10-11

Meldung des Nutzers: „Alle ‚Key‘ Effekte bis auf KeySynth reagieren nicht auf den Keyboard Controller.“

- **Ursache** (aus `logs/sysex.log`, 08:16–08:24 Uhr, und seinem Pedal-Backup von 07:57 Uhr): Auf dem Pedal
  liegen KeySynF als `09000F71`, KeyFM als `09000F72` und KeyPoly als `09000F73`, alle in der Kategorie SFX
  (sein Installer setzt das oberste Byte der ID auf die gewählte Kategorie; gebaut sind sie als `07000F7x`).
  Die Bridge kannte nur die eine ID aus `effect_id: 0x07000F61` und meldete bei allen anderen „No KeySynth
  effect in this preset – the keyboard is ignored“. Im Log zu sehen: bei `07000F61` „KeySynth is effect 1“,
  einen Effekt weiter (`07000F62`, das ist OctSynth, kein Key-Effekt) wieder „No KeySynth effect“.
- **Änderung:** Die Bridge kennt jetzt vier Synth-Effekte an den **unteren drei Bytes der ID**, in jeder
  Kategorie: KeySynth `…0F61`, KeySynF `…0F71`, KeyFM `…0F72`, KeyPoly `…0F73` (`zs.KEY_EFFECTS`,
  `zs.key_effect`). In `config.yaml` muss dafür nichts stehen; `effect_id` und `poly_effect_id` gelten nur
  noch für einen Build mit eigener ID und werden abgelehnt, wenn sie die ID eines der anderen Key-Effekte sind.
  Aus der Beispielkonfiguration sind die beiden Zeilen gestrichen. Die Zeile `effect_id: 0x07000F61` in der
  `config.yaml` des Nutzers kann stehen bleiben.
- **Regler** aus den Manifesten (KeySynF 0.10, KeyFM 0.10, KeyPoly 0.11) in `zoom_sysex.py`: KeySynF Wave 0–9,
  Cutoff, Reso, FEnv, FDcy, Atk, Rel, Glide, LFO, Rate; KeyFM Ratio 0–15, Index, MEnv, MDcy, Fdbk, Atk, Rel,
  Glide, LFO, Rate. Die bisher nur angenommenen Bereiche von KeyPoly stimmen mit seinem Manifest überein.
  Bei KeySynF und KeyPoly ist die obere Hälfte des LFO-Reglers der Filter (Flt1 … Flt50) und nicht das
  Tremolo: in der Liste heißt sie dort „Flt“ statt „Trm“. Ein Controller auf einem Namen, den mehrere Effekte
  haben, stellt den Regler dessen, der im Preset ist („Wave“: zehn Stellungen bei KeySynF, vier bei KeyPoly).
- Meldung ohne Effekt jetzt: „No KeySynth, KeySynF, KeyFM or KeyPoly in this preset – the keyboard is
  ignored“; in der Oberfläche „no synth effect in this preset“.
- **Verwechslungsgefahr geprüft:** Unter den 125 Effektdateien seines Backups endet keine andere ID auf eine
  der vier Nummern (doppelt kommen nur die Nummern der Zoom-Effekte `…000010`–`…0000D0` vor).
- Keine neue Nachrichtenart: Noten gehen wie bisher als `set_param` an die Key-Regler (Parameter 2, bei
  KeyPoly 2–5), nur wenn der Patch-Dump des Pedals einen der vier Effekte an dem Platz zeigt.
- **Nicht am Pedal gelaufen:** KeySynF, KeyFM und KeyPoly über die Bridge. Offen dort: ob sie auf die
  Key-Werte so reagieren wie KeySynth (laut Manifest dasselbe Format), die vier Stimmen von KeyPoly, die
  Regler-Zuordnungen.

## Keyboard synth: Zuweisungen pro Synth, kompakter Block, 2026-10-17

Wunsch des Nutzers (nach dem Lauf der vier Key-Effekte, „Soweit läuft die Bridge“): Im Block „Keyboard
Synth“ fehlen durch die neuen Synths Parameter, und er soll kompakter werden: „DropDown Menü mit den
jeweiligen Parametern und dann assignen?“ (Bisher standen dort nur die Regler des Effekts im Preset, als
eine Zeile je Regler; die der anderen Synths ließen sich nicht zuweisen.)

- **Oberfläche:** zwei Listen, „Synth“ (KeySynth, KeySynF, KeyFM, KeyPoly, mit „in this preset“ und der
  Zahl der Zuweisungen) und „Parameter“ (dessen Regler, zugewiesene mit „· CC n“ bzw. „· Mod wheel“),
  daneben ein Knopf Learn / Clear / Cancel. Darunter die Zuweisungen des gewählten Synths als kleine
  Schilder mit ×. Bei einem Preset-Wechsel springt die Synth-Liste auf den Effekt des Presets; sonst bleibt
  die Wahl. Die Zeilen für Arp und Seq bleiben, wie sie waren.
- **Zuweisungen gelten pro Synth** (neues Modul `synth_controls.py`), gespeichert als `"KeyFM/Index": 21`.
  Ein Controller stellt je Synth einen Parameter; derselbe darf bei KeySynF Cutoff und bei KeyFM Index
  stellen. Arp/Seq, Effekt-Schalter und Szenen behalten ihren Controller für sich allein. Das ist meine
  Auslegung von „jeweiligen Parametern“; vorher galt ein Name für alle Synths, die ihn haben.
- **Alte Dateien** (`"Vib": 1`, auch in Exporten und Einstellungsdateien für den Pi) werden beim Lesen auf
  jeden Synth verteilt, der den Regler hat; die `controls.json` des Nutzers mit `"Vib": 1` wird so zu Vib
  auf CC 1 bei allen vier.
- Geprüft gegen die simulierten Geräte im Browser: Learn → Cancel mit Hinweis → gelernt, Clear über ×,
  Wechsel des Synths, Preset-Wechsel, Handy-Breite ohne Überlauf, keine Skriptfehler.
- Nichts am Nachrichtenweg zum Pedal geändert; neu ist nur, welcher Regler ein Controller stellt.

## Durchsicht des Codes und Raspberry-Pi-Version, 2026-10-17 (Auftrag: „Optimiere den Code nun und analysiere ihn nach Fehlern. Fertige dann die Raspberry Pi Version. Beachte, auf dem Raspberry Pi bzw. SD Karte ist noch garnichts drauf.“)

### Gefundene und behobene Fehler

- **Eine fehlerhafte Anfrage aus der Oberfläche beendete die ganze Bridge.** `_run_commands` fing nur
  `UserError`; jede andere Ausnahme lief aus der Hauptschleife und stoppte alles. Konkret auslösbar mit
  „drop“ einer Szene, wenn `slot` eine Liste war (unhashbar → `TypeError`). Jetzt: `drop` prüft auf Zahlen,
  und jede unerwartete Ausnahme einer Anfrage wird mit Traceback ins Log geschrieben, als „ERROR in a request
  …“ gemeldet und der Oberfläche als Fehler zurückgegeben; die Bridge läuft weiter.
- **Wiederholter Effekt-Schalter in eine kürzere Kette** (Pedal bestätigt nicht, und vor der Wiederholung
  meldet der Patch-Dump weniger Effekte): Die Wiederholung wäre von der Allowlist abgewiesen worden
  (`NotAllowed` → Bridge beendet) bzw. `IndexError`. Jetzt wird sie dann nicht gesendet.
- **Halb geöffnete Ports blieben offen**, wenn ein Gerät zwischen Auflisten und Öffnen verschwand. Unter ALSA
  ist jeder offene Port ein Client, davon gibt es nur begrenzt viele. Jetzt werden sie geschlossen.
- **Szenen-Controller:** Einen Fußschalter für eine Szene in Preset B anzulernen, nahm ihn der Szene in Preset A
  weg, obwohl die Regeln denselben Controller in verschiedenen Presets erlauben. Jetzt behält jedes andere
  Preset seinen (ein Fußschalter = Szene 1 in jedem Preset). Effekt-Schalter und Keyboard-Regler nehmen ihn
  weiterhin überall weg.
- **Oberfläche:** Die neuen Schilder im Block „Keyboard synth“ (2026-10-17) hießen `.chips/.chip` wie die
  Werte-Schilder der Szenen und gaben diesen 8 px Abstand oben dazu → umbenannt in `.tags/.tag`. Eine offene
  Auswahlliste im Synth-Block wird nicht mehr unter der Maus neu gebaut.
- **README:** Dateiliste nannte zwei Dateien, die es im Repository nicht gibt.

### Optimierungen

- Ohne Bildschirm (`--settings`) wird der Zustand für die Oberfläche nicht mehr zehnmal pro Sekunde gebaut.
- `config_schema.store` schreibt die Einstellungsdatei erst vollständig (`.new`, `fsync`) und benennt dann um:
  Ein Stromausfall dazwischen lässt nie eine halbe Datei zurück.

### Linux-Portnamen (vorher ein sicherer Fehler auf dem Pi)

python-rtmidi nennt Ports unter ALSA „Client:Port Nummer:Nummer“, z. B. `KOMPLETE KONTROL A61:KOMPLETE
KONTROL A61 MIDI 1 28:0`. Der Name vom Mac („KOMPLETE KONTROL A61“) passte als Teilstring auf **beide** Ports
der A61 (Tasten und DAW) → „no unique port“, das Keyboard wäre nie verbunden worden. Jetzt
(`zs.port_candidates`, `find_port`): genauer Name, sonst genauer Client- oder Portname, sonst Teilstring; sind
es mehrere Ports **eines** ALSA-Clients, gilt der erste (wie macOS ihn zuerst nennt). Zwei Geräte gleichen
Namens werden weiter nicht geraten. Wird nichts gefunden, nennt die Meldung die vorhandenen Ports (fürs
Journal auf dem Pi). Nicht geklärt: der Mac-Name des UAC-232 („UAC-232 ZOOM UAC-232 MIDI I/O Port“) passt
unter Linux vermutlich nicht.

### Raspberry-Pi-Version (leere Karte → laufende Bridge)

Recherche: Raspberry Pi OS ab dem Trixie-Image vom 24.11.2025 richtet sich mit **cloud-init** ein; Imager 2.0
schreibt `user-data`, `network-config`, `meta-data` auf die Boot-Partition. Debian Trixie hat `python3-mido`
1.3.3 und `python3-rtmidi` 1.5.8, dieselben Versionen wie auf dem Mac. Overlay-Dateisystem unter Trixie laut
Berichten unzuverlässig (`do_overlayfs` macht nicht-interaktiv auch /boot schreibgeschützt) → nicht automatisch.

- **Ablauf:** Karte mit Imager (Pi OS Lite 64-bit, Benutzer, WLAN) → am Mac `Write Pi Card.command` bzw.
  `export.py`: schreibt Einstellungen, das Programm nach `bootfs/expression-bridge/` (mit `VERSION`) und hängt
  `bash /boot/firmware/expression-bridge/pi/install.sh` an `runcmd` in `user-data` (Imagers Einträge bleiben,
  nicht doppelt) → Karte in den Pi, einschalten.
- **`pi/install.sh`** (als root, beim ersten Start): richtet zuerst einen Wiederholungsdienst ein
  (`expression-bridge-setup.service`, läuft bei jedem Start bis zum Erfolg), wartet auf Internet und Uhrzeit,
  installiert die drei Pakete per apt, prüft sie, legt die udev-Regel für USB-Sticks an (Mount read-only unter
  `/media/stick/<Partition>` per `systemd-mount`, blockiert nichts), Journal nur im Speicher, apt-/man-db-Timer
  aus, installiert und startet `expression-bridge.service`. Protokoll in `expression-bridge-install.txt` auf der
  Karte. LED: Herzschlag beim Installieren, schnelles Blinken bei Fehlschlag.
- **Das Programm läuft von der Boot-Partition** (kein Kopieren, kein venv): Karte am Mac neu schreiben bringt
  Programm und Einstellungen zusammen auf Stand. Dienst als root (LED, Stick-Übernahme auf die Boot-Partition),
  aber `ProtectSystem=strict` mit nur der Boot-Partition beschreibbar; `Nice=-10`; `PYTHONDONTWRITEBYTECODE`.
- **Geprüft:** `tests/test_pi_install.py` lässt `install.sh` gegen nachgebaute Befehle (apt-get, systemctl,
  getent …) und einen Ordner als System laufen: Erfolg, kein Internet, Pakete scheitern. `tests/test_export.py`:
  Karte vorbereiten, `user-data` ergänzen, alte Dateien entfernen, das Programm auf der Karte ist vollständig
  importierbar. Ganze Testreihe zusätzlich unter Python 3.11 (der Pi hat 3.13).
- **Nicht geprüft (nur am Pi):** ob cloud-init `runcmd` wie erwartet ausführt, die Pakete unter Trixie, die
  LED-Muster, ob neue Stick-Mounts im Namensraum des Dienstes sichtbar werden (ProtectSystem), Portnamen der
  Geräte unter Linux, Strom, Stecker ziehen. Keine WLAN-Bedienung (eigenes WLAN des Pi) – das wäre der nächste
  Schritt.

## KeyWave dazu, KeySynF heißt KeySweep, 2026-10-17 (Wunsch des Nutzers)

„Effekt ‚KeyWave‘ wird auch von Bridge gesteuert. ‚KeySynF‘ wird in ‚KeySweep‘ umbenannt.“

- **KeyWave** ist der Wavetable-Prototyp (bisher WaveTbl, Ordner `prototypes/wavetable`, Manifest 0.10 schon
  als „KeyWave“/`KEYWAVE`): Nummer `…0F7C` (gebaut `07000f7c`; am Pedal am 11.10. als `09000F7C` in SFX gesehen).
  Regler: Key, Level, Wave 0–100, WEnv, WDcy, Sub, Atk, Rel, Glide, LFO, Rate, LDest 0–2 (Wave/Pitch/Level).
  Sein LFO ist eine reine Tiefe (0 = aus), deshalb **ohne** „Vib“/„Trm“ (`SynthEffect(..., lfo=None)`); ein Rad
  auf „LFO“ beginnt mit LFO aus. Key-Format und Ereignislogik wie KeySynth (`pk_synth.h`, wie KeyFM/KeySweep).
- **KeySweep** = KeySynF: gleiche ID `…0F71`, gleiche Regler (Manifest heißt schon „KeySweep“). Gespeicherte
  Zuweisungen „KeySynF/…“ (controls.json, Exporte, Einstellungsdatei des Pi) gelten weiter und werden beim
  Lesen auf „KeySweep/…“ umgeschrieben (`zs.RENAMED`, `synth_controls.split`).
- Meldung ohne Effekt: „No KeySynth, KeySweep, KeyFM, KeyPoly or KeyWave in this preset“.
- **Aus dem Log nachgetragen:** KeySynF/KeySweep, KeyFM und KeyPoly liefen am 11. und 12.10. über die Bridge am
  MS-60B+: 483, 685 und 525 bestätigte Key-Nachrichten (KeyPoly auf allen vier Key-Reglern), keine unbestätigte.
  KeyWave ist über die Bridge noch nicht gespielt.

## Alle fünf Synths am Pedal, 2026-10-18 01:59–02:09 Uhr (Test des Nutzers, Log ausgewertet)

- Neuer Stand (mit KeyWave/KeySweep) lief, Chocolate und A61 angesteckt. Alle fünf Effekte erkannt, beim
  Durchblättern auch schnell hintereinander (02:09:09–02:09:10 KeyFM → KeyPoly → KeySweep → KeySynth → KeyWave).
- Gesendete / bestätigte Key-Nachrichten: KeyFM 572/571, KeyPoly 146/146, KeySweep 137/134, KeySynth 6/6,
  **KeyWave 54/54**. Die vier fehlenden sind „overtaken“ (eine Nachricht nach wenigen Millisekunden von der
  nächsten ersetzt, die bestätigt wurde, wie schon gemessen); keine unbestätigte Note, keine Warnung.
- Mod-Rad (Vib, alte Zuweisung auf alle Synths verteilt) auf KeyFM: 50 Werte an den LFO-Regler; vier im
  12-ms-Abstand von der nächsten überholt, der letzte Wert jeweils bestätigt.
- **KeyWave am Pedal gedreht** (02:08:05–02:08:51; zuerst falsch als „kein Regler bewegt“ gemeldet, weil ich nur
  die von der Bridge gesendeten Werte gezählt hatte): Die Meldungen des Pedals bestätigen Nummern und Bereiche aus
  dem Manifest: Parameter 3 Level (9–79), 4 Wave (1–100), 5 WEnv (0–100), 6 WDcy (0–100), 7 Sub (1–100),
  13 LDest (0–2). Die Bridge hat dabei nichts gesendet (diese Regler sind keinem Controller zugewiesen) und
  nichts angemahnt.

## Erster Start des Raspberry Pi von leerer Karte, 2026-10-18 (Test des Nutzers, Bericht von der Karte gelesen)

- Karte mit „Write Pi Card“ beschrieben (Stand 00b40f5), im Pi gestartet, grüne LED im Herzschlag.
- **1. Start:** cloud-init hat `install.sh` gestartet; nach 15 Minuten Warten „NOT INSTALLED: no internet“,
  schnelles Blinken. Die Uhr stand dabei noch auf dem 06.10. (Datum des Images, noch keine Zeit aus dem Netz).
  Warum das Netz fehlte, ist offen (WLAN zu spät oder nicht erreichbar).
- **2. Start:** Der Wiederholungsdienst lief wie vorgesehen; Netz da, `apt-get update` (Trixie, arm64),
  python3-mido 1.3.3, python3-rtmidi 1.5.8 neu (dazu libjack-jackd2-0, libopus0, python3-packaging),
  python3-yaml 6.0.2 war schon da. „Installed. The bridge runs …“ um 03:42.
- Debians mido meldet `importlib.metadata` die Version 0.0.0 → im Bericht stehen jetzt die Versionen der
  Debian-Pakete (`dpkg-query`).
- Die Mac-Lautstärke heißt „BOOTFS“ (groß); macOS unterscheidet nicht, /Volumes/bootfs geht auch.
- Der Kartenleser am USB-Hub wurde nach dem Pi zuerst gar nicht gefunden (nur der USB-2-Teil des Hubs war zu
  sehen); nach erneutem Einstecken ging es. An der Karte lag es nicht.
- Erster Versuch zu spielen: vom Keyboard kam nichts am Pedal an (Ursache nicht festgehalten, kein Protokoll
  auf der Karte). Danach laut Nutzer: **„Alles funktioniert.“** Pedal und Keyboard am Pi.
- Noch offen am Pi: LED-Zustände der Bridge, Stick, Bluetooth-MIDI (nicht gebaut).

## MS Plus Bridge: neue Oberfläche, Effektnamen, beliebiges Keyboard, Pi mit Oberfläche, WLAN, Bluetooth, 2026-10-18 (Wunsch des Nutzers)

- **Name:** „Expression Bridge“ heißt jetzt **MS Plus Bridge** (Oberfläche, README, Meldungen, App
  `MS Plus Bridge.app`; `Start Bridge.command` ersetzt die alte App). Dateinamen auf dem Pi bleiben
  (`expression-bridge.yaml`, Ordner, Dienste), damit vorhandene Karten und Sticks weiter gelten.
- **Effekt- und Reglernamen:** Der Patch-Dump liefert nur Effekt-IDs. Die Namen stehen in den .ZD2-Dateien
  des Pedals (Backups von nam2zoom/Installer): ID bei Offset 96, Kurzname bei 100, Reglertabelle ab „OnOff“
  in Einträgen zu 56 Bytes (Name 12, Höchstwert, Grundwert …); Parameter 2 = erster Regler. `effect_names.py`
  liest sie, `zoom_effects.json` hält 142 Effekte aus den Backups dieses MS-60B+. Echte Dumps aus dem Log
  aufgelöst: „KeySynth, AnalogDly“, „ZNR, 160 Comp, RC Drive, BassGEQ, 1176 LMT“. Leerer Platz = ID 0.
  Die Bridge liest den Patch jetzt immer, wenn `query_patch` freigegeben ist (vorher nur mit Synth oder
  Effekt-Schaltern) – für die Namen. Offen: ob das Pedal einen Effektwechsel in einem Platz meldet
  (dann würden die Namen sofort folgen; jetzt erst beim nächsten Preset-Wechsel).
- **Oberfläche:** ein Block „Preset“ mit Nummer und Name des Presets und den Teilen Effects (mit Namen),
  Expression pedal (zugewiesene Parameter eingeklappt, „Add parameter“ listet die Regler der Effekte des
  Presets mit Bereich, leere Plätze ohne Auswahl, Änderungen werden sofort gespeichert, kein „Add preset“
  mehr), Scenes (Werte mit Namen) und Synth parameters (früher „Keyboard synth“). Export/Import unter
  Devices. „External sequencer (MPC)“ → „External sequencer“, „Expression controller“ → „Foot controller“.
  Mit Vorschau-Server (erfundener Zustand) im Browser angesehen, auch in Handybreite.
- **Any keyboard:** `synth.keyboard: "*"` nimmt den ersten MIDI-Eingang, der nicht Pedal ist und nicht wie
  Fußcontroller oder externer Sequencer heißt (auch wenn die gerade fehlen); „Midi Through“, IAC und Zoom
  bleiben außen vor.
- **Oberfläche auf dem Pi:** `bridge.py --settings … --listen` (Dienst) zeigt die Oberfläche auf Port 80 im
  Netz; Änderungen schreibt die Bridge in `expression-bridge.yaml` (ganz, fsync, umbenennen; die vorige wird
  .prev). Zugriff nur von der eigenen Seite (Origin = Host). Im Safe Mode sagt die Seite, warum.
- **Eigenes WLAN des Pi:** `export.py` legt einmal `pi: {wlan_name, wlan_password}` in config.yaml an
  (Passwort zufällig, 10 Zeichen), es geht mit der Einstellungsdatei auf die Karte und steht in der
  Oberfläche unter Devices. `install.sh` legt damit ein NetworkManager-Profil im AP-Modus an
  (Priorität -10, `ipv4.method shared`, 10.42.0.1): Der Pi nimmt es nur, wenn beim Start kein bekanntes
  WLAN da ist. **Der Hotspot wird nur für die Installation gebraucht.**
- **Bluetooth MIDI:** `pi/bluetooth-midi.sh` als eigener Dienst: sucht, solange kein MIDI-Gerät verbunden
  ist, vertraut/paart/verbindet Geräte mit dem BLE-MIDI-Dienst (03b80e5a-…). BlueZ macht daraus einen
  ALSA-Port. Ungeprüft: ob BlueZ in Raspberry Pi OS mit MIDI-Unterstützung gebaut ist, und welchen
  Namen der Chocolate Plus über Bluetooth hat.
- **Installationsstufe:** `install.sh` hat `LEVEL=2` und schreibt `level=`/`wlan=` in
  /var/lib/expression-bridge/installed. Die Bridge startet beim Start `systemd-run … install.sh --update`,
  wenn die Karte eine höhere Stufe oder ein anderes WLAN mitbringt (der Pi des Nutzers hat Stufe 1:
  er aktualisiert sich nach dem nächsten Beschreiben der Karte selbst). Pakete werden nur geladen, wenn
  welche fehlen (bluez ist auf Lite meist schon da) – dann ohne Internet.
- Gefunden beim Testen: In Bash wird `"$name”` (typografisches Anführungszeichen direkt nach der Variablen)
  als Teil des Variablennamens gelesen → `${name}`.
- Tests: tests/test_names.py neu, test_pi_install und test_headless erweitert.

## Durchsicht nach Fehlern, 2026-10-18 (Wunsch des Nutzers)

- **Neustart-Schleife auf dem Pi (behoben):** `install.sh` notierte `wlan=` nur, wenn das eigene WLAN
  angelegt werden konnte. Scheiterte nmcli, sah die Bridge bei jedem Start einen Unterschied, startete
  das Update, und das Update startete die Bridge neu – ohne Ende. Jetzt wird das gewünschte WLAN notiert,
  angelegt oder nicht. Zweite Ursache derselben Art: Bash (`read`, `sha256sum`) und Python rechneten den
  Fingerabdruck verschieden, sobald Leerzeichen am Rand stehen. Jetzt rechnen beide mit
  `config_schema.wlan_fingerprint` (install.sh ruft es über Python auf, `IFS= read`); mit echtem Python
  gegen eine Datei mit Leerzeichen geprüft: gleich.
- **Oberfläche auf dem Pi:** „Settings file for the Pi“ griff außerhalb von `call()` auf die Bridge zu und
  scheiterte, während sie neu startete (AttributeError im HTTP-Thread). `Current` liest die Bridge jetzt
  einmal je Aufruf (kein Wettlauf mit dem Neustart). Über das Netz werden nur noch Adressen und Namen des
  Pi angenommen (IP, Name, Name.local), damit eine fremde Webseite per DNS-Rebinding nicht herankommt.
- **Gelernter Bereich auf dem Pi:** ging verloren, wenn das Preset schon vier Parameter hatte (nur
  gelernt, nicht zugewiesen) – wird jetzt in die Einstellungsdatei geschrieben.
- **Auswahlliste:** Pong Delay hat 14 Regler; 13 und 14 (Parameter 13/14) nimmt die Bridge ohne Lernen
  nicht an – jetzt gesperrt mit „learn it from the pedal first“. Regler ohne Bereich (Onf_2, Dummy mit
  Höchstwert 0) werden nicht angeboten.
- **Bluetooth:** Während ein MIDI-Gerät verbunden ist, werden andere bekannte (ausgeschaltete) Geräte
  nur noch einmal pro Minute versucht statt alle 5 s – jeder Verbindungsversuch nutzt den Funk.
  Neuer Test gegen ein nachgebautes `bluetoothctl`.
- **Optimiert:** Effekteinträge der Kette werden je ID einmal gebaut (die Oberfläche bekommt den Zustand
  zehnmal pro Sekunde); die Namenstabelle hat einen Index für die unteren 24 Bit statt einer Suche.
  Das WLAN-Passwort erscheint auf dem Mac, sobald „Write Pi Card“ es angelegt hat, ohne Neustart.
- Zurückgenommen: „Learn“ bei den Effekt-Schaltern war versehentlich ohne Freigabe gesperrt.
- Oberfläche im Browser erneut geprüft (Ändern, Vertauschen, Entfernen, Safe Mode): keine Konsolenfehler.
  310 Tests grün.

## Parameter allgemein: jeder Controller für jeden Effekt, Help-Klappen weg, 2026-10-19 (Wunsch des Nutzers)

„Synth Parameter … Parameter generell machen? Weil jetzt fehlt, dass man auch Parameter von anderen
Effekten steuern kann.“ – „Und die Help Drop Down Menüs weg.“

- **Zuweisung mit Controller:** `Mapping` hat `cc` (None = Expression-Pedal, sonst die CC-Nummer eines
  Reglers von Keyboard, Fußcontroller oder externem Sequencer). Ein Regler setzt über seinen ganzen Weg
  (0–127) den Bereich von–bis mit Kurve, wie das Pedal. Pro Controller bis 4 Parameter je Preset,
  zusammen bis 12. In config.yaml, Einstellungsdatei und Export steht `cc` nur, wenn es einer ist –
  ältere Dateien gelten unverändert.
- **Lernen:** `/api/parameter` „learn“ (für eine Zuweisung oder mit `new` für einen noch nicht
  zugewiesenen Parameter, der erst beim Bewegen angelegt wird) → nächster bewegter Regler. Das
  Expression-Pedal bewegt = zurück ans Pedal. Preset-Wechsel bricht ab.
- **Ein Regler, eine Aufgabe:** Schalter (Effekt, Szene, Arp/Seq) haben Vorrang. Wird ein Regler als
  Schalter gelernt, verlieren Zuweisungen mit ihm ihren Eintrag (bei einer Szene nur im eigenen Preset);
  wird er für einen Parameter gelernt, schaltet er nichts mehr. Synth-Knöpfe (in jedem Preset) und
  Parameter dürfen sich einen Regler teilen.
- **Oberfläche:** Teil „Parameters“ statt „Expression pedal“ und „Synth parameters“; jede Zeile zeigt
  „← Expression pedal“ bzw. „← CC 21“, aufgeklappt Controller-Auswahl, „Learn controller“, Heel/Toe bzw.
  „At 0 / At 127“. Hinzufügen mit „Controlled by: Expression pedal | A knob or pedal – move it“ (mit
  Cancel). Die Synth-Zuweisungen bleiben (deine 6 Keyboard-Regler gelten weiter) als Abschnitt „Synth
  knobs, in every preset“ darin.
- **Help-Klappen entfernt**, Inhalte in der README (Abschnitt „Using the interface“ und die
  Fachabschnitte). Dabei fielen drei Platzhalter weg, die das Skript befüllte (`bendRange`,
  `synthEffects`, `seqMax`) – sonst wäre die Seite stehen geblieben; geprüft mit einem Abgleich aller
  `$("…")`-IDs gegen die Seite. Der Hinweis zum Pattern-Format steht jetzt im Tooltip.
- Im Browser mit erfundenem Zustand geprüft (Handybreite): keine Konsolenfehler. Tests:
  tests/test_parameters.py (14) neu. **Am Pedal noch nicht gespielt.**

## „Synth knobs, in every preset“ entfernt, 2026-10-19 (Wunsch des Nutzers)

„Synth knobs, in every preset bitte entfernen. Man kann die Knob auch in den Parametern auswählen.“

- Der Abschnitt (Synth-Liste, Knopf-Liste, Learn, Tags) ist weg. Synth-Knöpfe weist man wie jeden
  Parameter über „Add parameter“ zu, pro Preset; die Key-Knöpfe des Synths, den die Tasten spielen,
  sind in der Liste gesperrt („played by the keys“). LFO wie ein Mod-Rad: Bereich 50→0 (Vib) bzw.
  50→100 (Trm/Flt).
- Die Zuweisungen von vorher (controls.json, je Synth in jedem Preset, beim Nutzer u. a. Mod-Rad → Vib
  auf vier Synths und CC 14 → KeyWave/Wave) **gelten weiter**, damit nichts still verloren geht; sie
  stehen unter den Parametern eines Presets mit diesem Synth („Mod wheel · in every preset with
  KeySynth“) mit „Remove“. Neue entstehen so nicht mehr (die Schnittstelle /api/synth bleibt für Arp/Seq).
- Im Browser geprüft (Vorschau mit erfundenem Zustand): Abschnitt weg, alte Zuweisung als Zeile,
  Remove entfernt sie, Key gesperrt, keine Konsolenfehler.

## Test am Mac nach dem Umbau, 2026-10-19 02:19–02:23 (Lauf des Nutzers, Log ausgewertet)

- Bridge mit A61 und MS-60B+ (ohne Chocolate) gestartet: KeyFM in Preset 011 gefunden; „controller 14 now
  sets effect 1, parameter 3, 0–10“ gelernt und gedreht: 45 Werte gesendet, 42 sofort bestätigt
  (6 ms). Um 02:22:47 und 02:22:50 meldete das Pedal von sich aus `64 20 00 64 01 …` (Platz 100 =
  Patch-/Systemeinstellung, wohl eine Bedienung am Pedal); dabei blieben zwei Bestätigungen aus →
  Edit-Modus neu, Abstand 20 ms, danach alles bestätigt. Kein Fehler der Bridge.
- **Gefunden:** Die Eingänge dieses Macs sind „Akai Network - DAW Control“, „Akai Network - MIDI“,
  „IAC-Treiber Bus 1“ (deutsch, nicht „IAC Driver“), „Komplete Kontrol A DAW“ u. a. „Any keyboard“ hätte
  alphabetisch den Akai-Netzwerkport genommen. Jetzt bleiben Loop-backs (IAC in jeder Sprache, Midi
  Through), Netzwerk-Sitzungen, DAW-Ports, Zoom und rtmidi außen vor (Regex `NOT_A_KEYBOARD`); mit dieser
  Liste bleibt nur die A61. Der Nutzer hat die A61 mit Namen eingetragen, war also nicht betroffen.

## Oberfläche ruhiger, 2026-10-19 (Wunsch des Nutzers)

- Rechts neben „Effects“, „Parameters“ und „Scenes“ stehen keine Namen mehr; nur noch Hinweise, wenn
  etwas fehlt (Pedal nicht verbunden, Kette nicht gemeldet, Schalten nicht freigegeben, eine Szene wird
  gerade aufgenommen). Beim Preset fällt der Pedalwert („pedal 64“, die Stellung des Expression-Pedals
  0–127) weg; der Balken mit „Expression pedal 64“ darunter bleibt.
- Szenen: der Name steht als Beschriftung, daneben „Recall“ (orange: zuletzt abgerufen) statt eines
  Knopfs mit dem Namen.
- Arpeggiator und Step-Sequencer zeigen ausgeschaltet nur „Off“.

## „Kann die Bridge nicht öffnen“, 2026-10-19 (Meldung des Nutzers)

- Ursache: Die Bridge von 02:19 lief noch im Terminal (PID 6020, Port 8765 belegt, Pedal offen). Ein
  zweiter Start brach mit „The interface cannot be started (Address already in use)“ ab.
- Jetzt: Ist der Port belegt und antwortet dort eine Bridge (gleiches Profil, `/api/state`), öffnet der
  zweite Start deren Oberfläche im Browser und endet ohne Fehler. Etwas anderes auf dem Port: die
  bisherige Meldung. Gegen die laufende Bridge geprüft (erkannt), zwei Tests neu, 327 grün.

- Effekte in zwei Spalten, die nach unten laufen wie die Kette (links 1–3, rechts 4–6; bei weniger
  Effekten gleichmäßig verteilt), auf dem Handy eine Spalte. Wunsch des Nutzers, im Browser geprüft.
- Pedal-Anzeige als kleiner Indikator in die Titelzeile von „Parameters“ verschoben („Pedal“, Balken 56 px,
  Wert 0–127), sichtbar auch zugeklappt; unter dem Presetnamen steht sie nicht mehr. Wunsch des Nutzers,
  im Browser geprüft (breit und schmal), Oberflächentests grün.

## Presetkopf, alte Synth-Zuweisungen, Umbenennen (2026-10-19, Wünsche des Nutzers)

- „Preset on pedal“ heißt jetzt „Preset“; Nummer und Name stehen nebeneinander in gleicher Größe (16 px,
  10 px Abstand). Bei den alten Synth-Zuweisungen steht nur noch der Controller, ohne „in every preset
  with …“. Im Browser geprüft, 93 Tests der betroffenen Teile grün.
- **Preset umbenennen (noch nicht gebaut):** Der Patch-Dump hat den Namen zweimal, 10 Zeichen ab Offset 26
  und einen Block „NAME“ mit 32 Bytes (28 Zeichen, mit Leerzeichen aufgefüllt, dann 4 × 00), in allen
  Dumps aus den Logs „USER-012“. Laut zoom-explorer meldet das Pedal Änderungen am Namen als
  `64 20 00 5F <Zeichenindex> <ASCII>` (ein Schwall, je Zeichen eine Nachricht). Ob es diese Nachricht
  auch *annimmt*, ist dort offen („Send: ?“). zoom-explorer selbst benennt um, indem es den ganzen Patch
  mit neuem Namen ins Edit-Buffer schickt (`64 12 01 <Länge> <Patch> <CRC>`); das wäre eine deutlich
  größere Nachricht und ein Eingriff, den die Sicherheitsregeln bisher ausschließen. In den Logs gibt es
  noch keine `5F`-Meldung. Nächster Schritt mit dem Nutzer: einen Namen am Pedal ändern, während die
  Bridge läuft (sie protokolliert alles Empfangene), dann das Format daraus übernehmen und erst nach
  „Go“ testweise senden.

## Preset umbenennen gebaut, 2026-10-19 (nach Beobachtung am Pedal)

- **Beobachtet (03:21 Uhr, Nutzer hat Preset 011 am Pedal umbenannt und gespeichert):** Je Zeichen
  `64 20 00 5F <Stelle> <ASCII> 00 00 00 00`, Stellen 0–27 (0x1B), Leerzeichen 0x20 zum Auffüllen. Beim
  Bewegen des Cursors schickt das Pedal Schwälle über mehrere Stellen; dazwischen jeweils 15 × „Program
  Change 0“ *ohne* CC0/CC32. Beim Speichern: `45 00 00 01 00 00 00 50 06 <Patch> <CRC>` (Bank 1, Programm 0,
  848 Bytes, CRC stimmt), Name darin „DDU.BSER-012“, danach Tempo, CC0, CC32, PC wie bei einem Presetwechsel.
- **Fehler dabei gefunden und behoben:** Die Bridge las jeden Program Change als Presetwechsel. Während am
  Pedal ein Name bearbeitet wird, wäre sie auf Programm 0 der Bank gesprungen (bei Preset 011 zufällig
  dasselbe) und hätte die Zuweisungen des falschen Presets gesendet. Jetzt zählt ein Program Change nur
  direkt nach Bank Select (CC32); so kommen beide echten Fälle (Antwort auf `33`, Presetwechsel).
- **Gebaut:** Nachrichtenart `rename_patch` (eigene Freigabe, `probe.py approve rename_patch query_patch`),
  nur Stellen 0–27 und die Zeichen aus Zooms Handbüchern (A–Z, a–z, 0–9, Leerzeichen,
  ``!#$%&'()+,-.;=@[]^_`{}~``). „Rename“ im Presetkopf; die Bridge sendet alle 28 Stellen im Abstand von
  10 ms, liest danach den Patch und meldet, ob der Name angekommen ist (mit Zahl der Acks). Ein
  Presetwechsel während des Sendens bricht ab. Der Parser liest jetzt den ganzen Namen aus dem Block
  „NAME“ (alle 149 Dumps aus den Logs gelesen); am Pedal bearbeitete Namen erscheinen live.
- Nebenbei: Die Parameterzeilen machten die Seite auf dem Handy 26 px zu breit (Grid ohne
  `minmax(0, 1fr)`), behoben.
- **Offen, braucht den Test am Pedal (nach „Go“):** Nimmt das MS-60B+ die Zeichen an, bestätigt es sie,
  zeigt es den neuen Namen sofort an, und bleibt er nach Speichern am Pedal? Tests: 10 neue, Suite 338
  bis auf einen alten Allowlist-Test grün, der die Namensnachricht verbot (angepasst: nur noch falsche
  Stellen, Zeichen und Bytes werden abgelehnt).

## Preset umbenennen am Pedal getestet, 2026-10-19 03:38 Uhr (mit dem Nutzer, nach Freigabe)

- Preset 011 „DDU.BSER-012“ → „YouOU“: 28 Zeichen gesendet (10 ms Abstand), alle 28 bestätigt mit
  `64 20 01 5F <Stelle> <Zeichen>`; zurückgelesener Patch „YouOU“. Am Pedal gespeichert (`45 …` mit „YouOU“,
  CRC stimmt), auf 012 und zurück: weiterhin „YouOU“.
- Preset 012 „Empty“ → „ooo“ (Kleinbuchstaben): alle 28 bestätigt, zurückgelesen „ooo“. Nicht gespeichert,
  auf 011 und zurück: wieder „Empty“. Ein Name verhält sich also wie ein gedrehter Regler.
- Acks: 56 von 56, nach 3–99 ms (Median 25 ms). Beim ersten Namen kam das Pedal bei 10 ms Abstand nicht ganz
  hinterher (letztes Ack 77 ms nach dem Senden), verloren ging nichts.
- Offen: ob das Display des Pedals den neuen Namen sofort zeigt (Nutzer fragen).
- Kein „Rename“-Knopf mehr: Ein Klick auf den Presetnamen öffnet das Eingabefeld (der Name ist ein Knopf
  ohne Rahmen, beim Überfahren gestrichelt unterstrichen; gesperrt, solange nicht freigegeben oder nicht
  verbunden). Die Beschriftung „Preset“ über Nummer und Name ist weg, der Bereich heißt schon so. Wunsch des
  Nutzers, im Browser geprüft (breit, schmal, langer Name gekürzt, kein seitliches Scrollen).
- Namen ohne „Apply“/„Cancel“: Ein Klick auf den Namen öffnet an seiner Stelle ein Feld; Enter (Handy:
  „Fertig“, das Feld steckt in einem Formular) oder ein Klick daneben übernimmt, Escape behält den alten,
  ein unveränderter Name wird nicht gesendet, ein ungültiges Zeichen lässt das Feld mit Hinweis offen.
  Gemeinsame Funktion `editInPlace` für den Presetnamen und die Szenennamen (Szenen bis 24 Zeichen, über
  `/api/scenes` „rename“, das es schon gab; der Zustand nennt `scenes.max_name`). Die Szenenzeilen werden
  nicht neu aufgebaut, solange darin getippt wird. Feld 16 px, damit das Handy nicht hineinzoomt. Wunsch
  des Nutzers, im Browser geprüft (Enter, Klick daneben, Escape, ungültiges Zeichen, Handy), 40 Tests grün.

## Effekte tauschen und verschieben am Pedal beobachtet, 2026-10-19 12:08–12:10 (nur mitgeschrieben)

Der Nutzer hat bei laufender Bridge an Preset 011 („YouOU“: BaMnPitch | BassTsDRV | KeySweep | ChorusOne |
BrghtRoom) KeySweep durch die Kette geschoben, einen sechsten Effekt hinzugefügt (BlackOpt), Effekte
ausgetauscht (Platz 5 über AMPG SVT, AnalogDly, Z-Synth … bis Ba AnaOct, Platz 3 bis BlackOpt), nicht
gespeichert. Die Bridge hat nichts Neues gesendet.

- **Keine kurze Nachricht fürs Verschieben oder Tauschen:** Nach jedem Schritt schickt das Pedal von
  sich aus den ganzen Patch (`64 12 00 <Länge> <Patch> <CRC>`, wie die Antwort auf `64 13`, nur mit 00).
  Ein Tausch des Effekttyps kommt *nicht* als `64 20 00 <slot> 01 …`. Hinzufügen: Effektzahl 5 → 6, erst
  ein Platz mit ID 0, dann der gewählte Effekt. Cursor am Pedal: `64 20 00 64 01 <slot>`.
- **Verschieben = Blöcke tauschen (offline geprüft):** Für 20 Verschiebungen um eine Stelle ergibt das
  Vertauschen der ID (ab Offset 36) und des 24-Byte-Blocks in „EDTB“ im Dump davor genau den Dump danach,
  bis auf 2–3 Bytes in „PRM2“ (Bytes 10–12: der am Pedal gewählte Effekt, der mitwandert). zoom-explorer
  (ZoomPatch.swapEffectsInSlots) tauscht dazu noch die Bits der Effektplätze in PRM2 (ungültig, Preamp,
  BPM, Line Selector) und lädt den ganzen Patch per `64 12 01 …` ins Edit-Buffer.
- **Tauschen:** zoom-explorer sendet Parameter 1 mit der vollen ID (`64 20 00 <slot> 01 <ID in 5 × 7 Bit>`),
  das Pedal setzt die Regler auf Grundwerte. Am MS-60B+ ungeprüft; das Pedal selbst meldet es nur per Dump.
- **Nebenbefund:** Zuweisungen, Schalter und Szenen hängen an der Position. Beim Verschieben am Pedal
  zeigten die Zuweisungen von 011 auf andere Effekte; die Bridge hat es nur durch ihre Prüfungen nicht
  gesendet („Nothing is sent to effect 2, parameter 1 … KeySweep's Key knob“). Sie folgt dem Synth-Effekt
  schon (KeySweep ist Effekt 1–6), den übrigen Zuweisungen nicht.

## Zuweisungen gehen mit ihrem Effekt, 2026-10-19 (Wunsch des Nutzers nach der Beobachtung)

- **Was:** Parameter-Zuweisungen (`effect` in config.yaml, Hex-ID), Szenen (`chain`: Effektnummer → ID, in
  controls.json) und gelernte Bereiche (ziehen mit den Zuweisungen um) merken sich ihren Effekt. Nach jedem
  Patch-Dump (`Session._on_patch` → `Bridge.follow_effects`): Steht der Effekt an seinem Platz, bleibt alles;
  steht er genau einmal woanders und nicht auf dem Platz eines gleichen Effekts, der dort hingehört, ziehen
  Zuweisungen, Szenenwerte/-schalter und Bereiche mit (sofort gespeichert); sonst wartet die Zuweisung
  (`Target.away`, nichts gesendet, in der Oberfläche „not in this preset now“, Szenen-Chips gestrichelt).
  Nimmt ein verschobener Effekt den Platz eines fehlenden ein, weichen dessen doppelte Zuweisungen (Activity).
- **Effekte über die ganze ID vergleichen:** Die unteren 24 Bit sind nicht eindeutig (BlackOpt 01000020,
  ChorusOne 06000020, AnalogDly 0A000020, BrghtRoom 0B000020).
- **Alte Zuweisungen** bekommen ihren Effekt beim nächsten echten Presetwechsel (`64 26` → `fresh`), aus
  dem gespeicherten Stand, nicht beim Start (da kann das Pedal Ungespeichertes halten). Neue sofort beim
  Speichern (`_remember_effects` in `_store`/`_store_controls`). Ohne Patch-Abfrage bleibt alles beim Platz.
- **Bewusst nicht:** Die Effektschalter (für alle Presets gleich) bleiben an der Position.
- Simulation: `World.rearrange`, `World.swap_effect` schicken wie das Pedal den Patch (`64 12 00`).
  11 neue Tests (tests/test_follow.py), Suite 348 grün nach Anpassung erwarteter Formate.
- **Offen:** Test am Pedal (es wird nichts Neues gesendet, nur dieselben Parameter an den neuen Platz).

## Mitziehen am Pedal getestet, 2026-10-19 12:42–12:44 (mit dem Nutzer)

- Preset 010 → 011: die beiden alten Zuweisungen auf BassTsDRV (Effekt 2) haben ihren Effekt gelernt.
- BassTsDRV am Pedal auf Platz 3, dann 4 geschoben: „BassTsDRV is effect 3/4 now – what is assigned to it goes
  along“; das Expression-Pedal (SINCO, 12:43:28 verbunden) setzte danach Platz 4: 86 von 86 bestätigt.
- BassTsDRV gegen einen anderen Effekt getauscht: „Nothing is sent to BassTsDRV · VOL / · Gain: that effect is
  not in this preset now“. Preset 010 und zurück ohne Speichern: „BassTsDRV is effect 2 now“, wieder wie gespeichert.
- 16 unbestätigte Werte um 12:43:45–48: Der Nutzer war im Auswahlmenü des Pedals (Dump erst 12:43:53), wie
  bekannt antwortet es dann verzögert. Pedal-Latenz TX→Ack in diesem Preset: Median 47 ms (sonst 4–11 ms).
- **Zwei Fehler gefunden und behoben:**
  1. Beim Verschieben kurz „Nothing is sent to effect 3, parameter 1 … KeySweep's Key knob“: Die Zuweisungen
     wurden nachgezogen, bevor die neue Stelle von KeySweep bekannt war. Jetzt erst Synth-Effekt suchen
     (`Session._find_synth`), dann nachziehen.
  2. Nach „verschoben, nicht gespeichert, Preset gewechselt und zurück“ galten bis zum Dump (~0,2 s) noch die
     verschobenen Plätze; ein Expression-Wert wäre beim falschen Effekt gelandet (hier ChorusOne). Jetzt warten
     Werte von Zuweisungen mit bekanntem Effekt nach einem Presetwechsel auf die Kette (`Target.unplaced`);
     kommt sie nicht binnen 1 s (`PLACE_SECONDS`), gilt wie früher der Platz.
- 3 neue Tests, Suite 351 grün.

## Latenz in Preset 011 untersucht, 2026-10-19 (alle Logs ab 17.10., 1934 bestätigte Werte)

- **Ursache ist der Effekt BassTsDRV:** Pedal-Zeit Senden→Ack bei BassTsDRV (Gain und VOL) Median 46–51 ms,
  bei allen anderen gemessenen Effekten 5–11 ms. Sauberer Vergleich in derselben Sitzung, demselben Preset und
  derselben Kette (5 Effekte): KeySweep-Key um 12:44:56 nach 5–6 ms bestätigt, BassTsDRV um 12:43 nach 40–60 ms.
- Aufgeteilt: Eine einzelne BassTsDRV-Änderung bei freiem Pedal braucht ~40 ms (min. 20 ms); jede weitere, die
  schon wartet, kommt ~20 ms nach der vorigen dran. Beide Parameter hängen am Expression-Pedal, also gehen je
  Pedalwert zwei Nachrichten raus und die zweite wartet (Median 49 ms).
- Die Bridge selbst fügt ~2 ms hinzu (CC→Ack 49–56 ms vs. Senden→Ack 47 ms).
- Werte kamen hier höchstens alle 52–60 ms (früher per USB gemessen: alle 33 ms). Deshalb nie mehr als eine
  Nachricht im Rückstand. Bei schnelleren Werten (2 Parameter × 30/s × 20 ms > 1 s Arbeit pro Sekunde) würde
  sich bei BassTsDRV während eines Sweeps ein wachsender Rückstand aufbauen.
- Möglich: höchstens ein Wert pro Parameter unterwegs, immer der neueste (begrenzt den Rückstand); einen der
  beiden Parameter weglassen (~40 statt ~50 ms); anderen Effekt nehmen. Schneller machen kann die Bridge
  BassTsDRV nicht. Auswertung: scratchpad/latency.py (nicht im Projekt).

## Effekt- und Reglernamen neu eingelesen, 2026-10-19 (nach „Prototypen Auftrag 2“)

- **Quellen:** alle Pedal-Backups (`PROTOTYP/programme/installer/{daten,zoom-zt2-master}/Backups/ms-60bp_*`,
  `ms-plus-synth/backup`), die angepassten Uploads (`…/Adapted/ms-60bp`), die Builds in `prototypes/install_all`
  (Stand 15:14), `referenz/MS EFFECTS` und `keysynth/ms-60b-plus`. Ergebnis: 139 Effekte (96 Stock, 43 andere);
  vorher 142, weil eigene Effekte doppelt unter 07… und 09… standen.
- **Neue Regel in `effect_names.build`:** Eigene und Community-Effekte (Nummer ≥ F00) sind je Nummer ein Effekt,
  egal unter welcher Kategorie installiert und wie sie früher hießen; die neueste Datei gewinnt. Stock-Effekte
  teilen Nummern über Kategorien (BlackOpt 01000020, AnalogDly 0A000020) und gehen nach der ganzen ID; die
  höchste Stock-Nummer ist E0. Das Pedal meldet alle eigenen Effekte als 09000Fxx (Log), die Tabelle findet sie
  über die Nummer.
- **Stock-Effekte:** in allen Backups gleich, keine Änderung. **MS EFFECTS:** alle 16 standen schon (aus dem
  Backup vom 12.10.) mit denselben Reglern drin; die Dateien tragen andere Kategorie-Bytes als das Pedal
  (z. B. Pong Delay 08002110 in der Datei, 0A002110 am Pedal).
- **Geändert:**
  - FretSyn: Parameter 7/8 Quant/Glide → **Cutoff/Reso** (am Pedal installiert, Adapted 19.10. 02:39).
  - HOGi 0.15: −1Oct, Root, 5th, Oct, Oct5, 2Oct, 3Oct, Tone, Freq, Reso, EnvLo, EnvHi (Tabelle hatte noch
    die Harmo-Regler Oct, Oct5, 2Oct, 3Oct, 5th, Dry, Tone, Win, Atk, Dec).
  - SlamDrive 0.13: Drive, Shape, Tone, Mid, MidF, Dist, XFreq, Deep, Clean, Comp, Level (High → Dist, Low →
    Clean, Shape nach vorn).
  - Glider 0.15: Speed → Atk, Rel. FiltSyn 0.11: Mix → Dry.
  - HOGi, SlamDrive, Glider, FiltSyn (und DrumBox 0.12, gleiche Regler) sind die Builds von 14:50–15:14, noch
    nicht installiert: bis dahin zeigt die Oberfläche bei diesen vier schon die neuen Namen.
- **Gleich geblieben:** KeySynth, KeySweep, KeyFM, KeyPoly, KeyWave stimmen mit den Reglern in `zoom_sysex.py`
  überein (neuer Test). Die drei gespeicherten Zuweisungen in config.yaml liegen auf Stock-Effekten.
- **Ohne Datei:** BPM (09000FF0, im Pedal eingebaut) erscheint in Ketten, steht in keiner Datei und bleibt
  „Effect n“.
- Die laufende Bridge liest die Tabelle beim Start: neu starten. 2 neue Tests.

## Effekte wieder 1 3 5 links, 2 4 6 rechts, 2026-10-19

- Wunsch des Nutzers: Die Effekte in „Effects“ laufen wieder zeilenweise durch zwei Spalten (1 2 / 3 4 / 5 6,
  also links 1 3 5, rechts 2 4 6), statt nach unten (links 1–3, rechts 4–6). Auf dem Handy eine Spalte 1–6.
  Das `--rows` aus `renderEffects` ist weg. Im Browser geprüft (1024 px und 375 px, kein seitliches Scrollen).
- Ebenfalls auf Wunsch: Der Hinweis „Also assigned in preset …“ unter den Parametern ist weg, der Hintergrund
  hinter dem Panel ist schwarz statt Holz (`--wood` und die Maserung entfernt). Im Browser geprüft.

## Effekttabelle: SlamDrive 0.14, FiltSyn 0.13, 2026-10-19 21:10

- Nach dem Ende von „Prototypen Auftrag 2“ neu eingelesen (gleiche Quellen wie oben). Geändert nur:
  - SlamDrive 0.14: Comp, Deep, Bass, XFreq, Drive, Shape, Mid, MidF, LvlLo, LvlHi, LoCut (0–17), HiCut (0–14).
  - FiltSyn 0.13: Level fällt weg (Sqr, Sub, Oct regeln den Synth, Dry den Bass); 10 Regler.
- Beide noch nicht installiert (FILTSYN und SLAMDRV aus `install_all`).

## Effekt ändern aus der Bridge, 2026-10-19 (Wunsch des Nutzers, am Pedal noch ungetestet)

- **Nachricht:** wie zoom-explorer `64 20 00 <slot> 01 <ID in 5 × 7 Bit, niedrigste zuerst>` (z. B. HOGi
  09000F83 → `03 1F 00 48 00`). Neue Nachrichtenart `change_effect` mit eigener Freigabe
  (`probe.py approve change_effect query_patch`). Das Pedal setzt die Regler des neuen Effekts auf Grundwerte.
- **Allowlist:** Parameter 1 auf Platz 0–5 geht nur als `change_effect` durch, und nur
  - für einen Platz, in dem der Patch-Dump des Pedals einen Effekt zeigt (`ZoomSender.changeable`),
  - mit einer ID, die das Pedal selbst schon in einem Preset gemeldet hat (`ZoomSender.effects`), also eine,
    die es hat. Was passiert, wenn es eine ID nicht hat, ist unbekannt; deshalb nur gemeldete.
- **Gemeldete Effekte:** `Measurements.found` hält jede ID aus jedem gelesenen Patch fest, auf dem Rechner in
  `measurements.json` („effects“), auf dem Pi in der Einstellungsdatei (`pedal_effects`, mit `validate`
  geprüft, kommt beim Export mit; neu Gemeldete schreibt der Pi erst beim nächsten Schreiben der Datei).
  Startliste: 65 IDs aus den eigenen Logs (alle 1149 Dumps), einmal eingelesen.
- **Ablauf:** `Session.change_effect` sendet, nach 0,3 s wird der Patch gelesen (`EFFECT_CHECK_SECONDS`), höchstens
  2 s gewartet. Ergebnis in Activity und Oberfläche: „Effect 2 is HOGi now – save the preset on the pedal to
  keep it“ oder „The pedal did not take HOGi: effect 2 is ChorusOne“. Presetwechsel bricht ab. Ein Dump, der
  den neuen Effekt schon zeigt, beendet es früher. Zuweisungen auf den alten Effekt warten („away“).
- **Oberfläche:** Klick auf den Effektnamen in „Effects“ öffnet eine Auswahl, gruppiert nach den Kategorien des
  MS-60B+ (oberstes Byte der ID, nach zoom-explorer: Dynamics … Reverb) und „Custom and community“ (Nummer ab
  F00, außer LINE SEL und BPM). Auswahl sendet, Escape oder Klick daneben lässt es. Im Browser geprüft.
- 11 neue Tests (tests/test_change_effect.py); der Simulator nimmt einen Effektwechsel an (`takes_effects`).
- **Offen, mit dem Nutzer am Pedal:** ob das MS-60B+ die Nachricht annimmt, ob es bestätigt (`64 20 01 …`) und
  ob es von sich aus einen Dump schickt.

## Effekt ändern am Pedal getestet, 2026-10-19 22:10–22:11 (mit dem Nutzer)

- Preset 009, Platz 1 (PH+Dist, gespeichert) aus der Oberfläche getauscht:
  - 22:10:14 → Hall REV: TX `64 20 00 00 01 50 00 00 58 00` (0B000050), nach 116 ms `64 12 00` mit Hall REV.
  - 22:10:48 → AMPG SVT: TX `64 20 00 00 01 20 00 00 28 00` (05000020), nach 127 ms `64 12 00` mit AMPG SVT.
  - 22:11:28 Preset 008 und zurück ohne Speichern: wieder PH+Dist.
- **Kein Ack** (`64 20 01 …`) auf diese Nachricht: Das Pedal antwortet mit seinem eigenen Patch, wie beim Tauschen
  am Pedal. Die Bridge ist damit sofort fertig („Effect 1 is Hall REV now …“), das Nachlesen nach 0,3 s entfällt.
- Erster Regler des neuen Effekts im Dump = Grundwert aus der Effektdatei (Hall REV Pre DLY 48, AMPG SVT Gain 30).
- Der Nutzer: Anzeige und Klang richtig, alles wie erwartet.
- Vorher (22:07–22:08) hatte der Nutzer am Pedal selbst getauscht, mit der Seite der alten Bridge im Browser
  (Name dort nicht anklickbar); nach Neuladen ging es. Die Bridge ist jedem Tausch am Pedal gefolgt.
- Noch nicht probiert: ein eigener Effekt (FretSyn, HOGi …), ein anderer Platz als der erste.

## Effekt verschieben aus der Bridge, 2026-10-19 (Wunsch des Nutzers, am Pedal noch ungetestet)

- **Wie zoom-explorer:** Das Pedal hat keine Nachricht fürs Verschieben. zoom-explorer schickt den ganzen
  Patch ins Edit-Buffer, und zwar mit `28 <Patch, 7-Bit-gepackt>` (V1-Befehl, auch für die MS-Plus-Pedale),
  nicht mit `64 12 01`, wie in der Notiz vom Mittag vermutet. `28` stand bisher ausdrücklich auf der
  Verbotsliste.
- **Neue Nachrichtenart `move_effect`:** `28` geht nur als die eine Nachricht durch, die die Bridge gerade
  gebaut hat (`ZoomSender.upload`, nach dem Senden gelöscht). Zusätzlich prüft `check_patch` vor dem Bauen
  und noch einmal in der Allowlist: PTCF, 1–6 Effekte, EDTB-Block je Effekt mit seiner ID, PRM2-Bits für
  Amp, BPM und Line Selector passend zur Kette, Bits für „unbekannter Effekt“ 0.
- **Tausch `swap_effects`:** ID (ab 36), 24-Byte-EDTB-Block und die PRM2-Bits je Platz zweier Nachbarn
  tauschen; PRM2 10–12 (am Pedal gewählter Effekt) bleiben. Über mehrere Plätze: Nachbar für Nachbar, ein Patch.
- **Offline geprüft an den Logs:**
  - Alle 1223 Dumps bestehen `check_patch`. Die PRM2-Felder nach zoom-explorer stimmen in allen mit der
    Kette: „Preamp“-Feld (Byte 20) = Plätze mit Kategorie 05 (Bass amp, z. B. AMPG SVT), BPM (21–22) =
    09000FF0, Line Selector (22–23) = 09000F00.
  - Alle 23 Verschiebungen am Pedal: Tausch ergibt den nächsten Dump Byte für Byte, bis auf PRM2 10–12 und
    einmal Ein/Aus (dazwischen geschaltet).
  - Merkwürdigkeit: Nach einer Verschiebung am Pedal zeigt PRM2 10 den Platz, von dem der Effekt kam.
- **Ablauf:** `Session.move_effect` liest den Patch frisch (`64 13`, damit am Pedal gedrehte Regler nicht
  verloren gehen), baut daraus den neuen, sendet, liest nach 0,3 s nach. Solange er unterwegs ist (auch
  bei „Effekt ändern“), gehen keine Werte an die Plätze und keine Noten (`Session.rearranging`).
  Zuweisungen ziehen über `follow_effects` mit. Activity notiert, ob das Pedal den Patch genau so hält.
- **Oberfläche:** Griff ⠿ links in jeder Effektzeile; auf einen anderen Effekt ziehen (Pointer-Events, also
  auch mit dem Finger), Pfeiltasten einen Platz, Escape bricht ab. Zuerst Pfeile ‹ ›, auf Wunsch des Nutzers
  durch Ziehen ersetzt. Im Browser geprüft (Ziehen 1 → 6, Klick ohne Ziehen sendet nichts, Pfeiltaste).
- Testdaten: `tests/pedal_patches.json` (zwei echte Verschiebungen und ein Patch mit AMPG SVT aus dem Log).
  10 neue Tests (tests/test_move_effect.py); der Simulator nimmt einen Patch an (`takes_patches`).
- **Offen, mit dem Nutzer am Pedal:** ob das MS-60B+ `28` annimmt, was es antwortet, ob es danach genau
  diesen Patch hält.

## Effekt verschieben am Pedal getestet, 2026-10-19 22:41–22:43 (mit dem Nutzer)

- Preset 009, zwei Effekte, aus der Oberfläche gezogen:
  - 22:41:18 BrghtHall 1 → 2 (BrghtHall, HOGi → HOGi, BrghtHall), 22:41:23 zurück.
  - 22:43:26 EnvFilter 1 → 2 (vorher am Pedal EnvFilter und AnalogDly eingesetzt).
- Jeweils: `64 13` frisch gelesen, 2 ms später `28 …` (976 Bytes) gesendet, nach 0,3 s gelesen. Das Pedal
  **antwortet auf `28` nicht** (kein Ack, kein eigener Dump), hält aber genau den gesendeten Patch
  („the pedal holds the patch as sent“, verglichen ohne PRM2 10–12), 3 von 3.
- Der Nutzer: Anzeige und Klang richtig. 22:43:42 Preset 008 und zurück ohne Speichern: wieder das
  gespeicherte PH+Dist.
- Noch nicht probiert: mehr als zwei Effekte, über mehrere Plätze.

## Key-Synths: neue Reglerreihenfolge, Key bleibt vorne, 2026-10-20

- **Anlass:** Der Nutzer wollte Key an letzter Stelle („Prototypen Auftrag 2“, Commit 065824d). Das geht nicht gut:
  Im Patch hat jeder Regler-Platz eine feste Bitbreite. Regler 1–5 und 9 haben 12 Bit, 6–8 und 10–12 nur 8 Bit
  (0–255), Key braucht 0–1000. Entscheidung des Nutzers: Key bleibt vorne. Neu gebaut (Commit 02ee7c8, in
  `prototypes/<effekt>/install/`, install_all folgt); am Pedal noch ungetestet und nicht installiert.
- **Breiten geprüft (Workflow, 4 Agenten, nur lesend):**
  - Logs: 138 auswertbare Reglerwerte (Kette unverändert zwischen Meldung und Dump) passen 138/138 zu zoom-explorers
    Breiten (12,12,12,12,12,8,8,8,12,8,8,8,12,…). zoom-zt2s decode_preset.py (Regler 9–12 je 12 Bit) liegt bei allen
    12 Werten daneben, die beide unterscheiden (z. B. KeyFM gemeldet 100/50, zoom-zt2 liest 612/499), und liefert
    699 Werte über dem Höchstwert des Effekts. Die Feldanfänge (Bit 30, 42, 54, 66, 78, 90, 98, 106, 114, 126,
    134, 142) folgen eindeutig aus den Daten.
  - Quellen: zoom-explorer hat die Breiten ab Regler 10 erst am 2026-09-25 korrigiert (Commit 1836328), ohne
    Angabe des Pedals; zoom-zt2 hat Regler 9–12 2025 ohne Prüfung ergänzt (cacfa9c).
  - Effekte: Kein Effekt (Stock, Community, eigene) hat einen Regler über 255 auf Platz 6–8 oder 10–12.
  - Gegenprobe: Die Breite von Regler 12 ist nicht gemessen (kein Effekt mit 13 Reglern; Bit 150+ immer 0), ebenso
    nicht, ob das Pedal einen Live-Wert über 255 auf einem 8-Bit-Platz abschneidet. Sichere Plätze für 0–1000: 1–5, 9.
- **Neue Reihenfolge** (KeySynth 1.12, KeySweep 0.11, KeyFM 0.11, KeyPoly 0.12, KeyWave 0.11): Level fällt weg,
  Mix (0 = nur Bass, 50 = beides voll, 100 = nur Synth) ist jetzt der letzte Regler. KeySynth: früheres Mix heißt
  Blend, Wave1 hat 10 Wellen; KeyPoly: Wave hat 10. Key bzw. Key1–Key4 bleiben Regler 1 bzw. 1–4.
- **Bridge:** Die fünf Reglerlisten in `zoom_sysex.py` folgen den neuen Builds; `zoom_effects.json` neu mit
  `prototypes/<effekt>/install/` als Quelle. Jeder Synth-Effekt kennt jetzt selbst die Lage seiner Key-Regler
  (`SynthEffect.key_param`, `key_index`; `KEY_PARAM` ist weg), `build_set_key` nimmt den Effekt mit. Der Key-Wert
  aus dem Patch kommt aus allen Reglerwerten (`PatchEffect.values`, `EDTB_WIDTHS`), nicht mehr aus „dem ersten“.
- Gespeicherte Tastatur-Zuweisungen des Nutzers (Vib, KeyWave/Wave, KeySynth/Pitch) sind nicht betroffen. Eine
  Zuweisung „KeySynth/Mix“ (früher Oszillator-Mix) würde jetzt den neuen Mix stellen.

## Editor, 2026-10-20 (Wunsch des Nutzers)

- „Preset“ → „Editor“ (eingeklappt): je Effekt der Kette alle Regler als Schieber mit Wert, Namen wie am Pedal.
- **Werte:** aus dem Patch (`PatchEffect.values`, `EDTB_WIDTHS`; 151/151 am Pedal gedrehte Regler, Regler 1–12,
  stimmen), danach aus den Meldungen und Acks des Pedals (`Session.knob_values`). Ein Ziehen am Pedal bewegt den
  Schieber (Abfrage alle 0,4 s), außer der Schieber wird gerade gehalten.
- **Senden:** `Session.edit_knob` prüft Effekt bekannt, Regler bekannt, 0..Höchstwert aus der Effektdatei, nicht die
  Key-Regler des gespielten Synths; `_flush` schickt die Werte nach denen der Zuweisungen, je Regler nur den neuesten.
  Die Allowlist bekommt dafür je Regler der Kette 0..Höchstwert (in `refresh`). Nach einer Änderung der Kette verfallen
  noch nicht gesendete Werte. Oberfläche: höchstens alle 60 ms ein Wert pro Schieber, beim Loslassen der letzte.
- Nebenbei gefunden und behoben (eigener Commit 93ed53d): Seit 7ecc947 überschrieb `refresh` in der Szenen-Schleife den
  Synth-Effekt mit einer ID. In Presets mit Szenenwerten setzten Tastatur-Regler nichts, und ein Szenenwert auf dem
  Synth-Platz brach die Bridge ab (AttributeError). Neuer Test.
- Im Browser geprüft (Vorschau, 1000 px und 375 px). 5 neue Tests (tests/test_editor.py). Am Pedal noch nicht benutzt.

## Prüfung der drei Commits (Key-Reihenfolge, Szenen-Fix, Editor), 2026-10-20

Workflow mit drei Prüfern (Editor/Allowlist, Key-Umbau, Zusammenspiel), jeder Fund mit Simulator-Lauf belegt.
Behoben (Tests in tests/test_editor.py, die Original-Skripte der Prüfer laufen jetzt sauber):
- **Hoch: Wert für einen ersetzten Effekt erreichte den neuen.** Ein Pedalwert, der während eines Effektwechsels
  wartete (`pending`), wurde in `refresh` übernommen und gesendet, obwohl das Ziel „away“ war; früher stoppte
  das nur die Allowlist, die Editor-Freigaben haben sie geöffnet (z. B. ChorusOne·Tone → HOGis 5th, oder die
  Key-Note eines zweiten Synths). Jetzt: blockierte/gestoppte Ziele verlieren `pending` und `awaiting`, `_flush`
  und die Wiederholung in `_miss` überspringen sie.
- **Hoch (älter, von „Effekt ändern/verschieben“): Noten an den alten Platz des Synths.** `_on_patch` meldete
  den Wechsel fertig (`_effect_done` → `_play`), bevor `_find_synth` den neuen Platz kannte: Key/Gate-off ging an
  den Effekt, der jetzt am alten Platz steht, eine losgelassene Taste blieb hängen. Jetzt erst Synth suchen und
  `refresh`, dann den Wechsel prüfen.
- Editor: Schieber senden die ID ihres Effekts, die Bridge lehnt ab, wenn dort jetzt ein anderer steht, und
  während ein Effekt gewechselt/verschoben wird. Key-Regler aller Synth-Effekte der Kette sind gesperrt, nicht nur
  die des gespielten (`Session.key_params`, Zustand „keys“). Werte werden erst nach Ack angezeigt; ohne Ack geht
  ein Wert einmal erneut (`edit_awaiting`). Ein Editorwert setzt `last_value` der Ziele desselben Reglers zurück
  (sonst sendete ein Szenenaufruf danach nichts) und gehört zu einer gerade aufgenommenen Szene.
- Unbekannter Ausgang (kein Patch nach Wechsel/Verschieben): wartende Editorwerte verfallen, keine Note, der
  Patch wird neu angefragt. Durchgereichter Programmwechsel: Kette, Werte, Editor-Warteschlange werden geleert.
- `SynthEffect` verweigert Key-Regler außerhalb der 12-Bit-Plätze. Zuweisungen auf „Level“ (alte Exporte,
  Einstellungsdateien) werden ausgelassen statt die ganze Datei abzulehnen (`synth_controls.REMOVED`).
- Offen: `keysynth/` im Repo enthält noch KeySynth 1.10 (alte Reihenfolge). README sagt es; ob 1.12 dort hinein
  soll, entscheidet der Nutzer.

## Zweite Prüfung: Effektparameter und Bridge, 2026-10-20 (Wunsch des Nutzers)

Workflow mit vier Prüfern (nur lesend): eigene Effekte und ihre Quellen; Tabelle gegen alle Dumps des Pedals und
Stock-/Community-Effekte; Bridge-Logik; Oberfläche und README. Danach behoben (Oberfläche und README von zwei Agenten,
Bridge selbst), 11 neue Tests (tests/test_guards.py, tests/test_names.py).

**Effektparameter**
- Die Tabelle nahm je Effekt die neueste Datei aus allen Quellen, also auch nicht installierte Builds. Gegen alle
  Dumps: 9 Effekte passten nicht zum Pedal (KeySynth 1.11, KeySweep 0.10, KeyFM 0.10, KeyPoly 0.11, KeyWave 0.10,
  FiltSyn 0.10, HOGi 0.14, SlamDrive 0.12, Glider 0.14 sind installiert; letzte Installation 2026-10-19 02:39).
- **Neue Regel: die Tabelle beschreibt das Pedal.** Gelesen aus den Pedal-Backups (Installer und
  `ms-plus-synth/backup/2026-*`), den hochgeladenen Kopien `Adapted/ms-60bp` beider Installer und MS EFFECTS; nicht
  aus `install_all` oder `prototypes/*/install|build`. Ergebnis gegen alle 1223 Dumps: nur noch 4 Abweichungen, alle
  HOGi vor der Installation von 0.14. 139 Einträge (97 Zoom inkl. LINE SEL, 42 andere), BPM per Name ohne Datei.
- **Synth-Reihenfolge aus der Tabelle:** `zoom_sysex.installed()` nimmt die Reglerliste des installierten Builds,
  wenn die Tabelle ihn hat und Key auf 12-Bit-Plätzen liegt; sonst die eingebauten Listen (Builds vom 2026-10-20).
  Die Tests setzen `MS_PLUS_BRIDGE_SYNTHS=builtin` (tests/__init__.py), damit sie nicht vom Pedal abhängen.
  „Level“ ist damit wieder gültig, solange ein Build mit Level installiert ist (`synth_controls.removed`).
- **Schutz:** Passt ein Dump nicht zur Tabelle (Wert über dem Höchstwert oder hinter der Reglerzahl), gilt der Effekt
  in dieser Sitzung als unbekannter Build: Warnung, Regler nur per Nummer, kein Editor, keine Tastatur-Regler (beim
  Synth nur Noten). Fängt nicht alles (HOGi, Glider haben überall 0–100).
- Sonst: Stock- und Community-Effekte stimmen, die 8/12-Bit-Regel gilt in allen 4152 Dateien. Vier Effekte werden im
  anderen Chat noch umgebaut (OctSynth 1.02, Ringer 0.11, WideChorus 0.13, FMSynth 0.11); `install_all` und LISTE.md
  hängen 17 von 25 Versionen hinter den Manifesten.

**Bridge (behoben)**
- Editorwert, der ohne Ack wiederholt wird, ging nach einem Wechsel an den neuen Effekt oder brach die Bridge ab:
  Warteschlangen tragen die Effekt-ID und verfallen mit jeder Kettenänderung; eine Ablehnung der Allowlist bricht
  die Bridge nie mehr ab („WARNING: not sent“).
- Ausdruckspedal und Szenen konnten den Key-Regler eines nicht gespielten Synths schreiben: gesperrt für jeden
  Synth-Effekt in der Kette (`Session.key_params`).
- Szenenwerte gingen direkt nach einem Presetwechsel an alte Plätze; Szenenschalter während Verschieben an den Effekt,
  der dort hinrückt: Ein Szenenaufruf vor der Meldung der Kette oder während eines Wechsels wartet jetzt darauf.
- Nach Kabel ab/an blieb ein offenes Gate offen: Alle Key-Werte werden aus dem Patch gelesen, ein Gate ohne Note
  wird geschlossen.
- `Measurements.move` warf Lernbereiche weg, die eine behaltene Zuweisung noch brauchte (nächster Start verweigert).
- Eine Szenenaufnahme blieb nach einem Neuverbinden hängen; durchgereichter Programmwechsel ließ Noten an den alten
  Synth-Platz; die Pi-Oberfläche zeigte das Hotspot-Passwort (jetzt nur am Rechner).
- Oberfläche: Teile wurden bei jeder Abfrage neu aufgebaut (Klicks gingen verloren), Key-Regler aller Synths in
  „Add parameter“ gesperrt, Import nennt Szenen, sicherer Modus, Editor sperrt während eines Wechsels, Bedienelemente,
  die die Bridge ablehnen würde, sind gesperrt, leerer Platz ohne Auswahl, Fokus nach Verschieben per Pfeiltaste.
- README: zwölf Zuweisungen je Preset (vier je Regler), KeyPoly lief am Pedal, Editor-Teil, „Controller“ statt
  „On/Off“, Effektwechsel werden sofort gelesen.

## Effekt hinzufügen und alle Effekte auswählbar, 2026-10-20 (Wunsch des Nutzers)

- **„Add an effect“** unter „Effects“: ein leerer Platz am Ende der Kette (höchstens sechs), dann per Klick auf
  „Empty – choose an effect“ einen Effekt wählen. Neue Nachrichtenart `add_effect` (eigene Freigabe), derselbe
  Patch-Upload `28 …` wie beim Verschieben, nur als die eine vorbereitete Nachricht.
- **Vorlage aus den Logs:** 19 Hinzufügungen am Pedal (an Platz 1–6). Das Pedal fügt die ID 0 (4 Byte) und einen
  24-Byte-Block `01 00 … 00` ein (an, ID 0, alle Regler 0), die eigene Länge im Kopf (Byte 4–7) wächst um 28, die
  Effektzahl um 1; der Puffer bleibt 848 Byte (hinter der eigenen Länge steht nur Rest-Inhalt, den das Pedal nicht
  liest). `zs.insert_empty` gibt für 17 von 19 den nächsten Dump Byte für Byte (ohne PRM2 10–12); bei den zwei
  übrigen wurden dazwischen Regler eines anderen Effekts gedreht (im Log nachgesehen). PRM2-Bits der Plätze danach
  rücken auf. Testdaten: drei echte Hinzufügungen in tests/pedal_patches.json.
- **Effekt für einen leeren Platz:** `change_effect` nimmt jetzt auch leere Plätze (am Pedal ungetestet).
- **Auswahl = was das Pedal hat:** Bisher nur, was es in einem Preset gemeldet hatte (64). Jetzt auch jede
  Effektdatei aus seiner eigenen Dateiliste: Der Installer fragt sie ab (`60 04 25 …`, je Datei eine Antwort mit
  Namen ab Byte 11), die Bridge sieht die Antworten auf demselben Anschluss mit und nimmt den Effekt dazu
  (`effect_names.by_file`; die Tabelle merkt sich dafür den Dateinamen jedes Effekts). Es wird nie etwas
  entfernt: Der Installer liest die Liste nach dem Löschen und vor dem Neuschreiben ersetzter Effekte (am
  2026-10-19 02:39 fehlten HOGi, DrumBox, FretSyn in der letzten vollständigen Liste und kamen gleich danach neu).
  Aus allen Dateilisten der Logs übernommen: 124 Effekte (die 123 installierten Dateien und BPM).
- Simulator: Presets wie am Pedal (848-Byte-Puffer, eigene Länge im Kopf; 985 Byte SysEx), neue leere Plätze
  beim Upload. 10 neue Tests (tests/test_add_effect.py).
- Offen, mit dem Nutzer am Pedal: Hinzufügen, und einen Effekt für einen leeren Platz wählen.

## Hinzufügen am Pedal getestet, 2026-10-20 08:03–08:05 (mit dem Nutzer)

- Preset 009 (PH+Dist): 08:03:42 „Add“ → Upload (Länge 196, 2 Effekte), 0,3 s später gelesen: PH+Dist, leer. 08:03:46
  SlowATTCK für den leeren Platz gewählt → eigener Dump des Pedals nach 105 ms mit SlowATTCK. 08:03:49 noch ein Platz,
  08:03:55 BassGEQ dafür. 08:03:57 BassGEQ von Platz 3 auf 1 gezogen (über zwei Plätze, ein Upload), 08:03:59
  PH+Dist von 2 auf 3. 08:04:59 Preset 008 und zurück ohne Speichern: wieder nur PH+Dist.
- Nach beiden Hinzufügungen hielt das Pedal genau den gesendeten Patch (bis zu seiner Länge, ohne PRM2 10–12).
  Effekt für einen leeren Platz: wie ein Effektwechsel, kein Ack, eigener Dump nach ~0,1 s.
- Der Nutzer: alles richtig. Activity notiert die Gleichheit jetzt auch beim Hinzufügen; der Vergleich geht nur bis
  zur Länge des Patches (danach steht Rest-Inhalt).

## Effekt löschen, 2026-10-20 (Wunsch des Nutzers: „löschen einbauen und selber am Pedal testen“)

- **Ein kleines × neben dem Effektnamen** unter „Effects“ löscht den Effekt (auch einen leeren Platz); die danach
  rücken auf. Der letzte Effekt eines Presets bleibt (das Pedal kennt keine leere Kette): stattdessen einen anderen
  wählen. Neue Nachrichtenart `delete_effect` (eigene Freigabe), derselbe Patch-Upload `28 …` wie beim Verschieben
  und Hinzufügen, nur als die eine vorbereitete Nachricht.
- **Vorlage aus den Logs:** 4 Löschungen am Pedal (2026-10-19, Platz 2 von 3, 3 von 6, 2 von 6, 3 von 3). Das Pedal
  nimmt ID (4 Byte) und EDTB-Block (24 Byte) heraus, die eigene Länge schrumpft um 28, die Effektzahl um 1, der
  Puffer bleibt 848 Byte; PRM2-Bits der Plätze danach rücken nach unten. `zs.remove_effect` gibt bei zwei davon den
  nächsten Dump Byte für Byte, bei den anderen beiden bis auf PRM2 10–12 (bei einer wurden dazwischen ein Regler von
  Effekt 3 und das Tempo gedreht, im Log nachgesehen).
- **PRM2 Byte 10–12 entschlüsselt:** Bit 0–4 immer 12, ab Bit 5 je 4 Bit ein Platz: der am Pedal angewählte
  Effekt, dann alle danach bis zum letzten, so viele wie in 24 Bit passen (in allen 1223 Dumps fast immer genau so).
  Beim Löschen am Pedal: Löscht man einen Effekt vor dem angewählten, rückt die Auswahl mit; löscht man den
  angewählten, wird der danach angewählt, beim letzten der neue letzte. Zweimal ließ das Pedal einen nicht mehr
  vorhandenen Platz in der Liste stehen. `remove_effect` schreibt die Liste in der üblichen Form neu.
- Simulator brauchte nichts Neues. 14 neue Tests (tests/test_delete_effect.py), dabei: Noten gehen nach dem Löschen
  an den Synth, wo er jetzt ist; ist der Synth gelöscht, erreicht keine Note den Effekt an seinem Platz.
  Alle 427 Tests grün.

## Löschen am Pedal getestet, 2026-10-20 08:29–08:31 (selbst, auf Wunsch des Nutzers)

- Der Nutzer war nicht dabei und hatte mich gebeten, selbst am Pedal zu testen; `delete_effect` habe ich deshalb
  selbst freigegeben (08:28:59). Das A61 war seit 08:13 weg, die Bridge wartete auf einen Controller: Für den Test
  lief die neue Bridge aus einem Skript, mit dem virtuellen IAC-Bus als Keyboard (Kopie der config.yaml, die echte
  blieb unverändert), gesteuert über die API der Oberfläche.
- Preset 009 (PH+Dist): leerer Platz am Ende hinzugefügt und gelöscht; AMPG SVT ans Ende, leerer Platz davor, diesen
  gelöscht (Amp-Bit rückt von Platz 3 auf 2), dann AMPG SVT als letzten gelöscht; BlackOpt und AMPG SVT dazu,
  BlackOpt aus der Mitte gelöscht, dann AMPG SVT. Zum Schluss zweimal per Klick auf das × in der Oberfläche
  (Handy- und Desktop-Breite) einen leeren Platz gelöscht.
- Alle 7 Löschungen: 0,3 s später gelesen hielt das Pedal den Patch genau wie gesendet, diesmal auch PRM2 10–12.
  Nach einem hinzugefügten Platz ergänzt das Pedal die Liste in PRM2 10–12 selbst (0 → 0,1). Am Ende war das
  Preset Byte für Byte wieder das vom Anfang (nicht gespeichert, nichts am Pedal verändert).
- Oberfläche: das × rutschte auf dem Handy beim langen „Empty – choose an effect“ an den Zeilenanfang; jetzt hängt
  es am Namen. Danach die normale Bridge (`bridge.py`) mit dem neuen Code wieder gestartet, sie wartet aufs A61.

## Review Löschen und Pi-Version, 2026-10-20 (vier Prüfer im Hintergrund, dann selbst nachgeprüft)

**Löschen (3 Funde, alle behoben):**
- Das × schickt jetzt die ID des Effekts mit, für den es gebaut wurde; ist bis dahin ein anderer an dem Platz
  (Preset gewechselt, Zeile noch nicht neu gebaut), wird nichts gelöscht.
- Szenen-Einträge des gelöschten Effekts machen Platz, wenn der nachrückende Effekt Einträge in derselben Szene
  hat (eine Szene merkt sich je Platz einen Effekt). Das stand so nirgends: jetzt Zeile in Activity, README ehrlich.
- Ein per Passthrough-Programmwechsel abgebrochenes Löschen hieß „Changing the effect stopped“; jetzt eine
  gemeinsame Formulierung für beide Wege.

**Pi, Einstellungen im Betrieb (alle behoben):**
- Schwer: Ein Preset mit Szenen, aber ohne Zuweisungen (beim Nutzer 091 und 092) bekam beim Anwählen einen leeren
  Eintrag unter `mappings:`. Auf dem Pi: Datei ungültig, jede weitere Änderung „Not kept“. Auf dem Mac: config.yaml
  mit `"9/0":` ohne Inhalt, beim nächsten Start Absturz (AttributeError). Jetzt kein leerer Eintrag, und ein leerer
  Eintrag von früher wird beim Start übersprungen. Die config.yaml des Nutzers war noch sauber.
- Schwer: Eine abgelehnte Änderung während Learn (Fehler beim Schreiben) beendete die Bridge. Jetzt WARNING.
- Eine Änderung, die die Regeln der Datei bricht, blieb im Speicher und blockierte jede weitere. Jetzt geht der
  Speicher auf die zuletzt geschriebene Datei zurück (`_restore_settings`).
- Eine Zuweisung ohne Lernbereich, die auf einen Platz rückt, an dem ein anderer Effekt einen Lernbereich hinterlassen
  hat, verletzte die Lernbereich-Regel (Mac: nächster Start bricht ab). Der fremde Bereich macht jetzt Platz
  (`Measurements.forget`).
- Keyboard ab- und wieder angewählt: auf dem Pi kamen Controller, Arpeggiator und Muster vom Start zurück. Jetzt
  folgt `cfg` jeder geschriebenen Datei.
- Mehrfaches Schreiben je Änderung machte `.prev.yaml` zur neuen Datei; gleicher Inhalt wird nicht mehr geschrieben.
- Vom Pedal gemeldete Effekte werden auf dem Pi sofort in die Datei geschrieben.
- Der Pi gibt seine Einstellungsdatei nicht mehr übers Netz heraus (sie enthält das Passwort seines eigenen WLANs);
  der Knopf fehlt dort.
- Learn für einen Effekt-Schalter erst, wenn das Schalten freigegeben ist (sonst ungültige Datei).
- `schema_version` 2 (pedal_effects, Effekt der Zuweisung, Kette der Szene). Ein neuerer Stand sagt jetzt „Made
  by a newer program … write the card“. Ein Pi mit dem Programm vom 18.10. lehnt einen Stick mit den heutigen
  Einstellungen ab: **die Karte muss neu geschrieben werden** (README und export.py sagen es).

**Linux (behoben, am Pi noch ungetestet):**
- Ein in der Pi-Oberfläche gewählter Port wurde mit vollem ALSA-Namen samt Nummern gespeichert („… 32:0“) und nach
  einem Neustart oder Bluetooth-Reconnect nicht mehr gefunden. Jetzt zählt Client und Port ohne die Nummern.
- „Midi Through“ und die eigenen Ports der Bridge stehen nicht mehr in den Geräte-Listen.
- Zwei gleiche optionale Geräte (gleicher Name unter Linux) beendeten die Bridge; jetzt bleibt das Gerät außen vor.
- Schnelles Umstecken: Unter Linux behält der Port seinen Namen, liefert aber nichts mehr. Antwortet das Pedal
  zwischen zwei Edit-Enables gar nichts, verbindet die Bridge neu.
- Nach dem Umbenennen der Einstellungsdatei wird auch der Ordner auf die Karte geschrieben (fsync).
- install.sh: WLAN-Funk entsperren, Hinweis ohne WLAN-Land, Access Point mit rsn/ccmp/pmf aus wie nmclis eigener
  Hotspot. LEVEL bleibt 2: Ein schon eingerichteter Pi bekommt das erst bei einer Neuinstallation (das eigene WLAN
  war noch nie am Pi getestet).
- Geprüft und in Ordnung: alle Module und Daten kommen auf die Karte, keine neuen Pakete nötig, Python 3.11/3.13
  ohne Probleme, nichts liest zur Laufzeit Mac-Pfade.

## Presets wählen und speichern, 2026-10-20 (Wunsch des Nutzers)

- Oberfläche: grünes + statt „Add an effect“, rotes × (größer, fürs Handy), leere Plätze heißen nur „Empty“.
  Ein Klick auf eine gesperrte Nummer oder auf „Save“ sagt, welche Freigabe fehlt (auf dem Handy gibt es keinen
  Tooltip; der Nutzer konnte vorher nicht erkennen, warum nichts passierte).
- **Wählen:** Klick auf die Nummer listet die 100 Presets (zehn Bänke zu zehn). Die Bridge schickt, was das Pedal
  beim Wählen selbst schickt: Bank Select (CC 0 = 0, CC 32 = Bank) und Program Change. Neue Freigabe `select_preset`
  (keine SysEx). Laut zoom-explorer speichert ein Pedal der Plus-Serie beim Program Change nichts (die alte MS-Serie
  speicherte da ungespeicherte Änderungen).
- **Namen:** `46 00 00 <Bank> <Programm>` je Platz (neue Freigabe `query_preset`), nach dem Verbinden einer nach
  dem anderen; das Pedal antwortet mit `45 00 00 …`, derselben Form, in der es einen am Pedal gespeicherten Preset
  meldet. Gemeldete Speicherungen am Pedal aktualisieren den Namen.
- **Speichern:** `45 00 00 <Bank> <Programm> <Länge> <Patch> <CRC>` (neue Freigabe `save_preset`). Alle 9
  Speichermeldungen des Pedals in den Logs (18./19.10.) baut `build_save_preset` Byte für Byte nach, CRC
  eingeschlossen. Nur als die eine vorbereitete Nachricht aus dem frisch gelesenen Patch, Plätze 0–99; danach liest
  die Bridge den Platz zurück. Auf einen anderen Platz nur nach Rückfrage; Zuweisungen, Szenen und Lernbereiche gehen
  mit, das Pedal wechselt dorthin. `45` und `32` (Speichern der alten Serie) stehen sonst auf der Verbotsliste.
- Simulator: gespeicherte Presets, `46`/`45`, Program Change lädt den gespeicherten Preset. Ein alter Test
  verließ sich darauf, dass der Simulator Program Changes ignoriert; jetzt lädt er im Test dasselbe Preset neu.
  10 neue Tests (tests/test_presets.py).

## Presets am Pedal getestet, 2026-10-20 14:58–15:01 (mit dem Nutzer)

- Freigaben vom Nutzer (14:58). Namen: 100 Anfragen, 100 Antworten in 2,5 s (14:58:27–29). Aufgefallen: 093
  und 094 sind nicht leer (USER-007, USER-003) – mein Vorschlag, dort zu testen, war falsch; leer sind 012–049,
  053–092, 100.
- 14:58:31 011 über die Nummer gewählt: das Pedal meldet den Wechsel 2 ms später (`64 26`, Tempo, CC 0/32, PC),
  wie beim Wählen am Pedal.
- 14:58:38 011 auf 012 gespeichert (012 war leer): zurückgelesen genau wie gesendet, Pedal auf 012, Zuweisungen
  mitgegangen.
- 15:00:22 in 012 ChorusOne gelöscht, 011 und wieder 012 über die Nummer gewählt: ChorusOne wieder da. **Ein
  Program Change speichert nichts.**
- 15:00:42 ChorusOne erneut gelöscht, „Save“ auf 012 selbst: zurückgelesen wie gesendet; 15:00:50 am Pedal 011
  und 012 gewählt: 012 ohne ChorusOne. Speichern am eigenen Platz hält.
- 012 ist jetzt eine Kopie von 011 („YouOU“) ohne ChorusOne, mit denselben Zuweisungen in der Bridge.

## Rückgängig und Effekttabelle, 2026-10-20 (Wunsch des Nutzers)

- Effekttabelle neu aus Backups, Adapted (heute 23:03 vom Installer geschrieben) und MS EFFECTS: weiter 139
  Effekte, bei 17 neue Regler (Key-Synths, Kick, HOGi, SlamDrive …). Tests grün, Commit 40712d1.
- **Rückgängig (↶ neben „Save“):** Die Antwort `45 …` auf das Lesen eines Platzes enthält den ganzen Patch; die
  Bridge merkt sich ihn je Platz und schreibt beim Speichern „davor“ + was sie für den Platz hält (Zuweisungen,
  Szenen, Lernbereiche) beiseite. Rückgängig = derselbe Speichern-Weg mit dem alten Patch (keine neue Freigabe),
  danach wechselt das Pedal auf den Platz. Verfällt, wenn der Platz am Pedal überschrieben wird. 4 neue Tests.
  Am Pedal noch nicht probiert.
- **Offen: nur Effekte, die im Pedal sind.** Die Auswahl zeigt, was das Pedal je gemeldet hat (Presets, und
  Dateilisten, die an der Bridge vorbeigingen); entfernt wird nie etwas. Die Liste lesen kann nur der Weg des
  Installers (`60 25 …` im PC-Modus `52`, Dateizugriff); das ist unser verbotener Bereich und nimmt das Pedal aus
  dem normalen Betrieb. Nicht ohne Entscheidung des Nutzers eingebaut. Aus den Ordnern des Installers lässt es sich
  nicht ablesen (Backup vom 13.10. nur 98 Dateien, Adapted 52).

## Oberfläche und „Read Pedal“, 2026-10-20 (Wunsch des Nutzers)

- Eingeklappte Blöcke zeigen ihren Zustand rechts, aufgeklappte nicht mehr (die Pedal-Anzeige bleibt); das +
  sitzt in der Titelzeile von „Effects“; ↶ ist ein blaues rundes Symbol mit SVG-Pfeil.
- „Settings file for the Pi“ und „Export all“ sind nicht dasselbe: Export all ist das JSON für „Import…“ (Zuweisungen,
  Szenen, Schalter, Tastatur), die Settings-Datei das YAML für die Pi-Karte (zusätzlich Geräte, Freigaben, WLAN).
- **Read Pedal:** liest die Dateiliste des Pedals wie der Installer (`52` PC-Modus an, `60 25 00 00 * 00` und
  `60 26 …` je Eintrag, `60 21 40 …`, `60 09`, `53`), vorher Edit-Modus aus, nachher wieder an. Neue Freigabe
  `read_pedal`; die Dateinachrichten sind nur erlaubt, solange die Liste gelesen wird, und nur diese Bytes (nichts
  zum Öffnen, Schreiben, Löschen). Danach sind genau die Effekte wählbar, deren Dateien dort stehen (plus Kette,
  BPM). Eine unvollständige Liste (<10 Dateien oder abgebrochen) ändert nichts; PC-Modus wird immer verlassen.
  Aus dem Installer abgeleitet, am Pedal NICHT probiert: unklar sind die genaue Antwort auf `52`, ob `51` davor
  nötig ist und ob das Ende der Liste als Antwort ohne Namen kommt (so macht es der Installer).
  Tests: 3 + Allowlist (tests/test_add_effect.py), Simulator mit Dateiliste und PC-Modus.

## Effects und Editor in einem, 2026-10-21 (Wunsch des Nutzers)

- Der Teil „Editor“ ist weg: Jeder Effekt hat einen Pfeil (▸/▾) hinter dem Namen, der seine Regler als Slider
  unter der Zeile aufklappt (offene Effekte merkt sich die Seite je Platz und Effekt). Statt „On“/„Off“ eine
  grüne/rote Lampe wie oben rechts, ein Klick schaltet. ↶ sieht aus wie + und ×.
- „Read Pedal“ ging nicht, weil `read_pedal` nicht freigegeben war (nicht in approvals.json): Ein Klick sagt jetzt,
  welche Freigabe fehlt, statt nichts zu tun.

## Parameter in die Effekte, 2026-10-21 (Wunsch des Nutzers, nach drei Entwürfen)

- Entwürfe: drei Varianten parallel (Workflow), der Nutzer wählte A (Leiste neben dem Regler) mit dem Live-Punkt
  aus B; danach: Heel/Toe als Griffe auf dem Slider und als antippbare Zahlen, nur ein Weg zum Zuweisen (Symbol
  antippen, dann Pedal oder Controller bewegen – „Pick“ fiel weg), über den gelernten Bereich hinaus springt es
  zurück.
- Umgesetzt: Der Teil „Parameters“ ist weg. Im aufgeklappten Effekt hat jeder Regler links ein Symbol (Pedal oder
  Drehknopf). Verknüpfte Regler zeigen H/T-Griffe, gelernten Bereich (gestrichelt), Live-Punkt und eine Leiste
  (Controller, Heel → Toe zum Antippen, ⇅, Kurve, ×). Zugeklappte Effekte zeigen Chips. In der Titelzeile
  „n / 12 linked“ und die Pedal-Anzeige. Ein aufgeklappter Effekt geht über beide Spalten; auf dem Handy rutscht
  die Leiste unter den Slider.
- Zuweisungen, deren Effekt gerade nicht in der Kette ist oder dessen Regler unbekannt sind, stehen weiter mit den
  alten Feldern unter den Effekten. „Learn from pedal“ und „Read Pedal“ stehen darunter.
- Bridge: `param_result` sagt der Oberfläche, wie das Lernen eines Controllers ausging (vorher nur Activity).
- Im Browser gegen die laufende Bridge geprüft (Preset 013, THRSHLD 0→60): Eintippen außerhalb springt zurück und
  speichert nichts; 40 eintippen, Griff ziehen (45), ⇅ und Kurve speichern – danach alles wieder auf 0→60 linear.
  Symbol antippen → „Move the expression pedal or a controller now…“ → Abbrechen.

## Read Pedal am Pedal, Link am Ende, Punkt verschiebbar, 2026-10-21

- **Read Pedal am Pedal (01:23):** PC-Modus an (`52` → `00 00`), Liste `60 25`/`60 26` je Eintrag beantwortet mit
  `60 04 25 … <Name, 12 Stellen> 01 00 00 <Größe>`, Ende der Sitzung `60 21 40 …` → `60 03 7F 7F 7F 7F 0F`, `60 09` →
  `60 05 00` (nach 0,9 s), PC-Modus aus `53` → `00 00`, Edit-Modus wieder an. Die Bridge hielt die Liste nach 7
  Dateien für beendet: „160_COMP.ZD2“ hat 12 Zeichen, füllt alle 12 Stellen, danach kommt 01 statt 0 – der Name
  war für `parse_file_entry` keiner (das traf auch alle Listen, die früher am Installer vorbeigingen: Namen mit 12
  Zeichen wie KKFLNG1U.ZD2 fehlten). Jetzt 12 Stellen gelesen; Ende wie beim Installer (Antwort ohne `04` oder ohne
  Name). Nichts geändert, weil die Liste zu kurz war (Schutz griff).
- Beim PC-Modus lädt das Pedal das Preset neu wie gespeichert: 013 hatte 160 Comp ungespeichert, danach „Empty“.
  Die Oberfläche fragt jetzt vor dem Lesen.
- Oberfläche (Wunsch): Symbol vorne weg; rechts „Link“ (antippen, Controller bewegen) bzw. bei verknüpften Reglern
  eine Liste: Pedal / CC n / „Move a controller…“. Der hohle Punkt zeigt den echten Reglerwert und lässt sich ziehen
  (setzt den Regler wie ein freier Slider; am Pedal probiert: 38 und 40 gesendet und bestätigt). Das + ist
  gezeichnet und sitzt mittig zum Titel.

## Schalter wie Parameter, Read Pedal beim Start, 2026-10-21

- Effekt-Schalter: „Learn“ steht gleich hinter der Lampe (statt „no controller“); zugewiesen „CC 22“ wie bei den
  Reglern, daneben Value/Press und ein × zum Entfernen.
- **Read Pedal beim Start** (Wunsch des Nutzers): nachdem die Namen der Presets gelesen sind, einmal von selbst –
  nur wenn das Preset auf dem Pedal genau dem gespeicherten entspricht (Vergleich bis zur Länge, ohne PRM2 10–12),
  denn der PC-Modus lädt es neu. Sonst nicht, mit einer Zeile in Activity. Braucht `read_pedal`, `query_preset`,
  `query_patch`. 2 neue Tests.
- **Am Pedal beim Start (02:01:45):** 52 → Liste: 295 Dateien in 0,5 s, das Ende kam als `60 03 7A 7F 7F 7F 0F`
  (keine `04`) und wurde erkannt; `60 21 40 …` → `60 03 7F 7F 7F 7F 0F`, `60 09` → `60 05 00` nach 0,6 s, 53. Das
  Pedal war 1,1 s im PC-Modus. Ergebnis: 138 Effekte, die die Tabelle beschreibt; zur Auswahl stehen jetzt 139
  (mit BPM). Von der Tabelle fehlt auf dem Pedal nur N2Z Bank.

## SlamDrive neu, N2Z Bank, 2026-10-21

- Der Installer hat um 02:13 den neuen SlamDrive aufgespielt (Adapted); die Tabelle hat jetzt seine Reihenfolge:
  CmpLo, Deep, Bass, XFreq, CmpHi, Drive, Shape, Mid, MidF, High, LvlLo, LvlHi (XFreq zeigt am Pedal 40–500 Hz,
  in der Bridge bleibt es die Stufe 0–100). Keine Zuweisung oder Szene nutzte SlamDrive. Am Pedal gespeicherte
  Presets mit dem alten SlamDrive spielen jetzt mit vertauschten Reglern (Hinweis der anderen Session).
- „Wider“ (statt WideChorus) ist noch nicht gebaut: install_all/WIDECHOR.ZD2 heißt noch WideChorus. Tabelle neu,
  sobald es installiert ist.
- **N2Z Bank** fehlte nicht: Auf dem Pedal ist seit dem 13.10. der Build von nam2zoom 1.1.1 (04001787). Die Tabelle
  führte daneben den alten Build 09000F87 derselben Datei N2ZBANK.ZD2, und Read Pedal ordnete die Datei der ersten
  Nummer zu. Jetzt gilt beim Bauen der Tabelle: eine Datei auf dem Pedal ist ein Effekt, der neuere Build bleibt.
  138 Effekte. 1 neuer Test.

## Name eines leeren Presets, 2026-10-21 (Fehler, vom Nutzer bemerkt)

- Preset 014 hieß „Empty“. Die Bridge setzte FMSynth hinein (03:08:19), und das Pedal nannte das Preset **von selbst**
  „USER-012“. Gemeldet hat es davon nur einen Teil: `64 20 00 5F <Stelle> <Zeichen>` für U, S, E, R (Stellen 0–3),
  „1“ (Stelle 6) und Leerzeichen an Stellen 16 und 20–27, die schon Leerzeichen waren – das „-“, die „0“ und die „2“
  (Stellen 4, 5, 7) nicht. Die Bridge setzte die Zeichen in „Empty“ ein und zeigte „USERy 1“. Im Patch (gelesen beim
  Speichern um 03:10:18) stand richtig „USER-012“.
- Fix: Nach jedem Namenszeichen, das das Pedal von sich aus meldet, liest die Bridge das Patch (höchstens einmal pro
  Sekunde, das letzte nach dem letzten Zeichen) und zeigt den Namen daraus. Gesendet wird dabei nichts Neues
  (`query_patch`). 1 neuer Test mit genau den gemeldeten Stellen; ohne den Fix zeigt er „USERy 1“.

## Effektdateien vom Pedal lesen, 2026-10-21 (Wunsch des Nutzers: „nicht jeder hat einen Ordner“)

- Wer die Bridge bekommt, hat die Backups und Adapted-Ordner nicht, aus denen `zoom_effects.json` gebaut wird.
  Darum liest die Bridge jetzt die Effektdateien, die die Tabelle nicht beschreibt, **vom Pedal selbst**: beim
  Read Pedal, solange das Pedal ohnehin im PC-Modus ist, nach der Liste. Neue Nachrichtenart `read_effect_file`
  mit eigener Freigabe: nur Öffnen zum Lesen der einen `.ZD2` aus der gerade gelesenen Liste (8.3-Name), ihr Status
  (`60 05 00`) und die nächsten 512 Byte (`60 22 14 2F 60 00 0C 00 04 00 00 00`), genau wie der Installer beim
  Backup; jede Antwort mit CRC geprüft. Schreiben, Löschen, andere Dateien bleiben verboten.
- **Was als „nicht beschrieben“ gilt:** Kein Eintrag für den Dateinamen – oder ein Custom-Effekt mit anderer Größe
  (anderer Build, wie SlamDrive heute). Die Liste des Pedals nennt die Größe (7 Bit je Byte ab Byte 27, gemessen:
  160_COMP.ZD2 = 15907, SLAMDRV.ZD2 = 33993). Zooms eigene Effekte nur nach Namen: Bei anderer Firmware können ihre
  Dateien anders groß sein, ihre Regler aber nicht – sonst würde jemand mit anderer Firmware beim ersten Start
  alle ~100 neu lesen.
- **Nur so weit wie nötig:** Die Regler-Tabelle liegt etwa in der Mitte der Datei. An 27 Dateien, die der Installer
  am 17.–21.10. gelesen hat (aus dem Log wieder zusammengesetzt), ergab das Teil-Lesen dieselben Regler wie die
  ganze Datei, nach 10 bis 37 von 19 bis 67 Blöcken. Ein Block dauert beim Installer 4–9 ms (Status, Lesen,
  Status); die meiste Zeit kostet das Ende der Datei-Sitzung, `60 09` wird erst nach ~0,6 s beantwortet – zusammen
  knapp eine Sekunde pro Effekt. Höchstens 20 auf einmal, der Rest beim nächsten Mal. Die Oberfläche wartet bis 60 s.
- Gelesenes steht in `pedal_effects.json` (auf dem Pi `pedal-effects.json` neben der Einstellungsdatei, ohne
  Ordner nur für diesen Lauf) und ersetzt den Tabelleneintrag derselben Datei. `zoom_effects.json` hat jetzt zu
  jeder Datei die Größe (neu gebaut, sonst unverändert: 138 Effekte, 97 von Zoom, 41 andere).
- Simulation: Das Pedal liefert Dateien wie das echte (Liste mit Größe, Öffnen, Status, Blöcke mit CRC, die
  Antworten wie im Log). Tests u. a. mit der echten Größe aus der Liste und dem echten ersten Block von Wider.
- **Noch nicht am Pedal:** Die Bridge hat noch keine Datei selbst gelesen. Nachrichten und Reihenfolge sind die aus
  dem Backup-Code des Installers (wie zoomzt2: zweimal öffnen, je Block Status – Lesen – Status, danach `60 21 40 …`
  und `60 09`); die Antworten des Pedals im Log passen dazu. **Anders als der Installer** schließt die Bridge eine
  Datei mitten drin, sobald die Regler-Tabelle gelesen ist – das zeigt kein Log. Beim Hardware-Test darum danach
  noch einmal die Liste lesen (Read Pedal). Freigabe `read_effect_file` am 21.10. auf „Freigeben“ des Nutzers von
  mir eingetragen.
- **Prüfung durch vier Agenten** (drei Blickwinkel, ein Prüfer), 10 Funde bestätigt, alle behoben:
  - Bricht die Liste ab, schließt die Bridge jetzt auch die Datei-Sitzung (`60 21 40`, `60 09`), bevor sie den
    PC-Modus verlässt – wie der Installer nach einem Fehler.
  - Jede Antwort wird nach ihrer Form zugeordnet (aus dem Log des Installers um 02:20: Öffnen `60 04 20`, das zweite
    Öffnen manchmal `60 03 7F 7F 7F 7F 0F`, Status/Schließen `60 03 …`, Leseanfrage `60 04 22 00`, Daten
    `60 04 22 01`, hinter dem Ende `60 03 78 7F 7F 7F 0F`, `60 09` → `60 05 00` nach 0,6 s): eine verspätete Antwort
    wird übergangen, nicht für die nächste gehalten.
  - Antwortet das Pedal bei einer Datei nicht mehr, endet das Lesen für dieses Mal, die Liste gilt trotzdem.
    Nicht geöffnete oder per CRC falsche Dateien kommen beim nächsten Mal nach den anderen dran; eine Datei ohne
    Effekt wird gemerkt und nicht wieder gelesen. Höchstens 20 Dateien und keine nach 20 s begonnen; Activity sagt
    vorher, welche. Der Bestätigungstext nennt jetzt bis zu einer halben Minute.
  - Nach dem Lesen prüft das nächste Patch jeden Effekt neu (vorher blieb ein gelesener Effekt „anderer Build“,
    und seine Regler kamen nicht durch die Erlaubnisliste).
  - Synth-Effekte: Ihre Belegung wird beim Start gebaut. Wird eine andere Belegung gelesen, spielt die Bridge bis
    zum nächsten Start nur Noten (sonst hätten Regler-Nachrichten nach der alten Belegung durchgehen können); ab dem
    nächsten Start die gelesene (`zs.use_installed_synths`).
  - Eine neu gebaute Tabelle mit demselben Build (gleiche Größe) gewinnt gegen das Gelesene; der Cache ist nach
    Dateinamen geordnet, eine ID ist ein Effekt (Konflikte stehen in Activity).
- 9 neue Tests dazu (u. a. Liste bricht ab, Datei antwortet nicht, Synth anderer Build, gleiche Größe, eine ID).
- **Nachprüfung durch zwei Agenten:** die zehn Fixes bestätigt, dabei neue Randfälle gefunden, alle behoben:
  - Ein langes Read Pedal hielt die Hauptschleife an: Klicks bekamen „not responding“ und liefen 20 s später doch.
    Jetzt verfällt eine Anfrage, auf die die Oberfläche nicht mehr wartet; zwischen den Dateien wird der Zustand
    veröffentlicht, der Knopf zeigt „Reading…“.
  - Nach jeder vollständigen Liste vergisst die Bridge, was sie von Dateien gelesen hat, die nicht mehr (oder nicht in
    dieser Größe) auf dem Pedal sind – sonst hätte ein alter Build eine richtige oder neu gebaute Tabelle verdrängt.
  - Eine Datei, die vor ihrer Größe endet, gilt als nicht gelesen (nicht als Effekt ohne Regler). Nicht lesbare
    Dateien werden gemerkt und auch nach einem Neustart erst nach den anderen versucht; eine leere Datei wird nicht
    geöffnet. Warnungen zu doppelten IDs nennen jetzt den, der wegfällt, und kommen aus dem aktuellen Stand.
  - Synth-Effekte: Liegen die Key-Regler im gelesenen Build woanders, spielt die Bridge ihn bis zum Neustart gar
    nicht (sonst gingen Noten in andere Regler); einen Build ohne passende Key-Regler kann sie nicht spielen und sagt
    das, statt „beim nächsten Start“ zu versprechen.
  - Pi: Die Bridge liest das Gelesene (`pedal-effects.json` neben der Einstellungsdatei) und baut die Synth-Belegungen,
    *bevor* sie die Einstellungen prüft; ohne Netz nur lesend. `export.py` nimmt das Gelesene des Computers mit auf
    die Karte (was der Pi selbst gelesen hat, bleibt) und prüft ebenfalls mit den gelesenen Belegungen.
  - Die Simulation kennt jetzt auch die drei übrigen Antwortformen aus dem Log (zweites Öffnen abgelehnt mit Status
    -1 danach, `60 05 00` nach 0,6 s, Dateiende als leerer Teil). Zusammen 34 Tests für das Lesen von Dateien.
- **Am Pedal, 04:02 (mit dem Nutzer, Freigabe auf sein „Freigeben“):** Bridge aus einem Testskript gestartet, dessen
  Tabelle Wider, SlamDrive und HOGi nicht kannte; Gelesenes ging in den Scratchpad, nicht in die Einstellungen.
  Read Pedal las die Liste und dann **4 Dateien** vom Pedal: DRUMBOX.ZD2 (auf dem Pedal 30163 Byte, die Tabelle hat
  30065 – ein neuerer Build), HOGI.ZD2, SLAMDRV.ZD2, WIDER.ZD2. Gelesen jeweils nur bis zur Regler-Tabelle: 37 von
  59, 36 von 59, 36 von 67, 22 von 38 Blöcken. 401 `read_effect_file`-Nachrichten, genau 2 × Öffnen + 3 je Block.
  PC-Modus 4,9 s (52 um 24.759, 53 um 29.647). Das zweite Öffnen beantwortete das Pedal dreimal mit
  `60 03 7F 7F 7F 7F 0F` (wie beim Installer), jedes Schließen mit `60 03`, `60 09` nach ~0,6 s mit `60 05 00`.
  Ergebnis: alle vier mit derselben ID und genau den Reglern der Tabelle. Kein WARNING, nichts blockiert.
- **Danach noch einmal Read Pedal (04:03):** alle 295 Einträge der Liste, keine Datei erneut geöffnet, 1,2 s PC-Modus –
  das Pedal arbeitet nach dem vorzeitigen Schließen normal weiter.

## Werte als Text, 2026-10-21 (Wunsch des Nutzers: „im Effects Feld haben die Parameter nur Zahlen“)

- Viele Regler zeigen am Pedal Text statt Zahl (DrumBox Pattern Rock … Click, Delay Time 1 … 4000 und dann
  Notenwerte). Die Oberfläche zeigt sie jetzt genauso, Notenwerte als Brüche (Entscheidung des Nutzers): 1/16,
  1/8. punktiert, 1/8T Triole, 1/4×2. Gespeichert wird weiter die rohe Zahl.
- **Woher die Texte kommen:** Wort 9 (+36) eines Knopf-Eintrags der Parametertabelle ist die Anzeigefunktion
  (A4 = Wert, B4 = Puffer, A6 = Länge; 0 = Zahl), Wort 12 (+48) Bit 0x4 markiert den letzten Knopf.
  - Eigene Effekte: eine 13-Befehls-Vorlage, die 8 Byte je Wert aus einer Texttabelle kopiert; `effect_names`
    liest sie mit struct (`read_zd2` gibt `"labels"` mit zurück). Read Pedal liest dafür im Mittel 5,7 Blöcke mehr
    (höchstens HITS.ZD2: 21 → 90 von 104).
  - Zooms eigene Effekte rechnen den Text im Code. `tools/value_labels` (nur Mac, Entscheidung des Nutzers: ins
    Repo unter `tools/`) lässt die Funktion für jeden Wert in einem C674x-Interpreter über dis6x-Listings laufen,
    je Wert zweimal mit anderen Registern, Stack, Puffer und RCPSP-Schätzung, und verweigert alles nicht
    Modellierte; gelesen wird nur `.const`. Testfixtures sind eigene, mit TIs cl6x kompilierte Funktionen –
    keine Disassembly von Zooms Code im Repo.
  - Ergebnis in `zoom_effects.json`: 138 von 138 Effekten aus den Ordnern (Backups, Adapted, MS EFFECTS,
    install_all), **257 Knöpfe in 108 Effekten, 0 Fehler**, identisch mit der Recherche bis auf N2Z Bank (s. u.).
    Vorlage und Interpreter stimmen in allen 107 Vorlagen-Knöpfen (44 Effekte) überein. Knöpfe, deren Texte nur
    ihre Zahlen sind (MemWatch Mark: 0, 1), bekommen keine.
  - Die Tabelle steht jetzt mit einem Effekt pro Zeile, jede Textliste auf eigener Zeile (`write_table`);
    Notenzeichen bleiben die des Pedals (0x16 = 1/32 … 0x1A = 1/2), die Oberfläche schreibt sie aus.
- **HoldDelay:** `read_zd2` las bisher einen Knopf zu weit (`['2', 20]`, Parameter 11). Seit dem Ende am Flag hat
  HoldDelay 9 Knöpfe; nur dieser Eintrag geändert (alle Kopien von HLDDLY1U.ZD2 in PROTOTYP: 34347 Byte, 9
  Knöpfe). Keine Zuweisung, Szene oder Messung nutzte ihn.
- **Tabelle nicht neu gebaut:** In PROTOTYP liegen inzwischen 12 neue eigene Effekte (09000F8B–F96) und andere
  Builds von KeyWave (28587 statt 28589) und DrumBox (30163 statt 30065). Ob das alles auf dem Pedal ist, ist
  offen – darum nur HoldDelay korrigiert und die Texte eingetragen; die Effekte selbst wie vorher. Was die Tabelle
  nicht beschreibt, liest Read Pedal ohnehin vom Pedal, samt Texten aus der Vorlage.
- **N2Z Bank** (N2ZBANK.ZD2) wird bei jedem Read Pedal gelesen (24 → 50 Blöcke): Eine Bank mit anderen Modellen
  kann gleich groß sein. Das Backup von heute 04:38 hat schon eine neue Bank (AMPG1, AMP75 … statt SVTSM, SVTUH …),
  die Tabelle hat deren Texte. Ein eigener Effekt, den das Pedal in anderem Build hat, behält die Texte der
  Tabelle, solange seine Knöpfe gleich sind.
- **Bridge und Oberfläche:** Knöpfe mit Texten haben `"text": true`, `effects.labels_version` steigt mit Tabelle
  oder Gelesenem, `GET /api/labels?id=XXXXXXXX` gibt `{"labels": {"<param>": [...]}}` (400 bei falscher ID oder
  während Read Pedal die Bridge hält – die Oberfläche fragt nach 3 s erneut). `ui.html` fragt je Effekt-ID der Kette
  einmal; jede Wertanzeige geht durch `valueText`. Heel/Toe: Text wie angezeigt, Zahl gleich einer angezeigten,
  oder `#n` für den Rohwert; bis 32 Werte eine Liste. Wertespalte 10ch (OD-BOOST 63 px, 8ch nur 53 px),
  Link-Streifen 236 px; die Heel/Toe-Knöpfe ohne Großschreibung („Rock“, nicht „ROCK“).
- **Prüfung durch Agenten** (Werkzeug, Tabelle/Bridge, Oberfläche, dann ein Prüfer): fünf Fehler behoben – die Kette
  behielt nach dem Vergessen beim Read Pedal alte Namen/Texte; Notenzeichen in der Texttabelle eines eigenen
  Effekts galten als nicht druckbar; ein anderer Build vom Pedal verlor die Texte der Tabelle (Pong Delay macht
  seine nur im Code); eine abgelehnte `/api/labels`-Anfrage galt bis zur nächsten Version als „keine Texte“;
  Heel/Toe in Großbuchstaben. Im Browser gegen eine simulierte Bridge geprüft: „1/4x2“ als Toe von Delay Time
  ergab 948, DrumBox Pattern als Liste mit ausgegrauten Werten außerhalb des gelernten Bereichs, 375 px ohne
  waagrechtes Scrollen.
- **Am Pedal (04:27–04:35, mit dem Nutzer):** Preset 018, Delay Time per Bridge auf 941 gesetzt, der Nutzer hat
  Time und Mode am Pedal durchgedreht (alle Stellungen kamen in der Bridge an). Anzeige wie die Texte: Zahlen bis
  4000, dann Notenwerte, Triolen als Viertel bzw. Halbe mit „3“ (941 zwischen 1/16 und 1/16. → 1/8T, 944 zwischen
  1/8 und 1/8. → 1/4T), ab 948 Viertel mit 2x bis 16x; Mode Short, Long und ein Metronom-Symbol (Text im Code:
  BPM SYNC); OctSynth Type „Brass“. Danach Time wieder 943, nichts gespeichert.

## KeyBand und Pluck, 2026-10-21 (Wunsch aus der Prototypen-Sitzung im Auftrag des Nutzers)

- Die Tastatur spielt zwei neue Effekte wie die fünf bisherigen: **KeyBand** (Nummer 0x000F94, gebaut `07000f94`,
  vom Installer in SFX als `09000f94`) und **Pluck** (0x000F8F, gebaut `07000f8f`). Beide eine Stimme, kein LFO,
  **kein Mix**: Level ist der Pegel dessen, was sie dazugeben, der Bass läuft immer unverändert durch. Knöpfe aus
  `prototypes/keyband/manifest.json` und `prototypes/pluck/manifest.json` in PROTOTYP (KEYBAND.ZD2 in `install_all`
  hat dieselbe Liste) als `BAND_KNOBS` und `PLUCK_KNOBS` in `zoom_sysex.py`. In `zoom_effects.json` stehen sie nicht:
  nur über die Nummer bekannt, bis die Tabelle oder Read Pedal ihre Dateien hat. Am Pedal noch nicht gespielt.
- Eine neue Note (Key von 0 oder ein Sprung um mehr als 9 Klicks) wird sofort angeschlagen, kleinere Schritte sind
  Bends. Pluck zupft bei jeder neuen Note (nur mit Trig auf Key), überhört Bends und klingt mit Decay aus; Key 0
  beendet das nicht, die Bridge schickt beim Loslassen trotzdem Key 0 wie bei den anderen, damit derselbe Ton danach
  wieder eine neue Note ist. Töne außerhalb C1–E6 hält der Effekt selbst dort fest.
- **Entscheidung A des Nutzers: Anschlagstärke → Vel von KeyBand, automatisch.** KeyBand liest Vel beim Anschlag.
  Vor jedem Key, der eine neue Note anschlägt, schickt die Bridge die Anschlagstärke der Taste, auf den Knopf
  umgerechnet (`round(v · 100 / 127)`, mindestens 1; 127 → 100, 100 → 79, 64 → 50), dann den Key – beide sofort über
  denselben Weg (`sender.send`, nicht über die Warteschlange in `_flush`), Vel zuerst; der Key wartet nur diese eine
  Nachricht. Nicht, wenn das Pedal den Wert schon hält (laut Patch, am Pedal gedreht oder zuletzt gesendet), nie bei
  Bend-Schritten; beim Wiederholen eines unbestätigten Keys nur, wenn auch Vel unbestätigt ist (siehe unten).
  Arpeggiator und Sequencer: der Anschlag der Taste, aus der die Note kommt (Sequencer: die Taste, von der das
  Muster spielt; Arpeggiator: die Akkordtaste, die Note selbst vor einer Oktave darunter); unbekannt: Vel bleibt.
  Ist dem Knopf in diesem Preset ein Regler zugewiesen (Tastatur-Regler oder Expression), gewinnt der Regler: kein
  automatisches Vel.
- **Entscheidung B des Nutzers: Trig von Pluck auf Key.** Pluck zupft über den Key-Knopf nur mit Trig = 0 (Key);
  sein Standard ist 1 (Bass), dann überhört es Key. Schlägt die Tastatur (oder Arpeggiator/Sequencer) eine Note auf
  Pluck an und steht Trig laut Patch bzw. eigenem Senden nicht auf 0, setzt die Bridge Trig zuerst auf 0, gleiche
  Reihenfolge wie bei A, und sagt in Activity „Pluck: Trig set to Key, so the keyboard plays it – save the preset on
  the pedal to keep it“ (einmal je Setzen; wird Trig am Pedal wieder auf Bass gedreht, beim nächsten Ton erneut).
  Steht Trig schon auf 0: nichts. Ein dem Knopf zugewiesener Regler gewinnt.
- **Umsetzung:** `SynthEffect.velocity` (der Knopf für die Anschlagstärke, nur KeyBand: "Vel") und
  `SynthEffect.needs` (Knöpfe, die die Tastatur auf einem Wert braucht, Knopf → (Wert, Anzeige am Pedal), nur Pluck:
  `{"Trig": (0, "Key")}`); hat ein anderer Build den Knopf nicht, fällt das weg. In `bridge.py`:
  `Session._before_note` (aus `_play`, direkt vor `_send_key`; die Meldung erst nach dem Key), `_strike_knob`
  (sendet einen solchen Knopf), `_velocity`, `Keys.velocities` (je Taste der letzte Anschlag). Gesendet wird nur,
  was die Allowlist gerade hat: nicht bei einem anderen Build, als die Tabelle beschreibt (dort spielt die Tastatur
  nur Noten, und die Allowlist hat nur die Key-Knöpfe), auch nicht direkt nach Read Pedal (`_take_read` leert
  `unmatched`, die Allowlist kommt erst mit dem nächsten Patch neu) – sonst würde `NotAllowed` den Key mitnehmen.
  Keine neue Nachrichtenart: `set_param` über `ZoomSender.send`, Allowlist `synth_targets` (jeder Knopf des
  Effekts in seinem Platz).
- **Echo des Pedals:** Es bestätigt diese Werte wie jeden mit `64 20 01`. In `_on_zoom` geht das in den
  allgemeinen Zweig: `knob_values` folgt (der Wert steht schon beim Senden drin). Ein Ack ist nie ein am Pedal
  gedrehter Knopf, also kein `_on_knob` – nichts wird gelernt, keine Warnung, keine Zählung als Note, kein
  „anderer Build“ (Vel ≤ 100 und Trig 0 passen ohnehin). Ein Target für den Knopf gibt es nur bei einer Zuweisung:
  wird sie gesendet, sendet die Bridge selbst nichts; ist sie gesperrt (Bereich größer als der Knopf), sendet die
  Bridge, und `_on_ack` kehrt sofort zurück, weil das Target nichts erwartet.
- **Nach der Durchsicht (gleicher Tag):** `strike_awaiting` hält je (Platz, Parameter) die vor einer Note
  gesendeten, noch unbestätigten Werte in Sendereihenfolge. (1) Wird der Key wiederholt (`_key_miss`, Key > 0),
  gehen diese Werte vorher noch einmal mit – vorher ging bei einem tauben Pedal (Effektmenü offen, siehe
  `_key_miss`) nur der Key wieder, Trig blieb auf Bass, obwohl Activity „set to Key“ sagte, und die nächste Note
  schickte Trig auch nicht mehr. Die Meldung kommt nicht noch einmal. (2) Ein Ack eines älteren Werts, während ein
  neuerer von uns unterwegs ist (das Pedal bestätigt in Sendereihenfolge), ändert `knob_values` nicht mehr: vorher
  konnte Vel 100 (spät bestätigt) die gerade gesendeten 50 überschreiben, und die nächste Note mit 127 ging ohne Vel
  raus. Ein am Pedal gedrehter Knopf leert die Liste (sein Wert gilt). Geleert wird sie auch bei Presetwechsel und
  neuer Kette. Nicht nach einer Zeit vergessen: das Pedal bestätigt einen Wert, den es schon hält, gar nicht – sonst
  ginge Trig bei jeder Note wieder raus. (3) Die Allowlist-Prüfung in `_strike_knob` (siehe Umsetzung) statt der
  Prüfung auf `unmatched`.
- **Nebenwirkung:** Eine Zuweisung ohne Effektnamen aus Dateien vor 2026-10-17 („Level“: n) fiel bisher weg, weil
  kein Effekt mehr Level hatte; jetzt gilt sie für Level von KeyBand und Pluck (wie „Atk“ nun auch KeyBand/Atk
  setzt). `test_editor` entsprechend angepasst.
- **Tests:** in `tests/test_key_effects.py` (beide IDs in 07 und 09, Knopflisten, keine LFO-Hälften und kein Mix,
  Vel vor dem Key und nur wenn nötig, nicht bei Bends – auch nicht bei einer neuen Taste, die der Effekt als Bend
  nimmt –, zwei Tasten zugleich, ein spätes Ack eines älteren Vel, Regler gewinnt (controls.json und Zuweisung im
  Preset), anderer Build auch direkt nach Read Pedal, Arpeggiator über zwei Oktaven und Sequencer, das Ack wird
  nicht gelernt; Trig vor dem ersten Key, Meldung einmal, nichts bei 0, am Pedal zurückgedreht, Regler und
  Expression gewinnen, eine gesperrte Zuweisung nicht, ein verlorenes Trig geht mit dem wiederholten Key). `sim.py`:
  `World.whole_patch` – das Patch trägt alle Knöpfe statt nur des ersten, nur auf Wunsch, damit sich andere Tests
  nicht ändern.

## Bearbeiten ohne Controller, 2026-10-22 (Wunsch des Nutzers)

- Wunsch: „Wenn kein Keyboard, Sequencer oder Foot Controller angeschlossen ist, sollte ich trotzdem das Preset
  bearbeiten können.“ Bisher wartete die Bridge mit `synth:`-Abschnitt ohne Controller („Waiting for devices: no
  controller connected …“), ohne den Abschnitt war der Expression-Controller Pflicht (`zs.find_port`). Ging der
  letzte Controller, hieß es „Connection lost: no controller left“ bzw. „SINCO is gone“, und die Oberfläche konnte
  am Preset nichts mehr ändern (Editor, Effekte, Name, Szenen, Presets, Read Pedal).
- **Jetzt genügt das MS-60B+ allein.** Jeder Controller (Expression-Controller, Keyboard, externer Sequencer) ist
  optional, mit und ohne `synth:`-Abschnitt, und kommt oder geht, während die Bridge läuft (`Session._check_ports`,
  wie bisher nur mit Keyboard): „SINCO connected“ / „SINCO is gone“; geht eine Notenquelle, werden die Tasten
  geleert und das Gate geschlossen. Geht der letzte, bleibt die Session beim Pedal, kein „Connection lost“, kein
  neues Edit Enable. Ein leerer Port (`''`) bleibt keiner. Mehrdeutige Ports wie bisher über `_present` (einmal
  gesagt, ausgelassen); ohne `synth:` hieß das vorher „Waiting for devices: no unique port …“.
- **Activity:** „Ready: no controller → ZOOM MS Plus Series, firmware 1.20“ und dazu einmal „No controller connected –
  the preset can be edited in the interface; a controller joins when it is plugged in“ (`NO_CONTROLLER`): beim Start
  ohne Controller und wenn der letzte geht, nicht bei jeder Prüfung der Ports. Mit Controllern bleibt die Zeile wie
  sie war („Ready: SINCO + KOMPLETE KONTROL A61 → …“). Der Status für die Oberfläche bleibt „Ready“; welche Geräte
  fehlen, zeigt „Devices“ ohnehin („SINCO (not connected)“).
- **Lampe des Pi:** bleibt auf „waiting“ (langsam blinkend), solange nur das Pedal da ist, „ready“ (bzw. „fallback“)
  erst, wenn ein Controller dazukommt, und wieder „waiting“, wenn der letzte geht. Der Pi ohne Bildschirm ist zum
  Spielen da: „steady“ heißt, er lässt sich spielen. Sein Preset lässt sich trotzdem in seiner Oberfläche bearbeiten.
  README beim Licht.
- **Lernen:** Einen Effekt-Schalter lernen sagt „No controller is connected.“ jetzt, wenn wirklich keiner da ist
  (`Session.controllers` leer); `effects.controller` im Zustand ebenso (die Oberfläche prüft das schon vor dem
  Klick). Einen Parameter oder eine Szene lernen wartet wie bisher auf den nächsten bewegten Controller – einer, der
  inzwischen eingesteckt wird, kommt dazu und kann bewegt werden. Der Knopf eines Synth-Effekts verlangt weiter ein
  Keyboard („The keyboard is not connected.“).
- **Einstellungen (meine Entscheidung, damit „das Pedal allein“ auch eingestellt werden kann):** `Bridge.__init__`
  verlangt die CC des Expression-Pedals nur noch, wenn ein Expression-Controller eingetragen ist (vorher auch ohne
  `synth:` mit leerem `chocolate:` „config.yaml is incomplete“); „Devices“ nimmt „None“ für alle Controller (vorher
  „With neither a keyboard nor an expression controller there is nothing to bridge.“); `config_schema.validate`
  verlangt keinen Controller mehr (vorher „Neither an expression controller nor a keyboard is set up.“). Lässt sich
  zurücknehmen, wenn eine Einstellung ohne jeden Controller nicht gewollt ist – für den Wunsch selbst (Controller
  eingetragen, aber nicht eingesteckt) braucht es das nicht.
- Keine neue Nachrichtenart; ans Pedal geht nichts anderes als vorher. Am Pedal noch nicht ausprobiert.
- **Tests:** angepasst `test_synth` (ohne Controller verbindet die Bridge, das Keyboard kommt dazu; ohne `synth:`
  kommt der Expression-Controller dazu und geht wieder; nur das Pedal eingestellt startet), `test_external` (der
  Sequencer allein geht: die Session bleibt), `test_bridge` (der Controller aus- und eingesteckt: eine Session),
  `test_devices` („None“ für alle wird genommen), `test_export` (eine Datei ohne Controller ist gültig). Neu
  `tests/test_pedal_alone.py`: nur das Pedal – Knopf im Editor, Effekt aus, Name, „Learn“ eines Schalters abgelehnt,
  die Zeile einmal in zwei Sekunden mit Portprüfungen alle 50 ms; Keyboard und Expression-Controller kommen und
  gehen, Noten, Expression und danach der Editor; die Lampe des Pi „waiting“ → „ready“ → „waiting“.
- **Nach der Prüfung:** Die Zeile sagt nur, was stimmt (`Session._no_controller`): ist gar kein Controller
  eingetragen, heißt sie „No controller set up – the preset can be edited in the interface; a controller is chosen
  under Devices“ (`NO_CONTROLLER_SET_UP`), denn keiner kann dazukommen; ohne Oberfläche (Pi ohne `--listen`) fehlt
  der Teil über die Oberfläche („No controller connected – a controller joins when it is plugged in“). `--no-ui` auf
  dem Rechner sieht die Bridge nicht, dort bleibt der Satz über die Oberfläche. Eine `config.yaml` ganz ohne
  `chocolate:`-Zeile gilt als „kein Expression-Controller“ (`cfg["ports"].get("chocolate")` an allen Stellen); ohne
  `synth:` und mit `cc: null` lief sie sonst in einen `KeyError` statt in eine Meldung. `tests/sim.py`: eine Welt
  ohne offenen Port (die Bridge wartet auf ein Gerät) endet eine Sekunde nach ihrer Länge beim Auflisten der Ports –
  vorher hingen die neuen Tests gegen den alten Code endlos, statt zu scheitern. README: „reconnects after
  replugging (tested with the controller …)“ stimmte nicht mehr, ein Controller kommt jetzt ohne neue Verbindung
  zurück. Tests: `test_pedal_alone` (ohne `chocolate:`-Zeile; die Lampe des Pi mit dem Text ohne Oberfläche),
  `test_synth` (nur das Pedal eingestellt: „No controller set up …“), `test_change_effect` (KeyWave unter zwei IDs).

## Eigene Effekte in den Kategorien des Pedals, 2026-10-22 (Frage und Entscheidung des Nutzers)

- Die Effektauswahl hatte alle eigenen und Community-Effekte in einer Gruppe „Custom and community“, weil das
  erste Byte der ID nur sagt, wohin der Installer einen Effekt gelegt hat (alle 35 Prototypen sind als 07 gebaut,
  29 der 41 eigenen Effekte der Tabelle liegen in SFX). Auf die Frage des Nutzers, warum sie nicht in den
  Kategorien stehen, hat er „wie am Pedal“ gewählt: Jeder Effekt steht jetzt in der Kategorie, in der er
  installiert ist, wie im Menü des Pedals – die meisten eigenen in SFX, die Community-Effekte in Filter, Drive,
  Reverb usw.
- Ein eigener Effekt, den ein älteres Preset unter der ID eines früheren Builds meldete (KeyWave als 07000F7C, die
  Tabelle und die Dateiliste des Pedals: 09000F7C), stand vorher doppelt nebeneinander in „Custom and community“,
  nach der Umstellung in zwei Kategorien. Jetzt steht er einmal, unter der ID der Tabelle (`effect_choices`); ist
  die ältere die einzige gemeldete, steht sie in ihrer Kategorie. Steht er unter der älteren ID in der Kette, zeigt
  die Oberfläche sie dort wie bisher als eigenen Eintrag. In der `measurements.json` des Nutzers kommt das nicht
  vor.

## Am Pedal: ohne Controller, Pluck und KeyBand, 2026-10-22 (mit dem Nutzer: „Klappt alles“)

- **Start 02:38:** Keyboard da, Preset wie gespeichert, also Read Pedal beim Start: 9 Dateien vom Pedal gelesen
  (N2Z Bank, Dimension, FMSynth, KeyPoly, Orchestra, OrganSynth, Pluck, SlamDrive, VoxSynth), 9 s PC-Modus, keine
  Warnung; 148 Effekte zur Auswahl. Pluck (PLUCK.ZD2, 26683 Byte) und KeyPoly haben genau die Knöpfe der Bridge.
- **Ohne Controller:** Keyboard abgezogen (02:47:55) → „No controller connected …“, die Sitzung blieb; wieder
  eingesteckt → „KOMPLETE KONTROL A61 connected“. Unter Devices das Keyboard auf None (02:48:14) → „Ready: no
  controller → ZOOM MS Plus Series“; danach am Pedal Effekte geschaltet, in der Oberfläche einen Effekt gelöscht und
  Pluck eingesetzt – alles ohne Controller.
- **Pluck (03:00):** Keyboard wieder gewählt → „Pluck is effect 1 – the keyboard plays it“, beim ersten Ton
  „Pluck: Trig set to Key …“; Trig (Parameter 4) genau einmal auf 0 gesendet und bestätigt.
- **KeyBand (03:01–03:03):** in der Oberfläche eingesetzt und gespielt: 445 Key-Werte gesendet, 425 bestätigt
  (20 vom nächsten überholt, wie bei schnellen Tönen bekannt), Bestätigung im Median 9,7 ms, höchstens 21,8 ms.
  235-mal Vel vor dem Ton (Werte 20 … 97, je nach Anschlag), 229 davon bestätigt. Kein WARNING. Der Nutzer:
  „Klappt alles“.

## DSP-Grenze des MS-60B+ gemessen, 2026-10-22 (mit dem Nutzer, 03:26–03:56)

- Jede `.ZD2` gibt ihre DSP-Last selbst an: ein float hinter den 16 Byte des INFO-Abschnitts (KeySynth 52,
  KeyBand 55, Pluck 75; Zooms Effekte 6,64 bis 142,02, die Bass-Amps 103–142; N2Z Bank 150). Gemessen wurde, ob
  das Pedal die Summe dieser Angaben prüft, mit Effekten aus dem neuesten Backup, über die Bridge eingesetzt
  (`change_effect`), nichts gespeichert:
  | Kette | Summe | Pedal |
  |---|---|---|
  | SUN CB + BaTalkSyn | 281,53 | PROCESS OVERFLOW |
  | Rockstar + BaTalkSyn | 274,01 | Overflow |
  | BassSynth + BaTalkSyn | 270,22 | Overflow |
  | AC 370 + BaTalkSyn | 266,45 | läuft |
  | EBH360 + BaTalkSyn + BassGEQ | 269,93 | läuft |
  | EBH360 + BaTalkSyn + LoopRoll | 270,04 | Overflow |
  | dasselbe, EBH360 vorher ausgeschaltet (vom Pedal bestätigt) | 270,04 | Overflow |
- **Ergebnis:** Das Budget ist **270** (wie nam2zooms „full patch budget“), und **ausgeschaltete Effekte zählen mit**.
- **Über MIDI prüft das Pedal nicht:** Es nimmt jeden Effekt an (die Bridge las ihn jedes Mal im Patch zurück, auch
  SUN CB + BaTalkSyn + AC 370 = 408) und zeigt dann PROCESS OVERFLOW. Ein Effekt im Overflow lässt sich nicht
  schalten: 16 Schaltbefehle für ihn, keiner bestätigt. Die Bridge muss die Grenze also selbst einhalten.
- Zwei Amps: Nach dem zweiten Amp verweigerte die Bridge selbst das Hinzufügen und Löschen eines Platzes („the amp
  bits of PRM2 do not match the effects“) – ihre Schutzregel für das Zurückschicken des Patches, kein Nein des Pedals.

## DSP-Anzeige, 2026-10-22 (Wunsch des Nutzers, Entwürfe A und C)

- **Wunsch:** die DSP-Last der Kette sehen und beim Einsetzen eines Effekts wissen, ob er noch passt. Aus den
  Entwürfen (Mockup mit Daten des Pedals) hat der Nutzer A und C gewählt („sieht gut aus“).
- **A, Titelzeile „Effects“:** zwischen „… linked“ und dem Expression-Pedal „DSP“, ein Balken mit einem Teil je
  Effekt in Kettenfolge (Breite = Anteil am Budget; ausgeschaltet blasser, zählt trotzdem; Hover nennt ihn,
  „AMPG SVT: 121“) und „204 / 270“; orange über 90 %, rot über dem Budget. Auch zugeklappt sichtbar. Bis 520 px
  Breite bricht die Zeile um, das Pedal steht in einer eigenen Zeile rechts. Effekte mit unbekannter Last fehlen im
  Balken, sein Tooltip nennt sie. Ist der Balken versteckt (keine Last bekannt), rückt das Pedal wie vorher nach
  rechts in die Titelzeile, auch auf dem Telefon (eine CSS-Regel mehr als im Entwurf, und die Telefon-Regel des
  Pedals gilt nur hinter einem sichtbaren Balken: `.dsp:not([hidden]) + .pedal`; im Review gefunden, im Browser
  bei 375 px und Desktop-Breite geprüft). Die Teile des Balkens werden nur neu gemacht, wenn sich Effekte, Lasten
  oder an/aus ändern (`dspSignature`; der Entwurf machte sie bei jeder Abfrage alle 400 ms neu, der Teil unter dem
  Zeiger samt seinem Namen wäre so jedes Mal ersetzt worden).
- **C, Effektwahl** (das native `<select>`): Ein Effekt, der neben den anderen Plätzen (an oder aus) nicht mehr
  passt, heißt „Name  – too much“ und ist deaktiviert, der Effekt des Platzes nie; sonst keine Zahlen (Wunsch des
  Nutzers nach dem Entwurf: „nur das too much anzeigen“ – die Kopfzeile „DSP free here“ und „Name · Last“ sind
  wieder raus; „Name · Last“ kam am selben Tag zurück, siehe „Effektliste mit DSP-Zahlen“, die Summe steht im Balken). Unbekannte Last: nie deaktiviert. Verglichen wird wie in der Bridge auf
  zwei Stellen genau, gezeigt gerundet. Die Teile nur für den Entwurf (`?choose`, `select.size = 22`, das
  Öffner-Skript) sind nicht übernommen.
- **effect_names.py:** `read_zd2` gibt `"dsp"` (float auf zwei Stellen; fehlt, wenn die Datei keine angibt oder
  der Wert keine Last ist: NaN, unendlich, negativ). Die Chunks gehen jetzt über `_chunks` (auch für `_program`);
  `_dsp` liest den float hinter den 16 Byte von INFO. `read_zd2_part` ist erst fertig, wenn auch die Last bekannt
  ist (INFO liegt vor DATA, beim Lesen vom Pedal also immer längst da). `build` übernimmt sie, `table()` reicht sie
  für gelesene Builds weiter, `dsp(effect_id)` gibt sie (None: unbekannt).
- **zoom_effects.json:** neue Option `python effect_names.py --dsp ORDNER …` (`set_dsp`): setzt nur `"dsp"` der
  vorhandenen Einträge, aus der Datei mit Name und Größe des Eintrags (sonst der neuesten dieses Namens, das wird
  gesagt), schreibt mit `write_table`; die Menge der Effekte bleibt (die neueren aus PROTOTYP bewusst nicht in der
  Tabelle). Gelaufen mit Backups, Adapted, „referenz/MS EFFECTS“, install_all: alle 138 Einträge. Beweis per
  JSON-Vergleich: ohne `"dsp"` gleich der Tabelle vorher, Schlüssel und Feldreihenfolge gleich; im Diff 138 Zeilen,
  jede nur um `"dsp": …` hinter `"size"` länger. DrumBox: keine Datei mit 30065 Byte, die neueste (30163) gelesen,
  38. FretSyn: dieselbe Größe 26001 sagt in älteren Backups 55, im neuesten und in Adapted 70 (nur der float
  unterscheidet sich) – 70 genommen. KeySynth der Tabelle (25054 Byte) 47, die ältere Datei in `keysynth/` (19040)
  52. KeyPoly, SlamDrive, FMSynth, OrganSynth, VoxSynth aus dem Backup 02:09 (die Builds der Tabelle). Während der
  Arbeit kam ein Backup um 04:07 dazu; ein zweiter Lauf ergab dieselben Werte. BPM hat keine Datei, keine Last.
- **Read Pedal:** `pedal_effects.json` hält `"dsp"` (null, wenn die Datei keine angibt; dann nicht wieder gelesen).
  Ein Effekt-Eintrag ohne `"dsp"` (vor heute gelesen) wird genommen, gilt aber als nicht beschrieben
  (`described`) und wird beim nächsten Read Pedal einmal neu gelesen – beim Nutzer die 16 Effekte des Caches
  (KeyBand, Pluck, Orchestra …), also beim nächsten Start bzw. Read Pedal einmalig rund 16 s PC-Modus mehr. Ein
  ungültiger Wert (Text, negativ, true) verwirft den Eintrag wie jeder andere nicht so geschriebene.
- **bridge.py:** `DSP_BUDGET = 270` (Kommentar verweist auf die Messung). `chain_entry` hat `"dsp"` (leerer Platz
  0, Synth-Effekt ohne Tabelleneintrag und unbekannte None), `effect_choices` ebenso, `state()["effects"]
  ["dsp_budget"]`. **Schutz:** `over_budget` in `change_effect` (der einzige Weg, der eine Effekt-ID an einen
  Platz bringt; Hinzufügen setzt 0, Verschieben ändert nichts): die anderen Plätze (an oder aus, bekannte Lasten)
  plus der neue Effekt, auf zwei Stellen gerundet, über 270 → abgelehnt, z. B. „LoopRoll takes 14 of the DSP, 14
  are free in this preset – the pedal would show PROCESS OVERFLOW“ (269,93 geht, 270,04 nicht). Unbekannte Last:
  durchgelassen. Gezeigt wird halb aufgerundet wie `Math.round` der Oberfläche (`_shown`).
- **Tests:** neu `tests/test_dsp.py` (19): KEYSYNTH.ZD2 → 52, synthetische Dateien (zwei Stellen, ohne INFO, NaN /
  unendlich / negativ, INFO zu kurz), `read_zd2_part` wartet auf die Last, jede Tabellenzeile hat `dsp`, die vier
  gemessenen Effekte und ihre Summen, `set_dsp` und `--dsp`, `build`; Cache: ohne `dsp` einmal neu gelesen, `null`
  nicht, ungültig verworfen, eigener Build bringt seine Last; Read Pedal liest einen alten Eintrag einmal neu;
  Schutz: 269,93 gesendet, 270,04 abgelehnt (nichts gesendet), ausgeschaltet zählt, der ersetzte Effekt zählt
  nicht, unbekannte Last durchgelassen; Zustand mit Budget und Lasten der Auswahl; `node --check` auf das Skript
  von `ui.html` (wenn node da ist). Angepasst: `test_names` (`chain_entry` mit `dsp`), `test_effect_files` (Cache
  mit `"dsp": null`). Oberfläche zusätzlich im Browser mit einem eigenen Testserver (Port 8797, Zustand des
  Entwurfs, Kette und Auswahl aus `chain_entry`/`effect_choices`) angesehen: Breite 375 px und Desktop, rot über
  dem Budget, „– too much“ bei DYN Comp und LoopRoll, BassGEQ wählbar.
- Keine neue Nachrichtenart. Am Pedal noch nicht ausprobiert.

## KeyBand vierstimmig (0.20), 2026-10-22 (Wunsch des Nutzers über die Prototypen-Sitzung)

- **Was kam:** KeyBand (Nummer 0x000F94, am Pedal `09000f94`) ist jetzt Build 0.20 mit vier Stimmen, heute auf dem
  MS-60B+ installiert. Knöpfe aus `prototypes/keyband/manifest.json` (= `prototypes/install_all/KEYBAND.ZD2`, 27321
  Byte, gleich geprüft): Key1–Key4 (je 1000), Voice 3 (Piano/EPian/Strng/Brass), Level, Atk, Dec, Rel, Tone, Move,
  Vel (je 100). Chord und Oct sind weg. Jeder Key-Knopf ist eine Stimme im Key-Format von KeyPoly (0 = aus, (n−1) ·
  10 Cent über C0); eine neue Note ist Key von 0 oder ein Sprung um mehr als 9 Klicks, kleinere Schritte sind Bends.
  Vel liest der Effekt je Stimme im Block ihres Anschlags, die Stimme behält ihn; mehrere Anschläge in einem Block
  (ein Akkord) bekommen alle den Vel, der dann steht – laut Prototypen-Sitzung in Ordnung. Kein LFO, kein Mix, Level
  bleibt (der Pegel des Instruments).
- **zoom_sysex.py:** `BAND_KNOBS` ist 0.20 (Kommentar wie bei den anderen, mit dem Hinweis auf 0.1x). Neu
  `key_knobs(knobs)`: wo ein Build seine Key-Knöpfe hat, nach den Namen – das erste „Key“ (eine Stimme) oder „Key1“
  und die direkt folgenden „Key2“ … (höchstens `MAX_VOICES`). `SynthEffect` nimmt die Stimmenzahl daraus, der
  Parameter `voices` ist weg (die Namensprüfung ist damit überflüssig, die Prüfung auf 12 Bit bleibt; ein Key-Knopf
  hinter den Plätzen von `EDTB_WIDTHS` ist jetzt ein `ValueError` statt eines `IndexError`). `installed()` ohne
  `voices`: Der Build der Tabelle bzw. das, was Read Pedal gelesen hat, entscheidet Knöpfe und Stimmen – ein Pedal mit
  KeyBand 0.1x wird mit einer Stimme gespielt (Vel dort Parameter 12), eins mit 0.20 mit vier (Vel Parameter 13).
  Ohne Eintrag (nichts gelesen, oder `MS_PLUS_BRIDGE_SYNTHS=builtin`): die eingebaute Liste 0.20 mit vier Stimmen.
  KeyPoly und die anderen wie vorher (KeyPoly: Key1–Key4, also vier).
- **bridge.py, Noten und Vel:** Die Noten verteilt die Bridge wie bei KeyPoly (`_assign`, nichts Neues: eine Note
  behält ihre Stimme, die fünfte nimmt die der ältesten). Vel geht wie bisher in `_before_note` direkt vor den Key der
  Stimme, die anschlägt, mit dem Anschlag der Taste ihrer Note (`_velocity`: die Taste, beim Sequencer die Taste, von
  der er spielt, beim Arpeggiator die Akkordtaste; Noten, die das Sustain-Pedal hält, behalten ihn, `Keys.velocities`
  wird nie geleert), nie bei Bends, nicht wenn Vel ihn schon hält. Vier verschieden angeschlagene Tasten zugleich:
  Vel/Key1, Vel/Key2, Vel/Key3, Vel/Key4; gleich angeschlagen: Vel einmal. Die Werte dafür stehen jetzt in
  `_strike_values(voice)`. **Geändert beim Wiederholen** (`_key_miss`): Vorher ging mit dem wiederholten Key der
  zuletzt gesendete, unbestätigte Wert wieder raus – mit vier Stimmen oft der Vel einer anderen Stimme (zwei Tasten
  bei taubem Pedal: beide Keys mit dem Vel der zweiten). Jetzt gehen die Werte dieser Note (ihr eigener Vel, Plucks
  Trig) mit, wenn sie unbestätigt sind oder das Pedal laut Bridge gerade etwas anderes hält – nur bei einem Anschlag:
  `KeySent.struck` (in `_play` mit `_strikes` gesetzt, derselben Bedingung wie in `_before_note`) trennt ihn vom
  Bend-Schritt, der allein wiederholt wird. Fund des Reviews: Zuerst stand dort `if sent.value:`; dann ging bei vier
  Stimmen vor fast jedem wiederholten Bend-Schritt der Vel einer anderen Stimme raus, und bei Pluck setzte die
  Wiederholung Trig still auf Key zurück, wenn der Spieler ihn während der Note auf Bass gedreht hatte (Test).
- **bridge.py, anderer Build bis zum Neustart:** `keys_in_place` prüft jetzt mit `zs.key_knobs` statt auf gleiche
  Namen: Jeder Key-Knopf, den die Bridge spielt, muss im Build der Tabelle ein Key-Knopf sein („Key“ und „Key1“ sind
  dasselbe Format). Bridge auf 0.20, Read Pedal findet 0.1x: Key2–Key4 wären dort Level, Voice und Chord → die
  Tastatur lässt KeyBand in Ruhe („No … in this preset“). Bridge auf 0.1x, Read Pedal findet 0.20: der eine Key ist
  Key1 → nur Noten auf Key1, kein Vel (Parameter 12 wäre dort Move; die Allowlist hat bei einem anderen Build nur die
  Key-Knöpfe) – vorher hätte „Key“ ≠ „Key1“ es ganz liegen lassen. `can_play_build` nimmt die Stimmen aus dem Build
  (vorher mit den Stimmen der Bridge: ein 0.1x hätte bei einer Bridge auf 0.20 als „cannot play this build“ gegolten,
  obwohl der nächste Start ihn spielt). Die beiden Meldungen (nach Read Pedal und beim Patch) sagen jetzt je nach
  `keys_in_place` „plays notes only“ oder „leaves it alone“ – vorher immer „plays notes only“, wenn spielbar.
  `plays_other_build` und `matches_table` unverändert.
- **synth_controls.py:** `REMOVED` hat Chord und Oct dazu: Eine Zuweisung „KeyBand/Chord“ oder „KeyBand/Oct“ fällt
  still weg, statt eine Einstellungsdatei oder einen Import abzulehnen (`problems`); „Pluck/Oct“ und „Oct“ ohne Effekt
  bleiben (Plucks Oct). In `controls.json` des Nutzers gibt es keine KeyBand-Zuweisung.
- **Grenzen:** Sagt die Tabelle 0.20, hat das Pedal aber 0.1x (alter Build wieder installiert, ohne Read Pedal),
  passen die Werte eines 0.1x-Patches in die Bereiche von 0.20 – am Patch allein nicht zu unterscheiden, die Noten
  gingen dann auch in Level, Voice und Chord. Umgekehrt (Tabelle 0.1x, Pedal 0.20) erkennt die Bridge den anderen
  Build am Patch, sobald Vel (dort Parameter 13) nicht 0 ist; ein Preset mit Vel 0 und Key2–Key4 auf 0 sieht aus wie
  0.1x, dann ginge Vel in Parameter 12 (bei 0.20 Move). Read Pedal (beim Start, wenn sich die Größe der Datei geändert
  hat) fängt beides. **Presets, die mit 0.1x gemacht sind, müssen neu gemacht werden** (laut Prototypen-Sitzung):
  0.20 nimmt ihre Werte nach Platz, Level 80 von 0.1x ist Key2, also ein Ton; die Bridge schließt solche Key2–Key4
  beim ersten Patch (auf 0), wie jeden Key, den nichts spielt (Test).
- **Beim Nutzer:** `pedal_effects.json` hat KEYBAND.ZD2 noch als 0.1x (26747 Byte). Beim nächsten Start nimmt die
  Bridge daher 0.1x; Read Pedal beim Start (wenn es läuft) liest die Datei neu (andere Größe) und meldet den anderen
  Build: bis zum Neustart nur Noten auf Key1. Nach einem weiteren Start vierstimmig. Die laufende Bridge (Port 8765)
  ist nicht angefasst.
- **README:** Einleitung und Funktionsliste (KeyBand bei den vierstimmigen), Tabelle und Punkte unter „The synth
  effects“ (0.20, 0.1x, Vel je Stimme), Abschnitt jetzt „Four voices: KeyPoly and KeyBand“, Absatz zu anderen Builds
  bei Read Pedal.
- **Tests:** in `tests/test_key_effects.py` – Knopflisten (0.20; mit `manifest.json` und `KEYBAND.ZD2` aus PROTOTYP
  verglichen, `read_zd2`, übersprungen, wenn die Dateien fehlen), Stimmen aus den Knopfnamen (`key_knobs`, auch Key3
  an anderer Stelle, Key5, ohne Key1), alle Vel-Tests auf vier Stimmen umgestellt (Vel Parameter 13, Keys 2–5) und neu:
  Akkord aus vier Tasten mit je eigenem Vel direkt vor seinem Key, gleich angeschlagen Vel einmal, fünfte Taste wie bei
  KeyPoly (die älteste bekommt ihre Stimme mit ihrem Vel zurück), Sustain-Pedal, Bend-Fall mit fünfter Taste, ein
  wiederholter Key mit seinem eigenen Vel, ein wiederholter Bend-Schritt ohne Vel (und bei Pluck ohne Trig); Klasse
  `Builds`: ein Pedal mit 0.1x (Cache) einstimmig mit Vel auf 12, mit
  0.20 vierstimmig, ein 0.1x-Preset auf 0.20, Read Pedal nach dem Start in beide Richtungen (keine Noten bzw. nur
  Key1 ohne Vel, die Meldungen); Zuweisungen an Chord/Oct fallen weg. `tests/test_names.py`: `SynthEffect` ohne
  `voices`, vergleicht auch die Stimmen.
- Keine neue Nachrichtenart; ans Pedal geht nichts anderes als `set_param` auf die Knöpfe des Effekts. KeyBand 0.20
  am Pedal noch nicht über die Bridge gespielt.
- **Zweites Review, von mir behoben:** Ein Build mit Key-Knöpfen, die keine Stimme sind (ein Key3 an anderer Stelle,
  ein Key5, „Key“ neben „Key2“), wurde mit weniger Stimmen gespielt und der übrige Key-Knopf wurde ein Regler – ein
  Rad darauf hätte Noten gespielt. Jetzt lehnt `SynthEffect` so einen Build ab (`KEY_NAME`), `installed()` nimmt die
  eingebaute Liste, und die Tastatur lässt den Effekt in Ruhe (Test). README: Die Bridge liest alle vier Key-Knöpfe
  aus dem Patch (nicht nur Key1); Pluck in der Tabelle „yes, 2026-10-22“.
- Der Ablauf dafür lief zweimal nebeneinander (der erste brach mit einem Netzwerkfehler scheinbar ab, lief aber
  weiter); den zweiten habe ich vor seinem Prüfer-Fix angehalten, die volle Suite danach selbst laufen lassen.

## Effektliste mit DSP-Zahlen, 2026-10-22 (Wunsch des Nutzers)

- Der Nutzer wollte die Zahlen doch: In der Auswahlliste der Effekte (nur dort, nicht am Platz und nicht im Balken)
  steht jetzt hinter jedem Effekt seine Last, „AC 370 · 127“. Einer, der neben den anderen Plätzen nicht mehr passt,
  ist nur deaktiviert und grau (`select option:disabled`, #6f6a60; `--muted` wäre neben dem Weiß der anderen kaum
  zu unterscheiden), ohne Zusatz: Ein „(too much)“ dahinter wollte der Nutzer nach dem ersten Blick nicht mehr. Ohne bekannte Last nur der Name. Die Kopfzeile „DSP free here“ aus dem
  Entwurf bleibt draußen.
Im Entwurfs-Server geprüft (Kette 204 / 270, Platz 4): 150 Einträge, 15 grau, darunter alle
  Bass-Amps; die übrigen weiß mit Zahl. README angepasst.

## Handyansicht: „Link“ als Symbol, Synth-Zeile in Activity, 2026-10-22 (Wunsch des Nutzers)

- **„Link“ auf dem Handy:** In der schmalen Ansicht (Container ≤ 520 px) stand der Knopf „Link“ in einer eigenen
  Zeile unter jedem Regler, jeder Parameter brauchte zwei Zeilen. Jetzt ist er dort nur das Pedal-Symbol in einer
  vierten Spalte (28 px) am Ende der Reglerzeile; der Text „Link“ ist nur versteckt (`aria-label` „Link Gain“,
  Tooltip wie vorher). Was einen verknüpften Regler steuert, das Warten auf den Controller und „a note: the keys
  play it“ bleiben in der zweiten Zeile. Die vierte Spalte gibt es in jeder Zeile, auch in den verknüpften, damit
  die Regler gleich lang bleiben. Auf dem Desktop unverändert: Symbol und „Link“.
- **Hinweis unter den Reglern** im Wortlaut des Nutzers: „Tap “Link” & move exp. pedal or controller. Up to 12 per
  preset, 4 per controller.“ (die Zahlen wie vorher aus `state.limits`).
- **Synth-Zeile** („KeyBand plays from the keyboard in this preset · note → confirmed 9.1 ms“, auch „keyboard not
  connected“, Stimmen, externer Sequencer) steht nicht mehr unter den Effekten, sondern in beiden Ansichten oben in
  Activity über dem Log (zu, solange Activity zu ist).
- Im Entwurfs-Server geprüft (neu: `--linked` mit einem Regler am Pedal, einem an CC 16 und einem, der auf seinen
  Controller wartet), bei 375 px und 1100 px: keine waagrechte Rollleiste, Symbol 28×26 px.
- `tests/test_dsp.py` (Page) suchte noch „– too much“, jetzt `select option:disabled`.

## Reglerzeile: Symbol vorne, keine Warte-Zeile, ohne Exp/CC-Knopf, 2026-10-22 (Wunsch des Nutzers)

- Auf Rückfrage: **auf Desktop und Handy gleich**, und das Symbol ist **immer orange**, wenn der Regler verknüpft
  ist (auch bei einem CC, dessen Wert, Bereich und Punkt blau bleiben). Blau heißt nur noch: wartet.
- Jede Reglerzeile beginnt mit dem Symbol (`button.link-icon`): (Symbol) (Name) (Slider) (Wert), auf dem Desktop
  rechts daneben die Leiste des Links. Grau: nichts steuert ihn (Pedal-Symbol). Orange: verknüpft, das Pedal-Symbol
  für das Expression-Pedal, das Knopf-Symbol (`KNOB_ICON`, war bis jetzt unbenutzt) für einen Controller. Blau
  blinkend (`wait-blink`): wartet auf seinen Controller.
- Getippt: nicht verknüpft oder verknüpft → `learn` wie vorher der „Link“-Knopf bzw. der Exp/CC-Knopf (der nächste
  bewegte Controller übernimmt); wartend → `cancel` (ersetzt das × der Warte-Zeile). Tooltip und `aria-label`
  nennen den Controller („CC 16 sets it“, „CC 16 sets Ultra“), da die Leiste ihn nicht mehr zeigt.
- Weg: die Zeile „Move the expression pedal or a controller now…“ (`.knob-wait`, `.wait-dot`), die blaue Tönung
  der wartenden Zeile, der Knopf „Exp“/„CC 16“ vorne in der Leiste (`.link-controller`) und der „Link“-Knopf
  (`.link-button`). Die Leiste ist jetzt: Ferse → Spitze, ⇅, Kurve, ×. Ein Key (Synth) hat kein Symbol, nur eine
  leere Zelle, damit die Spalten stehen; „a note: the keys play it“ wie vorher.
- Desktop-Spalten: 24 px Symbol, 52 px Name, Slider, 10ch Wert, 200 px Leiste (vorher 236 px mit dem Exp/CC-Knopf).
  Handy (Container ≤ 520 px): 28 px Symbol (28×28), 46 px Name, Slider, 10ch; die Leiste darunter ab der Spalte des
  Namens, 36 px hoch, Zahlen 15 px, Knöpfe 34×28, Kurven-Symbol 18 px.
- Der Hinweis unter den Reglern bleibt im Wortlaut des Nutzers („Tap “Link” & move exp. pedal or controller…“),
  obwohl „Link“ jetzt nur noch im Tooltip steht – dem Nutzer gesagt.
- Im Entwurfs-Server geprüft (375 px und 1000 px, `--linked`, KeyBand mit seinen vier Keys): Zustände und Farben
  per `getComputedStyle`, die Posts beim Tippen (`cancel`; `learn` mit `new` für einen freien Regler; `learn` ohne
  `new` für einen verknüpften), keine waagrechte Rollleiste.
- **Nachtrag (Wunsch des Nutzers):** „a note: the keys play it“ bei den Keys ist weg (`.link-none` entfernt). Ein
  Key hat weder Symbol noch Hinweis, sein Slider ist nur grau: `input[type=range]:disabled` bekommt
  `accent-color: #6f6a60`, damit er auch in Safari grau ist und nicht nur blasser orange. Das gilt für jeden
  gesperrten Slider, also auch für alle, solange das Pedal nicht verbunden ist. Im Entwurfs-Server bei 375 px
  geprüft (KeyBand: Key1–Key4 grau, ohne Symbol, eine Zeile je Key).

## Key-Synths: Regler 12 heißt „Level“ statt „Mix“, 2026-10-22 (Nachricht der Prototypen-Sitzung „Prototypen Auftrag 2“)

- **Was die Prototypen-Sitzung meldet** (auf Wunsch des Nutzers weitergegeben): KeySynth 1.13, KeySweep 0.12, KeyFM
  0.12, KeyPoly 0.14 und KeyWave 0.12 sind seit 21:06 auf dem MS-60B+ (gleiche Ids, Kategorie SFX; Backup vorher:
  `~/Zoom MS Installer/Backups/ms-60bp_20261022-210025`). Reihenfolge der Regler unverändert; nur Regler 12 (der
  letzte) war „Mix“ (0–100, Grundwert 50, Bass gegen Synth) und ist jetzt „Level“ (0–100, Grundwert 80, nur der
  Pegel des Synths; 80 so laut wie Mix 50, 100 das 1,5-Fache). Der Bass läuft immer unverändert durch. Alte Presets:
  der gespeicherte Mix-Wert landet auf Level (Mix 50 → Level 50, etwa 4 dB leiser; der Nutzer stellt 80 ein).
  KeyBand, Pluck und Kick unverändert. Die Bridge schreibt Regler 12 nicht von sich aus.
- **Geprüft:** die fünf neuen Dateien aus `~/Desktop/PROTOTYP/prototypes/install_all/` mit `read_zd2` gelesen (nur
  gelesen): gegenüber der Tabelle ist nur der Name von Regler 12 und die Dateigröße anders; DSP (47, 50, 20, 65, 32)
  und Werttexte gleich. Die Größen der alten Dateien im Backup sind die der Tabelle (KeyPoly: die Tabelle hatte noch
  eine ältere Größe), und die Installation ändert die Größe nicht (KeyBand, Pluck gleich groß).
- **Geändert:** `zoom_effects.json` (die fünf Einträge: `["Level", 100]`, neue `size`, mit `write_table`, sonst
  nichts), die eingebauten Listen in `zoom_sysex.py` (letzter Regler `("Level", 100)`, Kommentare: Builds vom
  2026-10-22), `synth_controls.REMOVED` um „Mix“ ergänzt (eine Zuweisung an Mix wird weggelassen, nicht als Fehler
  genommen; „Level“ bleibt drin für Builds ohne Level, solange ein Pedal noch einen Mix-Build hat). Ohne Effektnamen
  ist „Level“ jetzt das Level jedes Synth-Effekts (vorher nur KeyBand und Pluck). README: Tabelle und Absatz zu den
  Builds.
- **Beim Nutzer:** `controls.json` hat keine Zuweisung an Mix, nichts geht verloren. `pedal_effects.json` hat
  KEYPOLY.ZD2 noch als alten Build (27267 Byte, mit Mix): bis Read Pedal die neue Datei liest (andere Größe), spielt
  die Bridge KeyPoly mit „Mix“ als Namen von Regler 12. An den gesendeten Parametern ändert das nichts.
- **Tests:** „Mix“ als Beispielregler in `test_effects`, `test_synth`, `test_key_effects`, `test_poly` durch „Level“
  ersetzt (`test_poly` hatte ich zuerst übersehen: die volle Suite fand es, 2 von 593 rot, danach grün);
  `test_editor`: Zuweisungen an Mix werden weggelassen; Level ohne Effekt gilt für alle sieben; ein Build mit Mix
  (aus der Tabelle) behält Mix und lässt Level weg.
- **Korrektur der Prototypen-Sitzung (am selben Abend, Wunsch des Nutzers: „entferne das Dry Signal aus allen Key
  Effekten, es soll nur der Synth zu hören sein“):** Die nächsten Builds (KeySynth 1.14, KeySweep 0.13, KeyFM 0.13,
  KeyPoly 0.15, KeyWave 0.13; werden gebaut, Installation heute Nacht, die Sitzung meldet sich) schalten den Bass
  stumm, solange der Effekt an ist: nur der Synth, ohne gehaltene Taste Stille; aus (Bypass) läuft der Bass durch,
  beim Umschalten blenden beide über. Gleiche Ids, gleiche Regler (Regler 12 bleibt Level). KeyBand gehört der
  Sitzung „Synths, Effekte und Tools“ und lässt den Bass bis auf Weiteres durch. Die Bridge braucht den Bass nirgends
  (kein Mithören, keine Messung daran). Am Pedal: Ein Pluck mit Trig = Bass hinter einem eingeschalteten Key-Synth
  in der Kette bekommt dann den Synth statt des Basses. Zu tun, wenn sie installiert
  sind: die Größen der fünf Dateien in `zoom_effects.json` (die Bridge liest sie sonst per Read Pedal vom Pedal);
  README sagt es schon.
- **Bridge neu gestartet (Wunsch des Nutzers, 21:32 und 21:34):** Beim ersten Start war KeyPoly noch „Mix“: Die Synths
  werden beim Start aus Tabelle und Cache gebaut, und erst danach verwarf die Bridge den alten KEYPOLY-Eintrag im
  Cache (die Tabelle beschreibt jetzt die Datei auf dem Pedal; wie dokumentiert: ein Build gilt ab dem nächsten
  Start). Nach dem zweiten Start haben alle sieben „Level“ (`/api/state`, synth.knobs). Gelesen wurde beide Male nur
  N2ZBANK.ZD2 (wird bei jedem Start gelesen).

## Key-Synths ohne Bass und KeyBand 0.21 auf dem Pedal, 2026-10-23 (Nachrichten der Prototypen-Sitzungen)

- **Installiert** 07:09 (Prototypen-Sitzung „Prototypen Auftrag 2“, Backup `ms-60bp_20261023-070251`): KeySynth 1.14,
  KeySweep 0.13, KeyFM 0.13, KeyPoly 0.15, KeyWave 0.13 und KeyBand 0.21. Gleiche Ids und Regler; an ist nur der
  Synth bzw. das Instrument zu hören, aus läuft der Bass durch. Die übrigen 29 Effekte unverändert.
- **Geprüft** (die Dateien aus `install_all` nur gelesen, `read_zd2`): Regler und Werttexte gleich, Größen wie
  gemeldet (25386, 24354, 22473, 27369, 28467; KeyBand 28099), DSP gleich bis auf KeySynth 48 (vorher 47).
- **Geändert:** `zoom_effects.json` – Größe der fünf, DSP von KeySynth (mit `write_table`). KeyBand steht wie bisher
  nicht in der Tabelle (die Bridge kennt es nach Nummer und liest seine Datei vom Pedal; ihr Cache hat noch 0.20 mit
  27321 Byte, die neue Größe lässt sie die Datei beim nächsten Start oder per Read Pedal neu lesen – gleiche Regler,
  also spielt sie weiter vierstimmig). README (Builds vom 2026-10-23, KeyBand-Zeile der Tabelle, KeyBand und Pluck)
  und Kommentare in `zoom_sysex.py`. Die Sitzung „Synths, Effekte und Tools“ schrieb, die Tabelle brauche einen
  Eintrag mehr für KeyBand 0.21: nicht nötig, s. o.
- **Volle Suite** 593 OK. **Bridge gestartet** 07:39 (lief seit 01:53 nicht mehr): las N2ZBANK.ZD2 und KEYBAND.ZD2
  (0.21, 28099 Byte, gleiche zwölf Regler) vom Pedal; in der Effektliste KeySynth 48, die anderen wie vorher. Ein
  zweiter Start war nicht nötig: 0.20 und 0.21 haben dieselben Regler.

## Fehlersuche in der Bridge, 2026-10-23 (Wunsch des Nutzers: „Bridge nach Fehlern und Bugs suchen und ggf. neue Versionen bauen“)

- **Vorgehen:** Logs der laufenden Bridge durchgesehen (`logs/sysex.log*`), dann fünf Prüfer im Hintergrund (Bridge
  in drei Teilen, Oberfläche, die übrigen Module mit dem Pi-Teil), nur lesend, mit Nachstellung am Simulator. Jeden
  Fund selbst nachgelesen; zu jedem Fix ein Test, der mit dem alten Code rot ist (geprüft mit `git stash`).
- **Logs:** kein Fehler. „no ack for Key2 …“ um 07:42:59 fiel in das Drehen von Dec am Pedal (bekannt: dabei
  bestätigt es nichts; Wiederholung wie vorgesehen). N2Z Bank wird bei jedem Read Pedal gelesen (Absicht); das
  zweite Lesen um 07:39:25 kam vom Knopf.
- **Behoben, Pedal und Tastatur:**
  - Wiederholungen ohne Bestätigung (Key, Wert einer Zuweisung, Effektschalter) gingen auch während eines
    Verschiebens/Tauschens/Einfügens/Löschens hinaus – an den Platz, an dem dann ein anderer Effekt stand. Jetzt
    nicht; der Wert einer Zuweisung wartet, die Note kommt nach dem nächsten Patch (`_find_synth`/`_play`).
  - Eine Taste, losgelassen während das Pedal ein Verschieben nicht meldete, ließ die Note hängen (`_on_patch`
    prüfte `voice.note is None`, die Note war noch eingetragen). Beim Beenden während eines Umbaus: Gate-off,
    wenn der Synth seinen Platz behält.
  - Eine aufgeschobene Patch-Abfrage fiel weg, wenn ein Synth eingerichtet, aber nicht im Preset war: Name nach
    dem Umbenennen am Pedal („USERy 1“ statt „USER-012“), Schalter ohne Bestätigung, am Pedal gewählter Effekt.
    Nur die Synth-Suche (Taste gedrückt) entfällt jetzt noch.
  - Ein Tastatur-Regler setzte nichts, wenn eine Zuweisung desselben Reglers nichts sendet (Effekt nicht da,
    Bereich zu groß): er teilte deren Target. Bestätigungen gehen an das Target, das sendet.
  - Sustain blieb gedrückt, wenn die Tastatur mit gedrücktem Pedal abgezogen wurde.
- **Behoben, Presets:**
  - Speichern: Wählte der Fußschalter ein anderes Preset, bevor der Patch kam, wurde dessen Patch in den Platz
    geschrieben („Preset 009 saved“ mit dem Patch von 002). Jetzt „Saving stopped: preset changed“.
  - Undo nach einem Speichern, dessen Platz noch nicht gelesen war, bot das vorige Speichern an (schrieb ein
    anderes Preset zurück). Jetzt nichts.
  - Umbenennen: ein weitergereichter Programmwechsel hielt es nicht an, der Rest des Namens ging ins nächste
    Preset. Umbenennen während eines Speicherns/Umbaus wird abgelehnt.
  - Read Pedal beim Start verglich mit dem Patch vom Start; ein danach gedrehter Regler fiel nicht auf und ging
    beim Lesen verloren. Jetzt mit frisch abgefragtem Patch. Und nur einmal je Lauf, nicht nach jedem
    Wiederverbinden (PC-Modus mitten im Spielen).
  - BPM wurde nach Read Pedal als 00000FF0 behalten (nur die Nummer), eine Id, die das Pedal nicht hat. Jetzt mit
    der ganzen Id; Ids ohne Kategorie (erstes Byte 0) werden beim Lesen verworfen (beim Nutzer stand 00000FF0 in
    `measurements.json`, das nächste Read Pedal räumt es weg).
  - Ein leerer Platz verschoben: „Effect 00000000 is effect 2 now“ → „An empty place is effect 2 now“.
- **Behoben, Oberfläche/API:**
  - `/api/parameter` mit `"slot": "0"` (Text) wurde angenommen; der nächste bewegte Regler beendete die Bridge
    (`StopIteration`). Jetzt 400.
  - Szenen: Übernehmen zählte die Werte eines inzwischen ersetzten Effekts mit („höchstens 8“) und verwarf dabei
    die Aufnahme. Jetzt erst aussortieren, dann zählen; abgelehnt läuft die Aufnahme weiter.
  - Import: Tastatur-Regler wurden nicht gegen vorhandene Effektschalter abgeglichen (ein Regler, zwei Aufgaben;
    „File for the Pi“ danach abgelehnt). Am Pi wurde ein Import halb übernommen (Presets geschrieben, Rest
    abgelehnt). Jetzt erst alles, dann einmal schreiben.
  - `ui.html`: Ein offenes Feld, eine offene Liste oder Szene speicherte in das Preset, das beim Schließen am Pedal
    war (Zuweisungen eines anderen Presets ersetzt) – jetzt nur in das, für das sie gebaut wurden, sonst Meldung.
    Speichern einer Zeile zerstörte das Feld, in das man mit Tab gewechselt war. Antippen des Live-Punkts schickte
    den Wert des vorigen Ziehens. Abgelehntes Read Pedal zeigte die alte Erfolgsmeldung und ließ den Knopf grau;
    abgelehntes Verknüpfen zeigte die alte Erfolgsmeldung.
- **Behoben, Dateien und Pi:**
  - `config.yaml`, `controls.json`, `measurements.json`, `approvals.json` werden ganz geschrieben und dann
    umbenannt (`zs.write_whole`), nicht mehr abgeschnitten und neu beschrieben.
  - `config_schema.validate` stürzte bei einem von Hand eingetragenen Regler wie `KeySynth/Rate: [1]` ab (am Pi:
    Neustart-Schleife statt Meldung); Schlüssel wie „²“ ebenso. `expression` ohne Zeile `cc:` (ohne
    Expression-Controller) gab KeyError beim Start.
  - `install.sh` las das WLAN immer aus der aktuellen Datei, die Bridge lief bei kaputter aktueller mit der
    vorigen: Unterschied → install.sh bei jedem Start, eigenes WLAN gelöscht. Jetzt dieselbe Wahl
    (`load_with_fallback`); Einstellungen vom Stick, die nicht gespeichert werden konnten, lösen kein Update aus.
  - `effect_names.table()`: eine Datei, gelesen und ohne Effekt (abgebrochene Installation, 0 Byte), ließ den
    Tabelleneintrag eines anderen Builds stehen – der Effekt war wählbar. Jetzt nicht.
  - `setup_logging` schloss die alte Logdatei nicht (ResourceWarning in den Tests).
- **Nicht geändert:** Verschieben schickt weiter keine Id des gemeinten Effekts mit (die Oberfläche prüft das
  Preset; ein Verschieben ist ohne Speichern rückgängig). Ein Wert, der während eines Verschiebens wartet, geht
  verloren, wenn seine Zuweisung den Platz wechselt (wie vorher; die nächste Bewegung sendet ihn).

## Neue Builds angekündigt: Pluck 0.12 und KeyBand 0.22, 2026-10-24 (Nachricht der Prototypen-Sitzung „Prototypen Auftrag 2“)

- **Angekündigt, noch nicht installiert** (Fehlersuche über alle 35 Effekte, `prototypes/BERICHT.md`, letzter
  Abschnitt): Pluck 0.12 nennt Regler 5 „Tone“ statt „Bright“ (Platz, Bereich, Werte gleich). KeyBand 0.22 spielt
  die Key-Regler bis 1000 = D#8 (bis 0.21 alles über E7 als E7), Dec wirkt auch bei Strng/Brass. Angemeldete
  Rechenlast neu u. a. KeyBand 86, Pluck 106, Orchestra 116, KeySweep 52, Melody 64. Ids bleiben.
- **Bridge:** Die eingebaute Liste von Pluck hat „Tone“ (`zoom_sysex.PLUCK_KNOBS`); bis die neue Datei vom Pedal
  gelesen ist, gilt der Cache mit „Bright“. `zoom_sysex.KNOB_RENAMED` und `synth_controls.split`: eine Zuweisung an
  „Pluck/Bright“ oder „Pluck/Tone“ geht an den Namen, den der gespielte Build hat (vorher wäre „Pluck/Bright“ mit
  einem Build 0.12 als unbekannt abgelehnt worden, am Pi die ganze Einstellungsdatei). Beim Nutzer ist nichts an
  Pluck zugewiesen. KeyBand bis D#8: Die Bridge schickte schon immer den ganzen Bereich (12–111), nichts zu ändern.
  README: Pluck-Zeile und der Absatz zu KeyBand und Pluck.
- **Nach der Installation:** `zoom_effects.json` – Größe und DSP der geänderten Effekte, die die Tabelle beschreibt
  (u. a. KeySweep 52, KeyFM, KeyWave und die übrigen eigenen); KeyBand, Pluck, Orchestra, Melody, Hits, Metro, HOGi
  liest die Bridge vom Pedal (andere Größe → neu gelesen, mit der neuen Rechenlast).
- **Nachtrag zur Fehlersuche:** In der vollen Suite (615) war `test_not_read_when_a_knob_was_turned_after_the_patch_came`
  einmal rot, einzeln grün: Mit dem schnellen Lesetempo der Tests konnte die Bridge vor dem Drehen (1,0 s) durch
  sein. Jetzt liest der Test die Namen langsamer (bis etwa 2,5 s). Ebenso unabhängig vom Takt gemacht: das
  Wiederverbinden (Pedal 1,3 s weg, länger als der Abstand der Port-Prüfungen) und der Sustain-Test (Gate-off vor
  dem Ende statt auf 0,1 s genau). Alle drei sind mit dem Code vor der Fehlersuche weiterhin rot.

## Builds vom 2026-10-24 auf dem Pedal (Nachricht der Prototypen-Sitzung „Prototypen Auftrag 2“)

- **Installiert** 08:24 (Installer 1.3, Code 0; Backup `ms-60bp_20261024-081322`): 29 Effekte ersetzt, gleich
  geblieben BusMeter, KeyPoly, KeySynth, Ladder, MemWatch, VoxSynth. Die sha256 von KeyBand, Pluck, Orchestra,
  KeySweep, KeyFM und KeyWave in `install_all` stimmen mit der Meldung überein (nur gelesen).
- **`zoom_effects.json`:** 19 Einträge, deren Datei in `install_all` liegt, auf diese Builds gebracht (Name, Regler,
  Größe, DSP, Werttexte aus der Datei; Texte, die nur der Code macht, bleiben wie bei `kept_labels`): meist nur die
  Größe; DSP KeySweep 50 → 52, CycleMeter 10 → 23, HOGi 77 → 80. HOGi (EnvLo/EnvHi → Env/Level) und SlamDrive
  (XFreq bis 52, mit Texten) beschrieb die Tabelle noch als ältere Builds; die Bridge spielte sie nach dem Cache
  (vom Pedal gelesen) schon so. Beim Nutzer ist an beiden nichts zugewiesen und keine Szene. VoxSynth: die Tabelle
  hatte noch eine ältere Größe (29358 statt 29390 wie auf dem Pedal). Die „nur Texte“-Builds (FiltSyn, FMSynth,
  Kick, Ringer, OSCDLY) ändern Beschreibungen, nicht die Werttexte der Regler.
- **Vom Pedal gelesen** (nicht in der Tabelle, wie bisher): Cloud, Dimension, Drone, Hits, Impact, KeyBand, Melody,
  Metro, Orchestra, Pluck – andere Größe, also beim nächsten Start (Read Pedal) neu gelesen, mit der neuen
  Rechenlast (KeyBand 86, Pluck 106 und Tone, Orchestra 116, Melody 64 …).
- README: Builds vom 2026-10-24, KeyBand-Zeile (bis D#8).

## Szenen mit Drone und Tempo-Meldung, 2026-10-26 (Rückmeldung des Nutzers über die Prototypen-Sitzung)

- **Befund Drone-Szenen** (`logs/sysex.log`, Bridge seit 00:42): kein Fehler beim Abrufen. Szene 1 (Preset 008)
  hält Root = 7. Root 9 wurde um 00:52:19 gesetzt, bevor „Capture“ für Szene 2 lief; die Aufnahme nahm danach nur
  Oct = 2 (00:52:29, „Scene “2”: 1 value“), heute ist Szene 2 leer. Ein Wechsel auf Szene 2 setzt Root also nicht.
  Die Meldung „Scene “1” does not set Drone · Root …“ ab 02:39 ist richtig: In Preset 008 standen auf Platz 1
  danach DrumBox, FiltSyn usw., kein Drone mehr.
- **Gebaut (Entscheidung des Nutzers: „Aufnahme live zeigen“):** Während „Capture“ zeigt die Zeile der Szene blau,
  was sie schon hat (letzter Wert je Regler, Schalter; `scenes.captured` im Zustand); vorher nur die Zahl. Ohne
  etwas: „nothing taken yet“ mit dem Hinweis, dass vorher Gesetztes nicht zählt. Statuszeile: „turn the knobs on
  the pedal or here“. README (Szenen, Schritt 2). Im Mock geprüft, auch auf 375 px.
- **Tempo:** Das Pedal meldet eine Änderung des Patch-Tempos selbst (`64 20 00 64 02 <lo> <hi>`, z. B. 03:30:56–58,
  03:34:27 = 120). Der Vorschlag, danach den Tempo-Regler von DrumBox/Metro/Melody bei Sync neu zu setzen: Antwort
  des Nutzers hier „Nicht jetzt“; die Prototypen-Sitzung meldete danach ein „Ja“ des Nutzers. Nachgefragt, Antwort
  des Nutzers: „Tempo lassen wir so“ – nicht gebaut.
- **Gesehen, nicht geändert (Nutzer: nicht gewählt):** Ein losgelassener Slider schickt seinen Wert dreimal (Wert,
  beim Loslassen derselbe Wert noch einmal, ohne Bestätigung dann die Wiederholung von `_check_edits`).
- **Volle Suite** 615, einmal rot: `test_seq.OwnClock.test_latch_plays_on_and_a_new_key_moves_it`, ein Schritt
  16,5 ms statt höchstens 15 ms daneben (Takt unter Last; der Test ist seit 2026-10-09 unverändert). Einzeln
  dreimal mit `test_arp` grün.

## Builds vom 2026-10-26 auf dem Pedal (Nachricht der Prototypen-Sitzung „Prototypen Auftrag 2“)

- **Installiert** gegen 07:00 (Installer 1.3, Code 0; Backup `ms-60bp_20261026-064635`): 24 Effekte ersetzt. Die
  sha256 der 13 gemeldeten Dateien in `install_all` und von LISTE.md stimmen (nur gelesen).
- **`zoom_effects.json`:** 16 Einträge auf diese Builds gebracht, wie am 24.10.: Größe und neue Werttexte (Rate,
  Cutoff, Tone, Freq … als Hz, SubFq als 0.60x–4.00x, Glider/Freezer Tone mit „Open“ als letzter Stufe). Regler und
  DSP der Tabellen-Effekte gleich. Die Texte stehen als Tabellen in den Dateien (`read_zd2` liest sie); gesendet wird
  weiter 0–100. Zurückgelesen: jeder Eintrag mit Datei in `install_all` hat deren Größe, Regler und Texte.
- **Pluck 0.13:** Regler 10 „Dry“ (0–100, Vorgabe 80 = Bass wie er ist; ein Patch eines früheren Builds hat dort 0,
  also keinen Bass). In `zoom_sysex.PLUCK_KNOBS` angehängt (gegen die Datei geprüft); eine Tastatur-Zuweisung an
  „Pluck/Dry“ geht damit. Die Bridge setzt Effekte mit change_effect ein, die Vorgabe setzt das Pedal.
- **Vom Pedal gelesen** wie bisher (nicht in der Tabelle): Orchestra 0.14 (Dry als Regler 12, 118), Drone 0.12 (69),
  Melody 0.12 (69), Metro 0.12 (26), Pluck 0.13 (117), dazu Dimension, Hits, Impact mit Werttexten. Der Cache der
  Bridge hat noch die Stände vom 23.10. (beim Start am 26.10. nicht gelesen: das Preset hatte ungespeicherte
  Änderungen); das nächste Read Pedal liest sie.
- README: Builds vom 2026-10-26, Pluck-Zeile (Dry).
- **Nachtrag 19:55** (Backup `ms-60bp_20261026-195120`): SlamDrive 0.20 (die Schwelle von CmpLo/CmpHi wandert mit
  XFreq), Orchestra 0.15 und Pluck 0.14 (Tonmessung 1,1 ms früher). Regler, Texte und Rechenlast gleich; sha256 der
  drei und von LISTE.md stimmen. In der Tabelle nur die Größe von SlamDrive (36409 → 37637); Orchestra und Pluck
  liest die Bridge vom Pedal.

## Ein Synth-Build, den Read Pedal liest, gilt sofort, 2026-10-27 (vom Nutzer bemerkt: „Pluck Parameter sind noch nicht in der Bridge“)

- **Befund:** Bridge gestartet 02:44 mit dem Cache vom 23.10. (Pluck 0.11: Bright, kein Dry); danach las sie
  PLUCK.ZD2 0.13/0.14 vom Pedal. Synth-Effekte baute sie nur beim Start: bis zum nächsten Start zeigte der Editor
  Pluck ohne Regler („unmatched“), die Tastaturliste Bright ohne Dry; Activity: „WARNING: Pluck on the pedal is
  another build … its knobs are taken from the table at the next start“. So schon 2026-10-22 bei KeyPoly (Mix).
- **Geändert:** `Bridge.synths_from_builds` baut die Synth-Effekte nach jedem Lesen neu (`zs.use_installed_synths`;
  ein Effekt mit gleichen Reglern behält sein Objekt, damit nichts gestört wird, was ihn spielt) und liest die
  Tastatur-Zuweisungen neu (`synth_controls.read`: Namen wie der Build sie hat, z. B. Bright → Tone; eine an einen
  Regler, den es nicht mehr gibt, fällt weg wie beim Start). `Session._play_builds` ruft das in `_take_read` und
  nach `forget_unlisted` (eine Datei weg: wieder der Build der Tabelle) und sagt „<Name>: the keyboard plays it as
  the build on the pedal from now on“; der Effekt der Kette wird beim nächsten Patch neu gefunden (`_find_synth`,
  Stimmen wie der Build). Meldungen für einen Build, den die Bridge weiter anders spielt (Key-Regler nicht dort, oder
  `MS_PLUS_BRIDGE_SYNTHS=builtin`), über `_other_build`.
- **Tests:** neu `test_a_pluck_read_after_the_start_has_its_knobs_and_controllers_at_once` (mit dem alten Code rot:
  Bright statt Tone, kein Dry); die zwei KeyBand-Tests „… until the next start“ prüfen jetzt das Spielen sofort
  (0.1x eine Stimme, 0.20 vier). README (KeyBand 0.1x: ein später gelesener Build gilt sofort).
- **Gegengelesen** (zwei Prüfer, je Fund ein Widerleger; im Simulator nachgestellt), zwei echte Fehler meiner
  ersten Fassung behoben: (1) Wartete ein Tastatur-Regler auf „Learn“ (z. B. KeyBand/Chord) und Read Pedal las einen
  Build ohne diesen Regler, beendete der nächste bewegte Controller die Bridge (KeyError in `refresh`). Jetzt wartet
  er nicht mehr; `refresh` und `learned_control` übergehen einen Namen, den der Build nicht hat. (2) Der Neubau gleich
  nach `forget_unlisted` nahm für eine Datei neuer Größe, die (noch) nicht gelesen war, den Build der Tabelle bzw.
  die eigene Liste und meldete „plays it as the build on the pedal“; nebenbei fielen dabei Zuweisungen weg. Jetzt
  werden nur die Effekte neu gebaut, deren Datei eben gelesen wurde (`use_installed_synths(only)`); die Meldung nur,
  wenn ein Keyboard-Synth eingerichtet ist. Beide Fälle als Tests in `test_key_effects.Builds`.

## SlamDrive 0.21 auf dem Pedal, 2026-10-27 (Nachricht der Prototypen-Sitzung)

- Installiert 06:00 (Backup `ms-60bp_20261027-055952`); sha256 von SLAMDRV.ZD2 und LISTE.md stimmen. Nur der
  Kompressor geändert (leise Töne mit dem Anschlag ausgeglichen); Regler, Texte, Rechenlast gleich. In der Tabelle
  nur die Größe (37637 → 38987).

## Pi-Karte neu geschrieben, 2026-10-27 (Wunsch des Nutzers: „Pi Karte ist eingesteckt. Neue Version drauf laden“)

- Auf der Karte war noch der Stand vom 18.10. (`00b40f5`, Einstellungen `schema_version` 1, exportiert 04:03:30;
  am Pi nicht geändert). `export.py --check`: gültig. Geschrieben mit `export.py`: Einstellungen (2 Presets mit
  Zuweisungen, 26 gelernte Bereiche, 6 Tastatur-Controller), `pedal-effects.json`, Programm Stand `01fccc1`; die Karte
  wurde ausgeworfen. Beim nächsten Start übernimmt der Pi das Programm; ein höheres LEVEL oder ein anderes WLAN lässt
  install.sh noch einmal laufen. Am Pi noch ungetestet.
- **Am Pi (Nutzer, 2026-10-27):** Beim ersten Start lief `install.sh --update` durch (Bericht: „Updating … commit
  01fccc1“, bluez vorhanden, eigenes WLAN angelegt, „Installed (level 2)“); der Dienst vom 18.10. lief noch ohne
  `--listen`. `http://10.42.0.1` ging zuerst nicht: Der Pi kennt den Hotspot „SlamiPhone“ (Imager, Hostname
  MSPlusExpress). Er öffnete trotz eingeschaltetem Hotspot sein eigenes WLAN; damit funktioniert die Oberfläche am
  iPhone („es funktioniert, wenn er sein eigenes WLAN aufmacht“).

## Fassung für GitHub und Aufräumen, 2026-10-30 (Wunsch des Nutzers: „Version für Windows / Mac / Android / iOS / Linux fertig machen … danach den Ordner aufräumen“)

- **Plattformen:** iOS und Android können die Bridge nicht ausführen (Python, USB-MIDI); sie nutzen die Oberfläche
  im Browser. Entscheidung des Nutzers: auch Mac/Windows/Linux können sie ins WLAN geben – `listen: true` unter
  `ui:` in `config.yaml` oder `--listen` (bisher nur mit `--settings` auf dem Pi). Die Bridge sagt dann, wo ein
  Handy sie findet (`<Name>.local:8765` oder die Adresse, `ui.network_address`); der Browser am Rechner öffnet
  weiter 127.0.0.1. Schema: `ui.listen` true/false. Neuer Starter `Start Bridge.sh` für Linux; alle drei Starter
  reichen Optionen durch (`--listen`). README: Abschnitt „On a phone or tablet (iOS, Android)“, Plattformen oben,
  Linux-Starter, Dateiliste.
- **Repo:** `.gitignore` (bisher nur `.git/info/exclude`), `.DS_Store` nicht mehr verfolgt. `keysynth/` (KeySynth
  1.10 mit Quellen aus dem Original-Repo) aus dem Repo genommen (Wahl des Nutzers); seine KEYSYNTH.ZD2 liegt als
  Testdatei in `tests/files/` (test_dsp, test_value_labels lesen eine echte Build-Datei). README sagt, dass die
  Effekte nicht im Repo sind.
- **Archiv** (nichts gelöscht): `~/Desktop/MS Express Archiv 2026-10-30/` – `keysynth/`, die alten Logs
  `sysex.log.1–3`, `MS Plus Bridge.app` (der Mac-Starter macht sie beim nächsten Start neu), die `.DS_Store`.
  Geblieben: Programm, Tests, `tools/`, Pi-Dateien und die Einstellungen dieser Installation (config.yaml,
  controls.json, measurements.json, approvals.json, pedal_effects.json, logs/sysex.log, .venv).
- Volle Suite 619: einmal rot `test_poly.Voices.test_the_pitch_wheel_bends_every_voice_in_small_steps` (127 statt
  höchstens 80 ms, Takt unter Last; nichts davon geändert), einzeln dreimal grün.
- **Kurzanleitung und Bild (Wunsch des Nutzers):** `QUICKSTART.md` (Englisch: Download und Start je System, Einrichten
  mit probe.py, Spielen, Handy/Tablet, Keyboard-Synths – die Effekte sind nicht im Repo, sie kommen von anderswo);
  das README verweist oben darauf. Bild zum Posten `docs/ms-plus-bridge.png` (1280 × 640, auch als Social Preview
  für GitHub; aus HTML mit Chrome headless gerendert, Farben und Symbol der Oberfläche), oben im README; eine
  doppelt große Fassung auf dem Schreibtisch.

## FiltSyn heißt SweepSynth, 2026-10-30 (Nachricht der Prototypen-Sitzung)

- Seit 30.10. 01:17 auf dem Pedal: SweepSynth 0.16 (gleiche Id 09000F74, Datei FILTSYN.ZD2, Regler). Aus
  `install_all` in die Tabelle: Name und Größe (21037 → 21058). Die übrigen gemeldeten Builds (Bilder und
  Versionen) haben bei den Tabellen-Effekten dieselbe ZD2; Drone, Melody, Cloud liest die Bridge vom Pedal.
