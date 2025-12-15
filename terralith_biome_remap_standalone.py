"""
Standalone Terralith -> Vanilla biome remapper for Java worlds (1.18+).

Key points:
- This script edits Java Anvil region files (*.mca) directly.
- It remaps biome *palette strings* in chunk sections (fast; avoids touching packed biome arrays).
- It can optionally print a few palette samples so we can see what biome IDs are actually stored.

Typical usage:
  python terralith_biome_remap_standalone.py "C:\\path\\to\\world"
  python terralith_biome_remap_standalone.py "C:\\path\\to\\world" --y 118 132
  python terralith_biome_remap_standalone.py "C:\\path\\to\\world" --mapping-ini "C:\\path\\to\\mapping.ini"
  python terralith_biome_remap_standalone.py "C:\\path\\to\\world" --debug-sample 50

Dimensions:
  --dimension overworld  -> <world>/region
  --dimension nether     -> <world>/DIM-1/region
  --dimension end        -> <world>/DIM1/region
"""

from __future__ import annotations

import argparse
import ast
import configparser
import io
import math
import os
import sys
import re
import struct
import time
import zlib
import gzip
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from collections.abc import MutableSequence
import multiprocessing

import nbtlib


SECTOR_BYTES = 4096
HEADER_BYTES = SECTOR_BYTES * 2


def _normalize_biome_name(name: str) -> str:
    if name.startswith("universal_minecraft:"):
        return "minecraft:" + name.split(":", 1)[1]
    if name.startswith("universal_terralith:"):
        return "terralith:" + name.split(":", 1)[1]
    return name


def _normalize_target_biome_id(name: str) -> str:
    # Legacy mapping target often used by older lists; invalid in modern versions.
    if name == "minecraft:mountains":
        return "minecraft:windswept_hills"
    return name


