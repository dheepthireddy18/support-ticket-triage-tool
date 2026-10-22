"""
Multi-format file readers for ticket ingestion.
Supports reading ticket records from JSON and CSV files, packaging unexpected
columns into a metadata dictionary, and providing clean domain error handling.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

CORE_TICKET_FIELDS = frozenset({"id", "subject", "body", "customer", "created_at"})


class FileReadError(Exception):
    """Base exception for ticket file ingestion errors."""


class UnsupportedFormatError(FileReadError):
    """Raised when an unsupported file format is provided."""


class CorruptedFileError(FileReadError):
    """Raised when a file cannot be parsed due to corrupt syntax or decoding error."""


class EmptyFileError(FileReadError):
    """Raised when an input file contains no ticket records."""


def bundle_metadata(raw_record: dict[str, Any]) -> dict[str, Any]:
    """
    Separate core schema fields from unexpected fields, placing all unmapped
    attributes into a `metadata` dictionary on the record.
    """
    normalized: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    # If raw_record already has a metadata dictionary, initialize with it
    existing_meta = raw_record.get("metadata")
    if isinstance(existing_meta, dict):
        metadata.update(existing_meta)

    for key, value in raw_record.items():
        if key in CORE_TICKET_FIELDS:
            normalized[key] = value
        elif key == "metadata":
            continue
        else:
            # Package unexpected/extra columns into metadata
            # Only omit empty string values from CSV if desired, or preserve value
            metadata[key] = value

    normalized["metadata"] = metadata
    return normalized


def read_json_tickets(path: Path) -> list[dict[str, Any]]:
    """
    Read ticket records from a JSON file.
    Supports a list of objects or a single JSON object.
    """
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise CorruptedFileError(
            f"Corrupted JSON in '{path.name}': {exc.msg} (line {exc.lineno}, col {exc.colno})"
        ) from exc
    except UnicodeDecodeError as exc:
        raise CorruptedFileError(
            f"Encoding error in '{path.name}': {exc.reason}"
        ) from exc

    if isinstance(data, dict):
        records = [data]
    elif isinstance(data, list):
        records = data
    else:
        raise CorruptedFileError(
            f"Invalid JSON structure in '{path.name}': expected an array of ticket objects, got {type(data).__name__}"
        )

    if not records:
        raise EmptyFileError(f"JSON file '{path.name}' contains no ticket records.")

    return [bundle_metadata(r) if isinstance(r, dict) else r for r in records]


def read_csv_tickets(path: Path) -> list[dict[str, Any]]:
    """
    Read ticket records from a CSV file using csv.DictReader.
    Strips whitespace from column header names and bundles unmapped columns.
    """
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                raw_headers = next(reader)
            except StopIteration:
                raise EmptyFileError(f"CSV file '{path.name}' is empty.") from None

            headers = [h.strip() for h in raw_headers if h is not None]
            if not headers:
                raise EmptyFileError(f"CSV file '{path.name}' contains no headers.")

            records: list[dict[str, Any]] = []
            for line_no, row in enumerate(reader, start=2):
                if not row or all(not cell.strip() for cell in row):
                    continue  # Skip blank lines

                # Map headers to row cells
                row_dict: dict[str, Any] = {}
                for idx, header in enumerate(headers):
                    val = row[idx].strip() if idx < len(row) else ""
                    row_dict[header] = val

                records.append(bundle_metadata(row_dict))

    except (csv.Error, UnicodeDecodeError) as exc:
        raise CorruptedFileError(f"Error parsing CSV file '{path.name}': {exc}") from exc

    if not records:
        raise EmptyFileError(f"CSV file '{path.name}' contains no data rows.")

    return records


def read_tickets_file(file_path: Path | str) -> list[dict[str, Any]]:
    """
    Main entry point for reading tickets from an input file.
    Inspects file extension (.json or .csv), validates file presence and non-emptiness,
    and returns a list of dictionaries with unexpected columns bundled into `metadata`.

    :param file_path: Path to the JSON or CSV file.
    :return: List of record dictionaries.
    :raises FileNotFoundError: If the file does not exist.
    :raises EmptyFileError: If the file is 0 bytes or contains no records.
    :raises UnsupportedFormatError: If the file extension is not .json or .csv.
    :raises CorruptedFileError: If the file content cannot be parsed.
    """
    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError(f"Tickets file not found: {path}")

    if path.stat().st_size == 0:
        raise EmptyFileError(f"File '{path.name}' is empty (0 bytes).")

    suffix = path.suffix.lower()
    if suffix == ".json":
        return read_json_tickets(path)
    elif suffix == ".csv":
        return read_csv_tickets(path)
    else:
        raise UnsupportedFormatError(
            f"Unsupported file format '{suffix}' for '{path.name}'. Only .json and .csv files are supported."
        )

