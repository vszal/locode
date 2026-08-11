def total(rows):
    return sum(r.get('amount', 0) for r in rows)

data = [{'amount': 5}, {'amount': 7}, {'cost': 3}]
print(total(data))
