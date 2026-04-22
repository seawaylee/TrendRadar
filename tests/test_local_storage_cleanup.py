import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from trendradar.storage.local import LocalStorageBackend


class LocalStorageCleanupTests(unittest.TestCase):
    @patch("trendradar.storage.local.get_configured_time")
    def test_cleanup_old_data_removes_expired_meta_files(self, mock_get_configured_time):
        mock_get_configured_time.return_value = datetime(2026, 4, 22, 8, 0, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "output"
            meta_dir = data_dir / "meta"
            meta_dir.mkdir(parents=True, exist_ok=True)

            expired_file = meta_dir / "feishu_digest_history-old.json"
            expired_file.write_text("{}", encoding="utf-8")

            fresh_file = meta_dir / "feishu_digest_history-new.json"
            fresh_file.write_text("{}", encoding="utf-8")

            old_timestamp = datetime(2026, 4, 10, 8, 0, 0).timestamp()
            fresh_timestamp = datetime(2026, 4, 21, 8, 0, 0).timestamp()
            expired_file.touch()
            fresh_file.touch()
            import os

            os.utime(expired_file, (old_timestamp, old_timestamp))
            os.utime(fresh_file, (fresh_timestamp, fresh_timestamp))

            backend = LocalStorageBackend(data_dir=str(data_dir), enable_txt=False, enable_html=False)
            deleted_count = backend.cleanup_old_data(retention_days=7)

            self.assertEqual(deleted_count, 1)
            self.assertFalse(expired_file.exists())
            self.assertTrue(fresh_file.exists())


if __name__ == "__main__":
    unittest.main()
