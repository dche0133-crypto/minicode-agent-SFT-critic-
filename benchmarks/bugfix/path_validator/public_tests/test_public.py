from path_validator import is_safe_relative

def test_accepts_normal_and_rejects_parent_traversal():
    assert is_safe_relative('src/app.py')
    assert is_safe_relative('README.md')
    assert not is_safe_relative('../secret.txt')
