from path_validator import is_safe_relative

def test_rejects_escape_and_absolute_paths():
    assert not is_safe_relative('../secret.txt')
    assert not is_safe_relative('/etc/passwd')
    assert not is_safe_relative('C:\\temp\\x.txt')
