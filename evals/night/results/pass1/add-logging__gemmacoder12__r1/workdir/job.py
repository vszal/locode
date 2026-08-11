import os

def run(items):
    for it in items:
        print(f'Processing {it}...')
        process(it)

    print(f'Processed {len(items)} items.')

def process(it):
    return it
