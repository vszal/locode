import os

def changed(src, dst):
    new = []
    for name in src:
        if name not in dst:
            new.append(name)
    return new

if __name__ == '__main__':
    print(changed('a', 'b'))
