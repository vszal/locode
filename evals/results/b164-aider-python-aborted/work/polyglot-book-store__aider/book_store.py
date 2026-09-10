from functools import lru_cache
from itertools import combinations

# Discount rates for groups of size 1 to 5
# 1 book: 0% discount -> 800
# 2 books: 5% discount -> 760
# 3 books: 10% discount -> 720
# 4 books: 20% discount -> 640
# 5 books: 25% discount -> 600
DISCOUNTS = {
    1: 800,
    2: 760,
    3: 720,
    4: 640,
    5: 600
}

@lru_cache(maxsize=None)
def _min_cost(counts):
    """
    Recursively find the minimum cost for the given book counts.
    counts is a tuple of 5 integers representing the number of copies of books 1-5.
    """
    if all(c == 0 for c in counts):
        return 0
    
    min_cost = float('inf')
    
    # Try all possible group sizes from 5 down to 1
    for group_size in range(5, 0, -1):
        # Find indices of books that have count > 0
        available_books = [i for i, c in enumerate(counts) if c > 0]
        
        if len(available_books) < group_size:
            continue
            
        # Try all combinations of 'group_size' distinct books from available_books
        for combo in combinations(available_books, group_size):
            # Create a new counts tuple with the selected books decremented
            new_counts = list(counts)
            for idx in combo:
                new_counts[idx] -= 1
            
            # Calculate the cost for this grouping
            current_cost = DISCOUNTS[group_size] + _min_cost(tuple(new_counts))
            
            if current_cost < min_cost:
                min_cost = current_cost
                
    return min_cost

def total(basket):
    """
    Calculate the total price of the basket, applying optimal discounts.
    """
    if not basket:
        return 0
        
    # Count occurrences of each book (1-5)
    counts = [0] * 5
    for book in basket:
        if 1 <= book <= 5:
            counts[book - 1] += 1
            
    return _min_cost(tuple(counts))
