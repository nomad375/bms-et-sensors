import unittest

from app.mscl_offset_service import compute_export_clock_offset_ns


class OffsetServiceTests(unittest.TestCase):
    def test_empty_rows(self):
        off, skew = compute_export_clock_offset_ns(
            rows=[],
            node_id=1,
            min_skew_sec=2.0,
            recalc_threshold_sec=3.0,
            recalc_max_skew_sec=30.0,
            cache={},
            load_persisted_fn=lambda _nid: None,
            persist_fn=lambda _nid, _off: None,
            log_func=lambda _msg: None,
            now_ns=1_000_000_000,
        )
        self.assertEqual(off, 0)
        self.assertEqual(skew, 0)

    def test_choose_zero_for_small_skew(self):
        cache = {}
        persisted = {}
        off, skew = compute_export_clock_offset_ns(
            rows=[{"timestamp_ns": 10_000_000_000}],
            node_id=1,
            min_skew_sec=2.0,
            recalc_threshold_sec=3.0,
            recalc_max_skew_sec=30.0,
            cache=cache,
            load_persisted_fn=lambda _nid: None,
            persist_fn=lambda nid, o: persisted.setdefault(nid, o),
            log_func=lambda _msg: None,
            now_ns=11_000_000_000,
        )
        self.assertEqual(skew, 1_000_000_000)
        self.assertEqual(off, 0)
        self.assertEqual(cache[1], 0)

    def test_reuse_cached_offset(self):
        cache = {1: 5_000_000_000}
        persisted_calls = []
        off, _ = compute_export_clock_offset_ns(
            rows=[{"timestamp_ns": 10_000_000_000}],
            node_id=1,
            min_skew_sec=2.0,
            recalc_threshold_sec=3.0,
            recalc_max_skew_sec=30.0,
            cache=cache,
            load_persisted_fn=lambda _nid: None,
            persist_fn=lambda nid, o: persisted_calls.append((nid, o)),
            log_func=lambda _msg: None,
            now_ns=14_000_000_000,
        )
        self.assertEqual(off, 5_000_000_000)
        self.assertEqual(persisted_calls, [])

    def test_recalc_cached_offset_when_near_realtime(self):
        cache = {1: 1_000_000_000}
        persisted_calls = []
        off, skew = compute_export_clock_offset_ns(
            rows=[{"timestamp_ns": 10_000_000_000}],
            node_id=1,
            min_skew_sec=2.0,
            recalc_threshold_sec=3.0,
            recalc_max_skew_sec=30.0,
            cache=cache,
            load_persisted_fn=lambda _nid: None,
            persist_fn=lambda nid, o: persisted_calls.append((nid, o)),
            log_func=lambda _msg: None,
            now_ns=16_000_000_000,
        )
        self.assertEqual(skew, 6_000_000_000)
        self.assertEqual(off, 6_000_000_000)
        self.assertEqual(cache[1], 6_000_000_000)
        self.assertEqual(persisted_calls, [(1, 6_000_000_000)])


if __name__ == "__main__":
    unittest.main()
