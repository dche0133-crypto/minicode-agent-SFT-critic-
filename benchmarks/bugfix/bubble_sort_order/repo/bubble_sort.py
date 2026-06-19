def bubble_sort(nums):
    result = list(nums)
    for i in range(len(result)):
        for j in range(0, len(result) - i - 1):
            if result[j] < result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
    return result
