def process_server_url(url):
    url = url.replace('/panel', '')

    if url[-1] != '/':
        return url + '/'
    else:
        return url



