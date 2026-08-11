def run(data):
    header = 'START'
    out = [x ** 2 for x in data]
    footer = 'END'
    return header, out, footer
