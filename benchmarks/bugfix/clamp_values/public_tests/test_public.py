from clamp_values import clamp

def test_inside_range():
    assert clamp(5, 0, 10) == 5

def test_lower_bound():
    assert clamp(-2, 0, 10) == 0
