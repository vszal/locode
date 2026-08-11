def changed(src, dst):
    return [name for name in src if name not in dst]

if __name__ == '__main__':
    print(changed('a', 'b'))
