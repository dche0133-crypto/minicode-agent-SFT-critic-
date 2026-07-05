from slugify_title import slugify

def test_punctuation_and_whitespace():
    assert slugify('  Hello,   World!  ') == 'hello-world'
    assert slugify('CafE & Tea') == 'cafe-tea'
