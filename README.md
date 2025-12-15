## Minecraft Biome Remapper (Terralith → Vanilla) — V1.0

This project is a standalone tool that **edits Java region files (`*.mca`) directly** and remaps biome IDs by rewriting **chunk section biome palette strings** (fast and correct for Minecraft Java 1.18+ / 1.21.x).

It ships with a built-in default mapping designed to **remove Terralith biome IDs** (remap `terralith:*` → `minecraft:*`), and can also be used as a **generic biome remapper** via a custom mapping INI.

### Disclaimer (read first)
- This tool is **EXPERIMENTAL**. Use at your own risk.
- Always make a **full backup of your world** before running it.
- If you’re removing any **world-generation** mod/datapack, you will also need to update `level.dat` here: https://sawdust.catter1.com/tools/level-editor

### Install

From this folder:

```bash
python -m pip install -r requirements.txt
```

### Run (GUI)

```bash
python terralith_biome_remap_gui.py
```

### Build Windows EXE (optional)

Install PyInstaller once:

```bash
python -m pip install pyinstaller
```

```bash
.\build_exe.bat
```

### Run (safe first pass)

- **Dry-run + debug samples** (recommended first):

```bash
python terralith_biome_remap_standalone.py "C:\path\to\world" --dry-run
```

- **Full run** (writes changes, creates `.bak` backups for changed region files):

```bash
python terralith_biome_remap_standalone.py "C:\path\to\world"
```

### Options

- **Dimension**: `--dimension overworld|nether|end` (or an explicit region folder path)
- **Y filter** (inclusive, block coords): `--y 64 192` (optional; if not set, all Y levels are processed)
- **Parallelism**: `--processes N` (defaults to your CPU logical thread count)
- **Backups**: `--no-backup` (backups are stored alongside the region files as `r.X.Z.mca.bak`)
- **Optional rule**: remap any `terralith:*` not in `_MAPPING`:

```bash
--unmapped-terralith-to minecraft:plains
```

- **Load mapping from INI**:

```bash
--mapping-ini "C:\path\to\mapping.ini"
```

- **Export built-in default mapping to an INI**:

```bash
--export-default-mapping-ini "C:\path\to\mapping.ini"
```

### Mapping INI format (generic remapper)

```ini
[mapping]
some_mod:some_biome = minecraft:plains
minecraft:old_biome = minecraft:new_biome
```

### CLI usage (advanced)

Show help:

```bash
python terralith_biome_remap_standalone.py --help
```

Run (built-in default mapping):

```bash
python terralith_biome_remap_standalone.py "C:\path\to\world"
```

Dry-run (no files modified):

```bash
python terralith_biome_remap_standalone.py "C:\path\to\world" --dry-run
```

Use a custom mapping INI (generic remapper mode):

```bash
python terralith_biome_remap_standalone.py "C:\path\to\world" --mapping-ini "C:\path\to\mapping.ini"
```

Export the built-in Terralith→vanilla mapping to an INI (so you can edit it):

```bash
python terralith_biome_remap_standalone.py "C:\path\to\world" --export-default-mapping-ini "C:\path\to\mapping.ini"
```

#### Flags

- **`world`**: Path to the Minecraft world folder (the folder that contains `region/`).
- **`--dimension overworld|nether|end`**: Which dimension to process.
  - `overworld` → `<world>\region`
  - `nether` → `<world>\DIM-1\region`
  - `end` → `<world>\DIM1\region`
  - You can also pass an explicit region folder path.
- **`--y Y_MIN Y_MAX`**: Optional Y-range filter (inclusive). If not set, all Y levels are processed.
- **`--processes N`**: Number of worker processes (defaults to your CPU logical thread count).
- **`--dry-run`**: Do not write changes; only report what would change.
- **`--no-backup`**: Don’t create `.bak` backups (default is ON).
  - Backups are stored next to region files like `r.X.Z.mca.bak`.
- **`--mapping-ini PATH`**: Optional mapping INI path. If omitted, uses the built-in default mapping (Terralith → vanilla).
- **`--export-default-mapping-ini PATH`**: Write the built-in default mapping INI to `PATH` and exit.
- **`--probe-prefix PREFIX`**: Probe-only mode (no edits). Scans until it finds a biome palette entry starting with `PREFIX` (example: `terralith:`).
- **`--probe-max-regions N`**: Max region files to scan during probe (0 = no limit).
- **`--probe-max-chunks N`**: Max chunks to scan during probe (0 = no limit).
- **`--unmapped-terralith-to BIOME_ID`**: Optional fallback rule: any `terralith:*` biome not present in the mapping will be remapped to this biome (example: `minecraft:plains`).

#### Debug flags (CLI-only)

- **`--debug-sample N`**: Print up to N biome IDs observed in palettes (verification/troubleshooting).
- **`--debug-errors N`**: Print the first N chunk parse errors per region file (troubleshooting).
- **`--debug-structure N`**: Print NBT structure info for the first N chunks per region file (advanced troubleshooting).

### Credits

- **Minecraft**: Mojang Studios / Microsoft (this is a third-party community tool)
- **Terralith**: Terralith worldgen project (this tool provides a default Terralith→vanilla mapping)
- **nbtlib**: used for reading/writing Java chunk NBT
- **PyInstaller**: used to build the Windows `.exe`
- **Tkinter**: GUI framework (part of the Python standard library)
- **Sawdust Labs level editor**: referenced for updating `level.dat` when removing world-generation datapacks/mods

