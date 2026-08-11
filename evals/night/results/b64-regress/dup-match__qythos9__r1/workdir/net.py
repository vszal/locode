def fetch(url):
    timeout = 60
    return _get(url, timeout)

def poll(url):
    timeout = 30
    return _get(url, timeout)

def _get(url, timeout):
    return (url, timeout)
