def retry_delay(attempt, base=1, cap=30):
    return base * attempt
