#!/usr/bin/env python3
"""Print the key structure of an answer-bank JSON file: dotted key paths and value types only.

Values are never printed. Usage: bank_schema_dump.py [--bank PATH]
(default: $ANSWER_BANK_PATH, else ./answer_bank.json). Plain JSON only; the encrypted vault file
(answer_bank_vault.py) must be exported to JSON first.
"""
import argparse
import json
import os
import sys


def type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        kinds = {type_name(v) for v in value}
        if not kinds:
            return "list[]"
        return f"list[{kinds.pop()}]" if len(kinds) == 1 else "list[mixed]"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def walk(node, prefix=""):
    """Yield (dotted_path, type_name) for every leaf; an empty dict is a leaf of type 'dict'."""
    if isinstance(node, dict) and node:
        for key in sorted(node):
            yield from walk(node[key], f"{prefix}.{key}" if prefix else str(key))
    else:
        yield prefix or "<root>", type_name(node)


def dump(bank):
    return [f"{path}: {kind}" for path, kind in sorted(walk(bank))]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dump answer-bank key paths and value types (never values).")
    parser.add_argument("--bank", default=os.environ.get("ANSWER_BANK_PATH") or "./answer_bank.json")
    args = parser.parse_args(argv)
    try:
        with open(args.bank, encoding="utf-8") as fh:
            bank = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"cannot read bank: {type(exc).__name__}", file=sys.stderr)  # no path detail, no content
        return 2
    for line in dump(bank):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
