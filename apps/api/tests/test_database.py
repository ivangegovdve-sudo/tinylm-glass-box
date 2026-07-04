import unittest
import queue
import threading

from tinylm_glass_box_api.db import connect_db, initialize_db


class DatabaseTests(unittest.TestCase):
    def test_initialize_db_creates_expected_tables_and_enables_wal(self):
        with self.subTest("setup"):
            from tempfile import TemporaryDirectory
            from pathlib import Path

            with TemporaryDirectory() as temp_dir:
                db_path = Path(temp_dir) / "glass-box.db"
                connection = connect_db(db_path)
                initialize_db(connection)

                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }

                self.assertEqual(journal_mode.lower(), "wal")
                self.assertTrue({"runs", "seat_executions", "state_history", "event_log"} <= table_names)
                connection.close()

    def test_connection_allows_cross_thread_use_for_fastapi_request_handling(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "threadsafe.db"
            connection = connect_db(db_path)
            initialize_db(connection)
            result_queue: queue.Queue[str] = queue.Queue()

            def worker() -> None:
                try:
                    connection.execute("SELECT 1").fetchone()
                    result_queue.put("ok")
                except Exception as exc:
                    result_queue.put(type(exc).__name__)

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()

            self.assertEqual(result_queue.get_nowait(), "ok")
            connection.close()
