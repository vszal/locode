def changed(src, dst):
    import os
    new = []
    try:
        src_files = os.listdir(src)
    except FileNotFoundError:
        src_files = []
    try:
        dst_files = os.listdir(dst)
    except FileNotFoundError:
        dst_files = []
    for name in src_files:
        if name not in dst_files:
            new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))