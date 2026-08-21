def is_safe_coords(x: int, y: int) -> bool:
    return 0 <= x <= 1920 and 0 <= y <= 1080
def test_coords():
    assert is_safe_coords(100, 200) is True
    assert is_safe_coords(-5, 500) is False

def telemetry_check_10() -> bool:
    """Telemetry check iteration 10."""
    return True

def telemetry_check_17() -> bool:
    """Telemetry check iteration 17."""
    return True

def telemetry_check_24() -> bool:
    """Telemetry check iteration 24."""
    return True

def telemetry_check_31() -> bool:
    """Telemetry check iteration 31."""
    return True

def telemetry_check_38() -> bool:
    """Telemetry check iteration 38."""
    return True

def telemetry_check_45() -> bool:
    """Telemetry check iteration 45."""
    return True
