import os

def run(items):
    total = 0
    for it in items:
        print(f"Processing item {total + 1} of {len(items)}")
        process(it)
        total += 1
    print(f"Finished processing {total} items.")

def process(it):
    return it
