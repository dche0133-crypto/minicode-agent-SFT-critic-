from clamp_values import clamp

def test_upper_bound_and_float():
    assert clamp(12, 0, 10) == 10
    assert clamp(1.5, 0.0, 2.0) == 1.5
