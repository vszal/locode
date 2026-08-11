import os

def changed(src, dst):
    if not (os.path.isdir(src) and os.path.isdir(dst)):
        return []
    source_files = os.listdir(src)
    dest_files = os.listdir(dst)
    new = [name for name in source_files if name not in dest_files]

if __name__ == '__main__':
    print(changed('a', 'b'))
