# Bundled Examples

Drop `.dgm` files into this directory and they will appear in
**File → Examples…** automatically. No code changes needed.

## Naming

The filename (without `.dgm`) is the display name shown in the dialog.
Hyphens and underscores are turned into spaces, so:

- `flowchart-basics.dgm`  →  "flowchart basics"
- `op_amp_inverting.dgm`  →  "op amp inverting"

Use the casing/spacing you want users to see.

## Read-only behavior

Examples are loaded into the editor as **untitled** documents — saving
will trigger Save As, so the bundled file is never overwritten.

## Format version

Examples should be saved in the current file format. If you bump
`FORMAT_MAJOR` or `FORMAT_MINOR` in
`src/diagrammer/io/serializer.py`, re-save every example by running:

```
python tools/resave_examples.py
```

from the repository root. This loads each `.dgm`, runs any necessary
migrations, and writes it back in the current format so users on the
new build see clean files.
