from word_frequency import word_frequency

def test_ignores_punctuation_and_extra_whitespace():
    assert word_frequency('hello,  hello!\nworld') == {'hello': 2, 'world': 1}
