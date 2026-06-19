from bubble_sort import bubble_sort


def test_sorts_unsorted_numbers():
    assert bubble_sort([3, 1, 2]) == [1, 2, 3]


def test_keeps_sorted_numbers_sorted():
    assert bubble_sort([1, 2, 3]) == [1, 2, 3]
