def merge_intervals(intervals):
    if not intervals:
        return []
    result = [list(sorted(intervals)[0])]
    for start, end in sorted(intervals)[1:]:
        if start < result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result
