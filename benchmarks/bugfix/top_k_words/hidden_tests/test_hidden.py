from top_k_words import top_k_words

def test_tie_break_and_large_k():
    assert top_k_words(['b', 'a', 'c', 'b', 'a', 'c'], 3) == ['a', 'b', 'c']
    assert top_k_words(['x'], 5) == ['x']
