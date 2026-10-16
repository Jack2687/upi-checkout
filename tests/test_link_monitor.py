"""Vòng dò link nền: KHÔNG được bỏ đói link "Awaiting payment" sau khi job xong.

Đo thật 16/10 (job 3dcd91d4f58a): 2 link ở trạng thái `waiting` bị bỏ dò sau khi job xong
30 phút, nên chip "Awaiting payment" đứng im mãi — khách duyệt muộn cũng không ai biết, và
uỷ nhiệm hết hạn cũng không được đánh dấu.
"""
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine  # noqa: E402

LINK = "https://payments.stripe.com/upi/instructions/abc123"


def fake_task(state="waiting", link=LINK, task_id="t1", proxy=""):
    return SimpleNamespace(task_id=task_id, artifact={"upi_link": link}, link_state=state,
                           proxy=proxy, egress=None)


def fake_job(minutes_since_finish: float | None, tasks):
    finished = None
    if minutes_since_finish is not None:
        stamp = time.time() - minutes_since_finish * 60
        finished = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))
    return SimpleNamespace(finished_at=finished, _lock=threading.RLock(),
                           tasks={t.task_id: t for t in tasks})


class LinkMonitorTargetTests(unittest.TestCase):
    def setUp(self):
        self.keep = dict(engine._JOBS)

    def tearDown(self):
        engine._JOBS.clear()
        engine._JOBS.update(self.keep)

    def _targets(self, jobs):
        engine._JOBS.clear()
        for job in jobs:
            engine._JOBS["job-" + str(id(job))] = job
        return engine._link_monitor_targets()

    def test_running_job_is_hot(self):
        hot, cold = self._targets([fake_job(None, [fake_task()])])
        self.assertEqual(len(hot), 1)
        self.assertEqual(cold, [])

    def test_recently_finished_job_is_still_hot(self):
        hot, cold = self._targets([fake_job(2, [fake_task()])])
        self.assertEqual((len(hot), len(cold)), (1, 0))

    def test_job_finished_an_hour_ago_is_cold_but_still_tracked(self):
        """Đây là ca bị bỏ đói trước đây: job xong 60 phút -> vẫn phải nằm trong danh sách."""
        hot, cold = self._targets([fake_job(60, [fake_task()])])
        self.assertEqual(hot, [])
        self.assertEqual(len(cold), 1)

    def test_gives_up_only_after_the_long_window(self):
        self.assertEqual(self._targets([fake_job(60, [fake_task()])])[1] != [], True)
        long_gone = engine.MONITOR_MAX_IDLE_MIN + 60
        hot, cold = self._targets([fake_job(long_gone, [fake_task()])])
        self.assertEqual((hot, cold), ([], []))
        self.assertGreaterEqual(engine.MONITOR_MAX_IDLE_MIN, 12 * 60)   # ít nhất nửa ngày

    def test_terminal_and_linkless_tasks_are_skipped(self):
        job = fake_job(60, [fake_task(state="succeeded", task_id="t1"),
                            fake_task(state="waiting", link="", task_id="t2"),
                            fake_task(state="", link=LINK, task_id="t3")])
        hot, cold = self._targets([job])
        self.assertEqual([t.task_id for _, t in cold], ["t3"])

    def test_unchanged_state_still_reports_the_check_time(self):
        """Không đổi trạng thái vẫn phải emit mốc dò, nếu không UI thấy chip đứng im."""
        emitted: list[dict] = []
        job = fake_job(60, [fake_task()])
        job.emit = emitted.append
        job.log_line = lambda *_a, **_k: None
        job._lock = threading.RLock()
        task = next(iter(job.tasks.values()))
        task.link_fam = ""
        task.link_zero = False
        task.link_note = ""
        task.link_checked_at = 0
        task.link_first_seen = 0
        task.proxy = ""
        task.egress = {"proxy": "http://user:pass@px:8080"}
        seen: dict = {}
        with patch.object(engine.artifact_probe, "probe",
                          side_effect=lambda *a, **k: (seen.update(k) or
                                                       {"ok": True, "status": "waiting"})):
            engine._monitor_one(job, task)
        self.assertEqual([e["type"] for e in emitted], ["task_link_checked"])
        self.assertGreater(task.link_checked_at, 0)


if __name__ == "__main__":
    unittest.main()
