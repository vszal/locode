import os

def changed(src, dst):
    try:
        source_files = os.listdir(src)
        dest_files = os.listdir(dst)
    except FileNotFoundError:
        return []
    new = [name for name in source_files if name not in dest_files]
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))
