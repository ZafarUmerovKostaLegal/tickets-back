from application.html_pdf import sanitize_html


def test_scripts_and_outside_resources_are_forbidden_first_thing_in_head():
    html = '<!doctype html><html><head><title>x</title><script>fetch("http://internal")</script></head><body><img src="http://10.0.0.1/a.png"></body></html>'
    out = sanitize_html(html)
    head = out.index("<head>") + len("<head>")
    assert out[head:].startswith('<meta http-equiv="Content-Security-Policy"')
    assert "script-src 'none'" in out and "default-src data:" in out


def test_fragment_without_head_is_wrapped():
    out = sanitize_html("<p>hi</p>")
    assert out.startswith("<!doctype html><html><head><meta http-equiv=\"Content-Security-Policy\"")
    assert out.endswith("<body><p>hi</p></body></html>")
