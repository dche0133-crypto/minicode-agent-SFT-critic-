class TokenBucket:
    def __init__(self, capacity, tokens=None):
        self.capacity = capacity
        self.tokens = capacity if tokens is None else tokens

    def consume(self, amount=1):
        if self.tokens > 0:
            self.tokens -= amount
            return True
        return False
