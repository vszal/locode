import os

def changed(src, dst):
    source_files = []
    dest_files = []
    try:
        source_files = os.listdir(src)
        dest_files = os.listdir(dst)
    except FileNotFoundError:
        pass
    new = []
    for name in source_files:
        if name not in dest_files:
            new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))