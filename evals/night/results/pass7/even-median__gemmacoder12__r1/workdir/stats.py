def median(nums):
    s = sorted(nums)
    if len(s) % 2 == 0:
        return (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
    return s[len(s) // 2]
