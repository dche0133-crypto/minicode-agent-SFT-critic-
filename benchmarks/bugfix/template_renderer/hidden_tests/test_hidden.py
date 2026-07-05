import pytest
from template_renderer import render

def test_defaults_escaping_and_missing_key():
    assert render('{{name|guest}}', {}) == 'guest'
    assert render('\\{{name}}', {'name': 'Ada'}) == '{{name}}'
    with pytest.raises(KeyError):
        render('{{name}}', {})
