from utils.diagnostics.latency_profiler import TurnLatencyProfiler
def test_profiler():
    p = TurnLatencyProfiler()
    ms = p.mark_first_chunk()
    assert ms >= 0.0
