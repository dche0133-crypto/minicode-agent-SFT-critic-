from merge_intervals import merge_intervals

def test_overlapping_and_touching_ranges():
    assert merge_intervals([(1, 3), (2, 4), (8, 9)]) == [[1, 4], [8, 9]]
    assert merge_intervals([(1, 2), (2, 5)]) == [[1, 5]]
