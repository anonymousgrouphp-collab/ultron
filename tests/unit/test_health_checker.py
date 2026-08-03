from utils.diagnostics.system_health_checker import HealthCheck
def test_health():
    assert HealthCheck.is_healthy({'vram_used_pct': 80}) is True
    assert HealthCheck.is_healthy({'vram_used_pct': 98}) is False
