"""The text the pedal shows for each value of a knob (DrumBox Pattern: Rock, Rock2, HalfT …; Delay
Time: 1 … 600, 610 … 4000, then note values), read from the effect files (.ZD2) into the effect
table (zoom_effects.json, each effect's "labels"). A developer's tool for the Mac, not part of the
bridge and not for the Pi: it needs TI's disassembler dis6x (TI's C6000 code generation tools).

    .venv/bin/python -m tools.value_labels [--dis6x PATH] [--table PATH] [--dry-run] FOLDER [FOLDER …]

How an effect file defines that text (zd2.py): the knob's entry in the parameter table names a
value-display function (word 9; 0: the pedal shows the number). That is code, so the text is only
known by running it: c6x.py runs it, in a small C674x interpreter over dis6x's listing
(listing.py), for every value, and nothing else of the effect. generate.py does that for every
effect of the table whose file is in the folders (of several files of one name the newest of the
table's build, else the newest) and writes the table again (effect_names.write_table).
"""
