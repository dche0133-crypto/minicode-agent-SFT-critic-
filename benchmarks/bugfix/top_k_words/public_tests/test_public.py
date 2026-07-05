from top_k_words import top_k_words

def test_frequency_order_and_ties():
    assert top_k_words(['a', 'b', 'a', 'c', 'a', 'b'], 2) == ['a', 'b']
    assert top_k_words(['b', 'a', 'c', 'b', 'a', 'c'], 3) == ['a', 'b', 'c']
