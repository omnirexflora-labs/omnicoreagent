# deep_coder/observability_globals.py
from config import load_config
from observability import (
    get_logger,
    get_metrics_collector,
    AuditLogger,
    HealthChecker,
    RateLimiter,
)

CONFIG = load_config()

# Logger
log = get_logger(
    name="deep_coder",
    level=CONFIG.observability.log_level,
    fmt=CONFIG.observability.log_format,
    file=CONFIG.observability.log_file,
    max_bytes=CONFIG.observability.log_max_bytes,
    backup=CONFIG.observability.log_backup_count,
)

# Observability singletons
metrics = get_metrics_collector(CONFIG.observability.enable_metrics)
audit = AuditLogger(CONFIG.security.audit_log_file)
health = HealthChecker()
rate_limiter = RateLimiter(
    max_req=CONFIG.security.rate_limit_requests,
    window=CONFIG.security.rate_limit_window,
)

# Health checks
health.add("config", lambda: True)
health.add("redis", lambda: CONFIG.storage.memory_store_type == "redis")
