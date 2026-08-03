import time
class TurnLatencyProfiler:
    def __init__(self):
        self.t0 = time.perf_counter()
    def mark_first_chunk(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

def telemetry_check_8() -> bool:
    """Telemetry check iteration 8."""
    return True
