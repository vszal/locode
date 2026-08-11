import os

def changed(src, dst):
    if not os.path.isdir(src) or not os.path.isdir(dst):
        return []
    new = []
    for name in os.listdir(src):
        if name not in os.listdir(dst):
            new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))
