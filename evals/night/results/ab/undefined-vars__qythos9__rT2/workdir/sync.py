def changed(src, dst):
    import os
    try:
        source_files = os.listdir(src)
    except FileNotFoundError:
        source_files = []
    try:
        dest_files = os.listdir(dst)
    except FileNotFoundError:
        dest_files = []
    new = []
    for name in source_files:
        if name not in dest_files:
            new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))
