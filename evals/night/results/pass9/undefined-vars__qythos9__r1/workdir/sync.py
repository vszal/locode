import os

def changed(src, dst):
    new = []
    for name in os.listdir(src) if os.path.isdir(src) else []:
        if name not in os.listdir(dst) if os.path.isdir(dst) else []:
            new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))