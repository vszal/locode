def changed(src, dst):
    import os
    source_files = os.listdir(src)
    dest_files = os.listdir(dst)
    new = []
    for name in source_files:
        if name not in dest_files:
            new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))
