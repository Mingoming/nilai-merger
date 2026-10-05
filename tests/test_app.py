import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from test_pipeline import csv_bytes, upload


APP = Path(__file__).resolve().parents[1] / "app.py"


class AppReviewTests(unittest.TestCase):
    def open_review(self):
        at = AppTest.from_file(str(APP), default_timeout=15).run()
        name, data = upload(files={"B. INGGRIS XII.csv": csv_bytes(),
                                   "BAHASA INGGRIS XII.csv": csv_bytes()})
        at.file_uploader[0].set_value([(name, data, "application/zip")]).run()
        at.button(key="nm_analyze").click().run()
        self.assertEqual(len(at.exception), 0)
        return at

    def test_upload_review_merge_rerun_and_changed_decision(self):
        at = self.open_review()
        self.assertTrue(at.button(key="nm_process").disabled)
        analysis = at.session_state["nm_analysis"]
        at.radio[0].set_value("Gabungkan").run()
        self.assertIs(at.session_state["nm_analysis"], analysis)
        at.button(key="nm_process").click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["nm_result"].total_mapel, 1)
        self.assertEqual(len(at.get("download_button")), 1)
        at.run()
        self.assertEqual(len(at.get("download_button")), 1)
        self.assertTrue(any("Selesai" in line for line in at.session_state["nm_logs"]))
        at.radio[0].set_value("Tetap terpisah").run()
        self.assertEqual(len(at.get("download_button")), 0)
        at.button(key="nm_process").click().run()
        self.assertEqual(at.session_state["nm_result"].total_mapel, 2)

    def test_changed_upload_resets_analysis_review_and_output(self):
        at = self.open_review()
        at.radio[0].set_value("Tetap terpisah").run()
        at.button(key="nm_process").click().run()
        name, data = upload(files={"BIO.csv": csv_bytes()})
        at.file_uploader[0].set_value([(name, data, "application/zip")]).run()
        self.assertEqual(len(at.radio), 0)
        self.assertEqual(len(at.get("download_button")), 0)
        self.assertNotIn("nm_analysis", at.session_state)
        at.button(key="nm_analyze").click().run()
        at.button(key="nm_process").click().run()
        self.assertEqual(at.session_state["nm_result"].total_mapel, 1)
        self.assertEqual(len(at.exception), 0)

    def test_empty_and_invalid_uploads_show_errors_without_traceback(self):
        at = AppTest.from_file(str(APP), default_timeout=15).run()
        self.assertTrue(at.button(key="nm_analyze").disabled)
        at.file_uploader[0].set_value([("broken.zip", b"bad", "application/zip")]).run()
        at.button(key="nm_analyze").click().run()
        self.assertTrue(at.error)
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(at.button(key="nm_process").disabled)


if __name__ == "__main__":
    unittest.main()
