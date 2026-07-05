from template_renderer import render

def test_replaces_known_values_and_defaults():
    assert render('Hello {{name}}', {'name': 'Ada'}) == 'Hello Ada'
    assert render('{{name|guest}}', {}) == 'guest'
