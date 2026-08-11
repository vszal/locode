import os

def run(items):
    count = 0
    for it in items:
        print(f"Processing item {count + 1}/{len(items)}")
        process(it)
        count += 1
    print(f"Finished processing {count} items.")

def process(it):
    return it
