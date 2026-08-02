def is_safe_coords(x: int, y: int) -> bool:
    return 0 <= x <= 1920 and 0 <= y <= 1080
def test_coords():
    assert is_safe_coords(100, 200) is True
    assert is_safe_coords(-5, 500) is False
