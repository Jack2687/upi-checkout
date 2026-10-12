"""Phân loại kết quả flow: 403 lúc tạo checkout phải hiện đúng nguyên nhân.

Trước đây lỗi này bị ghi thành "no link — flow stopped at step 'approve'" nên đọc
UI tưởng hỏng ở bước approve của Stripe, trong khi thực tế OpenAI đã trả 403 ngay
khi tạo checkout và flow chưa hề chạm tới Stripe.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli  # noqa: E402

BLOCKED = [
    "[2026-10-12 21:02:29] fingerprint: acct=ca90cd4a device=b62054d5 screen=1366x768",
    "[2026-10-12 21:02:29] [WARN] all proxy seeds for this task have been tried",
    "[2026-10-12 21:02:29] [ERROR] all failed: checkout creation failed HTTP 403: <html>",
]


class ClassifyCheckoutBlockTests(unittest.TestCase):
    def test_checkout_403_gets_its_own_message(self):
        status, err, link, has_qr = cli.classify_cs_outcome(BLOCKED, "approve", None, False)
        self.assertEqual(status, "FAIL")
        self.assertIn("blocked checkout creation", err)
        self.assertIn("403", err)
        self.assertNotIn("flow stopped at step", err)
        self.assertEqual((link, has_qr), ("", False))

    def test_plain_approve_stall_still_reads_as_before(self):
        lines = ["[2026-10-12 21:00:00] [PM=IN] needs approve: UPI 0-value path",
                 "[ERROR] all failed: no redirect_url generated"]
        status, err, _, _ = cli.classify_cs_outcome(lines, "approve", None, False)
        self.assertEqual(status, "FAIL")
        self.assertIn("flow stopped at step 'approve'", err)

    def test_risk_decline_keeps_priority_over_checkout_message(self):
        lines = ["[PM=IN] needs approve", "setup_intent last_setup_error generic_decline",
                 "checkout created: cs_live_x"]
        _, err, _, _ = cli.classify_cs_outcome(lines, "approve", None, True)
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
