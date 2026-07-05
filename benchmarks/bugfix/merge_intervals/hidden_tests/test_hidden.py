from merge_intervals import merge_intervals

def test_touching_and_unsorted_ranges():
    assert merge_intervals([(5, 7), (1, 2), (2, 5)]) == [[1, 7]]
