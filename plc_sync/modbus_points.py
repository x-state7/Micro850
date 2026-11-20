"""Validate and decode configured Modbus points; organize safe batched reads."""
import math
import struct
from collections import defaultdict

from .modbus import MAX_COUNT, read_table

WIDTH = {"bool": 1, "uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "float32": 2}
TABLES = ("ir", "hr", "coil", "di")


def validate_plc(plc):
    if not isinstance(plc, dict) or not isinstance(plc.get("host"), str) or not plc["host"].strip():
        raise ValueError("plc.host must be set")
    for key, low, high, default in (("port", 1, 65535, 502), ("unit_id", 0, 255, 1)):
        value = plc.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"plc.{key} must be between {low} and {high}")
    positive(plc.get("timeout_seconds", 3), "plc.timeout_seconds")
    points = plc.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError("plc.points must be a nonempty list")
    names = set()
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("Each point must be an object")
        name, table, dtype = point.get("name"), point.get("table"), point.get("type")
        if not isinstance(name, str) or not name or len(name) > 80 or name in names:
            raise ValueError(f"Invalid/duplicate point name: {name!r}")
        names.add(name)
        if table not in TABLES or dtype not in WIDTH:
            raise ValueError(f"Invalid point {name} table/type")
        if (table in ("di", "coil")) != (dtype == "bool"):
            raise ValueError(f"Point {name}: coils/discrete inputs must be bool; registers must be numeric")
        addr = point.get("address")
        if type(addr) is not int or not 0 <= addr < 65536 or addr + WIDTH[dtype] > 65536:
            raise ValueError(f"Point {name}: address outside Modbus range")
        if point.get("word_order", "ABCD") not in ("ABCD", "CDAB", "BADC", "DCBA"):
            raise ValueError(f"Point {name}: bad word_order")
        if dtype in ("bool", "int16", "uint16") and point.get("word_order", "ABCD") != "ABCD":
            raise ValueError(f"Point {name}: word_order only applies to 32-bit types")
        positive(point.get("scale", 1), f"{name}.scale")
    return True


def positive(value, name):
    try:
        if type(value) is bool or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite and > 0") from exc
    return float(value)


def decode(point, registers):
    dtype = point["type"]
    if dtype == "bool":
        return bool(registers[0])
    if dtype in ("int16", "uint16"):
        raw = registers[0]
        if dtype == "int16" and raw >= 32768:
            raw -= 65536
    else:
        rawbytes = struct.pack(">HH", *registers)
        order = point.get("word_order", "ABCD")
        # 0,1,2,3 are the wire bytes A,B,C,D. Allow common word/byte swaps.
        layout = {"ABCD": (0, 1, 2, 3), "CDAB": (2, 3, 0, 1),
                  "BADC": (1, 0, 3, 2), "DCBA": (3, 2, 1, 0)}[order]
        rawbytes = bytes(rawbytes[i] for i in layout)
        raw = struct.unpack({"int32": ">i", "uint32": ">I", "float32": ">f"}[dtype], rawbytes)[0]
    out = raw / float(point.get("scale", 1))
    if not math.isfinite(out):
        raise ValueError(f"Invalid (NaN or infinite) register value for {point['name']}")
    if dtype in ("uint16", "int16", "uint32", "int32") and float(point.get("scale", 1)) == 1:
        return int(raw)
    return round(out, 6) if dtype != "float32" else out


def read_points(plc, reader=read_table):
    """Fetch all points. No partial scan is committed on a failed Modbus read."""
    groups = defaultdict(list)
    for p in plc["points"]:
        groups[p["table"]].append(p)
    values = {}
    for table in TABLES:
        if table not in groups:
            continue
        # Group contiguous spans, splitting any gap and protocol max limits.
        spans = []
        for point in sorted(groups[table], key=lambda p: p["address"]):
            start, end = point["address"], point["address"] + WIDTH[point["type"]]
            if spans and start <= spans[-1][1] and end - spans[-1][0] <= MAX_COUNT[table]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], end))
            else:
                spans.append((start, end))
        blocks = {}
        for start, end in spans:
            blocks[start] = reader(plc["host"], plc.get("port", 502), plc.get("unit_id", 1),
                                   table, start, end - start, float(plc.get("timeout_seconds", 3)))
        for p in groups[table]:
            for start, end in spans:
                if start <= p["address"] and p["address"] + WIDTH[p["type"]] <= end:
                    raw = blocks[start][p["address"] - start:p["address"] - start + WIDTH[p["type"]]]
                    values[p["name"]] = decode(p, raw)
                    break
    return values
