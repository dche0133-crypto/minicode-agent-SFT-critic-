from settings import DEFAULT_TIMEOUT

def request_options(config):
    return {'timeout': config.get('timeout', DEFAULT_TIMEOUT), 'retries': config.get('retries') or 1}
