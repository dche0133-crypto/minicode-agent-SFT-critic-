def apply_defaults(config):
    config.setdefault('retries', 3)
    config.setdefault('timeout', 30)
    return config