def _load_mapping_from_py(py_path: Path) -> Dict[str, str]:
    """
    Load _MAPPING from a python file (terralith_remapv2.py).
    If it fails, returns a small fallback mapping.
    """
    fallback = {"terralith:yellowstone": "minecraft:badlands"}
    if not py_path.exists():
        return fallback

    # Preferred: parse python AST and extract the literal dict assigned to _MAPPING.
    # This is robust across formatting, comments, and trailing commas.
    try:
        text = py_path.read_text(encoding="utf-8", errors="ignore")
        mod = ast.parse(text, filename=str(py_path))
        mapping_node = None
        for node in mod.body:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "_MAPPING":
                        mapping_node = node.value
                        break
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == "_MAPPING":
                    mapping_node = node.value
            if mapping_node is not None:
                break
        if mapping_node is None:
            raise ValueError("No _MAPPING assignment found")
        mapping = ast.literal_eval(mapping_node)
        if not isinstance(mapping, dict):
            raise ValueError("_MAPPING is not a dict")
    except Exception:
        # Fallback: best-effort regex + brace matching (older versions of this script used this).
        try:
            m = re.search(r"(?m)^\s*_MAPPING\s*=\s*\{", text)
            if not m:
                return fallback
            brace_start = text.find("{", m.end() - 1)
            if brace_start == -1:
                return fallback
            depth = 0
            end = None
            for i in range(brace_start, len(text)):
                c = text[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end is None:
                return fallback
            mapping = ast.literal_eval(text[brace_start:end])
            if not isinstance(mapping, dict):
                return fallback
        except Exception:
            return fallback

    out: Dict[str, str] = {}
    for k, v in mapping.items():
        if isinstance(k, str) and isinstance(v, str):
            out[_normalize_biome_name(k)] = _normalize_target_biome_id(_normalize_biome_name(v))
    return out or fallback


def _load_mapping_from_ini(ini_path: Path) -> Dict[str, str]:
    """
    Load mappings from an INI file.

    Expected format:
      [mapping]
      terralith:alpha_islands = minecraft:mushroom_fields
      terralith:cave/mantle_caves = minecraft:dripstone_caves
    """
    fallback = {"terralith:yellowstone": "minecraft:badlands"}
    if not ini_path.exists():
        return fallback

    # Critical: biome ids contain ':' which is normally a ConfigParser delimiter.
    # So we ONLY accept '=' as the key/value delimiter.
    cfg = configparser.ConfigParser(delimiters=("=",), interpolation=None)
    cfg.optionxform = str  # preserve case (biome ids are lowercase anyway, but keep exact)
    try:
        cfg.read(ini_path, encoding="utf-8")
    except Exception:
        return fallback

    if "mapping" not in cfg:
        return fallback

    out: Dict[str, str] = {}
    for k, v in cfg["mapping"].items():
        if not k or not v:
            continue
        k2 = _normalize_biome_name(str(k).strip())
        v2 = _normalize_target_biome_id(_normalize_biome_name(str(v).strip()))
        if k2 and v2:
            out[k2] = v2
    return out or fallback


def _load_mapping_from_ini_text(ini_text: str) -> Dict[str, str]:
    """
    Load mappings from an INI string (used for the built-in default mapping so the EXE can be standalone).
    """
    fallback = {"terralith:yellowstone": "minecraft:badlands"}
    cfg = configparser.ConfigParser(delimiters=("=",), interpolation=None)
    cfg.optionxform = str
    try:
        cfg.read_string(ini_text)
    except Exception:
        return fallback
    if "mapping" not in cfg:
        return fallback
    out: Dict[str, str] = {}
    for k, v in cfg["mapping"].items():
        if not k or not v:
            continue
        k2 = _normalize_biome_name(str(k).strip())
        v2 = _normalize_target_biome_id(_normalize_biome_name(str(v).strip()))
        if k2 and v2:
            out[k2] = v2
    return out or fallback


# Built-in default mapping (so a user can download ONLY the .exe and still run it).
# Users can supply their own mapping.ini to remap other mods/biomes.
DEFAULT_MAPPING_INI_TEXT = """[mapping]
terralith:alpha_islands = minecraft:mushroom_fields
terralith:alpha_islands_winter = minecraft:snowy_taiga
terralith:alpine_grove = minecraft:snowy_taiga
terralith:alpine_highlands = minecraft:stony_peaks
terralith:amethyst_canyon = minecraft:stony_peaks
terralith:amethyst_rainforest = minecraft:dark_forest
terralith:ancient_sands = minecraft:desert
terralith:arid_highlands = minecraft:desert
terralith:ashen_savanna = minecraft:savanna
terralith:basalt_cliffs = minecraft:windswept_gravelly_hills
terralith:birch_taiga = minecraft:birch_forest
terralith:blooming_plateau = minecraft:plains
terralith:blooming_valley = minecraft:plains
terralith:brushland = minecraft:plains
terralith:bryce_canyon = minecraft:badlands
terralith:caldera = minecraft:stony_peaks
terralith:cloud_forest = minecraft:jungle
terralith:cold_shrubland = minecraft:snowy_taiga
terralith:desert_canyon = minecraft:desert
terralith:desert_oasis = minecraft:desert
terralith:desert_spires = minecraft:desert
terralith:emerald_peaks = minecraft:stony_peaks
terralith:forested_highlands = minecraft:forest
terralith:fractured_savanna = minecraft:savanna
terralith:frozen_cliffs = minecraft:snowy_taiga
terralith:glacial_chasm = minecraft:snowy_taiga
terralith:granite_cliffs = minecraft:windswept_gravelly_hills
terralith:gravel_beach = minecraft:beach
terralith:gravel_desert = minecraft:desert
terralith:haze_mountain = minecraft:stony_peaks
terralith:highlands = minecraft:windswept_hills
terralith:hot_shrubland = minecraft:savanna
terralith:ice_marsh = minecraft:swamp
terralith:jungle_mountains = minecraft:jungle
terralith:lavender_forest = minecraft:flower_forest
terralith:lavender_valley = minecraft:plains
terralith:lush_desert = minecraft:desert
terralith:lush_valley = minecraft:plains
terralith:mirage_isles = minecraft:plains
terralith:moonlight_grove = minecraft:plains
terralith:moonlight_valley = minecraft:plains
terralith:mountain_steppe = minecraft:windswept_hills
terralith:orchid_swamp = minecraft:swamp
terralith:painted_mountains = minecraft:stony_peaks
terralith:red_oasis = minecraft:desert
terralith:rocky_jungle = minecraft:jungle
terralith:rocky_mountains = minecraft:stony_peaks
terralith:rocky_shrubland = minecraft:plains
terralith:sakura_grove = minecraft:flower_forest
terralith:sakura_valley = minecraft:plains
terralith:sandstone_valley = minecraft:desert
terralith:savanna_badlands = minecraft:savanna
terralith:savanna_slopes = minecraft:savanna
terralith:scarlet_mountains = minecraft:stony_peaks
terralith:shield_clearing = minecraft:plains
terralith:shield = minecraft:plains
terralith:shrubland = minecraft:plains
terralith:siberian_grove = minecraft:snowy_taiga
terralith:siberian_taiga = minecraft:snowy_taiga
terralith:skylands = minecraft:stony_peaks
terralith:skylands_autumn = minecraft:stony_peaks
terralith:skylands_spring = minecraft:stony_peaks
terralith:skylands_summer = minecraft:stony_peaks
terralith:skylands_winter = minecraft:snowy_taiga
terralith:snowy_badlands = minecraft:badlands
terralith:snowy_cherry_grove = minecraft:snowy_taiga
terralith:snowy_maple_forest = minecraft:snowy_taiga
terralith:snowy_shield = minecraft:snowy_taiga
terralith:steppe = minecraft:plains
terralith:stony_spires = minecraft:stony_peaks
terralith:temperate_highlands = minecraft:forest
terralith:tropical_jungle = minecraft:jungle
terralith:valley_clearing = minecraft:plains
terralith:volcanic_crater = minecraft:stony_peaks
terralith:volcanic_peaks = minecraft:windswept_savanna
terralith:warm_river = minecraft:swamp
terralith:warped_mesa = minecraft:desert
terralith:white_cliffs = minecraft:snowy_slopes
terralith:white_mesa = minecraft:desert
terralith:windswept_spires = minecraft:windswept_gravelly_hills
terralith:wintry_forest = minecraft:snowy_taiga
terralith:wintry_lowlands = minecraft:snowy_taiga
terralith:yellowstone = minecraft:badlands
terralith:yosemite_cliffs = minecraft:stony_peaks
terralith:yosemite_lowlands = minecraft:forest
terralith:cave/andesite_caves = minecraft:dripstone_caves
terralith:cave/desert_caves = minecraft:dripstone_caves
terralith:cave/diorite_caves = minecraft:dripstone_caves
terralith:cave/fungal_caves = minecraft:lush_caves
terralith:cave/granite_caves = minecraft:dripstone_caves
terralith:cave/ice_caves = minecraft:dripstone_caves
terralith:cave/infested_caves = minecraft:dripstone_caves
terralith:cave/thermal_caves = minecraft:dripstone_caves
terralith:cave/underground_jungle = minecraft:lush_caves
terralith:cave/crystal_caves = minecraft:lush_caves
terralith:cave/deep_caves = minecraft:dripstone_caves
terralith:cave/frostfire_caves = minecraft:lush_caves
terralith:cave/mantle_caves = minecraft:dripstone_caves
terralith:cave/tuff_caves = minecraft:dripstone_caves
"""


def write_default_mapping_ini(path: Path) -> None:
    path.write_text(DEFAULT_MAPPING_INI_TEXT, encoding="utf-8")


def _default_mapping_ini_path() -> Path:
    """
    Default mapping file path (next to the program).
    - When running from source: next to this .py file.
    - When packaged: next to the executable.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "mapping.ini"
    return Path(__file__).resolve().parent / "mapping.ini"


def _region_dir(world_path: Path, dimension: str) -> Path:
    dim = dimension.lower()
    if dim in ("overworld", "world", "0"):
        return world_path / "region"
    if dim in ("nether", "-1", "dim-1"):
        return world_path / "DIM-1" / "region"
    if dim in ("end", "1", "dim1"):
        return world_path / "DIM1" / "region"
    return Path(dimension)


@dataclass(frozen=True)
class ChunkPointer:
    idx: int
    off_sectors: int
    sector_count: int


def _read_locations(region_bytes: bytes) -> List[Tuple[int, int]]:
    locs: List[Tuple[int, int]] = []
    for i in range(1024):
        entry = region_bytes[i * 4 : i * 4 + 4]
        off = int.from_bytes(entry[:3], "big")
        count = entry[3]
        locs.append((off, count))
    return locs


def _iter_present_chunks(region_bytes: bytes) -> Iterable[ChunkPointer]:
    locs = _read_locations(region_bytes)
    for idx, (off, count) in enumerate(locs):
        if off and count:
            yield ChunkPointer(idx=idx, off_sectors=off, sector_count=count)


def _get_chunk_blob(region_bytes: bytes, off_sectors: int, sector_count: int) -> Optional[bytes]:
    if off_sectors == 0 or sector_count == 0:
        return None
    start = off_sectors * SECTOR_BYTES
    end = start + sector_count * SECTOR_BYTES
    if start + 5 > len(region_bytes) or end > len(region_bytes):
        return None
    length = struct.unpack(">I", region_bytes[start : start + 4])[0]
    if length <= 0:
        return None
    blob_end = start + 4 + length
    if blob_end > len(region_bytes):
        return None
    return region_bytes[start:blob_end]


def _chunk_blob_compression_type(blob: bytes) -> int:
    return blob[4] if len(blob) >= 5 else 2


def _decompress_chunk_nbt(blob: bytes) -> bytes:
    if len(blob) < 5:
        raise ValueError("Invalid chunk blob")
    length = struct.unpack(">I", blob[:4])[0]
    comp = blob[4]
    payload = blob[5:]
    if length != len(payload) + 1:
        if length - 1 <= len(payload):
            payload = payload[: length - 1]
        else:
            raise ValueError("Chunk length mismatch")
    if comp == 1:
        return gzip.decompress(payload)
    if comp == 2:
        return zlib.decompress(payload)
    if comp == 3:
        return payload
    raise ValueError(f"Unknown compression type: {comp}")


def _compress_chunk_nbt(nbt_bytes: bytes, compression_type: int) -> bytes:
    if compression_type == 1:
        payload = gzip.compress(nbt_bytes)
    elif compression_type == 2:
        payload = zlib.compress(nbt_bytes)
    elif compression_type == 3:
        payload = nbt_bytes
    else:
        payload = zlib.compress(nbt_bytes)
        compression_type = 2
    length = len(payload) + 1
    return struct.pack(">I", length) + bytes([compression_type]) + payload


def _serialize_nbt_root(root) -> bytes:
    f = nbtlib.File(root, gzipped=False, byteorder="big")
    buf = io.BytesIO()
    f.write(buf)
    return buf.getvalue()


def _get_sections(root) -> Optional[Sequence]:
    if isinstance(root, dict) and "sections" in root:
        return root["sections"]
    if isinstance(root, dict) and "Sections" in root:
        return root["Sections"]
    if isinstance(root, dict) and "Level" in root:
        lvl = root.get("Level")
        if isinstance(lvl, dict) and "sections" in lvl:
            return lvl["sections"]
        if isinstance(lvl, dict) and "Sections" in lvl:
            return lvl["Sections"]
    return None


def _section_y(section) -> Optional[int]:
    if isinstance(section, dict) and "Y" in section:
        try:
            return int(section["Y"])
        except Exception:
            return None
    return None


def _iter_biome_palette_lists(section) -> Iterable[List]:
    if not isinstance(section, dict):
        return
    biomes = section.get("biomes")
    if isinstance(biomes, dict):
        pal = biomes.get("palette")
        # nbtlib uses its own list type, so treat any MutableSequence as a palette list.
        if isinstance(pal, MutableSequence):
            yield pal
    biomes = section.get("Biomes")
    if isinstance(biomes, dict):
        pal = biomes.get("palette") or biomes.get("Palette")
        if isinstance(pal, MutableSequence):
            yield pal


def _remap_chunk_biome_palettes(
    root,
    mapping: Dict[str, str],
    y_min: Optional[int],
    y_max: Optional[int],
    unmapped_terralith_to: Optional[str],
    debug_samples: List[str],
    debug_limit: int,
) -> Tuple[bool, int]:
    sections = _get_sections(root)
    if not sections:
        return (False, 0)

    changed = False
    entries_changed = 0

    for section in sections:
        sy = _section_y(section)
        if sy is not None and y_min is not None and y_max is not None:
            sec_min = sy * 16
            sec_max = sec_min + 15
            if sec_max < y_min or sec_min > y_max:
                continue

        for palette in _iter_biome_palette_lists(section):
            # Debug: sample a few palette entries from the world
            if debug_limit > 0 and len(debug_samples) < debug_limit:
                for v in palette:
                    if len(debug_samples) >= debug_limit:
                        break
                    try:
                        s = _normalize_biome_name(str(v))
                    except Exception:
                        continue
                    debug_samples.append(s)

            for i in range(len(palette)):
                try:
                    raw = str(palette[i])
                except Exception:
                    continue
                norm = _normalize_biome_name(raw)
                new = mapping.get(norm)
                if (not new) and unmapped_terralith_to and norm.startswith("terralith:"):
                    new = unmapped_terralith_to
                if new and new != norm:
                    palette[i] = nbtlib.String(new)
                    changed = True
                    entries_changed += 1

    return (changed, entries_changed)


def _probe_for_biome_prefix(
    region_files: Sequence[Path],
    prefix: str,
    y_min: Optional[int],
    y_max: Optional[int],
    max_regions: int,
    max_chunks: int,
    log,
) -> int:
    """
    Scan region files until we find any biome palette entry starting with `prefix`.
    Prints the first hit(s) and returns 0 if found, 2 if not found.
    """
    pref = prefix.strip()
    if not pref:
        log("Probe prefix is empty.")
        return 2

    regions_scanned = 0
    chunks_scanned = 0

    for rf in region_files:
        if max_regions > 0 and regions_scanned >= max_regions:
            break
        regions_scanned += 1
        try:
            original = rf.read_bytes()
        except Exception:
            continue

        for ptr in _iter_present_chunks(original):
            if max_chunks > 0 and chunks_scanned >= max_chunks:
                break
            blob = _get_chunk_blob(original, ptr.off_sectors, ptr.sector_count)
            if blob is None:
                continue
            chunks_scanned += 1
            try:
                raw_nbt = _decompress_chunk_nbt(blob)
                nbt_file = nbtlib.File.parse(io.BytesIO(raw_nbt), byteorder="big")
                root = nbt_file
            except Exception:
                continue

            sections = _get_sections(root)
            if not sections:
                continue

            for section in sections:
                sy = _section_y(section)
                if sy is not None and y_min is not None and y_max is not None:
                    sec_min = sy * 16
                    sec_max = sec_min + 15
                    if sec_max < y_min or sec_min > y_max:
                        continue
                for palette in _iter_biome_palette_lists(section):
                    hits = []
                    for v in palette:
                        try:
                            s = _normalize_biome_name(str(v))
                        except Exception:
                            continue
                        if s.startswith(pref):
                            hits.append(s)
                    if hits:
                        uniq_hits = list(dict.fromkeys(hits))
                        log(f"FOUND in {rf.name} (chunk_idx={ptr.idx}, sectionY={sy}):")
                        for h in uniq_hits[:20]:
                            log(f"  - {h}")
                        return 0

        if max_chunks > 0 and chunks_scanned >= max_chunks:
            break

    log(f"Not found. Scanned regions={regions_scanned}, chunks={chunks_scanned}, prefix={pref!r}")
    return 2


def _read_timestamps(region_bytes: bytes) -> List[int]:
    ts: List[int] = []
    base = SECTOR_BYTES
    for i in range(1024):
        entry = region_bytes[base + i * 4 : base + i * 4 + 4]
        ts.append(int.from_bytes(entry, "big"))
    return ts


def _rebuild_region(original: bytes, updated_blobs: Dict[int, Tuple[bytes, bool]]) -> bytes:
    locs = _read_locations(original)
    ts = _read_timestamps(original)
    now_ts = int(time.time())

    header = bytearray(HEADER_BYTES)
    out = bytearray(header)

    current_sector = 2
    new_locs: List[Tuple[int, int]] = [(0, 0)] * 1024
    new_ts: List[int] = [0] * 1024

    for idx in range(1024):
        off, count = locs[idx]
        if off == 0 or count == 0:
            continue

        if idx in updated_blobs:
            blob, was_changed = updated_blobs[idx]
            new_ts[idx] = now_ts if was_changed else ts[idx]
        else:
            blob = _get_chunk_blob(original, off, count)
            if blob is None:
                continue
            new_ts[idx] = ts[idx]

        sectors_needed = max(1, math.ceil(len(blob) / SECTOR_BYTES))
        if sectors_needed > 255:
            raise ValueError(f"Chunk too large for region format ({sectors_needed} sectors)")

        new_locs[idx] = (current_sector, sectors_needed)

        out.extend(blob)
        pad = sectors_needed * SECTOR_BYTES - len(blob)
        if pad:
            out.extend(b"\x00" * pad)
        current_sector += sectors_needed

    # location table
    for idx, (off, count) in enumerate(new_locs):
        header[idx * 4 : idx * 4 + 3] = int(off).to_bytes(3, "big")
        header[idx * 4 + 3] = int(count) & 0xFF

    # timestamp table
    base = SECTOR_BYTES
    for idx, tsv in enumerate(new_ts):
        header[base + idx * 4 : base + idx * 4 + 4] = int(tsv).to_bytes(4, "big")

    out[0:HEADER_BYTES] = header
    return bytes(out)


def _process_region_file(
    region_file: str,
    mapping: Dict[str, str],
    y_min: Optional[int],
    y_max: Optional[int],
    unmapped_terralith_to: Optional[str],
    debug_limit: int,
    debug_errors: int,
    debug_structure: int,
    dry_run: bool,
    make_backup: bool,
) -> Tuple[str, int, int, int, List[str]]:
    path = Path(region_file)
    original = path.read_bytes()

    processed = 0
    changed = 0
    entries_changed = 0
    parse_errors = 0
    structure_printed = 0

    debug_samples: List[str] = []

    updated_blobs: Dict[int, Tuple[bytes, bool]] = {}

    for ptr in _iter_present_chunks(original):
        blob = _get_chunk_blob(original, ptr.off_sectors, ptr.sector_count)
        if blob is None:
            continue
        processed += 1

        comp = _chunk_blob_compression_type(blob)
        try:
            raw_nbt = _decompress_chunk_nbt(blob)
            # Region chunk payloads are full NBT files, so parse as File for compatibility.
            nbt_file = nbtlib.File.parse(io.BytesIO(raw_nbt), byteorder="big")
            # In nbtlib 2.x, File behaves like the root Compound (no `.root` attribute).
            root = nbt_file

            if debug_structure > 0 and structure_printed < debug_structure:
                try:
                    top_keys = list(root.keys()) if isinstance(root, dict) else []
                    print(f"[debug-structure] {path.name} idx={ptr.idx}: root keys={top_keys}")
                    secs = _get_sections(root)
                    if not secs:
                        print("[debug-structure]  sections: <missing/empty>")
                    else:
                        print(f"[debug-structure]  sections type={type(secs).__name__} len={len(secs)}")
                        s0 = secs[0]
                        s0_keys = list(s0.keys()) if isinstance(s0, dict) else []
                        print(f"[debug-structure]  section[0] keys={s0_keys}")
                        if isinstance(s0, dict):
                            b = s0.get('biomes') or s0.get('Biomes')
                            if b is None:
                                print("[debug-structure]  section[0].biomes: <missing>")
                            else:
                                b_keys = list(b.keys()) if isinstance(b, dict) else []
                                print(f"[debug-structure]  section[0].biomes type={type(b).__name__} keys={b_keys}")
                                if isinstance(b, dict):
                                    pal = b.get("palette") or b.get("Palette")
                                    print(f"[debug-structure]  section[0].biomes.palette type={type(pal).__name__}")
                except Exception:
                    pass
                structure_printed += 1

            was_changed, ec = _remap_chunk_biome_palettes(
                root, mapping, y_min, y_max, unmapped_terralith_to, debug_samples, debug_limit
            )
            if was_changed:
                buf = io.BytesIO()
                nbt_file.write(buf, byteorder="big")
                new_raw = buf.getvalue()
                new_blob = _compress_chunk_nbt(new_raw, compression_type=comp)
                updated_blobs[ptr.idx] = (new_blob, True)
                changed += 1
                entries_changed += ec
        except Exception as e:
            parse_errors += 1
            if debug_errors > 0 and parse_errors <= debug_errors:
                try:
                    print(f"[debug-error] {path.name} idx={ptr.idx}: {type(e).__name__}: {e}")
                except Exception:
                    pass
            continue

    if changed == 0 or dry_run:
        if debug_errors > 0:
            print(f"[debug] {path.name}: chunks processed={processed}, parse_errors={parse_errors}")
        return (path.name, processed, changed, entries_changed, debug_samples)

    rebuilt = _rebuild_region(original, updated_blobs)

    # backups + atomic replace
    if make_backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            backup_path.write_bytes(original)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(rebuilt)
    tmp.replace(path)

    return (path.name, processed, changed, entries_changed, debug_samples)


def run(argv: Optional[Sequence[str]] = None, *, log=print) -> int:
    """
    Main implementation. Kept separate so a GUI can call this and capture output via `log`.
    """
    parser = argparse.ArgumentParser(description="Remap Terralith biomes to vanilla biomes by editing .mca region files directly.")
    parser.add_argument("world", type=str, help="Path to the Minecraft world folder (contains region/).")
    parser.add_argument("--dimension", type=str, default="overworld", help="overworld|nether|end|or explicit region folder path.")
    parser.add_argument("--y", nargs=2, type=int, metavar=("Y_MIN", "Y_MAX"), help="Optional Y filter (inclusive).")
    parser.add_argument(
        "--processes",
        type=int,
        default=(os.cpu_count() or 1),
        help="Worker processes (default: CPU logical thread count).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write files, just report what would change.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak backups for modified region files.")
    parser.add_argument(
        "--mapping-ini",
        type=str,
        default="",
        help="Optional path to mapping INI file. If omitted, uses the built-in default mapping.",
    )
    parser.add_argument(
        "--export-default-mapping-ini",
        type=str,
        default=None,
        help="Write the built-in default mapping.ini to this path and exit.",
    )
    parser.add_argument("--debug-sample", type=int, default=0, help="Print N sampled biome palette entries from the world (set >0).")
    parser.add_argument("--debug-errors", type=int, default=0, help="Print first N chunk parse errors per region file (0 disables).")
    parser.add_argument("--debug-structure", type=int, default=0, help="Print structure for first N parsed chunks per region file (0 disables).")
    parser.add_argument("--probe-prefix", type=str, default=None, help="Scan until a biome palette entry starts with this prefix (eg terralith:). Exits without modifying.")
    parser.add_argument("--probe-max-regions", type=int, default=200, help="Max region files to scan in probe mode (0 = no limit).")
    parser.add_argument("--probe-max-chunks", type=int, default=200000, help="Max chunks to scan in probe mode (0 = no limit).")
    parser.add_argument(
        "--unmapped-terralith-to",
        type=str,
        default=None,
        help="Optional: remap any terralith:* biome not in the mapping to this biome id (eg minecraft:plains). Default: leave unmapped terralith biomes alone.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.export_default_mapping_ini:
        out_path = Path(args.export_default_mapping_ini)
        write_default_mapping_ini(out_path)
        log(f"Wrote default mapping.ini to: {out_path}")
        return 0

    world_path = Path(args.world)
    region_dir = _region_dir(world_path, args.dimension)
    if not region_dir.exists():
        raise SystemExit(f"Region folder not found: {region_dir}")

    mapping_src = "builtin"
    if args.mapping_ini:
        mapping_path = Path(args.mapping_ini)
        if not mapping_path.exists():
            raise SystemExit(f"Mapping INI not found: {mapping_path}")
        mapping = _load_mapping_from_ini(mapping_path)
        mapping_src = f"ini:{mapping_path}"
    else:
        mapping = _load_mapping_from_ini_text(DEFAULT_MAPPING_INI_TEXT)
    unmapped_terralith_to = _normalize_target_biome_id(_normalize_biome_name(args.unmapped_terralith_to)) if args.unmapped_terralith_to else None

    y_min = y_max = None
    if args.y:
        y_min, y_max = int(args.y[0]), int(args.y[1])
        if y_min > y_max:
            y_min, y_max = y_max, y_min

    region_files = sorted(region_dir.glob("r.*.*.mca"))
    if not region_files:
        raise SystemExit(f"No region files found in: {region_dir}")

    if args.probe_prefix:
        log(f"Probe mode: prefix={args.probe_prefix!r}")
        if y_min is not None and y_max is not None:
            log(f"Y filter: {y_min}..{y_max}")
        return _probe_for_biome_prefix(
            region_files,
            args.probe_prefix,
            y_min,
            y_max,
            int(args.probe_max_regions),
            int(args.probe_max_chunks),
            log,
        )

    log(f"Region folder: {region_dir}")
    log(f"Regions: {len(region_files)}")
    log(f"Mapping entries: {len(mapping)} (source: {mapping_src})")
    if unmapped_terralith_to:
        log(f"Unmapped terralith:* -> {unmapped_terralith_to}")
    if y_min is not None and y_max is not None:
        log(f"Y filter: {y_min}..{y_max}")
    else:
        log("Y filter: off (processing all Y levels)")
    log(f"Workers: {args.processes}")
    log(f"Backups: {'off' if args.no_backup else 'on'}")

    debug_limit = int(args.debug_sample) if args.debug_sample and args.debug_sample > 0 else 0
    debug_errors = int(args.debug_errors) if args.debug_errors and args.debug_errors > 0 else 0
    debug_structure = int(args.debug_structure) if args.debug_structure and args.debug_structure > 0 else 0

    regions_processed = 0
    regions_changed = 0
    chunks_processed = 0
    chunks_changed = 0
    palette_entries_changed = 0

    collected_samples: List[str] = []

    make_backup = not args.no_backup
    total_regions = len(region_files)
    started = time.time()
    last_progress = started
    with ProcessPoolExecutor(max_workers=args.processes) as ex:
        futures = [
            ex.submit(
                _process_region_file,
                str(p),
                mapping,
                y_min,
                y_max,
                unmapped_terralith_to,
                debug_limit,
                debug_errors,
                debug_structure,
                args.dry_run,
                make_backup,
            )
            for p in region_files
        ]

        for fut in as_completed(futures):
            name, c_proc, c_chg, e_chg, samples = fut.result()
            regions_processed += 1
            chunks_processed += c_proc
            chunks_changed += c_chg
            palette_entries_changed += e_chg
            if c_chg:
                regions_changed += 1
            if args.debug_sample > 0 and len(collected_samples) < args.debug_sample and samples:
                # merge samples until we hit limit
                for s in samples:
                    if len(collected_samples) >= args.debug_sample:
                        break
                    collected_samples.append(s)

            # Progress output (so long runs don't look "stuck")
            now = time.time()
            if c_chg or (now - last_progress) >= 5.0 or regions_processed == total_regions:
                elapsed = now - started
                rps = (regions_processed / elapsed) if elapsed > 0 else 0.0
                log(
                    f"Progress: regions {regions_processed}/{total_regions} "
                    f"({rps:.2f} r/s), chunks {chunks_processed}, changed_chunks {chunks_changed}, "
                    f"palette_changes {palette_entries_changed}"
                )
                last_progress = now

    elapsed = time.time() - started
    mm = int(elapsed // 60)
    ss = int(elapsed % 60)
    log(
        "Summary: "
        f"regions {regions_processed} processed, {regions_changed} changed; "
        f"chunks {chunks_processed} processed, {chunks_changed} changed; "
        f"palette entries changed: {palette_entries_changed}; "
        f"elapsed {mm:02d}:{ss:02d}"
    )
    if args.debug_sample > 0:
        uniq = list(dict.fromkeys(collected_samples))
        log(f"Sample biome palette entries (up to {args.debug_sample}, unique={len(uniq)}):")
        for s in uniq[: args.debug_sample]:
            log(f"  - {s}")
    if args.dry_run:
        log("Dry-run: no files were modified.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run(argv, log=print)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
