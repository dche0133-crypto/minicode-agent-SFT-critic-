from bubble_sort import bubble_sort


def test_handles_empty_list():
    assert bubble_sort([]) == []


def test_handles_duplicates():
    assert bubble_sort([2, 1, 2, 1]) == [1, 1, 2, 2]


def test_does_not_mutate_input():
    values = [3, 1, 2]
    assert bubble_sort(values) == [1, 2, 3]
    assert values == [3, 1, 2]
