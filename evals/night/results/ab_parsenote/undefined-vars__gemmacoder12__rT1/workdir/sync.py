import os

def changed(src, dst):
    source_files = os.listdir(src) if os.path.isdir(src) else []
    dest_files = os.listdir(dst) if os.path.isdir(dst) else []
    return [name for name in source_files if name not in dest_files]

if __name__ == '__main__':
    print(changed('a', 'b'))
