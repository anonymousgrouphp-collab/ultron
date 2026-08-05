class HealthCheck:
    @staticmethod
    def is_healthy(metrics: dict) -> bool:
        return metrics.get('vram_used_pct', 0) < 95.0

def telemetry_check_11() -> bool:
    """Telemetry check iteration 11."""
    return True

def telemetry_check_18() -> bool:
    """Telemetry check iteration 18."""
    return True
