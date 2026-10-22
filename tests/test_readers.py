"""
Unit tests for JSON and CSV file readers, metadata bundling, and error handling.
Compatible with both `pytest` and Python's built-in `unittest`.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ticket_triage.readers import (
    CorruptedFileError,
    EmptyFileError,
    UnsupportedFormatError,
    bundle_metadata,
    read_tickets_file,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_JSON = PROJECT_ROOT / "data" / "sample_tickets.json"


class TestFileReaders(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_sample_json_file(self):
        records = read_tickets_file(SAMPLE_JSON)
        self.assertEqual(len(records), 8)
        first = records[0]
        self.assertEqual(first["id"], "e4eb2955-8d61-49a2-ac15-f016a366951a")
        self.assertEqual(first["customer"], "CUST-101")
        self.assertIn("metadata", first)
        self.assertIsInstance(first["metadata"], dict)

    def test_read_single_json_object(self):
        file_path = self.dir_path / "single.json"
        data = {
            "id": "single-1",
            "subject": "Single ticket",
            "body": "Not in an array",
            "customer": "CUST-99",
            "created_at": "2026-10-22T10:00:00Z",
        }
        file_path.write_text(json.dumps(data), encoding="utf-8")

        records = read_tickets_file(file_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "single-1")
        self.assertEqual(records[0]["customer"], "CUST-99")

    def test_read_json_bundles_extra_columns_into_metadata(self):
        file_path = self.dir_path / "extra_cols.json"
        data = [
            {
                "id": "t-1",
                "subject": "System crash",
                "body": "Kernel panic",
                "customer": "CUST-5",
                "created_at": "2026-10-22T08:00:00Z",
                "department": "Engineering",
                "build_number": 402,
                "tags": ["critical", "infra"],
            }
        ]
        file_path.write_text(json.dumps(data), encoding="utf-8")

        records = read_tickets_file(file_path)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["id"], "t-1")
        # Extra columns should be bundled into metadata
        self.assertNotIn("department", rec.keys() - {"metadata"})
        self.assertEqual(rec["metadata"]["department"], "Engineering")
        self.assertEqual(rec["metadata"]["build_number"], 402)
        self.assertEqual(rec["metadata"]["tags"], ["critical", "infra"])

    def test_read_valid_csv_file(self):
        file_path = self.dir_path / "tickets.csv"
        csv_content = (
            "id,subject,body,customer,created_at\n"
            "c-101,Login fail,Cannot sign in,CUST-A,2026-10-21T09:00:00Z\n"
            "c-102,Billing query,Question on charges,CUST-B,2026-10-21T10:30:00Z\n"
        )
        file_path.write_text(csv_content, encoding="utf-8")

        records = read_tickets_file(file_path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["id"], "c-101")
        self.assertEqual(records[0]["customer"], "CUST-A")
        self.assertEqual(records[1]["id"], "c-102")
        self.assertEqual(records[1]["subject"], "Billing query")

    def test_read_csv_bundles_extra_columns_into_metadata(self):
        file_path = self.dir_path / "extra_csv.csv"
        csv_content = (
            "id,subject,body,customer,created_at,source_channel,severity\n"
            "c-201,Bug report,UI button broken,CUST-C,2026-10-21T12:00:00Z,Web,P2\n"
        )
        file_path.write_text(csv_content, encoding="utf-8")

        records = read_tickets_file(file_path)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["id"], "c-201")
        self.assertEqual(rec["metadata"]["source_channel"], "Web")
        self.assertEqual(rec["metadata"]["severity"], "P2")

    def test_bundle_metadata_helper(self):
        raw = {
            "id": "1",
            "subject": "Sub",
            "body": "Body",
            "customer": "Cust",
            "created_at": "2026-10-20T00:00:00Z",
            "extra_one": 123,
            "metadata": {"existing": True},
        }
        bundled = bundle_metadata(raw)
        self.assertEqual(bundled["id"], "1")
        self.assertEqual(bundled["metadata"]["existing"], True)
        self.assertEqual(bundled["metadata"]["extra_one"], 123)

    def test_read_csv_with_utf8_bom(self):
        file_path = self.dir_path / "bom_tickets.csv"
        # Prepend UTF-8 BOM (\ufeff) typical of Microsoft Excel exports
        csv_content = "\ufeffid,subject,body,customer,created_at\nbom-1,Excel issue,Tested with BOM,CUST-E,2026-10-22T08:00:00Z\n"
        file_path.write_bytes(csv_content.encode("utf-8"))

        records = read_tickets_file(file_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "bom-1")
        self.assertEqual(records[0]["customer"], "CUST-E")

    def test_read_csv_with_multiline_body(self):
        file_path = self.dir_path / "multiline_body.csv"
        csv_content = (
            'id,subject,body,customer,created_at\n'
            'm-1,Multiline error,"First line of description\nSecond line of description",CUST-M,2026-10-22T09:00:00Z\n'
        )
        file_path.write_text(csv_content, encoding="utf-8")

        records = read_tickets_file(file_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "m-1")
        self.assertIn("First line of description\nSecond line of description", records[0]["body"])

    def test_corrupted_json_syntax_raises_corrupted_file_error(self):
        file_path = self.dir_path / "corrupted.json"
        file_path.write_text("[{'invalid_json': True, missing_quote: 123}]", encoding="utf-8")

        with self.assertRaises(CorruptedFileError) as ctx:
            read_tickets_file(file_path)
        self.assertIn("Corrupted JSON", str(ctx.exception))

    def test_empty_file_zero_bytes_raises_empty_file_error(self):
        file_path = self.dir_path / "empty.json"
        file_path.write_text("", encoding="utf-8")

        with self.assertRaises(EmptyFileError) as ctx:
            read_tickets_file(file_path)
        self.assertIn("empty", str(ctx.exception).lower())

    def test_empty_json_array_raises_empty_file_error(self):
        file_path = self.dir_path / "empty_array.json"
        file_path.write_text("[]", encoding="utf-8")

        with self.assertRaises(EmptyFileError) as ctx:
            read_tickets_file(file_path)
        self.assertIn("contains no ticket records", str(ctx.exception))

    def test_empty_csv_no_data_rows_raises_empty_file_error(self):
        file_path = self.dir_path / "empty_rows.csv"
        file_path.write_text("id,subject,body,customer,created_at\n", encoding="utf-8")

        with self.assertRaises(EmptyFileError) as ctx:
            read_tickets_file(file_path)
        self.assertIn("contains no data rows", str(ctx.exception))

    def test_unsupported_file_format_raises_unsupported_format_error(self):
        file_path = self.dir_path / "tickets.xml"
        file_path.write_text("<tickets><ticket></ticket></tickets>", encoding="utf-8")

        with self.assertRaises(UnsupportedFormatError) as ctx:
            read_tickets_file(file_path)
        self.assertIn("Unsupported file format '.xml'", str(ctx.exception))

    def test_non_existent_file_raises_file_not_found(self):
        file_path = self.dir_path / "does_not_exist.json"
        with self.assertRaises(FileNotFoundError):
            read_tickets_file(file_path)


if __name__ == "__main__":
    unittest.main()

