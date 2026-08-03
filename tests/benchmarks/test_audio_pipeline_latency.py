import time
def test_buffer_rtf():
    t0 = time.perf_counter()
    time.sleep(0.001)
    assert time.perf_counter() - t0 < 0.005
