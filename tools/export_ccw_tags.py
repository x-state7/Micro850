"""Convert a Connected Components Workbench variable-list XLSX into read-only tag allowlist.

No spreadsheet dependency needed on the edge device; Python stdlib only.
Usage: python tools/export_ccw_tags.py reference/sql.xlsx config/tags.json
"""
import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
COL = re.compile(r"[A-Z]+")


def read_sheet(path):
    with ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(n.text or "" for n in si.findall(".//m:t", NS)))
        # This CCW export has one sheet. First standard worksheet is the variable grid.
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        for row in root.findall(".//m:sheetData/m:row", NS):
            items = {}
            for cell in row.findall("m:c", NS):
                address = cell.attrib.get("r", "")
                col_match = COL.match(address)
                if not col_match:
                    continue
                col = col_match.group()
                if cell.attrib.get("t") == "inlineStr":
                    val = "".join(n.text or "" for n in cell.findall(".//m:t", NS))
                else:
                    v = cell.find("m:v", NS)
                    val = (v.text or "") if v is not None else ""
                    if cell.attrib.get("t") == "s" and val:
                        val = shared[int(val)]
                items[col] = val
            yield items


def extract(path):
    tags = []
    seen = set()
    for item in read_sheet(path):
        name = item.get("A", "").strip()
        dtype = item.get("C", "").strip().upper()
        dimension = item.get("F", "").strip()
        if not name or dtype not in {"REAL", "LREAL", "BOOL", "DINT", "INT", "UINT", "UDINT"}:
            continue
        if dimension and not ("[" in name and "]" in name):
            # Skip aggregate array declaration: individual index rows follow in the CCW export.
            continue
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        tags.append({"name": name, "type": dtype})
    return tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("json_output", type=Path)
    args = ap.parse_args()
    tags = extract(args.xlsx)
    if not tags:
        raise SystemExit("No tags found; verify the CCW workbook format")
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps({"source": args.xlsx.name, "tags": tags}, indent=2) + "\n",
                                encoding="utf-8")
    print(f"Exported {len(tags)} unique scalar tags to {args.json_output}")


if __name__ == "__main__":
    main()
