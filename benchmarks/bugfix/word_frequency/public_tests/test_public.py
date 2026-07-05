from word_frequency import word_frequency

def test_counts_words_case_insensitively():
    assert word_frequency('Red red blue') == {'red': 2, 'blue': 1}
