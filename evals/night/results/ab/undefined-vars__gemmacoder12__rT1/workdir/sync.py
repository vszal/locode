import os

def changed(src, dst):
    new = []
    src = src if os.path.exists(src) else '.'
    dst = dst if os.path.exists(dst) else '.'
    for name in os.listdir(src):
        if name not in os.listdir(dst):
            new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))
