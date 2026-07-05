from dependency_order import dependency_order

def test_orders_dependencies_first():
    graph = {'build': ['compile'], 'compile': ['parse'], 'parse': []}
    assert dependency_order(graph) == ['parse', 'compile', 'build']
