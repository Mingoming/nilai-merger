import io
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

import pandas as pd

from src.excel_utils import csv_to_df
from src.naming import filename_key
from src import naming, processor


HEADERS = ["Surname", "First name", "Email address", "Grade/100.00", "Q. 1"]
ROWS = [["XII A", "Ani", "12-05-061@example.test", "80,5", "1"],
        ["XII A", "Budi", "12-05-062@example.test", "70", "0"]]


def csv_bytes(rows=None, sep=",", headers=None):
    return pd.DataFrame(ROWS if rows is None else rows,
                        columns=HEADERS if headers is None else headers).to_csv(
                            index=False, sep=sep).encode("utf-8-sig")


def upload(name="input.zip", files=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in (files or {}).items():
            archive.writestr(path, data)
    return name, buffer.getvalue()


def outputs(result):
    with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as archive:
        return {name: pd.read_excel(io.BytesIO(archive.read(name)))
                for name in archive.namelist()}


class ExistingBehaviorTests(unittest.TestCase):
    def test_safe_filename_normalization_keeps_meaningful_words(self):
        for name in [" MATEMATIKA-grades ", "matematika-grades(1)",
                     "MATEMATIKA-grades2", " MATEMATIKA  - grades(3)"]:
            self.assertEqual(filename_key(name), "matematika")
        self.assertEqual(filename_key("TO  BAHASA   INGGRIS XII"),
                         "to bahasa inggris xii")
        self.assertNotEqual(filename_key("BAHASA INGGRIS XII"),
                            filename_key("BAHASA INGGRIS LANJUT XII"))

    def test_comma_tab_and_optional_footer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grades.csv"
            for sep in [",", "\t"]:
                for footer in [False, True]:
                    rows = ROWS + ([["Overall average", "", "", "75", ""]]
                                   if footer else [])
                    path.write_bytes(csv_bytes(rows, sep))
                    frame = csv_to_df(path)
                    self.assertEqual(len(frame), 2)
                    self.assertIn("Budi", frame["First name"].tolist())

    def test_clean_dedupe_preserves_displayed_grade_and_blank_ids(self):
        frame = pd.DataFrame(ROWS + [
            ["XII A", "Ani", "12-05-061@other.example.test", "90,75", "1"],
            ["XII A", "Blank", "", "60", "0"],
            ["XII A", "Blank", None, "70", "0"],
            ["XII A", "Bad", "bad@example.test", "-", "0"],
        ], columns=HEADERS)
        final, audit = processor._dedupe_final(processor.rename_and_clean(frame))
        self.assertEqual(len(final), 5)
        self.assertEqual(final.loc[final.NoPes.eq("12-05-061"), "Nilai"].iloc[0], "90,75")
        self.assertEqual(audit["nopes_kosong"], 2)
        self.assertEqual(audit["nilai_tidak_numerik"], 1)

    def test_multiple_zips_exact_groups_and_single_subject(self):
        result = processor.process_zip_uploads([
            upload("a.zip", {"folder/MATH-grades.csv": csv_bytes(),
                             "BIO.csv": csv_bytes()}),
            upload("b.zip", {"MATH-grades2.csv": csv_bytes(sep="\t")})])
        self.assertEqual(result.total_csv, 3)
        self.assertEqual(result.total_mapel, 2)
        self.assertEqual(sorted(result.audit["final"].tolist()), [2, 2])
        self.assertTrue(all(name.endswith(".xlsx") for name in outputs(result)))


class ReviewTests(unittest.TestCase):
    def test_candidates_are_deterministic_suggestions_only(self):
        keys = ["b. inggris xii", "bahasa inggris xii",
                "bahasa inggris lanjut xii", "matematika xii", "bahasa inggris xi"]
        candidates = naming.similar_group_candidates(keys)
        self.assertEqual(candidates, naming.similar_group_candidates(reversed(keys)))
        pairs = {(c.left, c.right) for c in candidates}
        self.assertIn(tuple(sorted(keys[:2])), pairs)
        self.assertFalse(any("matematika xii" in pair for pair in pairs))
        self.assertFalse(any("bahasa inggris xi" in pair for pair in pairs))

    def test_only_explicit_merge_changes_grouping_and_analysis_is_reusable(self):
        analysis = processor.analyze_zip_uploads([upload(files={
            "B. INGGRIS XII.csv": csv_bytes(),
            "BAHASA INGGRIS XII.csv": csv_bytes(),
            "BAHASA INGGRIS LANJUT XII.csv": csv_bytes()})])
        self.assertEqual(len(analysis.groups), 3)
        self.assertEqual(processor.process_analysis(analysis).total_mapel, 3)
        pair = ("b. inggris xii", "bahasa inggris xii")
        self.assertEqual(processor.process_analysis(analysis, {pair: False}).total_mapel, 3)
        self.assertEqual(processor.process_analysis(analysis, {pair: True}).total_mapel, 2)
        self.assertEqual(len(analysis.groups), 3)
        self.assertEqual(processor.process_analysis(analysis).total_mapel, 3)

    def test_rejects_conflicting_transitive_decisions_and_unknown_groups(self):
        analysis = processor.analyze_zip_uploads([upload(files={
            "A.csv": csv_bytes(), "B.csv": csv_bytes(), "C.csv": csv_bytes()})])
        with self.assertRaisesRegex(ValueError, "terpisah"):
            processor.process_analysis(analysis, {("a", "b"): True,
                                                 ("b", "c"): True, ("a", "c"): False})
        with self.assertRaises(ValueError):
            processor.process_analysis(analysis, {("a", "unknown"): True})
        with self.assertRaises(ValueError):
            processor.process_analysis(analysis, {("a", "b"): "merge"})

    def test_reader_reports_delimiter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.csv"
            for sep in [",", "\t"]:
                path.write_bytes(csv_bytes(sep=sep))
                self.assertEqual(csv_to_df(path).attrs["delimiter"], sep)

    def test_metadata_differences_are_compatible_and_logged(self):
        logs = []
        extra = pd.DataFrame(ROWS, columns=HEADERS)
        extra["Institution"] = "school"
        result = processor.process_zip_uploads([
            upload("a.zip", {"MATH.csv": csv_bytes()}),
            upload("b.zip", {"MATH.csv": extra.to_csv(index=False).encode()})], logs.append)
        self.assertEqual(result.total_mapel, 1)
        text = "\n".join(logs)
        for part in ["metadata", "institution", "a.zip", "b.zip", "delimiter"]:
            self.assertIn(part, text)

    def test_mismatch_has_actionable_sources_and_preserves_other_subjects(self):
        result = processor.process_zip_uploads([
            upload("a.zip", {"folder/MATH.csv": csv_bytes(), "BIO.csv": csv_bytes()}),
            upload("b.zip", {"MATH.csv": csv_bytes(sep="\t",
                                                  headers=HEADERS[:-1] + ["Q. 2"])})])
        self.assertEqual(result.total_mapel, 1)
        text = "\n".join(result.errors)
        for part in ["MATH", "a.zip", "folder", "b.zip", "delimiter", "TAB",
                     "q. 1", "q. 2", "kolom wajib", "relevan"]:
            self.assertIn(part, text)
        self.assertEqual(list(outputs(result)), ["BIO.xlsx"])

    def test_missing_required_columns_and_all_failures_keep_details(self):
        with self.assertRaisesRegex(ValueError, "email address"):
            processor.process_zip_uploads([upload(files={"MATH.csv":
                csv_bytes([row[:2] + row[3:] for row in ROWS],
                          headers=HEADERS[:2] + HEADERS[3:])})])

    def test_broken_zip_empty_zip_and_bad_csv_do_not_kill_good_group(self):
        result = processor.process_zip_uploads([
            ("broken.zip", b"invalid"), upload("empty.zip", {"note.txt": b"hello"}),
            upload("good.zip", {"MATH.csv": csv_bytes(), "empty.csv": b""})])
        self.assertEqual(result.total_mapel, 1)
        self.assertIn("broken.zip", "\n".join(result.errors))
        self.assertIn("empty.csv", "\n".join(result.errors))
        self.assertIn("empty.zip", "\n".join(result.warnings))

    def test_normalized_headers_do_not_split_identity_or_question_columns(self):
        result = processor.process_zip_uploads([
            upload("a.zip", {"MATH.csv": csv_bytes()}),
            upload("b.zip", {"MATH.csv": csv_bytes(headers=["  " + h.upper() + " "
                                                           for h in HEADERS])})])
        frame = next(iter(outputs(result).values()))
        self.assertEqual(frame.columns.tolist(), ["Kelas", "Nama", "NoPes", "Nilai", "Q. 1"])
        self.assertEqual(len(frame), 2)

    def test_output_name_collisions_do_not_overwrite_subjects(self):
        result = processor.process_zip_uploads([upload(files={
            "A/B.csv": csv_bytes(), "B.csv": csv_bytes(),
            "Case.csv": csv_bytes(), "Case..csv": csv_bytes()})])
        self.assertEqual(result.total_mapel, 3)
        self.assertEqual(len(outputs(result)), 3)

    def test_collision_suffix_survives_grade_filename_normalization(self):
        result = processor.process_zip_uploads([upload(files={
            "MATH-grades.csv": csv_bytes(), "MATH-grades..csv": csv_bytes()})])
        self.assertEqual(len(outputs(result)), 2)
        self.assertEqual(result.total_mapel, 2)

    def test_ambiguous_normalized_headers_skip_only_bad_subject(self):
        result = processor.process_zip_uploads([upload(files={
            "GOOD.csv": csv_bytes(),
            "BAD.csv": csv_bytes([row + ["70"] for row in ROWS],
                                 headers=HEADERS + [" grade/100.00 "])})])
        self.assertEqual(list(outputs(result)), ["GOOD.xlsx"])
        self.assertIn("kolom ambigu", "\n".join(result.errors))

    def test_partial_excel_writes_are_excluded_from_final_zip(self):
        original = processor.df_to_excel
        for stage in ["step1", "final"]:
            with self.subTest(stage=stage):
                def failing_write(df, path):
                    if path.parent.name == stage and path.stem == "BAD":
                        path.write_bytes(b"partial workbook")
                        raise OSError("simulated write failure")
                    return original(df, path)
                with patch.object(processor, "df_to_excel", side_effect=failing_write):
                    result = processor.process_zip_uploads([upload(files={
                        "BAD.csv": csv_bytes(), "GOOD.csv": csv_bytes()})])
                self.assertEqual(list(outputs(result)), ["GOOD.xlsx"])
                self.assertEqual(result.total_mapel, 1)
                self.assertEqual(len(result.audit), 1)
                self.assertIn("BAD", "\n".join(result.errors))

    def test_mixed_tab_comma_overlap_279_plus_42(self):
        rows = [["XII", f"Student {i:03}", f"12-05-{i:03}@example.test", "70", "1"]
                for i in range(279)]
        extra = [row[:3] + ["90,5", "1"] for row in rows[:42]]
        result = processor.process_zip_uploads([
            upload("tab.zip", {"INFORMATIKA XII-grades.csv": csv_bytes(rows, "\t")}),
            upload("comma.zip", {"INFORMATIKA XII-grades2.csv": csv_bytes(extra)})])
        self.assertEqual(result.audit.iloc[0]["sebelum"], 321)
        self.assertEqual(result.audit.iloc[0]["final"], 279)
        self.assertEqual(result.audit.iloc[0]["id_duplikat"], 42)
        frame = next(iter(outputs(result).values()))
        self.assertEqual(len(frame), 279)
        self.assertEqual(frame.Nilai.astype(str).eq("90,5").sum(), 42)


if __name__ == "__main__":
    unittest.main()
