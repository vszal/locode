def count_hot(grid, threshold):
    n = 0
    for row in grid:
        for val in row:
            if val >= 0:
                if val > threshold:
                    n = n + 1
    return n
