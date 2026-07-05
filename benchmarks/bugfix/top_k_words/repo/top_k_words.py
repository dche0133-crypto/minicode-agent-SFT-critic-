def top_k_words(words, k):
    counts = {word: words.count(word) for word in set(words)}
    return sorted(counts, key=counts.get, reverse=True)[:k]
