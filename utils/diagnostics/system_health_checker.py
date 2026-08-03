class HealthCheck:
    @staticmethod
    def is_healthy(metrics: dict) -> bool:
        return metrics.get('vram_used_pct', 0) < 95.0
