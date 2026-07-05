"""Parse simple NAME=VALUE configuration records.

Each non-empty, non-comment line must contain exactly one ``=`` separator.
Whitespace around names and values is ignored. Names must be non-empty. Values
must be non-empty decimal integers greater than or equal to zero. Invalid input
must raise ValueError mentioning the original 1-based line number.
"""


def parse_records(text):
    records = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {line_number}: expected NAME=VALUE")

        name, value = line.split("=", 1)
        if not name:
            raise ValueError(f"line {line_number}: empty name")
        try:
            records[name] = int(value)
        except ValueError as exc:
            raise ValueError(f"line {line_number}: invalid integer") from exc
    return records
