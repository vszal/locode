import os

def changed(src, dst):
    try:
        source_files = os.listdir(src)
    except (FileNotFoundError, NotADirectoryError):
        source_files = []
    try:
        dest_files = os.listdir(dst)
    except (FileNotFoundError, NotADirectoryError):
        dest_files = []
    new = []
    for name in source_files:
        if name not in dest_files:
            new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))