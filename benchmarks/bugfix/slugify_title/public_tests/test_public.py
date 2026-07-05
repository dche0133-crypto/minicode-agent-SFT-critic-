from slugify_title import slugify

def test_basic_slug_and_punctuation():
    assert slugify('Hello World') == 'hello-world'
    assert slugify('Hello, World!') == 'hello-world'
