## Contributing

Thanks for considering contributing!

### Quick start (dev)

```bash
python -m pip install -r requirements.txt
python terralith_biome_remap_gui.py
```

### Reporting bugs

Please include:
- Your Minecraft version
- Your OS
- Whether you used the GUI or CLI
- The complete output log (copy/paste from the app’s Output panel)
- A small reproduction (if possible): one or two region files (`r.x.z.mca`) that show the issue

### Pull requests

- Keep changes focused.
- Prefer adding/adjusting CLI flags in `terralith_biome_remap_standalone.py` and wiring the GUI in `terralith_biome_remap_gui.py`.
- Avoid committing world files (`world/` is ignored by `.gitignore`).


