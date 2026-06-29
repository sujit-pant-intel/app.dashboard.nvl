"""
generate_flowchart.py

Simple helper to produce a pipeline flowchart using graphviz (Python wrapper).
Generates `pipeline_flow.svg` in the current folder.

Usage:
    python generate_flowchart.py

Requirements: `graphviz` (Python package) and the Graphviz system binary installed.
"""
from graphviz import Digraph


def build_graph(out_path: str = 'pipeline_flow') -> None:
    g = Digraph('yield_pipeline', format='svg')
    g.attr(rankdir='LR', fontsize='12')

    g.node('AQUA', 'AQUA fetch\n(optional)')
    g.node('Parse', 'Parse BinDefinitions\n(parse_bindef_to_crystalball.py)')
    g.node('GetDD', 'Append to Dashboard\n(get_dd_update.py)')
    g.node('Files', 'Outputs:\n- yield CSV\n- bindef CSV\n- dashboard.xlsx')

    g.edge('AQUA', 'Parse', label='yield CSV')
    g.edge('Parse', 'GetDD', label='bindef CSV')
    g.edge('AQUA', 'GetDD', label='yield CSV')
    g.edge('GetDD', 'Files')

    g.attr(label='Yield -> Bindef -> Dashboard pipeline', labelloc='t')
    g.render(out_path, cleanup=True)
    print('Generated', out_path + '.svg')


if __name__ == '__main__':
    build_graph()
