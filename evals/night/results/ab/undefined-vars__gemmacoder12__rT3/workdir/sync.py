import os

def changed(src, dst):
    new = []
    if os.path.exists(src) and os.path.exists(dst):
        for name in os.listdir(src):
            if name not in os.listdir(dst):
                new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))
